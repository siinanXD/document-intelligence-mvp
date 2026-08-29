"""Grounded question answering.

The model is given passages and asked to answer from them alone. Three things
make that more than a hope:

* The prompt supplies numbered sources and forbids anything not in them.
* The answer comes back as a schema with the source ids the model says it used,
  and those are **filtered against what was actually supplied** - a citation the
  model invented is dropped rather than returned as resolvable.
* When the passages do not answer the question, `has_sufficient_evidence` is
  false and the answer says so, instead of a confident guess.

Conflicting passages are kept, not reconciled: two clauses that disagree are a
fact about the documents, and merging them into one smooth answer would hide
exactly what the reader needs to see.
"""

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.base import EmbeddingProvider, LLMProvider
from app.services.retrieval import SearchHit, search
from app.services.vector_store import VectorStoreService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You answer questions about documents using only the numbered sources given to \
you.

Rules:
- Use only what the sources say. Never add facts from your own knowledge, and \
never infer beyond what is written.
- Cite the source id of every passage you actually used.
- If the sources do not answer the question, set has_sufficient_evidence to \
false and say plainly what is missing. Do not guess.
- If two sources disagree, report both positions and say they conflict. Do not \
reconcile them, average them, or pick one silently.
- Quote sparingly and answer in the language of the question."""


class GroundedAnswer(BaseModel):
    """The shape the model must answer in."""

    answer: str = Field(description="The answer, drawn only from the sources.")
    source_ids: list[str] = Field(
        default_factory=list,
        description="Source ids of the passages actually used.",
    )
    has_sufficient_evidence: bool = Field(
        description="False when the sources do not answer the question."
    )
    conflicting: bool = Field(default=False, description="True when the cited sources disagree.")


@dataclass(frozen=True)
class AskResult:
    answer: str
    has_sufficient_evidence: bool
    conflicting: bool
    sources: list[SearchHit] = field(default_factory=list)
    considered: int = 0


NO_EVIDENCE = "There is nothing in the available documents that answers this question."


def build_context(hits: list[SearchHit]) -> str:
    """Render passages as numbered sources the model can cite.

    Provenance travels with each passage so a citation can be checked against
    a page rather than taken on trust.
    """
    blocks = []
    for hit in hits:
        location = []
        if hit.page_number is not None:
            location.append(f"page {hit.page_number}")
        if hit.section_title:
            location.append(hit.section_title)
        where = f" ({', '.join(location)})" if location else ""
        blocks.append(f"[source_id: {hit.source_id}] {hit.document_filename}{where}\n{hit.text}")
    return "\n\n".join(blocks)


async def ask(
    session: AsyncSession,
    embeddings: EmbeddingProvider,
    llm: LLMProvider,
    vector_store: VectorStoreService,
    *,
    tenant_id,
    question: str,
    limit: int,
    document_ids: list | None = None,
) -> AskResult:
    """Answer a question from the tenant's own documents, with citations."""
    question = question.strip()
    if not question:
        return AskResult(answer=NO_EVIDENCE, has_sufficient_evidence=False, conflicting=False)

    hits = await search(
        session,
        embeddings,
        vector_store,
        tenant_id=tenant_id,
        query=question,
        limit=limit,
        document_ids=document_ids,
    )

    if not hits:
        # Nothing retrieved is not a question for the model: there is nothing
        # to ground an answer in, and asking anyway invites invention.
        logger.info("ask found no passages", extra={"tenant_id": str(tenant_id), "considered": 0})
        return AskResult(answer=NO_EVIDENCE, has_sufficient_evidence=False, conflicting=False)

    user_prompt = f"Sources:\n\n{build_context(hits)}\n\nQuestion: {question}"
    result = await llm.complete_structured(SYSTEM_PROMPT, user_prompt, GroundedAnswer)

    # A model may cite an id it was never given. Keep only the ones that were,
    # so every id in the response resolves to a real passage.
    supplied = {hit.source_id: hit for hit in hits}
    cited = [supplied[source_id] for source_id in result.source_ids if source_id in supplied]
    invented = len(result.source_ids) - len(cited)
    if invented:
        logger.warning(
            "model cited sources that were not supplied",
            extra={"tenant_id": str(tenant_id), "dropped": invented},
        )

    # Log identifiers and counts. Never the question, the passages or the answer.
    logger.info(
        "ask answered",
        extra={
            "tenant_id": str(tenant_id),
            "considered": len(hits),
            "cited": len(cited),
            "sufficient": result.has_sufficient_evidence,
        },
    )

    return AskResult(
        answer=result.answer,
        has_sufficient_evidence=result.has_sufficient_evidence,
        conflicting=result.conflicting,
        sources=cited,
        considered=len(hits),
    )
