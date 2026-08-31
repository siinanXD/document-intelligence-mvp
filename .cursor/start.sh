#!/usr/bin/env bash
set -euo pipefail

sudo service docker start

for _ in $(seq 1 30); do
  if docker info >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

docker info >/dev/null

docker compose up -d postgres qdrant

for _ in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U postgres -d document_intelligence >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

docker compose exec -T postgres pg_isready -U postgres -d document_intelligence >/dev/null

# The test suite defaults to a separate database. Create it once per fresh
# Cloud Agent VM; repeated starts leave it untouched.
if ! docker compose exec -T postgres psql -U postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='document_intelligence_test'" | grep -q 1; then
  docker compose exec -T postgres createdb -U postgres document_intelligence_test
fi

for _ in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1:6333/collections >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

curl --fail --silent http://127.0.0.1:6333/collections >/dev/null

# shellcheck disable=SC1091
source .venv/bin/activate
alembic upgrade head
