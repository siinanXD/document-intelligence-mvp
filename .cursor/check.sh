#!/usr/bin/env bash
set -euo pipefail

bash .cursor/start.sh

# shellcheck disable=SC1091
source .venv/bin/activate

export REQUIRE_DB=1

ruff check .
ruff format --check .
pytest --ignore=tests/test_docling_parser.py
pytest tests/test_docling_parser.py
if [ -f web/package-lock.json ]; then
  npm --prefix web run lint
  npm --prefix web run typecheck
  npm --prefix web test
fi
