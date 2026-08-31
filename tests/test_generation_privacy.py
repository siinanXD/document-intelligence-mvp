"""Default traces and logs must not contain document content, prompts or secrets."""

import json
import logging

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.models import Chunk
from app.providers.generation import RetrievalTrace, generation_from_prompt
from app.providers.openai_provider import OpenAILLMProvider
from app.providers.prompts import ASK_GROUNDED, DOCUMENT_PROFILE
from app.providers.tracing import TracingAdapter
from app.services.indexing import index_document
from app.services.profiling import ExtractedProfile, profile_document
from app.services.qa import GroundedAnswer, ask
from app.services.vector_store import VectorStoreService
from tests.test_indexing import _FakeEmbeddings
from tests.test_providers import _FakeChatClient, _FakeCompletions

SECRET_QUESTION = "SECRET_QUESTION_ZXQ_WHEN_IS_PAYMENT_DUE"
SECRET_PASSAGE = "SECRET_PASSAGE_ZXQ_payment is due on invoice INV-999."
SECRET_ANSWER = "SECRET_MODEL_ANSWER_ZXQ_thirty_days"
SECRET_API_KEY = "sk-this-must-never-appear-in-traces-ZXQ"
SECRET_COOKIE = "session=COOKIE_ZXQ_must_not_leak"


class _RecordingTracer(TracingAdapter):
    def __init__(self) -> None:
        self.records: list[dict] = []

    @property
    def captures_content(self) -> bool:
        return False

    async def record_generation(
        self, result, *, extra=None, input_payload=None, output_payload=None
    ):
        self.records.append(
            {
                "kind": "generation",
                "metadata": result.safe_metadata(),
                "extra": extra or {},
                "input_payload": input_payload,
                "output_payload": output_payload,
            }
        )

    async def record_retrieval(self, retrieval: RetrievalTrace, *, extra=None):
        self.records.append(
            {
                "kind": "retrieval",
                "metadata": retrieval.safe_metadata(),
                "extra": extra or {},
            }
        )


def _blob(records: list[dict]) -> str:
    return json.dumps(records, default=str)


@pytest_asyncio.fixture
async def store():
    client = AsyncQdrantClient(":memory:")
    yield VectorStoreService(client=client, collection="test_generation_privacy")
    await client.close()


@pytest.mark.asyncio
async def test_default_generation_traces_omit_prompts_answers_and_secrets(caplog):
    tracer = _RecordingTracer()
    completions = _FakeCompletions(content=SECRET_ANSWER)
    provider = OpenAILLMProvider(client=_FakeChatClient(completions), tracer=tracer)

    with caplog.at_level(logging.DEBUG):
        result = await provider.complete(ASK_GROUNDED, SECRET_QUESTION)

    assert result.content == SECRET_ANSWER
    assert tracer.records
    blob = _blob(tracer.records) + caplog.text
    for marker in (
        SECRET_QUESTION,
        SECRET_ANSWER,
        SECRET_API_KEY,
        SECRET_COOKIE,
        ASK_GROUNDED.system[:40],
    ):
        assert marker not in blob
    assert tracer.records[0]["input_payload"] is None
    assert tracer.records[0]["output_payload"] is None


async def test_ask_retrieval_trace_omits_chunk_text_and_the_question(
    db_session, store, tenant, make_document, monkeypatch, caplog
):
    tracer = _RecordingTracer()
    monkeypatch.setattr("app.services.qa.get_tracing_adapter", lambda: tracer)

    document = await make_document(tenant)
    db_session.add(
        Chunk(
            tenant_id=tenant.id,
            document_id=document.id,
            ordinal=0,
            text=SECRET_PASSAGE,
            source_id=f"{document.id}:00000",
        )
    )
    await db_session.flush()
    embeddings = _FakeEmbeddings()
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    class _LLM:
        provider = "fake"
        model = "fake-llm"

        async def complete_structured(self, prompt, user, schema):
            return generation_from_prompt(
                GroundedAnswer(
                    answer=SECRET_ANSWER,
                    source_ids=[f"{document.id}:00000"],
                    has_sufficient_evidence=True,
                ),
                prompt,
                provider=self.provider,
                model=self.model,
            )

    with caplog.at_level(logging.DEBUG):
        result = await ask(
            db_session,
            embeddings,
            _LLM(),
            store,
            tenant_id=tenant.id,
            question=SECRET_QUESTION,
            limit=5,
        )

    assert result.retrieval is not None
    blob = _blob(tracer.records) + caplog.text + json.dumps(result.retrieval.safe_metadata())
    for marker in (SECRET_QUESTION, SECRET_PASSAGE, SECRET_ANSWER, SECRET_API_KEY):
        assert marker not in blob
    assert result.retrieval.sources[0].source_id.endswith(":00000")
    assert result.retrieval.sources[0].rank == 1


async def test_profile_generation_does_not_trace_document_text(
    db_session, tenant, make_document, caplog
):
    tracer = _RecordingTracer()
    completions = _FakeCompletions(parsed=ExtractedProfile(title="Invoice"))
    llm = OpenAILLMProvider(client=_FakeChatClient(completions), tracer=tracer)
    document = await make_document(tenant)
    db_session.add(
        Chunk(
            tenant_id=tenant.id,
            document_id=document.id,
            ordinal=0,
            text=SECRET_PASSAGE,
            source_id=f"{document.id}:00000",
        )
    )
    await db_session.flush()

    with caplog.at_level(logging.DEBUG):
        await profile_document(db_session, llm, tenant_id=tenant.id, document_id=document.id)

    blob = _blob(tracer.records) + caplog.text
    assert SECRET_PASSAGE not in blob
    assert DOCUMENT_PROFILE.system[:40] not in blob
    assert tracer.records[0]["metadata"]["prompt_name"] == "document_profile"
