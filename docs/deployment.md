# Deployment

> **Audience:** operators standing this stack up. For *editing the code*, see [docs/development.md](development.md).

Three deployment shapes ship out of the box. Pick the closest match:

| Shape | Where it runs | TLS | Exposed ports | Data | Use it for |
|---|---|---|---|---|---|
| [**Development**](#development) | Laptop, dev VM | none | API + DB + Vite all exposed for tooling | synthetic / mock | local iteration, demos |
| [**Dev / staging**](#dev--staging) | Shared dev VM, team URL | optional (Let's Encrypt or self-signed) | only the proxy | synthetic | team integration testing, UAT, screenshots for stakeholders |
| [**Production**](#production) | Public VPS / managed host | mandatory (Let's Encrypt) | only the proxy | **review compliance first** | real workflows — read [docs/compliance.md](compliance.md) before pointing at real data |

All three run from the same `docker-compose.yml`. The prod and staging shapes layer `docker-compose.prod.yml` on top with progressively stricter env-var requirements.

---

## Development

### Quick start

```bash
git clone https://github.com/tantee/clinical-note-graph
cd clinical-note-graph
cp .env.example .env
docker compose up --build
```

The first build pulls the Postgres, Neo4j, Caddy, and Node base images and installs the Python deps including Presidio + spaCy + PyThaiNLP (the de-identifier pulls ~500 MB of model weights). Allow ~5 minutes for a cold build; subsequent `up` is ~10 seconds.

Default `.env` runs `AI_PROVIDER=mock` — no API key required, no outbound calls. The mock is a deterministic keyword extractor (ICD-10/SNOMED/LOINC/RxNorm), good enough to verify the whole pipeline works.

### Port map

| Service | URL | Notes |
|---|---|---|
| **Unified app (Caddy proxy)** | http://localhost | Recommended entry point. UI at `/`, API at `/api/*`, Swagger at `/docs`. |
| Frontend (Vite dev) | http://localhost:5173 | Direct access for Vue devtools / hot reload. |
| Backend (FastAPI) | http://localhost:8000 | Bypasses the proxy. Useful for `curl` and integration scripts. Swagger at `/docs`. |
| Neo4j Browser | http://localhost:7474 | Default creds `neo4j / neo4jpass`. Override with `NEO4J_USER` / `NEO4J_PASSWORD`. |
| Postgres | localhost:5432 | Default creds `cng / cngpass`. Override with `POSTGRES_USER` / `POSTGRES_PASSWORD`. |

If a port collides with another local service, override it in `.env`:

```env
PROXY_PORT=8080
BACKEND_PORT=8001
FRONTEND_PORT=5174
NEO4J_HTTP_PORT=7475
NEO4J_BOLT_PORT=7688
POSTGRES_PORT=5433
```

### Where data lives

- **Postgres** → docker volume `pgdata` (relational rows, embeddings, audit log).
- **Neo4j** → docker volumes `neo4jdata` + `neo4jlogs` (the knowledge graph).
- **Markdown vault** → docker volume `vaultdata` mounted at `/data/vault` in the backend container.
- **Code** → bind-mounted from your working tree (`./backend → /app`, `./frontend → /app`) so saves trigger hot reload.

### Resetting state

```bash
# Stop and remove all volumes (Postgres, Neo4j, vault, Caddy cache):
docker compose down -v

# Rebuild from scratch after pulling new code:
docker compose up --build
```

### Switching the AI provider

Three places work, in increasing persistence:

1. **`PATCH /api/config`** (or the `/#/config` UI) — overrides land in the `app_config` table and merge into `Settings` on every read. No restart. Survives container restart but not `down -v`.
2. **`.env`** — set the vars and `docker compose up`. Persistent across `down -v` because `.env` lives in your working tree.
3. **Quick-start preset** — copy one of `.env.option.{deepseek,gemini,hybrid}` into `.env` for a known-good stack. See [docs/ai-providers.md](ai-providers.md).

Persistent DB overrides (path 1) win over `.env` (path 2). Effective settings are visible at `GET /api/config` with secrets masked.

### Running the test suite

See [docs/operations.md → Testing](operations.md#testing). The fast path:

```bash
cd backend
pip install -r requirements.txt
pytest -q
```

The Presidio + spaCy + PyThaiNLP deps are heavy. If you only need to run unit tests, install the slim set: `pip install pydantic pydantic-settings pytest pytest-asyncio anyio SQLAlchemy fastapi httpx 'psycopg[binary]' neo4j` — the de-identification suite degrades gracefully to regex-only when NER libs are missing.

### Common dev pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `connection refused` on first request | Postgres / Neo4j still initialising | Wait for `docker compose logs backend` to show `Application startup complete`. Healthchecks gate backend on `service_healthy`, but the first init takes ~30s. |
| Hot reload not picking up backend changes | Permission on bind-mounted `./backend` | Ensure the host directory is owned by your user. The container runs as UID 10001 but reload is driven by mtime, which the bind-mount surfaces. |
| Vault changes not visible from host | Vault is in a named volume, not a bind | Add `./vault:/data/vault` to the backend service `volumes:` if you want bind-mount semantics. |
| `DEIDENTIFY_LEVEL=off` not taking effect | Settings cached at module import | Restart the backend container. The persisted overrides path (`/api/config`) takes effect on next request without restart. |
| `column "deidentified" of relation "ai_outputs" does not exist` | Schema migration `005_deidentified_flag.sql` not applied to an existing database | Postgres only runs `db/init/*.sql` on **first** boot. For existing volumes apply manually — see [Updates](#updates). |

For more troubleshooting: [docs/troubleshooting.md](troubleshooting.md). For dev workflows (debugging, IDE setup, running services in isolation), see [docs/development.md](development.md).

---

## Dev / staging

A shared environment between local dev and full production. Use it when:

- the team needs a stable URL to demo against, share screenshots from, or run UAT through;
- you need an integration target for a CI pipeline or an EMR client team;
- you want to validate the prod compose overlay without committing to a public domain + Let's Encrypt yet.

### What's different from production

| | Production | Dev / staging |
|---|---|---|
| Hostname | Public domain | LAN hostname or `cng.staging.example.com` |
| TLS | Let's Encrypt, mandatory | Optional. Plain HTTP, self-signed cert, or Let's Encrypt staging endpoint |
| Real PHI | Allowed only with full compliance review | **Never.** Synthetic data only. |
| Backups | Daily, off-host | Optional. Volumes survive container restarts; that's enough for most staging. |
| `BACKEND_API_KEY` | Mandatory, rotated quarterly | Recommended (smoke-tests the auth middleware), but a fixed dev key is fine |
| Restart policy | `restart: always` | `restart: unless-stopped` (inherited from `docker-compose.yml`) |
| Database ports exposed | No | Optional — handy for the team to run ad-hoc queries against staging Postgres / Neo4j |

### Boot

The prod compose overlay requires `CNG_DOMAIN`, `CADDY_EMAIL`, `FRONTEND_ORIGIN`, and `BACKEND_API_KEY` regardless of the deployment shape (compose's `:?` directive). For a no-TLS staging on a LAN hostname:

```env
# .env for dev VM (e.g. staging.internal:80)
CNG_DOMAIN=staging.internal              # any hostname Caddy can match
CADDY_EMAIL=devnull@example.com          # never used because no LE cert is fetched
VITE_API_BASE=http://staging.internal    # baked into the bundle
FRONTEND_ORIGIN=http://staging.internal  # CORS allow-list
BACKEND_API_KEY=dev-key-not-secret       # rotated separately from prod
UVICORN_WORKERS=2

# Use the deepseek/gemini/hybrid preset for the team's chosen LLM stack.
AI_PROVIDER=openai
AI_BASE_URL=https://openrouter.ai/api/v1
AI_API_KEY=sk-or-v1-…
AI_MODEL=google/gemini-2.5-flash

DEIDENTIFY_LEVEL=safe_harbor             # leave on even in staging — synthetic Thai data exercises the recognisers
```

Boot the same way as prod:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Caddy sees `CNG_DOMAIN=staging.internal` (not a public DNS name and not `localhost`) and serves HTTPS via an internal self-signed cert. Clients will see a cert warning — acceptable for staging; if your team finds that distracting, either swap to plain HTTP (see below) or import Caddy's root cert from `caddydata` into your trust store.

### Plain HTTP for staging (no cert warnings)

If TLS in staging is more friction than it's worth, override the Caddyfile with the dev version:

```yaml
# docker-compose.staging.yml — additional overlay
services:
  proxy:
    volumes:
      - ./caddy/Caddyfile.dev:/etc/caddy/Caddyfile:ro
    ports: !override
      - "80:80"   # no 443 mapping; Caddy serves plain HTTP
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.staging.yml up -d --build
```

This keeps the prod overlay's "DB ports closed, single-worker discipline" while serving plain HTTP. Suitable for an internal network only.

### Let's Encrypt **staging** endpoint (for iterating on TLS)

Public Let's Encrypt rate-limits at ~5 certs/week/domain — easy to hit while debugging. Switch Caddy to the staging endpoint while iterating:

```caddy
# caddy/Caddyfile.prod, inside the global block
{
    email {$CADDY_EMAIL:admin@example.com}
    admin off
    acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}
```

Restart the proxy; new certs come from LE's staging tree (untrusted by browsers, but unlimited issuance). Remove the line and restart again when ready to fetch a real cert.

### Hosting

The minimal staging host is **2 vCPU / 4 GB RAM / 25 GB SSD** — half the prod recommendation. Backend + frontend + Postgres + Neo4j + Caddy all on one node. If you find Neo4j throttling under team-sized ingest loads, the first lever is `NEO4J_PAGECACHE` (raise from 256m to 1g) before splitting services.

### Resetting staging

Faster than waiting for backups to restore:

```bash
docker compose down -v        # delete every volume
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The schema rebuilds from `backend/db/init/*.sql` on the empty Postgres volume; Neo4j constraints are recreated on backend startup. Re-ingest your synthetic data via `./examples/ingest.sh` (point it at the staging hostname).

### Promoting a build from staging → prod

There's no separate image — both deployments build from the same `Dockerfile`. The promotion is operational, not artifact-level:

1. Tag the commit you tested in staging (`git tag staging-2026-05-20 && git push --tags`).
2. On the prod host, `git fetch && git checkout <tag>`.
3. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` with the prod `.env`.
4. Apply any new SQL migrations manually — see [Updates](#updates).
5. Smoke-test via `/health`, `/ready`, and a single ingest through `./examples/ingest.sh`.

---

## Production

### Pre-flight checklist

Before you `docker compose up` against a real domain, confirm:

- [ ] **DNS** — `${CNG_DOMAIN}` resolves to the host's public IP. A and AAAA records both point at the same address.
- [ ] **Firewall** — ports 80 and 443 reachable from the public Internet (Let's Encrypt ACME HTTP-01 challenge needs port 80; users hit 443). Ports 5432/7474/7687 must NOT be public.
- [ ] **Disk** — at minimum 20 GB free. Steady-state usage depends on volume: see [Resource sizing](#resource-sizing).
- [ ] **Secrets** — `BACKEND_API_KEY`, `POSTGRES_PASSWORD`, `NEO4J_PASSWORD` are long random strings (don't reuse the dev defaults). Stored outside the repo (env file injected by your secrets manager, not committed).
- [ ] **AI provider strategy** — you've read [docs/compliance.md](compliance.md) and chosen a provider that matches your data classification (BAA-bound for real PHI; `DEIDENTIFY_LEVEL=safe_harbor` is on by default).
- [ ] **Backup destination** — somewhere off-host to write Postgres dumps + Neo4j snapshots. See [Backups](#backups).

### Required env vars

```env
CNG_DOMAIN=cng.example.com                # Caddy serves this host; auto-TLS via Let's Encrypt
CADDY_EMAIL=ops@example.com               # cert renewal notifications
VITE_API_BASE=https://cng.example.com     # baked into the frontend bundle at build time
FRONTEND_ORIGIN=https://cng.example.com   # CORS allowlist for the backend
BACKEND_API_KEY=<long random secret>      # required on /api/emr, /api/config, /api/export, /api/facts
POSTGRES_PASSWORD=<long random secret>
NEO4J_PASSWORD=<long random secret>
UVICORN_WORKERS=4                         # tune to CPU count; see Resource sizing
```

The prod overlay (`docker-compose.prod.yml`) treats `CNG_DOMAIN`, `CADDY_EMAIL`, `FRONTEND_ORIGIN`, and `BACKEND_API_KEY` as **required** — compose will fail to start with a clear error if any are missing.

### Boot

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The prod overlay differs from dev in:

- The `proxy` service is rebuilt from `caddy/Dockerfile.prod` and bakes the compiled Vue bundle into the image. The dev `frontend` service is disabled.
- Postgres, Neo4j, and the backend close their host-port mappings — only Caddy is exposed.
- The backend runs `uvicorn ... --workers ${UVICORN_WORKERS}` instead of `--reload`.
- `restart: always` on every service.

### TLS modes

The Caddyfile handles four deployments, picked by what you put in `CNG_DOMAIN`:

| Setup | `CNG_DOMAIN` | Result |
|---|---|---|
| **Public host with Let's Encrypt** | `cng.example.com` | Caddy fetches a real cert via ACME HTTP-01. Renewal is automatic. Ports 80 + 443 must be Internet-reachable. Set `CADDY_EMAIL` for renewal notifications. |
| **Behind another reverse proxy** *(TLS terminated upstream)* | `:80` | Caddy serves plain HTTP on port 80 — no ACME, no cert. Upstream `X-Forwarded-Proto` / `X-Forwarded-Host` are trusted (private-CIDR proxies by default) and passed through to the backend. Your upstream owns the cert + HTTPS redirect. |
| **Local prod test** | `localhost` | Plain HTTP on port 80, no cert. Useful to smoke-test the prod overlay before DNS is ready. |
| **Private CA / internal mTLS** | a non-public hostname (e.g. `cng.internal.lan`) | Caddy still tries ACME and will fail. Replace the `{$CNG_DOMAIN}` block in `caddy/Caddyfile.prod` with an explicit `tls /etc/cert.pem /etc/key.pem` and mount the cert + key in. |

#### Tuning the behind-proxy mode

Extra env vars (all optional):

| Var | Default | Effect |
|---|---|---|
| `CNG_TRUSTED_PROXIES` | `private_ranges` | CIDR(s) Caddy will trust `X-Forwarded-*` from. Override if your upstream lives outside RFC1918 / docker / k8s ranges. |
| `CNG_HSTS` | `max-age=31536000; includeSubDomains` | Strict-Transport-Security header. **Disable** when terminating TLS upstream if the upstream isn't already setting it — set to `max-age=0`. |

Example `.env` for behind-proxy mode:

```bash
CNG_DOMAIN=:80
FRONTEND_ORIGIN=https://cng.example.com   # the public-facing URL, used for CORS
BACKEND_API_KEY=...
# CADDY_EMAIL not required (no ACME)
```

### Resource sizing

The four containers in production, with the +500 MB Presidio install:

| Container | Min RAM | Recommended RAM | Disk (steady state, 10K encounters) |
|---|---|---|---|
| postgres | 256 MB | 1 GB | ~5 GB (rows + pgvector indexes; grows linearly) |
| neo4j | 512 MB (matches `NEO4J_PAGECACHE=256m`) | 2 GB (`NEO4J_PAGECACHE=1g`) | ~1 GB |
| backend | 768 MB (Presidio + spaCy resident) | 1.5 GB per worker | image ~1.5 GB on disk |
| proxy (Caddy) | 64 MB | 128 MB | negligible |

For a single-tenant VPS doing real workloads: **4 vCPU / 8 GB RAM / 50 GB SSD** is comfortable. The backend's CPU need scales with ingest rate — set `UVICORN_WORKERS` to roughly `vCPU - 1` so Postgres / Neo4j keep headroom.

Embedding ingest is the spiky part: ~40 calls per encounter, bounded-concurrency. If you push 100 encounters / minute, expect the embedding HTTP egress to dominate.

### Backups

| What | How | Cadence |
|---|---|---|
| **Postgres** | `docker compose exec postgres pg_dump -U $POSTGRES_USER $POSTGRES_DB \| gzip > backup.sql.gz` | Daily, retained 30 days. |
| **Neo4j** | `docker compose exec neo4j neo4j-admin database dump neo4j --to-path=/data/dumps` then ship the dump off-host. | Daily. Neo4j community lacks online backup — schedule when ingest is quiet. |
| **Markdown vault** | `tar czf vault.tgz $(docker volume inspect cng_vaultdata -f '{{.Mountpoint}}')` | Daily. Cheap. |
| **Caddy cert + state** | Volume `caddydata` holds the ACME account + leaf cert. Back up alongside Postgres. | Weekly is fine — Caddy will re-fetch from Let's Encrypt if lost. |

**Restore** to a fresh host: `docker compose down -v`, restore the volume contents, `docker compose up -d`. The `db/init/*.sql` migrations are idempotent (`CREATE … IF NOT EXISTS`), so they don't fight a restored database.

**At-rest encryption**: the prototype doesn't encrypt the volumes itself. Use full-disk encryption on the host (LUKS / FileVault / EBS encryption) — that covers Postgres, Neo4j, the vault, and the backups simultaneously. The `BACKEND_API_KEY` middleware controls *access*; FDE controls *theft of the disk*.

### Updates

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The init scripts in `backend/db/init/*.sql` are run by Postgres' first-boot mechanism. For an existing database, **new migrations don't auto-apply** — apply them manually:

```bash
docker compose exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB < backend/db/init/00X_new_migration.sql
```

Migrations land in order; the deployment in PR #11 added `005_deidentified_flag.sql`. Always run them in numeric order and only the ones newer than your current schema. There's no formal migration tracker — `git log backend/db/init` is the source of truth.

Neo4j constraints are created on backend startup (`ensure_constraints` in `app/main.py`), gated by `IF NOT EXISTS`, so they're safe to re-run.

### Observability

What's logged where:

- **Backend logs** → stdout, captured by `docker compose logs backend`. Each request emits one line with status, latency, and `X-Request-ID`. AI calls emit `redacted_categories=… ai_provider=… call_type=…` (see [docs/compliance.md → De-identification](compliance.md#de-identification)).
- **Caddy logs** → stdout, captured by `docker compose logs proxy`. Standard access log + TLS handshake errors.
- **Audit table** → `ai_outputs` in Postgres holds every AI call (model, tokens, cost, latency, raw response, de-identification counts). Query directly or use the Debug UI (`/#/debug`).
- **Audit log** → `audit_log` table records every state change (ingest, config patch, fact review).
- **Healthchecks** → `GET /health` (process up) and `GET /ready` (Postgres up). Both reachable through the Caddy proxy.

For Prometheus / Grafana, two pragmatic paths:

1. Run `cadvisor` + `node_exporter` in the compose for resource metrics.
2. Add a `/metrics` route to the backend exposing what's in `ai_outputs` — there's no built-in exporter yet (deliberately, since the audit table is already a richer source).

### Failure modes + recovery

| What broke | Symptom | First thing to check |
|---|---|---|
| Postgres down | `GET /ready` returns 503; ingest 5xxs | `docker compose logs postgres`. Disk full? OOM? |
| Neo4j down | Ingest succeeds for Stage 1/2 but graph stays stale | `docker compose logs neo4j`. Memory? Heap settings? |
| AI provider 5xx | Ingest job stuck in `stage_ai_extract`; `ai_outputs.error` populated | Check the provider's status page. The job will retry per the queue's retry policy. |
| Embedding model unavailable | Ingest succeeds but vector search returns nothing | `ai_outputs` rows with `call_type='embed'` show errors. Switch `AI_EMBEDDING_MODEL` via `PATCH /api/config` — no restart needed. |
| Disk full | Healthchecks pass briefly then fail; Postgres throws `ERROR: could not extend file` | Free space; the vault grows linearly with encounters. |
| Stuck job | Job in `pending` for > `JOB_LOCK_SECONDS` | `POST /api/jobs/{id}/requeue`. Locks expire automatically after `JOB_LOCK_SECONDS + JOB_GRACE_SECONDS`. |
| Let's Encrypt rate limit | Cert renewal fails | Check `docker compose logs proxy`; LE allows ~5 certs/week per domain. Use the staging endpoint while iterating: add `acme_ca https://acme-staging-v02.api.letsencrypt.org/directory` to the Caddyfile. |

### Security hardening checklist

- [ ] `BACKEND_API_KEY` set to a long random string (32+ bytes from `openssl rand -hex 32`); rotated quarterly.
- [ ] `CORS_ORIGINS` set to the public hostname only (no `*` in prod). Enforced by `docker-compose.prod.yml`'s `FRONTEND_ORIGIN` requirement.
- [ ] Postgres / Neo4j passwords rotated off the defaults. Stored in your secrets manager (Doppler, Vault, AWS SSM, etc.), not in committed `.env`.
- [ ] `DEIDENTIFY_LEVEL=safe_harbor` (the default). Set to `off` only with a signed BAA pinned to that provider.
- [ ] Full-disk encryption on the host.
- [ ] Container image scanning (Trivy / Snyk / Docker Scout) wired into CI. The `requirements.txt` pins are exact, so an upstream CVE shows up as a single diff.
- [ ] Outbound network policy: if your host has egress controls, allow-list the AI provider's API hostname; block everything else. The backend doesn't need general Internet access.
- [ ] Audit log retention: `audit_log` and `ai_outputs` grow forever by default. Add a cron that snapshots + truncates rows older than your retention window.
- [ ] No PHI committed to git: the sample data is synthetic (`HN-DEMO-1 Somchai Sample`); ensure nobody adds real data to `sample-data/` or commits a populated vault.

For the regulatory framing (HIPAA / GDPR / PDPA), see [docs/compliance.md](compliance.md).
