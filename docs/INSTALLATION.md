# Installation Guide

From zero to a running NutriMind AI. No prior knowledge assumed.

## System requirements

- **Python 3.13** — see [Python version](#0-python-version) below; the supported
  range is 3.11–3.14 and it is enforced by pip, not merely recommended
- ~1.5 GB free disk (dependencies incl. ChromaDB)
- Windows 10/11, macOS, or Linux
- Internet connection (UI assets load from CDNs; pip downloads packages)
- *(Optional, for live AI)* an IBM Cloud Lite account — free, no credit card

## 0. Python version

**Use Python 3.13.** That is what CI runs and what the deployment image will use.

The supported range is **3.11 – 3.14**, and it is not a matter of taste: the
`ibm-watsonx-ai` SDK declares `Requires-Python >=3.11,<3.15`, so pip refuses to
install outside it. Older SDK releases cap it lower still (1.3.x is `<3.14`,
1.2.x is `<3.13`), which means the interpreter and the SDK version have to be
chosen together — see the note in `requirements.in`.

3.13 rather than 3.14 for one practical reason: roughly twenty SDK releases work
on 3.13, but only three work on 3.14. If a release turns out to be broken, that
difference is your entire ability to roll back.

```powershell
py -3.13 --version    # Windows: should print Python 3.13.x
python3.13 --version  # macOS/Linux
```

If it is missing, install it from [python.org/downloads](https://www.python.org/downloads/)
(on Windows, tick **Add python.exe to PATH**). Installing 3.13 does not remove or
replace any other Python version you already have.

## 1. Get the code

```bash
git clone <your-repo-url> nutrimind-ai
cd nutrimind-ai
```

## 2. Create a virtual environment

Name the interpreter explicitly. `python` points at whichever version is first
on PATH, which is how a project ends up silently built against the wrong one.

**Windows (PowerShell / cmd):**
```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`. Confirm it took effect:

```bash
python --version        # Python 3.13.x
```

## 3. Install dependencies

```bash
pip install -r requirements.txt                          # to run the app
pip install -r requirements.txt -r requirements-dev.txt  # to run the tests too
```

Takes 2-5 minutes (ChromaDB is the largest). Versions are pinned exactly, so
everyone gets the set the tests were verified against.

`requirements-dev.txt` adds pytest, ruff and pip-audit. They are kept separate
so a production install never pulls a test runner into the deployed environment.

The optional `sentence-transformers` line is commented out by default — only
install it if you want offline semantic embeddings (pulls PyTorch, ~2 GB).

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

Requires `requirements-dev.txt` (see step 3).

```bash
pytest tests/ -q                                  # 180 tests, ~20 s
pytest tests/ --cov=nutrimind --cov-report=term   # with coverage (~88%)
ruff check .                                      # lint, as CI runs it
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
