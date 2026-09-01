#!/usr/bin/env bash
set -euo pipefail

python3.12 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev,parsing]"

if [ -f web/package-lock.json ]; then
  npm ci --prefix web
fi

python --version
node --version
npm --version
docker --version
docker compose version
