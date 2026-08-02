# Scripts

- `check_watsonx.py` — verifies IBM credentials, lists available Granite/embedding models, confirms configured model IDs.
- `upgrade_database.py` — applies pending migrations. **The deployment entry point; use it instead of `flask db upgrade`**, which cannot handle a database created before migrations were adopted.
- `check_schema.py` — compares the live database against the SQLAlchemy models; exits 1 on drift. CI runs it after applying migrations to PostgreSQL, and it works as a release gate after an upgrade.
- `seed_knowledge_base.py` — indexes every PDF in `knowledge_base/` into ChromaDB.

Run from the repo root with the virtualenv active, e.g. `python scripts/check_watsonx.py`.
