"""Profile extraction.

The LLM is a fake, because what is under test is the rule around it: absent
information stays absent, and nothing invented survives.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import Chunk, DocumentProfile
from app.services.profiling import (
    ExtractedProfile,
    ProfilingError,
    build_excerpt,
    profile_document,
)


class _FakeLLM:
    provider = "fake"
    model = "fake-llm"

    def __init__(self, profile: ExtractedProfile | None = None, error=None) -> None:
        self.profile = profile or ExtractedProfile()
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, system, user):
        raise AssertionError("profiling must use the structured path")

    async def complete_structured(self, system, user, schema):
        self.calls.append({"system": system, "user": user})
        if self.error:
            raise self.error
        return self.profile


@pytest_asyncio.fixture
def with_chunks(db_session):
    async def _add(tenant, document, texts):
        for ordinal, text in enumerate(texts):
            db_session.add(
                Chunk(
                    tenant_id=tenant.id,
                    document_id=document.id,
                    ordinal=ordinal,
                    text=text,
                    source_id=f"{document.id}:{ordinal:05d}",
                )
            )
        await db_session.flush()

    return _add


async def test_a_profile_is_persisted_and_summarised_on_the_document(
    db_session, tenant, make_document, with_chunks
):
    document = await make_document(tenant)
    await with_chunks(tenant, document, ["Service agreement between Acme and Globex."])
    llm = _FakeLLM(
        ExtractedProfile(
            title="Service Agreement",
            document_type="Service Agreement",
            language="en",
            summary="Acme provides services to Globex.",
            organizations=["Acme", "Globex"],
            identifiers=["SA-2024-1"],
        )
    )

    outcome = await profile_document(db_session, llm, tenant_id=tenant.id, document_id=document.id)

    assert outcome.profile.organizations == ["Acme", "Globex"]
    assert outcome.profile.identifiers == ["SA-2024-1"]
    # The headline fields land on the document so a listing needs no join.
    assert document.title == "Service Agreement"
    assert document.document_type == "service agreement"
    assert document.summary == "Acme provides services to Globex."


async def test_absent_information_stays_absent(db_session, tenant, make_document, with_chunks):
    """An empty profile is a correct answer, and must not be filled in."""
    document = await make_document(tenant)
    await with_chunks(tenant, document, ["Some text with nothing identifiable."])
    llm = _FakeLLM(ExtractedProfile())

    outcome = await profile_document(db_session, llm, tenant_id=tenant.id, document_id=document.id)

    assert outcome.profile.organizations == []
    assert outcome.profile.persons == []
    assert outcome.profile.identifiers == []
    assert outcome.profile.summary is None
    assert document.title is None
    assert document.document_type is None


async def test_blanks_and_duplicates_are_dropped(db_session, tenant, make_document, with_chunks):
    document = await make_document(tenant)
    await with_chunks(tenant, document, ["text"])
    llm = _FakeLLM(ExtractedProfile(organizations=["Acme", " acme ", "", "   ", "Globex"]))

    outcome = await profile_document(db_session, llm, tenant_id=tenant.id, document_id=document.id)

    assert outcome.profile.organizations == ["Acme", "Globex"]


async def test_a_document_with_no_chunks_is_not_sent_to_the_model(
    db_session, tenant, make_document
):
    document = await make_document(tenant)
    llm = _FakeLLM()

    outcome = await profile_document(db_session, llm, tenant_id=tenant.id, document_id=document.id)

    assert llm.calls == []
    assert outcome.profile.organizations == []


async def test_profiling_twice_replaces_rather_than_duplicates(
    db_session, tenant, make_document, with_chunks
):
    document = await make_document(tenant)
    await with_chunks(tenant, document, ["text"])

    await profile_document(
        db_session,
        _FakeLLM(ExtractedProfile(organizations=["Acme"])),
        tenant_id=tenant.id,
        document_id=document.id,
    )
    await profile_document(
        db_session,
        _FakeLLM(ExtractedProfile(organizations=["Globex"])),
        tenant_id=tenant.id,
        document_id=document.id,
    )

    profiles = (
        (
            await db_session.execute(
                select(DocumentProfile).where(DocumentProfile.document_id == document.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(profiles) == 1
    assert profiles[0].organizations == ["Globex"]


async def test_another_tenant_cannot_profile_the_document(
    db_session, tenant, other_tenant, make_document, with_chunks
):
    document = await make_document(tenant)
    await with_chunks(tenant, document, ["text"])

    with pytest.raises(ProfilingError):
        await profile_document(
            db_session, _FakeLLM(), tenant_id=other_tenant.id, document_id=document.id
        )


async def test_a_provider_failure_is_retryable_and_quotes_nothing(
    db_session, tenant, make_document, with_chunks
):
    document = await make_document(tenant)
    await with_chunks(tenant, document, ["CONFIDENTIAL contents"])
    llm = _FakeLLM(error=RuntimeError("CONFIDENTIAL contents"))

    with pytest.raises(ProfilingError) as caught:
        await profile_document(db_session, llm, tenant_id=tenant.id, document_id=document.id)

    assert "CONFIDENTIAL" not in str(caught.value)


def test_the_excerpt_keeps_whole_chunks_within_the_budget():
    """A party identified from half its name is worse than one not identified."""
    excerpt = build_excerpt(["a" * 40, "b" * 40, "c" * 40], max_characters=100)

    assert excerpt == "a" * 40 + "\n\n" + "b" * 40


def test_a_single_oversized_chunk_is_still_sent():
    excerpt = build_excerpt(["x" * 500], max_characters=100)

    assert len(excerpt) == 100
