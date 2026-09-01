"""Deterministic generation-quality metrics. No provider calls."""

from uuid import uuid4

from app.evaluation.cases import EvidenceJudgment, GenerationCase
from app.evaluation.compare import compare_reports
from app.evaluation.embeddings import HashingEmbeddings
from app.evaluation.generation_metrics import (
    aggregate_generation_scores,
    score_generation_case,
)
from app.evaluation.report import build_generation_report
from app.evaluation.scripted_llm import parse_supplied_sources
from app.providers.generation import GenerationResult, RetrievalSourceTrace, RetrievalTrace
from app.providers.prompts import ASK_GROUNDED
from app.services.qa import AskResult
from app.services.retrieval import SearchHit


def _case(**overrides) -> GenerationCase:
    values = {
        "id": "c1",
        "language": "en",
        "category": "answerable",
        "query": "How often is M3 inspected?",
        "tenant": "tenant_a",
        "answerable": True,
        "expected_conflict": False,
        "expected_facts": ("500 operating hours",),
        "forbidden_claims": ("999 operating hours",),
        "relevant": (
            EvidenceJudgment(document="operating_manual.pdf", contains="500 operating hours"),
        ),
        "forbidden_documents": ("beta_operating_manual.md",),
        "notes": "",
    }
    values.update(overrides)
    return GenerationCase(**values)


def _hit(source_id: str, filename: str, text: str) -> SearchHit:
    return SearchHit(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_filename=filename,
        source_id=source_id,
        text=text,
        score=1.0,
        ordinal=0,
        page_number=1,
        section_title="Motor",
        source_metadata={},
    )


def _result(*, answer: str, sufficient: bool, conflicting: bool, sources: list[SearchHit]):
    grounded = type("GA", (), {"source_ids": [hit.source_id for hit in sources]})()
    generation = GenerationResult(
        content=grounded,
        provider="evaluation",
        model="scripted-grounded",
        prompt_name=ASK_GROUNDED.name,
        prompt_version=ASK_GROUNDED.version,
        latency_ms=1.0,
        retry_count=0,
        trace_id="t",
        input_tokens=10,
        output_tokens=4,
        total_tokens=14,
        estimated_cost_usd=0.001,
        finish_reason="stop",
        request_id="r",
    )
    if sources:
        retrieval_sources = tuple(
            RetrievalSourceTrace(
                source_id=hit.source_id,
                document_id=str(hit.document_id),
                rank=index,
                score=hit.score,
            )
            for index, hit in enumerate(sources, start=1)
        )
    else:
        retrieval_sources = (
            RetrievalSourceTrace(
                source_id="r1",
                document_id=str(uuid4()),
                rank=1,
                score=1.0,
            ),
        )
    retrieval = RetrievalTrace(
        mode="semantic",
        candidate_count=len(retrieval_sources),
        supplied_count=len(retrieval_sources),
        sources=retrieval_sources,
    )
    return AskResult(
        answer=answer,
        has_sufficient_evidence=sufficient,
        conflicting=conflicting,
        sources=sources,
        considered=len(retrieval.sources),
        generation=generation,
        retrieval=retrieval,
    )


def test_factual_answer_requires_resolvable_citations():
    hit = _hit(
        "a:00000", "operating_manual.pdf", "Motor M3 is inspected every 500 operating hours."
    )
    result = _result(
        answer="Inspected every 500 operating hours.",
        sufficient=True,
        conflicting=False,
        sources=[hit],
    )
    score = score_generation_case(
        case=_case(),
        result=result,
        relevant_ids={"a:00000"},
        retrieved_ids={"a:00000"},
        resolvable_ids={"a:00000"},
        owned_filenames={"operating_manual.pdf"},
        cited_filenames={"a:00000": "operating_manual.pdf"},
    )
    assert score.generation_failed is False
    assert score.factual_with_resolvable_citations is True
    assert score.citation_precision == 1.0


def test_foreign_source_id_is_a_hard_generation_failure():
    hit = _hit("b:00000", "beta_operating_manual.md", "999 operating hours")
    result = _result(
        answer="Inspected every 999 operating hours.",
        sufficient=True,
        conflicting=False,
        sources=[hit],
    )
    score = score_generation_case(
        case=_case(),
        result=result,
        relevant_ids={"a:00000"},
        retrieved_ids={"a:00000"},
        resolvable_ids={"b:00000"},
        owned_filenames={"operating_manual.pdf"},
        cited_filenames={"b:00000": "beta_operating_manual.md"},
    )
    assert score.foreign_source_ids == ["b:00000"]
    assert score.forbidden_claim_hits == ["999 operating hours"]
    assert score.generation_failed is True


def test_unanswerable_must_not_claim_sufficient_evidence():
    case = _case(
        id="u1",
        category="unanswerable",
        answerable=False,
        expected_facts=(),
        relevant=(),
        query="When was the line decommissioned?",
    )
    result = _result(
        answer="There is nothing in the available documents that answers this question.",
        sufficient=False,
        conflicting=False,
        sources=[],
    )
    score = score_generation_case(
        case=case,
        result=result,
        relevant_ids=set(),
        retrieved_ids={"r1"},
        resolvable_ids=set(),
        owned_filenames={"operating_manual.pdf"},
        cited_filenames={},
    )
    assert score.no_evidence_correct is True
    assert score.generation_failed is False
    assert score.retrieval_failed is False


def test_conflict_flag_is_required_when_both_sides_were_retrieved():
    case = _case(
        id="conf",
        category="conflicting",
        expected_conflict=True,
        expected_facts=("500 Betriebsstunden", "1000 Betriebsstunden"),
        relevant=(
            EvidenceJudgment(document="maintenance_manual.docx", contains="500 Betriebsstunden"),
            EvidenceJudgment(document="revision_notes.md", contains="1000 Betriebsstunden"),
        ),
    )
    hits = [
        _hit("a:1", "maintenance_manual.docx", "500 Betriebsstunden"),
        _hit("a:2", "revision_notes.md", "1000 Betriebsstunden"),
    ]
    result = _result(
        answer="The sources disagree: 500 Betriebsstunden; 1000 Betriebsstunden",
        sufficient=True,
        conflicting=False,
        sources=hits,
    )
    score = score_generation_case(
        case=case,
        result=result,
        relevant_ids={"a:1", "a:2"},
        retrieved_ids={"a:1", "a:2"},
        resolvable_ids={"a:1", "a:2"},
        owned_filenames={"maintenance_manual.docx", "revision_notes.md"},
        cited_filenames={"a:1": "maintenance_manual.docx", "a:2": "revision_notes.md"},
    )
    assert score.conflict_correct is False
    assert score.generation_failed is True


def test_retrieval_failure_is_not_counted_as_generation_failure():
    result = _result(
        answer="There is nothing in the available documents that answers this question.",
        sufficient=False,
        conflicting=False,
        sources=[],
    )
    score = score_generation_case(
        case=_case(),
        result=result,
        relevant_ids={"a:00000"},
        retrieved_ids={"other"},
        resolvable_ids=set(),
        owned_filenames={"operating_manual.pdf"},
        cited_filenames={},
    )
    assert score.retrieval_failed is True
    assert score.generation_failed is False


def test_release_gate_fails_on_citation_leakage():
    embeddings = HashingEmbeddings()

    class _LLM:
        provider = "evaluation"
        model = "scripted-grounded"

    payload = aggregate_generation_scores([]).as_dict()
    payload["cross_tenant_leakage"] = 1
    payload["foreign_source_ids"] = 1
    report = build_generation_report(
        dataset_name="generation-v1",
        dataset_version="1.0.0",
        commit="abc",
        embeddings=embeddings,
        llm=_LLM(),
        prompt_name="ask_grounded",
        prompt_version="v1",
        judge="none",
        summary=payload,
    )
    baseline = {
        "thresholds": {
            "cross_tenant_leakage": 0,
            "foreign_source_ids": 0,
            "unresolvable_citations": 0,
            "forbidden_claim_cases": 0,
            "factual_citation_rate": 1.0,
        },
        "report": report,
    }
    leaked_summary = dict(payload)
    leaked_summary["cross_tenant_leakage"] = 2
    leaked_summary["foreign_source_ids"] = 2
    leaked = dict(report)
    leaked["summary"] = leaked_summary
    comparison = compare_reports(leaked, baseline)
    assert comparison.passed is False
    assert comparison.leakage == 2


def test_scripted_parser_reads_supplied_source_blocks():
    user = (
        "Sources:\n\n"
        "[source_id: doc:00000] operating_manual.pdf (page 1, Motor M3 inspection)\n"
        "Motor M3 is inspected every 500 operating hours.\n\n"
        "[source_id: doc:00001] revision_notes.md\n"
        "A field report names 1000 hours.\n\n"
        "Question: How often must motor M3 be inspected?"
    )
    sources = parse_supplied_sources(user)
    assert [item.source_id for item in sources] == ["doc:00000", "doc:00001"]
    assert sources[0].filename == "operating_manual.pdf"
    assert "500 operating hours" in sources[0].text
