#!/bin/sh
set -eu

# Bind dual-stack so Railway private IPv6 and public IPv4 both reach the API.
# Migrations are idempotent; the API is the only service that applies them.
alembic upgrade head
exec uvicorn app.main:app --host :: --port "${PORT:-8000}"
