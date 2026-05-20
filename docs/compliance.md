# Compliance & data handling

> **Audience:** operators, privacy/security reviewers, and anyone preparing this stack for clinical use. This is the **must-read** before pointing the stack at real data.

> **TL;DR:** This is a **prototype**. The default `AI_PROVIDER=mock` sends nothing
> off the host. Any other provider sends Protected Health Information (PHI) to
> a third party — that's a regulated activity in most jurisdictions. **Do not
> point this at real patient data without a privacy/security review.**

## What leaves the host when AI_PROVIDER is not `mock`

Five distinct call types each ship PHI-bearing text over HTTPS to whatever
endpoint `AI_BASE_URL` resolves to:

| Call | Endpoint | What's in the body |
|---|---|---|
| **extract** (per ingest) | `/v1/chat/completions` | Patient ID, encounter type, encounter date/time, document ID, **the full raw EMR text** (which typically contains patient name, DOB, clinical narrative) |
| **summary** / **coding** | `/v1/chat/completions` | A JSON-serialised `patient_facts` dict — patient row (HN/name/gender/birth_date/metadata), every encounter, every extracted fact (conditions, meds, observations, plans, allergies, diagnoses) with original `evidenceText` |
| **embed** (per fact + per markdown note at ingest, plus once per search query) | `/v1/embeddings` | Per-fact content + per-note markdown; for search, the user's free-text query |
| **rag** (Vector demo Q&A) | `/v1/chat/completions` | User's question + top-K retrieved chunks (PHI verbatim) + optional chat history |
| **audit row** | DB only | A row is written to `ai_outputs` for every call above (model, tokens, cost, latency, raw response). Stays local in Postgres — never re-sent outbound. |

Embeddings stored in pgvector also contain the source text verbatim — that's
how vector search returns snippets. If you back up Postgres, that backup
contains PHI.

## Where the data actually lands

The endpoint matters more than the model name. Same model behind different
providers means different jurisdictions, different retention policies,
different contracts.

| Provider | Host(s) | BAA available? | Notes |
|---|---|---|---|
| **mock** (default) | localhost only | n/a | No outbound calls. Safe for any data. |
| **OpenAI** direct (`api.openai.com`) | US | Yes, on **OpenAI Enterprise/Healthcare**; **not** on standard tier | Standard tier inputs may be used to improve services unless you opt out; Enterprise is separately contracted. Always verify with OpenAI's current ToS. |
| **OpenRouter** (`openrouter.ai`) | Routes to many upstreams (DeepInfra, Together, Novita, Z.AI, WandB, Google, AtlasCloud, …) | **No** | Each upstream has its own jurisdiction and policy. DeepSeek and GLM are usually served by Chinese-hosted providers. |
| **Self-hosted** (vLLM, Ollama, llama.cpp on your network) | Your host | n/a — you're the operator | The compliance burden moves to you (encryption at rest, access control, audit), but no third-party PHI transfer occurs. |

## Regulatory framing (informational — not legal advice)

- **HIPAA (US)** — PHI sent to a third-party processor requires a signed
  Business Associate Agreement (BAA). Inputs in the table above qualify as
  PHI under §164.514. Sending PHI to a provider without a BAA is a reportable
  breach.
- **GDPR (EU/UK)** — Health data is "special category" under Art. 9. Lawful
  basis (consent or Art. 9(2)(h) healthcare provision) plus an Art. 28
  processor contract is required. Cross-border transfers (e.g. EU → US)
  need an SCC + transfer impact assessment.
- **PDPA (Thailand)** — Sensitive personal data under §26 requires explicit
  consent. Cross-border transfer rules (§28) require adequate-protection
  determinations or explicit consent. The sample data ships with Thai
  clinical text — that's a deliberate signal that this codebase is built
  with Thai healthcare in mind, and PDPA applies the moment real patients
  are involved.

## Mitigations to reach for before production-like use

In rough order of how much they reduce risk:

1. **Keep `AI_PROVIDER=mock`** for demos, screenshots, and conferences. The
   mock provider produces deterministic output and never makes an outbound
   call.
2. **Self-host open-weight models.** The same `AI_PROVIDER=openai` code path
   works against a local vLLM / Ollama server serving Llama, Qwen, DeepSeek
   weights. PHI stays on your network. Cost and quality trade-offs apply.
3. **Sign a BAA** with OpenAI (Healthcare/Enterprise) or Anthropic and pin
   `AI_BASE_URL` to that tier. Other providers (OpenRouter, generic vendors)
   are unsuitable for PHI in BAA-bound deployments.
4. **De-identify before sending — now on by default.** `DEIDENTIFY_LEVEL`
   defaults to `safe_harbor`, which redacts the HIPAA Safe Harbor 18 at the
   outbound boundary in `ai_provider` for every one of the five call types
   (`extract`, `summary`, `coding`, `embed`, `rag`). The on-disk data model
   stays unredacted; only the LLM payload is rewritten. Implementation lives
   in `backend/app/services/deidentify.py` and tracks per-call catch counts
   in `ai_outputs.redaction_counts`. See [De-identification](#de-identification)
   below for the level switch and what it does/does not catch.
5. **Restrict to consented research/quality-improvement workflows** with
   IRB/Ethics Committee approval and an explicit data-use agreement.
6. **Encrypt the Postgres + Neo4j volumes at rest** and gate access with the
   `BACKEND_API_KEY` middleware. The vector embeddings are PHI-derived even
   though they're floating-point numbers, and the `content` column stores
   the verbatim text.
7. **Log + monitor every outbound call.** The existing `ai_outputs` table
   is already an audit trail; just don't drop it. Add alerting for
   unexpected providers / costs.

## De-identification

A redactor runs at the outbound boundary in `ai_provider` before any payload
leaves the host. It implements HIPAA Safe Harbor §164.514 across both
structured fields and free clinical text, and ships with Thai-specific
recognisers because the sample data is mixed Thai + English.

| Setting | Behaviour | When to use |
|---|---|---|
| `DEIDENTIFY_LEVEL=off` | No-op; PHI flows through as-is. | Only with a signed BAA pinned to that provider (e.g. OpenAI Enterprise, Anthropic Enterprise). |
| `DEIDENTIFY_LEVEL=regex_only` | Regex-only sweep. Catches HN/MRN/emails/phones/IPs/dates/Thai national IDs/etc. | Fast path for unit tests, low-throughput deployments, or environments where the +500 MB NER install isn't acceptable. |
| `DEIDENTIFY_LEVEL=safe_harbor` (default) | Regex + Microsoft Presidio (English narrative) + PyThaiNLP (Thai narrative) + per-request pseudonym map for names / HN / provider names; dates rounded to year; DOB → year + age class (`<30 / 30-44 / 45-64 / 65+`). | Production-shape demos with synthetic data and any deployment without a BAA. |

What gets caught by category (regex sweep, before NER): `EMAIL_ADDRESS`,
`URL`, `IP_ADDRESS` (v4 + v6), `PHONE_NUMBER` (international + Thai),
`TH_NATIONAL_ID` (Luhn-checked), `US_SSN` (defensive), `HN_PATIENT_ID` (our
`HN-XXXX` format), `MEDICAL_RECORD_NUMBER`, `HEALTH_PLAN_BENEFICIARY`,
`ACCOUNT_NUMBER`, `VEHICLE_ID`, `DEVICE_ID`, `BIOMETRIC`, `DATE_TIME`,
`LOCATION` (Thai admin divisions, room numbers). What NER adds on top:
`PERSON` and `LOCATION` from spaCy's `en_core_web_sm` (English) and
`pythainlp.tag.NER` (Thai) for names that don't follow a fixed pattern.

Audit columns on `ai_outputs`:
- `deidentified BOOLEAN` — `true` when the redactor ran on this call.
- `redaction_counts JSONB` — per-category counts (e.g. `{"PERSON": 2,
  "PHONE_NUMBER": 1, "DATE_TIME": 5}`).
- `raw_response` — what actually left the host, so an auditor can verify
  the redacted payload is PHI-free.

One log line per call: `redacted_categories=PERSON:2,EMAIL_ADDRESS:1
ai_provider=openrouter call_type=extract`.

Pseudonyms are deterministic per-request — the same name maps to the same
`PATIENT-A1` token throughout one HTTP call, so the LLM can reason about the
referent consistently. Across requests, the pseudonym map resets, so the
provider never sees cross-request linkage. The v1 implementation does not
keep a server-side re-identification map; rehydrating a redacted output back
to the original is intentionally out of scope.

For embedding ingestion (`/v1/embeddings`), **embedded text is redacted
text**. That means pgvector at rest is also de-identified, and vector
search returns redacted snippets. UI re-identification of those snippets
for a clinician is out of scope for v1.

**Image size cost:** Presidio + spaCy `en_core_web_sm` + PyThaiNLP add
~500 MB to the backend Docker image. The first build is slower; subsequent
builds reuse the Docker layer cache. If you swap PyThaiNLP for its medium
model, plan another ~1 GB. The redactor warm-loads NER pipelines at module
import, so first-request latency doesn't pay the model-load cost.

**Version pinning:** Presidio publishes patch releases on roughly a yearly
cadence. Pin `presidio-analyzer` / `presidio-anonymizer` in
`backend/requirements.txt` and review the changelog when bumping — model
updates can change recall and shift `redaction_counts` baselines.

## Regulatory re-evaluation (after de-identification, v1B)

With `DEIDENTIFY_LEVEL=safe_harbor` active, the posture changes by
regulation:

- **HIPAA (US):** Safe Harbor de-identification under §164.514(b) requires
  removal of all 18 identifier categories *and* "no actual knowledge that
  the information could be used alone or in combination with other
  information to identify an individual." This implementation removes the
  18 categories at the outbound boundary, so the *prompt that crosses the
  wire* is structurally Safe Harbor-shaped. **What still requires care:**
  (a) the on-disk Postgres / Neo4j / vault data remains fully identified;
  (b) NER recall is not 100% — clinical narrative with unusual phrasing
  can leak; (c) the LLM response may still mention an identifier that
  survived (e.g. a misspelled name regex misses). Treat this as a strong
  technical control, not a legal opinion that BAA-free providers become
  HIPAA-compliant.
- **GDPR (EU/UK):** Removing direct identifiers moves the data toward
  "pseudonymised" under Art. 4(5) — still personal data, but lower-risk
  processing under Art. 32. Cross-border transfers (EU → US) still need an
  SCC + transfer impact assessment unless the provider is in an adequacy
  jurisdiction. The redactor is one element of the Art. 25 "data protection
  by design" obligation, not a substitute for the Art. 28 processor
  contract.
- **PDPA (Thailand):** Thai national ID and Thai-formatted phones are
  redacted by the regex pass before any text leaves the host. Thai
  personal names are handled by PyThaiNLP NER in `safe_harbor` mode (less
  reliably than English; benchmark against your own narrative before
  relying on it). The §28 cross-border transfer rule still requires an
  adequacy determination or explicit consent — de-identification does not
  bypass that, it just reduces the magnitude of breach exposure if the
  determination is later challenged.

In all three regimes the redactor is necessary, not sufficient. The
[Mitigations](#mitigations-to-reach-for-before-production-like-use) ladder
above (BAA, encryption at rest, audit logging, consent-bound research
scope) still applies.

## Current posture of this repository

- Default `AI_PROVIDER=mock`, no outbound calls. Safe out of the box.
- Default `DEIDENTIFY_LEVEL=safe_harbor` — when you switch `AI_PROVIDER` to
  anything that talks HTTPS, the redactor is on. To turn it off explicitly
  (e.g. you have a signed BAA), set `DEIDENTIFY_LEVEL=off`.
- Sample data is **synthetic** (HN-DEMO-1 Somchai Sample) and crafted for
  prototyping; it intentionally mixes Thai and English to exercise that
  code path. It is NOT real patient data.
- The active `.env.option.*` presets (`deepseek`, `gemini`, `hybrid`) all
  route through **OpenRouter**, which does not offer BAAs. With the
  redactor on by default these presets are usable for synthetic-data
  demos; do **not** point them at real patient data without a signed BAA
  *and* a privacy review.
- No BAA enforcement, no provider allow-listing in code. These remain
  deliberate omissions — design those in before any deployment that
  touches a real EMR.

If you're considering using this codebase against a real patient
population, get a privacy/security review first. This is documentation,
not legal advice.
