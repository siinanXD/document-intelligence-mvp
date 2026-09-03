# Retrieval and generation evaluation

SIN-75 measures whether search retrieves the correct evidence. SIN-74 measures
whether `/ask` answers are grounded, cited and honest about missing or
conflicting evidence. Neither track parses electrical schematics or PLC
programs.

There was no evaluation harness on `main` before SIN-75. The runners call the
real `search`, `lexical.search` and `ask` services on documents ingested
through `ingest_upload` and `process_job`.

## Layout

```
evaluation/
  datasets/retrieval-v1/                 versioned retrieval golden corpus
  datasets/generation-v1/                grounded-answer cases over the same documents
  datasets/machine-intelligence-v1/      SIN-99 conveyor-line fixture and oracle
  baselines/retrieval-v1.json
  baselines/generation-v1.json
app/evaluation/                          metrics, ingest, runners, CLI
```

`track` on each dataset is `retrieval`, `generation`, or `machine_intelligence`. Later tracks
add a new dataset directory and metric functions; they should not replace this package.

Generation-v1 reuses the retrieval-v1 synthetic files via relative paths. It
does not copy customer data and it does not store generated answers.

## Commands

Deterministic CI evaluation (hashing embeddings, no paid provider):

```bash
# Uses TEST_DATABASE_URL when set, otherwise DATABASE_URL.
# The database transaction is rolled back; Qdrant is in-memory.
python -m app.evaluation
python -m app.evaluation --mode semantic
python -m app.evaluation --mode lexical
python -m app.evaluation --track generation
python -m app.evaluation --no-compare
```

`--track generation` uses the scripted LLM (`evaluation/scripted-grounded`).
That oracle may only cite source ids that were actually supplied in the prompt,
and it only emits gold facts when the matching evidence was retrieved. It is a
pipeline check, not a live-model quality target.

Optional live-provider runs (never ordinary CI):

```bash
python -m app.evaluation --embeddings live --no-compare --output evaluation/reports/live-retrieval.json

# Exercise the configured second-stage reranker in retrieval or generation.
python -m app.evaluation --embeddings live --reranker configured --no-compare \
  --output evaluation/reports/live-reranked-retrieval.json

# Bounded live generation. Case cap defaults to 8. Dollar cap defaults to
# $0.50 only when LLM_INPUT_USD_PER_MILLION and LLM_OUTPUT_USD_PER_MILLION
# are both set (generation + judge usage). Embeddings are bounded by cases.
python -m app.evaluation --track generation --llm live --embeddings live \
  --output evaluation/reports/live-generation.json

# Optional LLM-judge scores (groundedness/completeness). Fail-open, not a gate.
python -m app.evaluation --track generation --llm live --judge live \
  --max-cases 5 --max-cost-usd 0.25 \
  --output evaluation/reports/live-generation-judge.json
```

Live generation and live judge default to `--no-compare` against the scripted
hashing baseline. Record a separate live baseline if you want to compare live
runs to each other.

Exit codes: `0` pass, `1` quality-threshold regression, `2` citation leakage,
cross-tenant evidence, unresolvable citations or other hard invariant.

## Corpus

`evaluation/datasets/retrieval-v1` is synthetic machine documentation for
Packaging Line PL-04 (tenant A) and PL-99 (tenant B). Formats: PDF, DOCX,
XLSX, Markdown. Languages: English and German. Identifiers such as M3, K17,
B17, PL-04, Rev 2.3 and 6ES7315-2EH14-0AB0 appear as written text only.

Retrieval relevance judgments are `{document, contains}` pairs, not model
answers. Source ids are assigned at ingest (`{document_id}:{ordinal}`); the
runner resolves judgments to those ids after chunking.

Generation-v1 adds expected facts, `answerable`, `expected_conflict`,
forbidden claims and forbidden documents on the same fixtures. Gold labels are
independent of any generated answer.

## Embeddings and LLMs

- **hashing** (`evaluation` / `hashing-bow` / `v1`, 64 dimensions) is the
  default embedder. Signed hashing-trick unigrams and bigrams. Deterministic,
  offline, sensitive to ranking and chunking changes.
- **scripted** (`evaluation` / `scripted-grounded`) is the default generation
  LLM. No network.
- **configured reranker** is opt-in with `--reranker configured` and uses
  `get_reranker()` for both retrieval and generation tracks. The command fails
  if `RERANKER_PROVIDER` is still `none`; ordinary CI never enables it.
- **live** uses `get_embedding_provider()` / `get_llm_provider()` from
  settings. Opt-in only. Bounded by `--max-cases` (default 8 when live).
  `--max-cost-usd` applies to generation and judge usage only, and only
  when both LLM price settings are configured. Passing `--max-cost-usd`
  without those prices is an error. Embedding spend is bounded by the
  case cap. A truncated run (`stopped_reason` or missing cases) fails
  baseline comparison.

## Retrieval thresholds

`evaluation/baselines/retrieval-v1.json` records the measured hashing run and
the floors used for regression. Leakage must stay 0. Quality floors are the
measured values themselves (hashing-bow@v1, retrieval-v1 1.0.0):

| Mode | R@1 | R@3 | R@5 | P@1 | P@3 | P@5 | MRR | leakage |
|---|---|---|---|---|---|---|---|---|
| semantic | 0.3841 | 0.6812 | 0.7464 | 0.4348 | 0.2899 | 0.2000 | 0.6319 | 0 |
| lexical | 0.5000 | 0.8333 | 0.8889 | 0.5556 | 0.4074 | 0.2667 | 0.7593 | 0 |

They are not targets. Semantic Recall@5 leaves three failed cases:
`ans-m3-interval-en`, `multi-invoice-and-k17`, `ver-current-revision`. A later
live-provider run should be stored separately and must not replace this CI
baseline.

A partial reindex of mixed embedding identities can collapse recall (stale
points occupy top-k). Capture baselines on a fully reindexed corpus.

## Generation evaluation

The generation runner calls `app.services.qa.ask`. Deterministic checks run
first and are the release gate:

* every cited source id must resolve in PostgreSQL for that tenant
* citation precision against gold evidence
* no foreign / cross-tenant document ids (filenames are not unique)
* no forbidden-document citations or forbidden-claim substrings
* unanswerable cases must set `has_sufficient_evidence` false
* conflicting cases must set `conflicting` true when both sides were retrieved
* a truncated live run (`stopped_reason`, missing cases, or a quality metric
  of `None` when the baseline has a floor) fails comparison

Retrieval failures (gold evidence was not supplied to the model) and
generation failures (the model was given enough evidence and still answered
badly) are reported as separate lists. A factual answer (`has_sufficient_evidence`
true) without resolvable citations fails the gate.

The optional LLM-judge scores groundedness and completeness. It is never the
only release decision and is not invoked from ordinary CI. Judge errors are
fail-open: the run continues without judge scores.

The JSON report identifies dataset, prompt name/version, embedding
provider/model/version, LLM provider/model, commit, latency, tokens and cost
when the provider supplies usage. It does not store questions, answers or
passage text.

### Generation thresholds

`evaluation/baselines/generation-v1.json` records the measured
hashing + scripted run. Hard invariants are 0: `cross_tenant_leakage`,
`foreign_source_ids`, `unresolvable_citations`, `forbidden_claim_cases`.
Quality floors are the measured values of that run:

| citation_p | unanswerable | conflict | factual_cite | leakage |
|---|---|---|---|---|
| 1.0 | 1.0 | 1.0 | 1.0 | 0 |

Hashing retrieval missed `gen-multi-m3-bom`; that is a retrieval failure,
not a generation failure. All three conflict cases retrieved both sides and
set `conflicting`. They are not live-model targets.

Limitations: hashing retrieval will miss some multi-document and conflict
cases; those are retrieval failures, not generation failures. The scripted LLM
does not measure OpenAI answer quality. A live run is required before treating
a prompt or model change as an answer-quality improvement. The judge is
advisory.

## Machine intelligence fixture (SIN-99)

`evaluation/datasets/machine-intelligence-v1` is a synthetic 12-section conveyor
line (CL-12) plus a machine-readable oracle. It does not replace retrieval-v1 or
generation-v1. `python -m app.evaluation` still runs those two tracks only.

```bash
python -m app.evaluation.machine_intelligence          # regenerate committed sources + oracle
python -m app.evaluation.machine_intelligence --check  # fail on generator drift
```

The generator in `app/evaluation/machine_intelligence/` is the source of truth.
UTF-8 sources and `oracle.json` are committed. PDF/XLSX bytes are built in tests
from those sources. Manufacturer “datasheets” are JSON facts with
`https://example.invalid/...` URLs — not copyrighted manuals.

The SimaticML XML is labelled synthetic. `conformance/` holds a provenance
template and README, not a fake TIA export. SIN-93 must not claim TIA-export
compatibility until a legally cleared sample is added there.

