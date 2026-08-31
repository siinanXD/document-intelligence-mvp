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
