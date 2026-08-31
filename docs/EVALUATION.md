# Retrieval evaluation

SIN-75 measures whether the existing search services retrieve the correct
evidence. It is not an answer-quality benchmark (SIN-74) and it does not
parse electrical schematics or PLC programs.

There was no evaluation harness on `main` before this work. The runner calls
`app.services.retrieval.search` and `app.services.lexical.search` on documents
ingested through `ingest_upload` and `process_job`.

## Layout

```
evaluation/
  datasets/retrieval-v1/   versioned golden corpus
  baselines/retrieval-v1.json
app/evaluation/            metrics, ingest, runner, CLI
```

`track` on the dataset and on each case is `retrieval`. Later tracks
(document classification, connection extraction, PLC mapping, …) add a new
dataset directory and metric functions; they should not replace this package.

## Commands

Deterministic CI evaluation (hashing embeddings, no paid provider):

```bash
# Uses TEST_DATABASE_URL when set, otherwise DATABASE_URL.
# The database transaction is rolled back; Qdrant is in-memory.
python -m app.evaluation
python -m app.evaluation --mode semantic
python -m app.evaluation --mode lexical
python -m app.evaluation --no-compare
```

Optional live-provider run (never ordinary CI):

```bash
python -m app.evaluation --embeddings live --no-compare --output evaluation/reports/live.json
```

Exit codes: `0` pass, `1` quality-threshold regression, `2` cross-tenant leakage
or other hard invariant.

## Corpus

`evaluation/datasets/retrieval-v1` is synthetic machine documentation for
Packaging Line PL-04 (tenant A) and PL-99 (tenant B). Formats: PDF, DOCX,
XLSX, Markdown. Languages: English and German. Identifiers such as M3, K17,
B17, PL-04, Rev 2.3 and 6ES7315-2EH14-0AB0 appear as written text only.

Relevance judgments are `{document, contains}` pairs, not model answers.
Source ids are assigned at ingest (`{document_id}:{ordinal}`); the runner
resolves judgments to those ids after chunking.

## Embeddings

- **hashing** (`evaluation` / `hashing-bow` / `v1`, 64 dimensions) is the
  default. Signed hashing-trick unigrams and bigrams. Deterministic, offline,
  sensitive to ranking and chunking changes.
- **live** uses `get_embedding_provider()` from settings. Opt-in only.

## Thresholds

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

## Generation evaluation (SIN-74)

SIN-76 records generation metadata on `AskResult.generation` and retrieval
metadata on `AskResult.retrieval`. `app.evaluation.generation.generation_eval_record`
projects those into the fields a later generation-quality evaluator should
persist (case id, prompt name/version, provider/model, tokens, latency, cost,
cited source ids, finish status, trace id). This package still does not score
answers, and this milestone does not add a generation golden dataset.
