#!/usr/bin/env bash
# Boot the full stack with docker compose, wait for /health, run the E2E tests, tear down.
#
# Usage:
#   ./scripts/e2e.sh             # mock AI provider, no API key
#   API_KEY=secret ./scripts/e2e.sh
#
# Requires docker, docker compose, curl.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE="docker compose"

cleanup() {
  echo "→ Tearing down compose stack"
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "→ Building & starting stack"
BACKEND_API_KEY="${API_KEY:-}" $COMPOSE up -d --build

echo "→ Waiting for backend /health"
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    echo "  backend is up"
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "  backend never came up"
    $COMPOSE logs backend | tail -100
    exit 1
  fi
  sleep 2
done

echo "→ Running E2E tests"
CNG_E2E=1 CNG_BASE_URL=http://localhost:8000 CNG_API_KEY="${API_KEY:-}" \
  $COMPOSE exec -T backend pytest tests/test_e2e_smoke.py -v

echo "→ Smoke OK"
