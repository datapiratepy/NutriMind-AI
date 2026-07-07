# Final Submission Checklist — NutriMind AI v1.0.0

Work through top to bottom; everything unchecked requires *your machine*
(screenshots, live credentials, GitHub account).

## GitHub

- [ ] Create **public** repo `nutrimind-ai` with README enabled
- [ ] `git init && git add . && git commit -m "NutriMind AI v1.0.0"` → push
- [ ] Verify `.env` is NOT in the repo (`git ls-files | findstr .env` shows only `.env.example`)
- [ ] Delete the two ignored dev artifacts if desired (`.sync_probe.txt`, `.verify_phase3_bundle.py`)
- [ ] CI badge turns green on first push (Actions tab)
- [ ] Add repo description + topics (docs/PORTFOLIO_CONTENT.md)
- [ ] Capture screenshots per docs/SCREENSHOT_CHECKLIST.md → `docs/screenshots/`
- [ ] Confirm README images and Mermaid diagrams render on GitHub

## IBM SkillsBuild / AICTE upload

- [ ] Build the .pptx from docs/PRESENTATION.md using the official template in `_reference/` (keep its fonts: Arial 28/20)
- [ ] Slide 10: insert your real watsonx token-usage screenshot
- [ ] Slide 12: insert the public GitHub URL
- [ ] Slides 14-15: insert Credly + IBM BOB certificates
- [ ] Verify against docs/SUBMISSION_CHECKLIST.md (requirement map)

## Live-mode validation (once, before demo)

- [ ] `python scripts/check_watsonx.py` → all PASS
- [ ] `python scripts/seed_knowledge_base.py` with 1-3 nutrition PDFs
- [ ] One grounded chat question → citations appear; note similarity scores
      via /knowledge search (re-tune RAG_SIMILARITY_THRESHOLD only if needed)
- [ ] Screenshot watsonx usage page for slide 10

## Demo readiness

- [ ] Rehearse docs/DEMO_GUIDE.md once (5-7 min)
- [ ] Profile saved, 2-3 meals analyzed, water logged (dashboard not empty)
- [ ] Demo mode fallback tested (rename .env temporarily) — quota-proof demo

## Final sanity

- [ ] `pytest tests/ -q` → 156 passed
- [ ] `python run.py` → all 9 pages, both themes
- [ ] docs/MANUAL_TESTING.md sections 1, 4, 6, 8 spot-checked

---

## Official project statistics (v1.0.0)

| Metric | Value |
|---|---|
| Total tracked files | 124 |
| Python application modules | 47 (~4,100 non-blank lines) |
| Test files / tests | 19 files · **156 tests** · ~88% coverage |
| HTML templates | 16 |
| JavaScript modules | 7 (+1 design-system CSS) |
| HTTP endpoints | 36 (27 JSON/SSE API + 9 pages) |
| SQLAlchemy models | 7 |
| AI agents | 5 (Coordinator + 4 specialists) |
| System prompts | 5 versioned prompt files |
| Documentation files | 20 markdown docs (incl. README) |
| Curated food table | 85 foods with macros, micros, Hindi aliases |
| CI | GitHub Actions, demo-mode, zero secrets |
| Technologies | Python, Flask, IBM watsonx.ai, Granite, ChromaDB, SQLAlchemy, SQLite, SSE, Bootstrap 5, ReportLab, pypdf, pytest |
