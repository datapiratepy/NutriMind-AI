# IBM Cloud + watsonx.ai Setup Guide

Complete walkthrough from zero to a working NutriMind AI connection. No prior
IBM experience assumed. Time required: ~20 minutes.

All IBM facts below (model IDs, endpoints, plan limits) were verified against
the official IBM documentation on **2026-07-06** — sources at the end.

> **No IBM account yet?** NutriMind still runs: with no credentials it starts
> in **demo mode** automatically. Follow this guide when you want live
> Granite responses.

---

## 1. Create an IBM Cloud account

1. Go to <https://cloud.ibm.com/registration> and sign up (the Lite tier needs no credit card in most regions).
2. Verify your e-mail and log in to <https://cloud.ibm.com>.

## 2. Provision watsonx.ai

watsonx.ai consists of two services — you need both, and the signup flow creates them together:

| Service | Purpose | Lite plan includes |
|---|---|---|
| **watsonx.ai Studio** | Projects, Prompt Lab UI | 1 user, 10 CUH/month |
| **watsonx.ai Runtime** | Foundation-model inference (what NutriMind calls) | ~**300,000 tokens/month** + 20 CUH |

1. Go to <https://dataplatform.cloud.ibm.com/registration/stepone?context=wx>.
2. **Choose your region carefully** (e.g. Dallas or Frankfurt) — your API endpoint must match it forever (see §6).
3. Complete the guided setup; it provisions both Lite services into your account.

## 3. Create a project and associate the Runtime

1. In <https://dataplatform.cloud.ibm.com> (make sure the region selector shows *your* region), open **Projects → New project**, name it e.g. `nutrimind`, create it.
2. **Critical step:** open the project → **Manage → Services & integrations → Associate service** → pick your **watsonx.ai Runtime** (Lite) instance.
   *Skipping this is the #1 cause of "project not found / no associated instance" errors.*

## 4. Generate an IAM API key

1. <https://cloud.ibm.com> → **Manage → Access (IAM) → API keys** → **Create**.
2. Name it (e.g. `nutrimind-key`) and **copy the key immediately** — IBM shows it only once.
3. This is a *user* API key tied to your identity; do not create a Service ID key for this project.

## 5. Find your Project ID

Project → **Manage → General → Details → Project ID** → copy the UUID
(looks like `a1b2c3d4-....`). Do **not** confuse it with a *deployment space* ID.

## 6. Pick the regional endpoint

Must match the region from step 2:

| Region | WATSONX_URL |
|---|---|
| Dallas (us-south) | `https://us-south.ml.cloud.ibm.com` |
| Frankfurt (eu-de) | `https://eu-de.ml.cloud.ibm.com` |
| London (eu-gb) | `https://eu-gb.ml.cloud.ibm.com` |
| Tokyo (jp-tok) | `https://jp-tok.ml.cloud.ibm.com` |
| Sydney (au-syd) | `https://au-syd.ml.cloud.ibm.com` |
| Toronto (ca-tor) | `https://ca-tor.ml.cloud.ibm.com` |

## 7. Model IDs (why these defaults)

| Purpose | Default | Why |
|---|---|---|
| Chat | `ibm/granite-4-h-small` | The current-generation Granite chat model provided **multitenant** (billed per token) — the only kind usable on the Lite plan. Most other Granite models (incl. granite-3-3-8b-instruct and the Granite 4.1 family) are currently "deploy on demand" = dedicated paid hardware. |
| Embeddings | `ibm/granite-embedding-278m-multilingual` | The current non-deprecated IBM embedding model (768-dim, 12 languages). The Slate v2 models are deprecated with withdrawal dates in 2026. |

Catalogs change — **never trust a blog post (or this file) blindly**: run the
connectivity check below; it lists exactly what *your* region offers and warns
if your configured model is deprecated.

## 8. Configure `.env`

```bash
cp .env.example .env     # Windows: copy .env.example .env
```

Fill in:

```
WATSONX_APIKEY=<key from step 4>
WATSONX_PROJECT_ID=<UUID from step 5>
WATSONX_URL=<endpoint from step 6>
WATSONX_MODEL_ID=ibm/granite-4-h-small
WATSONX_EMBEDDING_MODEL_ID=ibm/granite-embedding-278m-multilingual
APP_MODE=auto
```

Rules: no quotes, no trailing spaces, never commit `.env` (it is git-ignored).

## 9. Run the connectivity check

```bash
pip install -r requirements.txt
python scripts/check_watsonx.py
```

Healthy output ends like:

```
  [PASS] IAM authentication + project — project a1b2c3d4…
  [PASS] Granite chat model — ibm/granite-4-h-small is available
  [PASS] Embedding model — ibm/granite-embedding-278m-multilingual is available
  [PASS] Chat generation — reply='OK' · 21 tokens
  [PASS] Streaming — received 4 chunks
  [PASS] Embedding generation — vector dimensions: 768
Summary: 9 passed, 0 failed, 0 warnings, 0 skipped
```

Useful flags: `--skip-inference` (consumes zero tokens), `--env path/to/.env`.
A full run costs < 100 of your ~300,000 monthly tokens.

Without credentials the same script proves the demo fallback works and exits
cleanly — that is expected, not an error.

## 10. Common setup mistakes

1. **Runtime not associated with the project** (§3.2 skipped) → project errors even with a valid key.
2. **Region mismatch** — project created in Frankfurt but `WATSONX_URL` points to Dallas → 404s.
3. **Wrong ID type** — a deployment-space ID in `WATSONX_PROJECT_ID`.
4. **Quotes or spaces in `.env`** — `WATSONX_APIKEY="abc "` breaks IAM auth (NutriMind strips these defensively, but don't rely on it elsewhere).
5. **Dedicated-only model configured** — e.g. `granite-3-3-8b-instruct` is deploy-on-demand; the check script lists valid multitenant alternatives.
6. **Lite tokens exhausted** — 429 errors near month-end; quota resets monthly. `APP_MODE=demo` keeps the app usable meanwhile.
7. **Corporate proxy/VPN** blocking `*.ml.cloud.ibm.com` or IAM (`iam.cloud.ibm.com`).

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `[FAIL] IAM authentication` / 401 | Bad or expired API key | Regenerate the key (§4); paste without quotes |
| 403 forbidden | Key from a different account | Use a key from the account owning the project |
| `project ... not found` / 404 | Wrong Project ID or missing Runtime association | §5 + §3.2 |
| `model not supported` | Model not multitenant in your region | Run check script; pick from its list |
| HTTP 429 | Rate limit or monthly quota used up | Wait / next month / demo mode |
| Timeouts | Network, proxy, wrong region URL | §6; test `curl https://us-south.ml.cloud.ibm.com` |
| SSL errors | Corporate TLS interception | Try another network; see SDK `verify` options |

## 12. Security checklist

- `.env` is git-ignored — verify with `git status` before your first push.
- Rotate the API key if it ever appears in a terminal recording or screenshot.
- The app validates configuration at startup and fails fast with plain-language messages; it never prints the key.

---

**Sources (checked 2026-07-06):**
[IBM foundation models](https://www.ibm.com/docs/en/watsonx/saas?topic=models-foundation) ·
[Supported encoder models](https://www.ibm.com/docs/en/watsonx/saas?topic=models-supported-encoder) ·
[watsonx.ai Runtime plans](https://www.ibm.com/docs/en/watsonx/saas?topic=cloud-watsonxai-runtime-plans) ·
[ibm-watsonx-ai SDK: ModelInference](https://ibm.github.io/watsonx-ai-python-sdk/v1.5.11/fm_model_inference.html) ·
[ibm-watsonx-ai SDK: Embeddings](https://ibm.github.io/watsonx-ai-python-sdk/v1.5.11/fm_embeddings.html)
