# Scripts

- `check_watsonx.py` — verifies IBM credentials, lists available Granite/embedding models, confirms configured model IDs (Phase 3).
- `seed_knowledge_base.py` — indexes every PDF in `knowledge_base/` into ChromaDB (Phase 5).

Run from the repo root with the virtualenv active, e.g. `python scripts/check_watsonx.py`.
