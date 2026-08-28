# document-intelligence-mvp

A multi-tenant document intelligence service. Documents are uploaded, parsed into a
normalized representation, chunked with provenance, embedded, indexed for semantic
search, and answered over with grounded citations.

This repository is at the bootstrap stage: the FastAPI skeleton, tooling and CI are
in place; persistence, ingestion and the AI layer land in the following milestones.

## Requirements

- Python 3.12

## Quickstart

```bash
git clone https://github.com/siinanXD/document-intelligence-mvp.git
cd document-intelligence-mvp

python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env        # fill in locally; never commit it

uvicorn app.main:app --reload
```

The API is then available on http://127.0.0.1:8000:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","version":"0.1.0","environment":"local"}
```

Interactive docs: http://127.0.0.1:8000/docs

## Development

```bash
ruff check .        # lint
ruff format .       # format
pytest              # tests
```

`ruff check .`, `ruff format --check .` and `pytest` run in CI on every push and pull
request via [`.github/workflows/ci.yml`](.github/workflows/ci.yml), on Python 3.12.
Tests mock every external provider - CI never makes a paid API call.

## Configuration

All configuration comes from the environment and is read through
`app/core/settings.py`. [`.env.example`](.env.example) lists the variable names.
`.env` is git-ignored and must never be committed.

## Project layout

```
app/
  main.py       FastAPI application factory
  api/          HTTP routes
  core/         settings and cross-cutting infrastructure
  providers/    external systems behind interfaces
tests/          pytest suite
docs/           workflow and design notes
```

## Contributing

Agents and humans follow the same workflow: one issue per branch and pull request,
small commits, green checks before pushing. See
[`docs/agent-workflow.md`](docs/agent-workflow.md) for the details and
[`CLAUDE.md`](CLAUDE.md) for architecture, tenant-isolation, provider and privacy rules.
