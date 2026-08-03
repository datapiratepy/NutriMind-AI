"""add user accounts and per-user ownership

Revision ID: 5416df905dc6
Revises: 08b6e47aa0ed
Create Date: 2026-08-02 20:16:58.462527

Turns a single-user application into a multi-user one without losing anything
that is already stored.

Autogenerate produced ``add_column(..., nullable=False)`` for every table, which
cannot work: existing rows have no value for the new column and the ALTER fails
immediately. This is rewritten as the standard three-phase backfill —

    1. add ``user_id`` as NULLABLE everywhere
    2. adopt any existing rows into one account
    3. tighten to NOT NULL and add the indexes and foreign keys

— because a schema change that carries data has to leave the database valid at
every step, not just at the end.

Adoption creates a placeholder account owning everything that existed before
this revision. It cannot be signed into until someone sets a password on it
(``python scripts/manage_users.py set-password legacy@nutrimind.invalid``),
so the data is preserved and reachable but not exposed. On an empty database no
account is created and this is a plain schema change.

Deliberately no imports from ``nutrimind``: a migration has to keep working
against the schema as it was on the day it was written, and application models
move on. Everything here is expressed in raw SQL and Alembic operations.
"""
import datetime as dt

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '5416df905dc6'
down_revision = '08b6e47aa0ed'
branch_labels = None
depends_on = None


#: Tables that gain an owner, and the constraint names used for their foreign
#: keys. Named explicitly rather than left to Alembic: SQLite renders unnamed
#: constraints anonymously, and `downgrade` then has nothing to drop by name.
OWNED_TABLES = (
    "bmi_records",
    "chat_messages",
    "documents",
    "meal_logs",
    "meal_plans",
    "user_profile",
    "water_logs",
)

#: RFC 2606 reserves `.invalid` precisely for addresses that must never resolve,
#: so this can neither collide with a real signup nor accidentally receive mail.
LEGACY_EMAIL = "legacy@nutrimind.invalid"

#: Werkzeug hashes always contain "$", so this sentinel can never match one and
#: no password will ever verify against it. Mirrors User.UNUSABLE_PASSWORD;
#: duplicated as a literal rather than imported, per the note above.
UNUSABLE_PASSWORD = "!"


def _fk_name(table: str) -> str:
    return f"fk_{table}_user_id_users"


def _adopt_existing_rows(connection) -> int | None:
    """Give every pre-existing row an owner. Returns the account id, or None.

    Returns None when the database is empty, which is the fresh-install case:
    creating a placeholder account there would leave a permanent, confusing
    ghost row in a brand-new deployment.
    """
    row_counts = {
        table: connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608
        for table in OWNED_TABLES
    }
    if not any(row_counts.values()):
        return None

    # A unique user_id on user_profile means several pre-existing profiles
    # cannot all be adopted by one account. The old code enforced "one profile"
    # only by convention (it read whichever row came back first), so this fails
    # loudly with instructions rather than raising an opaque IntegrityError
    # halfway through the migration.
    if row_counts["user_profile"] > 1:
        raise RuntimeError(
            f"Found {row_counts['user_profile']} rows in user_profile, but a "
            "profile now belongs to exactly one account. Keep the row you want "
            "and delete the others, then re-run the upgrade."
        )

    connection.execute(
        sa.text(
            "INSERT INTO users (email, password_hash, display_name, is_active, "
            "created_at) VALUES (:email, :pw, :name, :active, :created)"),
        {"email": LEGACY_EMAIL, "pw": UNUSABLE_PASSWORD,
         "name": "Existing data", "active": True,
         "created": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)},
    )
    user_id = connection.execute(
        sa.text("SELECT id FROM users WHERE email = :email"),
        {"email": LEGACY_EMAIL}).scalar_one()

    for table in OWNED_TABLES:
        connection.execute(
            sa.text(f"UPDATE {table} SET user_id = :uid"), {"uid": user_id})  # noqa: S608

    total = sum(row_counts.values())
    print(f"  adopted {total} existing row(s) into account {LEGACY_EMAIL} "
          f"(id={user_id}). Set a password on it to sign in:")
    print(f"    python scripts/manage_users.py set-password {LEGACY_EMAIL}")
    return user_id


def upgrade():
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=80), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    # -- phase 1: nullable columns, so existing rows remain valid ------------
    for table in OWNED_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))

    # -- phase 2: backfill ---------------------------------------------------
    _adopt_existing_rows(op.get_bind())

    # -- phase 3: tighten ----------------------------------------------------
    for table in OWNED_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column('user_id', existing_type=sa.Integer(),
                                  nullable=False)
            batch_op.create_index(batch_op.f(f'ix_{table}_user_id'), ['user_id'],
                                  # A user has exactly one profile.
                                  unique=(table == 'user_profile'))
            batch_op.create_foreign_key(_fk_name(table), 'users', ['user_id'],
                                        ['id'], ondelete='CASCADE')

    # water_logs was unique on `date` alone — the clearest evidence that
    # single-user was a schema invariant, since the second account to log water
    # on any day would have collided. Uniqueness moves to (user_id, date).
    with op.batch_alter_table('water_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_water_logs_date'))
        batch_op.create_index(batch_op.f('ix_water_logs_date'), ['date'],
                              unique=False)
        batch_op.create_unique_constraint('uq_water_user_date', ['user_id', 'date'])


def downgrade():
    """Reverse the schema change.

    Destructive by nature: dropping ``user_id`` discards who owned what, and
    ``water_logs`` regains a unique constraint on ``date`` that multi-user data
    will violate. Usable to back out immediately after a failed upgrade on a
    single-user database; for anything else, restore from backup.
    """
    with op.batch_alter_table('water_logs', schema=None) as batch_op:
        batch_op.drop_constraint('uq_water_user_date', type_='unique')
        batch_op.drop_index(batch_op.f('ix_water_logs_date'))
        batch_op.create_index(batch_op.f('ix_water_logs_date'), ['date'],
                              unique=True)

    for table in reversed(OWNED_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(_fk_name(table), type_='foreignkey')
            batch_op.drop_index(batch_op.f(f'ix_{table}_user_id'))
            batch_op.drop_column('user_id')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))
    op.drop_table('users')
