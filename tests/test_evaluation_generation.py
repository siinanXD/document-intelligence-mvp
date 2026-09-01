"""End-to-end golden generation evaluation through the real ask path.

Uses hashing embeddings, the scripted LLM, in-memory Qdrant and the test
PostgreSQL. No paid provider is contacted.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.evaluation.cases import load_generation_dataset
from app.evaluation.compare import compare_reports
from app.evaluation.embeddings import HashingEmbeddings
from app.evaluation.generation_runner import run_generation_dataset
from app.evaluation.ingest import ingest_dataset
from app.evaluation.report import build_generation_report, git_commit, load_report
from app.evaluation.scripted_llm import ScriptedLLM
from app.providers.local_storage import LocalStorageBackend
from app.providers.prompts import ASK_GROUNDED
from app.services.vector_store import VectorStoreService

BASELINE = Path(__file__).resolve().parents[1] / "evaluation" / "baselines" / "generation-v1.json"


@pytest_asyncio.fixture
async def golden_generation(db_session, tmp_path):
    dataset = load_generation_dataset()
    embeddings = HashingEmbeddings()
    llm = ScriptedLLM(dataset.cases)
    client = AsyncQdrantClient(":memory:")
    store = VectorStoreService(client=client, collection="eval_generation_v1_test")
    storage = LocalStorageBackend(tmp_path / "objects")
    documents = await ingest_dataset(db_session, storage, dataset, embeddings, store)
    result = await run_generation_dataset(db_session, dataset, documents, embeddings, llm, store)
    yield dataset, embeddings, llm, result
    await client.close()


async def test_generation_eval_reports_retrieval_and_generation_separately(golden_generation):
    _dataset, _embeddings, _llm, result = golden_generation
    summary = result.summary
    assert summary.total_cases >= 12
    assert summary.retrieval_failures >= 0
    assert summary.generation_failures >= 0
    assert set(summary.as_dict()) >= {
        "retrieval_failures",
        "generation_failures",
        "retrieval_failed_case_ids",
        "generation_failed_case_ids",
    }


async def test_generation_eval_has_zero_citation_leakage(golden_generation):
    _dataset, _embeddings, _llm, result = golden_generation
    assert result.summary.cross_tenant_leakage == 0
    assert result.summary.foreign_source_ids == 0
    assert result.summary.unresolvable_citations == 0
    assert result.summary.forbidden_claim_cases == 0


async def test_unanswerable_cases_do_not_claim_evidence(golden_generation):
    dataset, _embeddings, _llm, result = golden_generation
    unanswerable = {case.id for case in dataset.cases if not case.answerable}
    by_id = {score.case_id: score for score in result.scores}
    for case_id in unanswerable:
        score = by_id[case_id]
        assert score.no_evidence_correct is True
        assert score.has_sufficient_evidence is False


async def test_factual_answers_have_resolvable_citations(golden_generation):
    _dataset, _embeddings, _llm, result = golden_generation
    for score in result.scores:
        if score.has_sufficient_evidence:
            assert score.cited_source_ids
            assert score.all_citations_resolvable is True
            assert score.foreign_source_ids == []
            assert score.factual_with_resolvable_citations is True


async def test_conflict_cases_flag_disagreement_when_retrieved(golden_generation):
    dataset, _embeddings, _llm, result = golden_generation
    expected = {case.id for case in dataset.cases if case.expected_conflict}
    by_id = {score.case_id: score for score in result.scores}
    scored = [by_id[case_id] for case_id in expected if not by_id[case_id].retrieval_failed]
    for score in scored:
        assert score.conflict_correct is True
        assert score.conflicting is True


async def test_generation_metrics_meet_the_checked_in_baseline(golden_generation):
    dataset, embeddings, llm, result = golden_generation
    if not BASELINE.is_file():
        pytest.skip("generation baseline has not been recorded yet")
    report = build_generation_report(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        commit=git_commit(BASELINE.parents[2]),
        embeddings=embeddings,
        llm=llm,
        prompt_name=ASK_GROUNDED.name,
        prompt_version=ASK_GROUNDED.version,
        judge="none",
        summary=result.summary.as_dict(),
    )
    comparison = compare_reports(report, load_report(BASELINE))
    assert comparison.leakage == 0
    assert comparison.passed, comparison.threshold_failures


def test_ordinary_ci_does_not_run_live_generation_evaluation():
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    check = Path(__file__).resolve().parents[1] / ".cursor" / "check.sh"
    text = workflow.read_text(encoding="utf-8") + "\n" + check.read_text(encoding="utf-8")
    assert "--llm live" not in text
    assert "--judge live" not in text
    assert "--embeddings live" not in text
