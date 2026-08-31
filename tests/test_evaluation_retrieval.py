"""End-to-end golden retrieval evaluation through the real services.

Uses hashing embeddings, in-memory Qdrant and the test PostgreSQL. No paid
provider is contacted. The same path as `python -m app.evaluation`.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.evaluation.cases import load_dataset
from app.evaluation.compare import compare_reports
from app.evaluation.embeddings import HashingEmbeddings
from app.evaluation.ingest import ingest_dataset
from app.evaluation.report import build_report, git_commit, load_report
from app.evaluation.runner import run_dataset
from app.providers.local_storage import LocalStorageBackend
from app.services.vector_store import VectorStoreService

BASELINE = Path(__file__).resolve().parents[1] / "evaluation" / "baselines" / "retrieval-v1.json"


@pytest_asyncio.fixture
async def golden(db_session, tmp_path):
    dataset = load_dataset()
    embeddings = HashingEmbeddings()
    client = AsyncQdrantClient(":memory:")
    store = VectorStoreService(client=client, collection="eval_retrieval_v1_test")
    storage = LocalStorageBackend(tmp_path / "objects")
    documents = await ingest_dataset(db_session, storage, dataset, embeddings, store)
    results = await run_dataset(db_session, dataset, documents, embeddings, store)
    yield dataset, embeddings, results
    await client.close()


async def test_golden_corpus_has_zero_cross_tenant_leakage(golden):
    _dataset, _embeddings, results = golden
    for mode, result in results.items():
        assert result.summary["cross_tenant_leakage"] == 0, mode
        assert result.summary["leaked_sources"] == []


async def test_golden_corpus_separates_semantic_and_lexical(golden):
    _dataset, _embeddings, results = golden
    assert set(results) == {"semantic", "lexical"}
    assert results["semantic"].summary["evaluated_cases"] >= 1
    assert results["lexical"].summary["evaluated_cases"] >= 1
    semantic_ids = {score.case_id for score in results["semantic"].scores}
    lexical_ids = {score.case_id for score in results["lexical"].scores}
    assert semantic_ids.isdisjoint(lexical_ids)


async def test_unanswerable_cases_are_not_counted_as_evaluated(golden):
    dataset, _embeddings, results = golden
    unanswerable = {case.id for case in dataset.cases if case.category == "unanswerable"}
    scored = results["semantic"].summary
    assert scored["no_evidence_cases"] >= len(unanswerable)
    for score in results["semantic"].scores:
        if score.case_id in unanswerable:
            assert score.recall_at_5 is None
            assert score.mrr is None


async def test_golden_metrics_meet_the_checked_in_baseline(golden):
    dataset, embeddings, results = golden
    if not BASELINE.is_file():
        pytest.skip("baseline has not been recorded yet")
    report = build_report(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        commit=git_commit(BASELINE.parents[2]),
        embeddings=embeddings,
        modes={name: result.summary for name, result in results.items()},
    )
    comparison = compare_reports(report, load_report(BASELINE))
    assert comparison.leakage == 0
    assert comparison.passed, comparison.threshold_failures
