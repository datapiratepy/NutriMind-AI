"""Shared Flask extension instances.

Created unbound here (initialized in ``create_app``) to avoid circular
imports: models import ``db`` from this module, never from the app package.
"""

from __future__ import annotations

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

#: SQLAlchemy 2.x style database handle — bound in ``create_app``.
db = SQLAlchemy()

#: Alembic integration. The schema is owned by the versioned scripts in
#: ``migrations/``, never by ``create_all()`` — see ``create_app`` for why.
migrate = Migrate()

#: CSRF protection. Applies to every state-changing request, including the JSON
#: API: now that a session cookie exists, a cross-site request could ride it.
csrf = CSRFProtect()

#: Session-cookie authentication. Configured in ``create_app``.
login_manager = LoginManager()
