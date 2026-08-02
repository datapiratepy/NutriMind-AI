# Manual Testing Guide

Step-by-step verification for evaluators and pre-release checks.
Prerequisite: `pip install -r requirements.txt` (add `-r requirements-dev.txt`
to run the automated suite too). Each section lists the
steps and the **expected result**.

## 1. Demo mode boot (no credentials)

1. Ensure no `.env` exists (or `APP_MODE=demo`). Run `python run.py`.
2. Open http://127.0.0.1:5000.

**Expected:** app loads; topbar badge shows **Demo mode** (amber); no errors
in the console; `/api/system/info` shows `demo_active: true`.

## 2. IBM Live mode (with credentials)

1. Fill `.env` per docs/IBM_SETUP.md; run `python scripts/check_watsonx.py`.
2. Restart `python run.py`.

**Expected:** the check script ends `0 failed`; topbar badge shows **IBM
Live** (green); chat responses carry `llm_mode: live` in "How this was
answered"; `scripts/check_watsonx.py` consumed < 100 tokens.

## 3. Profile wizard

1. Go to /profile → complete steps 1-3 (e.g. 21 y, male, 175 cm, 70 kg,
   moderate, vegetarian, maintain, allergy "peanuts").
2. Watch step 2's live BMI preview while typing weight.

**Expected:** BMI preview updates instantly (22.9, "normal"); save shows a
success toast and redirects to the dashboard; /history → BMI tab has one
record. Entering age 500 → inline invalid state, no request sent.

## 4. Chat + agent routing + workflow panel

Send each of these on /chat and expand **How this was answered**:

| Message | Expected agent | Expected routing method |
|---|---|---|
| `hello` | Coordinator | rules (0 tokens) |
| `what is my BMI?` | Coordinator | rules — deterministic answer with your numbers |
| `How much protein is in paneer?` | Knowledge Agent | rules |
| `Today I ate 2 rotis, dal and an apple` | Meal Analyzer | rules |
| `Create a meal plan for me` | Meal Planner | rules |
| `Can diabetics eat bananas?` | Health Advisor | rules |
| `tell me something interesting` | Knowledge Agent | default (demo) / llm (live) |

**Expected everywhere:** progress statuses appear *before* text streams;
routing reason is human-readable; tool invocations are listed; tokens/time
shown; copy button works; scroll-up reveals the jump-to-bottom button.

## 5. Meal analyzer

1. /analyzer → "2 rotis, dal, paneer and an apple" → Analyze & log.

**Expected:** nutrition table with per-item rows and totals; macro chips;
quality score badge; suggestions paragraph; meal appears on dashboard
("Meals logged today") and /history. Unknown foods ("unicorn stew") →
friendly "couldn't identify" message, nothing logged.

## 6. Knowledge upload + RAG search

1. /knowledge → drop any text-based PDF.
2. After "indexed", use **Test retrieval** with a phrase from the document.
3. Delete the document; search again.

**Expected:** row shows status → indexed, page & chunk counts, upload date;
search returns chunks with similarity %, filename, page; after delete the
same search reports nothing above threshold. Corrupt/scanned PDF → status
**failed** with a readable reason (422 toast). Duplicate upload → rejected
with "already indexed".

## 7. Grounded vs general knowledge in chat

1. With a document indexed (live mode for semantic matching), ask a question
   the document answers.
2. Ask something unrelated to any document.

**Expected:** (1) green **Grounded** badge + source chips (file, page) and
[n] citations in the text; (2) **general knowledge** badge and the explicit
italic label at the end of the answer.

## 8. Meal planner + PDF export

1. /planner → Generate plan → wait for the card.
2. Click **PDF**.

**Expected:** four meals (Breakfast/Lunch/Snack/Dinner) with portions;
summary compares estimate vs deterministic target; PDF downloads with
header band, targets table, meals, hydration, notes, timestamp, disclaimer;
plan appears under Saved plans and reopens on click.

## 9. Dashboard

**Expected with data:** BMI card, health score with "Why this score?"
component breakdown, calorie progress vs target, water +1 button increments,
weekly bars (calories + water) after ≥2 days of data — honest empty state
before that; AI activity card counts responses/grounded/tokens; system
status matches /api/system/info.

## 10. Theme switching

Click the topbar theme button repeatedly.

**Expected:** cycles auto → light → dark; persists across reloads
(localStorage); auto follows the OS setting; no flash of wrong theme on
reload; both themes readable everywhere.

## 11. Mobile layout

DevTools responsive mode at 375 px width.

**Expected:** sidebar becomes a hamburger-off-canvas; no horizontal
scrolling on any page; chat input and send button usable; tables scroll
within their cards.

## 12. Error handling

1. Stop the server mid-chat → friendly toast, no raw stack.
2. POST /api/chat with an empty message (curl) → 400 with
   `{"error": {code, message, request_id}}`.
3. Upload an mp3 renamed to .pdf → 422 failed-status row with reason.
4. Visit /api/nope → JSON 404 envelope; /nope → styled 404 page.

**Expected:** every failure has a friendly explanation and a suggested
action; technical details appear only in `instance/logs/nutrimind.log`,
matched by the response's request ID.
