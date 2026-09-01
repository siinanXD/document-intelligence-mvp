#!/usr/bin/env bash
# Production smoke checks. Identifiers and health status only — never a
# question, answer, passage, filename or request body.
set -euo pipefail

base="${SMOKE_BASE_URL:-}"
if [ -z "$base" ]; then
  echo "SMOKE_BASE_URL is required (web origin or API origin, no trailing slash)" >&2
  exit 2
fi
base="${base%/}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

check() {
  local path="$1"
  local expected="$2"
  local file="$tmpdir/body.json"
  local code
  code="$(curl -fsS -o "$file" -w "%{http_code}" "${base}${path}")"
  if [ "$code" != "$expected" ]; then
    echo "FAIL ${path} http ${code} (want ${expected})" >&2
    exit 1
  fi
  python3 - "$file" "$path" <<'PY'
import json, sys
path = sys.argv[2]
payload = json.load(open(sys.argv[1], encoding="utf-8"))
status = payload.get("status")
if path.endswith("/health") and status != "ok":
    raise SystemExit(f"FAIL {path} status={status!r}")
if path.endswith("/ready") and status not in {"ready", "degraded"}:
    raise SystemExit(f"FAIL {path} status={status!r}")
checks = payload.get("checks") or {}
# Print check names and up/down only. Never dump the rest of the payload.
if checks:
    summary = " ".join(f"{name}={value}" for name, value in sorted(checks.items()))
    print(f"ok {path} {status} {summary}")
else:
    print(f"ok {path} {status} {payload.get('environment', '')} {payload.get('version', '')}")
PY
}

check "/health" "200"
# Readiness may be 200 or 503; both are valid smoke outcomes as long as the
# body classifies database and vector_store without leaking endpoints.
ready_file="$tmpdir/ready.json"
ready_code="$(curl -sS -o "$ready_file" -w "%{http_code}" "${base}/health/ready" || true)"
if [ "$ready_code" != "200" ] && [ "$ready_code" != "503" ]; then
  echo "FAIL /health/ready http ${ready_code}" >&2
  exit 1
fi
python3 - "$ready_file" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
checks = payload.get("checks") or {}
for name in ("database", "vector_store"):
    if name not in checks:
        raise SystemExit(f"FAIL /health/ready missing {name}")
summary = " ".join(f"{name}={value}" for name, value in sorted(checks.items()))
print(f"ok /health/ready {payload.get('status')} {summary}")
PY

echo "smoke passed"
