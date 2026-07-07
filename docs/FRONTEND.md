# NutriMind AI — Frontend Architecture

Phase 7 deliverable · Bootstrap 5.3 + vanilla JS, no build step (deliberate
for a Flask project: `git clone` → `python run.py` → working UI).

## 1. UI architecture

Server-rendered Jinja2 templates provide structure and static content; small
per-page JS modules add behavior against the existing JSON APIs. There is no
client-side router, bundler or framework — total custom JS is ~1,400 lines
across seven focused modules.

```
templates/
  base.html          app shell: topbar, responsive sidebar (offcanvas <lg),
                     footer, toast container, shared confirm modal, theme boot
  partials/          sample_prompts (reused home+chat), agent_badge (server-
                     side badge variant), footer
  <page>.html        one template per route, extends base.html
  errors/            standalone (no base.html dependency — safe during 500s)
static/js/
  app.js             window.NM: theme, toasts, confirm, fetch wrapper (API
                     error envelope → friendly messages), sanitized markdown,
                     health badge, sample-prompt wiring, "/" shortcut
  chat.js            SSE client + metadata rendering (the flagship page)
  dashboard.js / planner.js / analyzer.js / knowledge.js / profile.js
static/css/style.css design system (~250 lines of tokens + components)
```

## 2. Theme system

Tokens (`--nm-*`) defined per `[data-bs-theme]`; Bootstrap picks up the same
attribute. Three states — light / dark / auto — cycled by the topbar toggle,
persisted in `localStorage("nutrimind-theme")`; auto follows
`prefers-color-scheme` (with a change listener). An inline script in `<head>`
applies the theme before first paint to prevent flashing. IBM Carbon-inspired
palette: IBM Plex Sans, `#0f62fe` accent (accessible `#78a9ff` on dark), flat
surfaces, 10 px radii, no gradients.

## 3. Component reuse

CSS components: `.nm-card`, `.nm-badge(-accent/-green/-amber/-red)`,
`.nm-empty`, `.nm-skeleton`, `.nm-progress`, `.nm-table`, `.nm-prompt-chip`,
`.nm-flow` (workflow chips), `.nm-cite` (citation chips), `.nm-drop`
(upload), `.nm-step-dot` (wizard). Behavior components in `app.js` are used
by every page (toasts, confirm dialog, fetch, markdown). The chat metadata
badges exist in two variants by design: client-side (chat.js, from live SSE
meta) and server-side (`partials/agent_badge.html`, for history rendering).

## 4. SSE workflow (chat page)

`chat.js` POSTs to `/api/chat` and reads the response body as a stream
(fetch + ReadableStream — EventSource can't POST). Events are parsed on
`\n\n` boundaries and dispatched:

- `status` → progress line under the messages ("Searching the knowledge
  base…") so the UI is never silently waiting
- `routing` → progress line shows "intent → Agent (method)"
- `token` → appended to the assistant bubble, re-rendered as sanitized
  markdown (marked + DOMPurify), typing caret shown, auto-scroll
- `final` → badges (agent, grounded/general, sources, demo), footer (copy,
  timestamp, "How this was answered"), and the expandable metadata panel:
  **workflow visualization built purely from `meta`** (coordinator method →
  agent → tools actually used → Granite/demo → response), routing reason,
  tool invocation list, citation chips, provider/timing/tokens
- `error` → toast with the friendly message + hint

Session ID persists in `sessionStorage`; on reload the conversation is
restored from `/api/chat/history` using the server-side metadata.

## 5. Accessibility

Semantic landmarks (header/nav/main/footer), skip-to-content link, visible
`:focus-visible` outlines, `aria-live` chat region and BMI preview,
`aria-expanded` on disclosure buttons, labels on all inputs and icon-only
buttons, keyboard-operable upload dropzone (Enter/Space), Enter-to-send with
Shift+Enter newline, `prefers-reduced-motion` respected (animations off),
color always paired with icon/text (grounded = icon + word, not just green).

## 6. UI-support endpoints added this phase

`GET /api/meal-plans/<id>` (plan detail for the planner page),
`GET /api/chat/sessions` (conversation list for dashboard/history), and an
honest `501` stub for `GET /api/export/meal-plan/<id>.pdf` until Phase 8.
Backend behavior fix surfaced by tests at UTC midnight: "today" now uses the
UTC date everywhere, matching stored timestamps.

## 7. Known trade-offs

- CDN dependencies (Bootstrap, icons, marked, DOMPurify, IBM Plex) — an
  offline demo would need vendoring (documented for Phase 10).
- Live BMI preview duplicates the BMI formula client-side (preview only;
  authoritative values are always server-computed).
- Streaming markdown re-renders the whole message per token — fine for chat
  lengths; a diffing renderer is unnecessary complexity here.
