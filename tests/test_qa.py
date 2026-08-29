"""Grounded question answering.

The LLM is a fake that returns whatever the test needs, because what is under
test is the grounding machinery around it: what goes into the prompt, what
comes back out, and what is refused.
"""

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.models import Chunk
from app.services.indexing import index_document
from app.services.qa import NO_EVIDENCE, GroundedAnswer, ask, build_context
from app.services.retrieval import SearchHit
from app.services.vector_store import VectorStoreService
from tests.test_indexing import _FakeEmbeddings


class _FakeLLM:
    """Returns a prepared answer and records exactly what it was asked."""

    provider = "fake"
    model = "fake-llm"

    def __init__(self, answer: GroundedAnswer | None = None) -> None:
        self.answer = answer or GroundedAnswer(
            answer="Payment is due within thirty days.",
            source_ids=[],
            has_sufficient_evidence=True,
        )
        self.calls: list[dict] = []

    async def complete(self, system: str, user: str) -> str:
        raise AssertionError("grounded answering must use the structured path")

    async def complete_structured(self, system: str, user: str, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return self.answer


@pytest_asyncio.fixture
async def store():
    client = AsyncQdrantClient(":memory:")
    yield VectorStoreService(client=client, collection="test_qa")
    await client.close()


@pytest.fixture
def embeddings():
    return _FakeEmbeddings()


@pytest_asyncio.fixture
def indexed(db_session, store, embeddings):
    async def _index(tenant, document, texts: list[str]) -> list[str]:
        source_ids = []
        for ordinal, text in enumerate(texts):
            source_id = f"{document.id}:{ordinal:05d}"
            db_session.add(
                Chunk(
                    tenant_id=tenant.id,
                    document_id=document.id,
                    ordinal=ordinal,
                    text=text,
                    source_id=source_id,
                    page_number=ordinal + 1,
                    section_title=f"Clause {ordinal + 1}",
                )
            )
            source_ids.append(source_id)
        await db_session.flush()
        await index_document(
            db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
        )
        return source_ids

    return _index


async def test_an_answer_cites_the_sources_it_used(
    db_session, store, embeddings, tenant, make_document, indexed
):
    document = await make_document(tenant)
    (payment_source, _) = await indexed(
        tenant,
        document,
        ["Payment is due within thirty days.", "Either party may give notice."],
    )
    llm = _FakeLLM(
        GroundedAnswer(
            answer="Within thirty days.",
            source_ids=[payment_source],
            has_sufficient_evidence=True,
        )
    )

    result = await ask(
        db_session,
        embeddings,
        llm,
        store,
        tenant_id=tenant.id,
        question="When is payment due?",
        limit=5,
    )

    assert result.has_sufficient_evidence is True
    assert [hit.source_id for hit in result.sources] == [payment_source]
    assert result.sources[0].page_number == 1


async def test_a_cited_source_that_was_never_supplied_is_dropped(
    db_session, store, embeddings, tenant, make_document, indexed
):
    """A model can name an id it was never given; it must not become a citation."""
    document = await make_document(tenant)
    (real_source, _) = await indexed(
        tenant, document, ["Payment is due within thirty days.", "Notice period."]
    )
    llm = _FakeLLM(
        GroundedAnswer(
            answer="Within thirty days.",
            source_ids=[real_source, "totally-made-up:99999"],
            has_sufficient_evidence=True,
        )
    )

    result = await ask(
        db_session,
        embeddings,
        llm,
        store,
        tenant_id=tenant.id,
        question="When is payment due?",
        limit=5,
    )

    assert [hit.source_id for hit in result.sources] == [real_source]


async def test_no_retrieved_passages_means_the_model_is_never_asked(
    db_session, store, embeddings, tenant
):
    """With nothing to ground an answer in, asking anyway invites invention."""
    llm = _FakeLLM()

    result = await ask(
        db_session,
        embeddings,
        llm,
        store,
        tenant_id=tenant.id,
        question="When is payment due?",
        limit=5,
    )

    assert result.has_sufficient_evidence is False
    assert result.answer == NO_EVIDENCE
    assert result.sources == []
    assert llm.calls == []


async def test_insufficient_evidence_is_reported_not_guessed(
    db_session, store, embeddings, tenant, make_document, indexed
):
    document = await make_document(tenant)
    await indexed(tenant, document, ["Payment is due within thirty days."])
    llm = _FakeLLM(
        GroundedAnswer(
            answer="The documents do not state the governing law.",
            source_ids=[],
            has_sufficient_evidence=False,
        )
    )

    result = await ask(
        db_session,
        embeddings,
        llm,
        store,
        tenant_id=tenant.id,
        question="Which law governs?",
        limit=5,
    )

    assert result.has_sufficient_evidence is False
    assert result.sources == []
    assert result.considered == 1


async def test_conflicting_sources_are_both_preserved(
    db_session, store, embeddings, tenant, make_document, indexed
):
    """Two clauses that disagree are a fact about the documents."""
    document = await make_document(tenant)
    first, second = await indexed(
        tenant,
        document,
        [
            "Payment is due within thirty days.",
            "Payment is due within sixty days of invoice.",
        ],
    )
    llm = _FakeLLM(
        GroundedAnswer(
            answer="The documents disagree: one clause says thirty days, another sixty.",
            source_ids=[first, second],
            has_sufficient_evidence=True,
            conflicting=True,
        )
    )

    result = await ask(
        db_session,
        embeddings,
        llm,
        store,
        tenant_id=tenant.id,
        question="When is payment due?",
        limit=5,
    )

    assert result.conflicting is True
    assert {hit.source_id for hit in result.sources} == {first, second}
    # Both passages reached the model, rather than one being dropped as
    # redundant on the way in.
    prompt = llm.calls[0]["user"]
    assert "thirty days" in prompt
    assert "sixty days" in prompt


async def test_the_system_prompt_forbids_going_beyond_the_sources(
    db_session, store, embeddings, tenant, make_document, indexed
):
    document = await make_document(tenant)
    await indexed(tenant, document, ["Payment is due within thirty days."])
    llm = _FakeLLM()

    await ask(
        db_session,
        embeddings,
        llm,
        store,
        tenant_id=tenant.id,
        question="When is payment due?",
        limit=5,
    )

    system = llm.calls[0]["system"]
    assert "only what the sources say" in system
    assert "has_sufficient_evidence to false" in system
    assert "conflict" in system.lower()


async def test_another_tenant_gets_no_evidence_rather_than_an_answer(
    db_session, store, embeddings, tenant, other_tenant, make_document, indexed
):
    document = await make_document(tenant)
    await indexed(tenant, document, ["Payment is due within thirty days."])
    llm = _FakeLLM()

    result = await ask(
        db_session,
        embeddings,
        llm,
        store,
        tenant_id=other_tenant.id,
        question="When is payment due?",
        limit=5,
    )

    assert result.has_sufficient_evidence is False
    assert result.sources == []
    assert llm.calls == []


async def test_an_empty_question_asks_nothing(db_session, store, embeddings, tenant):
    llm = _FakeLLM()

    result = await ask(
        db_session, embeddings, llm, store, tenant_id=tenant.id, question="  ", limit=5
    )

    assert result.answer == NO_EVIDENCE
    assert llm.calls == []
    assert embeddings.calls == []


def test_the_context_labels_every_passage_with_its_provenance():
    hits = [
        SearchHit(
            chunk_id="c1",
            document_id="d1",
            document_filename="contract.pdf",
            source_id="d1:00000",
            text="Payment is due within thirty days.",
            score=0.9,
            ordinal=0,
            page_number=3,
            section_title="Payment",
            source_metadata={},
        )
    ]

    context = build_context(hits)

    assert "[source_id: d1:00000]" in context
    assert "contract.pdf" in context
    assert "page 3" in context
    assert "Payment" in context
    assert "Payment is due within thirty days." in context
