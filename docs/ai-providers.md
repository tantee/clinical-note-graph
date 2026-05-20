# AI provider configuration

> **Audience:** anyone choosing which model(s) and provider to point this stack at.

Any OpenAI-compatible endpoint works. The backend speaks the standard `/chat/completions` and `/embeddings` shape, so **`AI_PROVIDER=openai` is the right setting for OpenAI, OpenRouter, Groq, vLLM, Ollama, DeepSeek, Azure OpenAI, and every other compatible host** — only the `AI_BASE_URL` changes per host. The `openai` label is historical; treat it as "use the OpenAI wire protocol", not "talk to OpenAI specifically".

You can configure providers three ways:
- Edit `.env` and restart `docker compose up`.
- `PATCH /api/config` — overrides are stored in Postgres and merged into runtime settings without restarting.
- Visit `/#/config` in the UI and click **Quick setup** — it fills OpenRouter / OpenAI / Groq presets so you only need to paste your key.

You can also use a different model **per task** (extract, summary, coding) on top of a single provider — see [Per-task model overrides](#per-task-model-overrides) below.

## Provider quick-reference

| Provider | Key URL | `AI_BASE_URL` | Notes |
|---|---|---|---|
| **OpenRouter** (recommended) | <https://openrouter.ai/keys> | `https://openrouter.ai/api/v1` | One key → 200+ models incl. Claude, GPT-4o, Gemini, Llama, Qwen. Pay-per-use, no monthly commitment. Embeddings supported via `openai/text-embedding-3-*`. |
| **OpenAI** | <https://platform.openai.com/api-keys> | `https://api.openai.com/v1` | Native; best for `gpt-4o`, `gpt-4o-mini`, OpenAI embeddings. |
| **Anthropic via OpenRouter** | as above | as above | Use `AI_MODEL=anthropic/claude-3.5-sonnet` (or `claude-3.7-sonnet`, `claude-3.5-haiku`). Direct Anthropic API uses a different schema and isn't supported by this backend yet. |
| **Google Gemini via OpenRouter** | as above | as above | Use `AI_MODEL=google/gemini-2.0-flash-001` or `google/gemini-2.5-pro`. |
| **Groq** | <https://console.groq.com/keys> | `https://api.groq.com/openai/v1` | Very fast inference; good for `llama-3.3-70b-versatile`, `qwen-2.5-72b`. No embedding endpoint — leave `AI_EMBEDDING_MODEL` blank or point at OpenAI for embeddings. |
| **DeepSeek** | <https://platform.deepseek.com/api_keys> | `https://api.deepseek.com/v1` | Cheap; use `deepseek-chat` (V3) or `deepseek-reasoner` (R1). Reasoner is slow but good at structured extraction. |
| **Azure OpenAI** | Azure portal | `https://<resource>.openai.azure.com/openai/deployments/<deployment>` | Set `AI_MODEL` to your deployment name. |
| **Local (vLLM / LM Studio / Ollama)** | n/a | `http://host:port/v1` | Any OpenAI-compatible self-hosted server. From a container, use `host.docker.internal` or the LAN IP. |
| **Mock / offline** | n/a | leave blank, set `AI_PROVIDER=mock` | Deterministic keyword-based extractor with ICD-10 / SNOMED / LOINC / RxNorm lookups. Runs without a key. |

## OpenRouter setup (recommended)

OpenRouter exposes hundreds of models behind a single key, including the latest Claude, GPT, Gemini, Llama, Qwen, and DeepSeek. The cheapest way to compare models without juggling vendor accounts.

1. Get an API key at <https://openrouter.ai/keys>.
2. Edit `.env`:

   ```env
   AI_PROVIDER=openai
   AI_BASE_URL=https://openrouter.ai/api/v1
   AI_API_KEY=sk-or-v1-…
   AI_MODEL=anthropic/claude-3.5-sonnet
   AI_EMBEDDING_MODEL=openai/text-embedding-3-small
   ```

3. `docker compose up --build`. Or change it live without restart via the Config page or:

   ```bash
   curl -X PATCH http://localhost:8000/api/config \
     -H 'Content-Type: application/json' \
     -d '{"AI_PROVIDER":"openai","AI_BASE_URL":"https://openrouter.ai/api/v1","AI_API_KEY":"sk-or-v1-...","AI_MODEL":"anthropic/claude-3.5-sonnet"}'
   ```

## Per-task model overrides

Two knobs cover the basic case: `AI_MODEL` (default chat model) + `AI_EMBEDDING_MODEL` (embeddings). They apply to **every** chat call (`extract`, `summary`, `coding`) and every embed call respectively.

If you want a stronger model for the strict-JSON extract step but a cheap one for summaries, set the per-task overrides. Each falls back to `AI_MODEL` when blank:

| Env var | What it overrides |
|---|---|
| `AI_MODEL` | Default for any chat task that doesn't have a per-task override |
| `AI_MODEL_EXTRACT` | `POST /api/emr/ingest` — the strict-JSON extraction call |
| `AI_MODEL_SUMMARY` | `POST /api/patient/{id}/summary` |
| `AI_MODEL_CODING` | `POST /api/patient/{id}/coding/suggest` |
| `AI_EMBEDDING_MODEL` | Every `embed` call (per-note + per-problem) |

Example `.env`:

```env
AI_PROVIDER=openai
AI_BASE_URL=https://openrouter.ai/api/v1
AI_API_KEY=sk-or-v1-…

# Best schema-following model for the heavy extraction step…
AI_MODEL_EXTRACT=anthropic/claude-3.5-sonnet

# …a cheap model for the cosmetic summary…
AI_MODEL_SUMMARY=openai/gpt-4o-mini

# …and the default catches anything else (coding, future call types).
AI_MODEL=anthropic/claude-3.5-haiku

AI_EMBEDDING_MODEL=openai/text-embedding-3-small
```

### Model-fit guidance for the extraction step

The `extract` call type carries the heaviest reasoning load — the prompt now asks the model to populate, in addition to the structured facts:

- **Severity + temporality** on each condition (mild/moderate/severe/critical, active/resolved/in_remission/suspected/ruled_out, onset/resolved dates).
- **Inter-fact relationships** — `Medication-[treats]→Condition`, `Observation-[monitors]→Condition`, `Condition-[complication_of]→Condition`, `Observation-[panel_member_of]→Observation`, etc.

All these fields are **optional** in the schema — a weaker model that omits them still validates, and the graph layer falls back to the existing heuristic + co-occurrence paths. But you'll get a richer graph from a stronger model. Practical guidance:

| Model class | Extraction fit | Notes |
|---|---|---|
| `gemini-2.5-flash`, `claude-3.5-sonnet`, `claude-3.7-sonnet`, `gpt-4o`, `gpt-5-mini` | **Recommended.** Reliably produces severity, status, and a useful relationships list. | Use as `AI_MODEL_EXTRACT` if your default `AI_MODEL` is cheaper. |
| `claude-3.5-haiku`, `gpt-4o-mini`, `gemini-2.5-flash-lite` | OK on structured facts; relationships often sparse or missing. | Acceptable as a default; consider a stronger `AI_MODEL_EXTRACT` if the graph looks bare. |
| `deepseek-r1-0528`, `gpt-5-mini` (with reasoning) | Strongest on inter-fact reasoning — finds complications, "due to" links the others miss. | Expensive on reasoning tokens; only worth it if the graph quality matters more than per-call cost. |
| `llama-3.3-70b`, `qwen-2.5-72b` via Groq | Schema adherence sometimes wobbly on the relationships list; safer with a JSON-mode-strict provider. | Watch `ai_outputs.error` for validation failures. |
| `mock` (offline default) | Emits a deterministic demo relationships list (metformin treats diabetes, HbA1c monitors diabetes, etc.) so the graph works without an API key. | Not clinically meaningful; just a wiring test. |

**Rule of thumb:** if you check the graph and the only edges are `HAS_CONDITION` / `HAS_OBSERVATION`, your extract model is leaving the new fields empty. Try a stronger model just for extract via `AI_MODEL_EXTRACT` before increasing your spend across the board.

Three ways to change them (any one works, all merge into the same effective settings):

1. **UI** — `/#/config` → AI provider card → fill in the per-task fields → Save.
2. **API** — `PATCH /api/config` with any subset of the keys:

   ```bash
   curl -X PATCH http://localhost/api/config \
     -H 'Content-Type: application/json' \
     -d '{
       "AI_MODEL": "anthropic/claude-3.5-haiku",
       "AI_MODEL_EXTRACT": "anthropic/claude-3.5-sonnet",
       "AI_MODEL_SUMMARY": "openai/gpt-4o-mini"
     }'
   ```

   The overrides land in the `app_config` table and merge into `Settings` on every read — no restart required.
3. **`.env`** — set the env vars and `docker compose up`. Persistent DB overrides (1 + 2) take precedence over env defaults.

The Debug page's *By model* table breaks down spend per actual model used, so you can A/B different choices and compare token + dollar costs side-by-side.

## Cost-effective preset stacks

Three drop-in presets ship at the repo root. Pick one based on the trade-offs below, copy its `AI_*` lines into `.env`, replace the API key, and `docker compose up` — or paste them into `PATCH /api/config` for a live swap.

| Preset file | Stack | Realized cost / encounter¹ |
|---|---|---|
| `.env.option.deepseek` | DeepSeek V3.1 + GLM-4.5-Air + DeepSeek R1 (OSS Chinese) | ~$0.013 |
| `.env.option.gemini` | Gemini 2.5 Flash + Flash-Lite + GPT-5-mini (Western, cost-only) | ~$0.011 |
| `.env.option.hybrid` | Gemini Flash + GLM-Air + GPT-5-mini (recommended mix) | **~$0.0095** |

¹ Assumes a typical encounter: extract ≈ 6K in / 1.5K out, summary ≈ 2K in / 0.7K out, coding ≈ 4K in / 0.8K answer + reasoning. OpenRouter list rates, January 2026.

### Headline price comparison (per 1M tokens, in / out)

| Slot | DeepSeek/GLM | Gemini/GPT-5 |
|---|---|---|
| extract | `deepseek-chat-v3.1` — **$0.27 / $1.10** | `gemini-2.5-flash` — $0.30 / $2.50 |
| summary | `glm-4.5-air` — ~$0.20 / $1.10 | `gemini-2.5-flash-lite` — **$0.10 / $0.40** |
| coding | `deepseek-r1-0528` — $0.55 / $2.19 | `gpt-5-mini` — **$0.25 / $2.00** |
| embeddings | `text-embedding-3-small` — $0.02 (both) | same |

Headline rates favour DeepSeek, but R1's reasoning-token bill (often 2–5× the answer length, charged as output) flips the realized total. Bounded reasoning on `gpt-5-mini` is why the Gemini and Hybrid stacks finish cheaper end-to-end.

### Pros / cons

**DeepSeek + GLM (`.env.option.deepseek`)**
- ✅ Lowest input-token price across the board — wins big on long-note / extract-heavy workloads.
- ✅ Open weights — can self-host the same models later for data residency / on-prem / air-gapped deployments.
- ✅ R1's chain-of-thought is genuinely strong on multi-step medical reasoning (conflicting findings, ICD specificity).
- ❌ R1 burns reasoning tokens — coding spend is unpredictable.
- ❌ Latency: R1 can take 10–30s per coding call. Fine inside the async ingest pipeline, painful for a synchronous "suggest codes now" UI.
- ❌ Weaker Western clinical priors than GPT/Gemini; expect worse ICD specificity on rare conditions.
- ❌ Data residency: OpenRouter sometimes routes DeepSeek/Z.AI through Chinese-hosted upstreams — pin providers or move off OpenRouter if you process real PHI.

**Gemini + GPT-5-mini (`.env.option.gemini`)**
- ✅ Lower realized cost on coding (gpt-5-mini's reasoning is bounded vs R1).
- ✅ Fast: Gemini Flash and GPT-5-mini both respond in 1–4s.
- ✅ Stronger medical priors — richer clinical training data.
- ✅ Gemini structured-output is the gold standard for strict JSON — fewer retry-on-malformed-JSON cycles.
- ✅ US/EU-hosted upstreams; predictable for compliance reviews.
- ❌ Closed weights — no self-host escape hatch.
- ❌ Vendor lock to two big-lab APIs — slightly more concentrated risk.

**Hybrid (`.env.option.hybrid`) — recommended**
- ✅ Gemini Flash keeps extract reliability; GLM-Air keeps summary dirt cheap with better prose than Flash-Lite; gpt-5-mini handles coding without R1's latency tax.
- ✅ Cheapest realized cost of the three documented presets.
- ❌ Three different upstream providers — more billing dashboards if you ever leave OpenRouter.

### When to pick which

- High volume, short notes, no PHI concerns → **deepseek**. Low input prices dominate, R1's reasoning amortizes over simple cases.
- Complex cases, accuracy-sensitive coding, future PHI workload → **gemini**. Bounded reasoning, better priors, cleaner compliance.
- Best balance of cost, accuracy, and latency → **hybrid**.

After a few real ingests, check the Debug → *By model* table to see actual spend by model — if `coding` dominates, swap `AI_MODEL_CODING` for the same model you use on `extract` (typically a 30–40% total-cost cut at marginal quality impact on routine cases).

> ⚠️ Verify exact model slugs at <https://openrouter.ai/models> before applying — OpenRouter occasionally renames variants (e.g. dated suffixes like `-0528`). The slugs above match what was current at preset authoring time (2026-05).
