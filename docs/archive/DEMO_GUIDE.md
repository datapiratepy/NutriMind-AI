# Demo Guide — 5-7 minutes

Script for the IBM SkillsBuild evaluation / interview demo. Works fully in
demo mode; in live mode the same flow shows real Granite output. Have the
app running and a profile saved beforehand; keep one nutrition PDF indexed.

## 0:00 — Opening (30 s)

Home page. Say: *"NutriMind is a multi-agent nutrition assistant on IBM
watsonx.ai. Three ideas differentiate it: a coordinator routes requests to
specialized agents, factual answers are grounded in documents with
citations, and every number is computed deterministically in Python — the
LLM never invents calories."* Point at the mode badge (Demo/IBM Live).

## 0:30 — Architecture in 30 s

Open **About the AI**. Scroll the request-flow diagram: message →
coordinator → specialist → tools → Granite → answer+metadata. Point at the
live system-status grid: *"model, embedding provider, vector store — all
real diagnostics from /api/system/info."*

## 1:00 — Agent routing, visibly (90 s)

Open **Chat**. Send `what is my BMI?` → expand **How this was answered**:
*"rules routed this, the coordinator answered itself, zero AI tokens —
deterministic math."* Then send `How much protein is in paneer?` → show the
workflow panel: Coordinator → Knowledge Agent → Retriever → engine →
response. **Highlight: routing reason, tool list, token count.**

## 2:30 — Meal analysis: LLM for language, Python for math (60 s)

Send `Today I ate 2 rotis, dal, paneer and an apple`. Show the nutrition
table: *"items extracted from language, numbers priced against a curated
food-composition table, quality score computed from macro bands — then the
AI writes the assessment around those numbers."* Mention it auto-logged.

## 3:30 — RAG with honest grounding (90 s)

Open **Knowledge base**. Show the indexed PDF (status, chunks, date). Use
**Test retrieval** with a phrase from the document → similarity %, filename,
page. Back in chat, ask a question the document answers → **Grounded** badge
+ source chips. Then ask something off-topic → *"no invented evidence: it
says general knowledge, explicitly."* **This is the honesty story.**

## 5:00 — Planner + PDF (60 s)

Open **Planner**: targets chips (*"Mifflin-St Jeor, deterministic"*) →
Generate → four meals respecting the vegetarian preference and allergy →
click **PDF** and open it: branded header, targets table, disclaimer.

## 6:00 — Dashboard close (45 s)

Open **Dashboard**: health score → click *Why this score?* (*"explainable,
five components"*), weekly bars, AI activity card (*"grounded share and
token spend — the agent layer is measurable"*). Close: *"156 tests, ~88%
coverage, runs with zero credentials, and every AI decision in the UI is
traceable to metadata, not guesswork."*

## If asked for more

`scripts/check_watsonx.py` live (token-free with --skip-inference),
the error handling (`/api/nope`), theme toggle, mobile layout.

## Pitfalls

Don't refresh mid-stream · in demo mode semantic retrieval only matches
near-exact text (hash embeddings) — use a phrase from the PDF ·
have `instance/` pre-seeded so the dashboard isn't empty.
