# Installation Guide

From zero to a running NutriMind AI. No prior knowledge assumed.

## System requirements

- **Python 3.11 or newer** (3.10 works; 3.11+ recommended) — check with
  `python --version`
- ~1.5 GB free disk (dependencies incl. ChromaDB)
- Windows 10/11, macOS, or Linux
- Internet connection (UI assets load from CDNs; pip downloads packages)
- *(Optional, for live AI)* an IBM Cloud Lite account — free, no credit card

## 1. Get the code

```bash
git clone <your-repo-url> nutrimind-ai
cd nutrimind-ai
```

## 2. Create a virtual environment

**Windows (PowerShell / cmd):**
```bash
python -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`.

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

Takes 2-5 minutes (ChromaDB is the largest). The optional
`sentence-transformers` line is commented out by default — only install it
if you want offline semantic embeddings (pulls PyTorch, ~2 GB).

## 4. Run — Demo mode (zero configuration)

```bash
python run.py
```

Open **http://127.0.0.1:5000**. The topbar shows **Demo mode**: every
feature works with deterministic sample AI responses and a mechanically
functional RAG pipeline. Perfect for a first look or an offline evaluation.

## 5. Run — IBM Live mode

1. Follow **[IBM_SETUP.md](IBM_SETUP.md)** end-to-end (account, watsonx.ai
   project, IAM API key, region — ~20 minutes).
2. Copy the config template and fill in your values:
   ```bash
   copy .env.example .env        # Windows
   cp .env.example .env          # macOS/Linux
   ```
3. Verify before running the app:
   ```bash
   python scripts/check_watsonx.py
   ```
   Every line should be `[PASS]` (uses < 100 of your monthly Lite tokens).
4. *(Recommended)* Put 1-3 nutrition PDFs (WHO/ICMR guidelines etc.) into
   `knowledge_base/` and index them:
   ```bash
   python scripts/seed_knowledge_base.py
   ```
5. `python run.py` — the topbar now shows **IBM Live**.

## 6. Environment variables

Everything is optional in demo mode. See `.env.example` for the full,
commented list: IBM credentials (`WATSONX_APIKEY`, `WATSONX_PROJECT_ID`,
`WATSONX_URL`), model IDs, `APP_MODE` (live/demo/auto), `EMBEDDINGS_PROVIDER`,
`FLASK_SECRET_KEY`, `MAX_UPLOAD_MB`, and the RAG tuning knobs.

## 7. Run the tests

```bash
pytest tests/ -q                                  # 156 tests, ~20 s
pytest tests/ --cov=nutrimind --cov-report=term   # with coverage (~88%)
```

No API keys needed — the suite runs in demo mode.

## Common problems

| Symptom | Cause → fix |
|---|---|
| `python` not found | Use `python3`, or reinstall Python with "Add to PATH" checked (Windows) |
| `pip install` very slow / fails on chromadb | Upgrade pip first: `python -m pip install --upgrade pip`; retry — partial downloads resume |
| `ModuleNotFoundError: flask` when running | Virtual environment not activated — re-run the activate command |
| Port 5000 already in use | Edit `run.py` port, or stop the other app (macOS: AirPlay Receiver uses 5000 — disable in System Settings) |
| App starts but shows Demo mode despite `.env` | Run `python scripts/check_watsonx.py` — it names the exact problem (key, project, region, model) |
| `[FAIL] IAM authentication` | Re-check `WATSONX_APIKEY` — no quotes, no spaces; see IBM_SETUP.md §10-11 |
| UI looks unstyled | No internet: Bootstrap/fonts load from CDNs. Connect once, or vendor assets |
| Windows: `activate` blocked by execution policy | Run PowerShell as user: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Fresh start wanted | Delete the `instance/` folder — the app recreates DB, uploads and vector store |

Still stuck? Every error response carries a request ID — find the matching
line in `instance/logs/nutrimind.log` for the technical detail.
