# Screenshot & Demo Asset Checklist

Capture at ~1440×900, dark theme (looks best), with a saved profile and one
indexed PDF. Save into `docs/screenshots/` with these exact names (README
links them).

## Required

| File | Page / state | Must show |
|---|---|---|
| `home.png` | / | hero, sample prompt chips, mode badge |
| `dashboard.png` | /dashboard with data | health score + weekly bars + AI activity |
| `chat.png` | /chat after 2-3 messages | agent badges, grounded badge, streaming layout |
| `routing-metadata.png` | chat, "How this was answered" expanded | workflow panel, routing reason, tools, citations |
| `knowledge.png` | /knowledge | document table (status/chunks/date) + retrieval preview results |
| `planner.png` | /planner with a generated plan | targets chips + meals + summary |
| `analyzer.png` | /analyzer after analysis | nutrition table, quality badge, macro chips |
| `about.png` | /about | flow diagram + live status grid |
| `pdf-export.png` | the downloaded PDF open | header band + targets table + meals |

Rename three of these (chat, dashboard, knowledge) or update README paths —
it currently expects `chat.png`, `dashboard.png`, `knowledge.png`.

## Optional but high-impact

- `demo.gif` (≤ 30 s): type a question → statuses stream → answer + badges →
  expand metadata. Tools: ScreenToGif (Windows) or Kap (macOS). Keep < 10 MB
  for the README.
- `light-mode.png` — one shot proving both themes.
- watsonx.ai **token usage screenshot** for presentation slide 10 (capture
  after your live-mode demo run).

## Capture tips

Seed data first (profile + 2-3 analyzed meals + water) so nothing is empty ·
hide bookmarks bar · 100% zoom · crop the browser chrome or use a clean
window.
