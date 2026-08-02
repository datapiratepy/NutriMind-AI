# Scripts

- `check_watsonx.py` — verifies IBM credentials, lists available Granite/embedding models, confirms configured model IDs.
- `seed_knowledge_base.py` — indexes every PDF in `knowledge_base/` into ChromaDB.

Run from the repo root with the virtualenv active, e.g. `python scripts/check_watsonx.py`.
