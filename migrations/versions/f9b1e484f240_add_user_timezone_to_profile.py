"""add user timezone to profile

Adds ``user_profile.timezone`` so daily aggregates bucket by the user's own
calendar day rather than the UTC one.

Nullable with no backfill, deliberately. NULL means "not set", which
``local_today()`` resolves to UTC — exactly the behaviour every existing row had
before this column existed. So the upgrade changes nothing for anyone until they
choose a zone, which is the only safe default: guessing a timezone from the
``country`` field would silently move existing users' day boundaries and
retroactively reassign meals and water logs to different days.

No data is rewritten. Timestamps stay naive UTC (see nutrimind/utils/time.py);
this column only affects which day a stored timestamp is counted under.

Revision ID: f9b1e484f240
Revises: 5416df905dc6
Create Date: 2026-08-05 21:57:53.258519
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'f9b1e484f240'
down_revision = '5416df905dc6'
branch_labels = None
depends_on = None


def upgrade():
    # batch_alter_table because SQLite cannot ALTER TABLE ADD COLUMN with every
    # constraint form; on PostgreSQL this compiles to a plain ALTER.
    with op.batch_alter_table('user_profile', schema=None) as batch_op:
        batch_op.add_column(sa.Column('timezone', sa.String(length=64),
                                      nullable=True))


def downgrade():
    with op.batch_alter_table('user_profile', schema=None) as batch_op:
        batch_op.drop_column('timezone')
