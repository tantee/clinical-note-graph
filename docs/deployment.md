# Production deployment

The production overlay (`docker-compose.prod.yml`) collapses the dev `proxy` + `frontend` services into a single **Caddy** container that:

- compiles the frontend at image-build time and serves the static bundle from `/srv`,
- reverse-proxies `/api/*`, `/docs`, `/openapi.json`, `/redoc`, `/health`, `/ready` to the backend on the internal docker network,
- handles TLS termination — when `CNG_DOMAIN` is a real public hostname, Caddy auto-fetches a Let's Encrypt cert via ACME HTTP-01 (ports 80 and 443 must be Internet-reachable),
- gzip/zstd-encodes responses, immutable-caches hashed assets, sets HSTS + `X-Frame-Options` + `X-Content-Type-Options` + `Referrer-Policy`,
- closes Postgres / Neo4j / backend ports to the outside (only the proxy is exposed),
- enforces a CORS allowlist and an `X-API-Key` header on protected endpoints,
- adds health-checks to every service and `restart: always`.

Required env vars for prod:

```env
CNG_DOMAIN=cng.example.com                     # Caddy serves this host; auto-TLS via Let's Encrypt
CADDY_EMAIL=ops@example.com                    # cert renewal notifications
VITE_API_BASE=https://cng.example.com          # baked into the frontend bundle; same host since Caddy fronts both
FRONTEND_ORIGIN=https://cng.example.com        # CORS allowlist for the backend
BACKEND_API_KEY=<long random secret>           # required for /api/emr, /api/config, /api/export, /api/facts
UVICORN_WORKERS=4
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Local prod testing without a real domain: set `CNG_DOMAIN=localhost` and Caddy serves plain HTTP on :80. Useful for smoke-testing the prod overlay before pointing DNS.

Operational endpoints (served through the same Caddy proxy):

- `GET /health` — 200 whenever the backend process is up.
- `GET /ready` — 200 only when Postgres responds.
- Every response includes an `X-Request-ID` header that also appears in JSON 500/422 bodies for tracing.

If you'd rather run nginx than Caddy as the front-door, the Caddyfiles are short — swap them for an equivalent `server { … proxy_pass http://backend:8000; … }` block and use `proxy_pass http://frontend:5173` (or `root /srv` in prod). Caddy was chosen here because the auto-HTTPS path is one config line and one env var.
