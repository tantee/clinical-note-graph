# Deployment

> **Audience:** operators standing this stack up on either a laptop (dev) or a VPS (prod).

Two configurations ship out of the box: the **dev compose** (live-reload, ports exposed for tooling) and the **prod overlay** (single Caddy front-door, TLS, no exposed DB ports). Both run from the same `docker-compose.yml`.

If you only want to demo the project on your laptop, jump straight to [Development](#development). For production, follow [Production](#production) end-to-end — the pre-flight checklist is not optional.

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

The Caddyfile handles three deployments:

| Setup | `CNG_DOMAIN` | Result |
|---|---|---|
| **Public host with Let's Encrypt** | `cng.example.com` | Caddy fetches a real cert via ACME HTTP-01. Renewal is automatic. Ports 80 + 443 must be Internet-reachable. |
| **Local prod test** | `localhost` | Plain HTTP on port 80, no cert. Useful to smoke-test the prod overlay before DNS is ready. |
| **Behind an existing load balancer** (TLS-terminating) | the LB hostname | Caddy still serves HTTPS, but if the LB terminates TLS in front, set `CNG_DOMAIN` to the public hostname and have the LB forward to Caddy on port 80. Caddy still adds the security headers; the LB owns the cert. |

For internal-only deployments where you want HTTPS without a public DNS record, swap to a private CA — replace `caddy/Caddyfile.prod`'s `{$CNG_DOMAIN}` block with a `tls` directive pointing at your cert/key paths and mount them in.

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
