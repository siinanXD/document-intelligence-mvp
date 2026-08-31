"""Extracting what a document is.

The model is asked to describe a document from its own text, under one standing
rule: absent information stays absent. A profile with an empty `organizations`
list is a useful fact; a profile with a plausible-looking company name that
appears nowhere in the document is worse than no profile at all, because
everything downstream - relations, search, the answer a user reads - treats it
as evidence.
"""

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document, DocumentProfile
from app.providers.base import LLMProvider
from app.providers.prompts import DOCUMENT_PROFILE

logger = logging.getLogger(__name__)


class ExtractedProfile(BaseModel):
    """What the model is allowed to say about a document."""

    title: str | None = Field(default=None, description="The document's own title, if stated.")
    document_type: str | None = Field(default=None, description="Short lowercase noun phrase.")
    language: str | None = Field(default=None, description="ISO 639-1 code.")
    summary: str | None = Field(default=None, description="Two or three sentences.")
    organizations: list[str] = Field(default_factory=list)
    persons: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(
        default_factory=list,
        description="Reference numbers, case numbers, invoice numbers, contract ids.",
    )
    topics: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ProfilingOutcome:
    document_id: object
    profile: DocumentProfile


class ProfilingError(RuntimeError):
    """Profiling failed in a way worth retrying."""


def build_excerpt(texts: list[str], *, max_characters: int) -> str:
    """Take the opening of a document, whole chunks at a time.

    The beginning is where titles, parties, dates and reference numbers live.
    Chunks are kept intact rather than truncated mid-sentence, so the model is
    never asked to identify a party from half its name.
    """
    excerpt: list[str] = []
    remaining = max_characters
    for text in texts:
        if len(text) > remaining:
            break
        excerpt.append(text)
        remaining -= len(text)
    if not excerpt and texts:
        # A single chunk longer than the budget still beats sending nothing.
        excerpt.append(texts[0][:max_characters])
    return "\n\n".join(excerpt)


def _clean(values: list[str]) -> list[str]:
    """Drop blanks and duplicates, keeping the order the model gave."""
    seen: set[str] = set()
    cleaned = []
    for value in values:
        stripped = (value or "").strip()
        if stripped and stripped.lower() not in seen:
            seen.add(stripped.lower())
            cleaned.append(stripped)
    return cleaned


async def profile_document(
    session: AsyncSession,
    llm: LLMProvider,
    *,
    tenant_id,
    document_id,
    max_excerpt_characters: int = 12000,
) -> ProfilingOutcome:
    """Extract and persist a document's profile, replacing any previous one."""
    document = (
        (
            await session.execute(
                select(Document).where(
                    Document.id == document_id,
                    Document.tenant_id == tenant_id,
                    Document.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    if document is None:
        raise ProfilingError("the document to profile no longer exists")

    texts = list(
        (
            await session.execute(
                select(Chunk.text)
                .where(Chunk.tenant_id == tenant_id, Chunk.document_id == document_id)
                .order_by(Chunk.ordinal)
            )
        ).scalars()
    )

    if not texts:
        extracted = ExtractedProfile()
    else:
        excerpt = build_excerpt(texts, max_characters=max_excerpt_characters)
        try:
            generation = await llm.complete_structured(
                DOCUMENT_PROFILE, f"Document text:\n\n{excerpt}", ExtractedProfile
            )
            extracted = generation.content
        except Exception as exc:
            # The provider's message can quote the text it was given.
            raise ProfilingError(f"profile extraction failed ({type(exc).__name__})") from None

    profile = (
        (
            await session.execute(
                select(DocumentProfile).where(
                    DocumentProfile.tenant_id == tenant_id,
                    DocumentProfile.document_id == document_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if profile is None:
        profile = DocumentProfile(tenant_id=tenant_id, document_id=document_id)
        session.add(profile)

    profile.language = (extracted.language or None) or None
    profile.summary = (extracted.summary or None) or None
    profile.organizations = _clean(extracted.organizations)
    profile.persons = _clean(extracted.persons)
    profile.dates = _clean(extracted.dates)
    profile.identifiers = _clean(extracted.identifiers)
    profile.topics = _clean(extracted.topics)

    # The document carries the headline fields so a listing does not need a join.
    document.title = (extracted.title or "").strip() or None
    document.document_type = (extracted.document_type or "").strip().lower() or None
    document.summary = profile.summary

    await session.flush()

    logger.info(
        "document profiled",
        extra={
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "organizations": len(profile.organizations),
            "persons": len(profile.persons),
            "identifiers": len(profile.identifiers),
        },
    )
    return ProfilingOutcome(document_id=document_id, profile=profile)
