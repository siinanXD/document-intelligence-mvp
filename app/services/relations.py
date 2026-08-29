"""Deciding how two documents of the same tenant relate.

Every relation is drawn by a rule that can be stated in a sentence, and every
one records the signals that produced it. That matters more than accuracy here:
a relation a user cannot interrogate is a relation they will not trust, and
thresholds that live in configuration are meaningless if nobody can see which
one fired.

The rules are ordered from the most certain to the least, and the first that
matches wins. Identical bytes are not "possibly a version"; they are the same
file, and saying so is not a judgement call.

Nothing here compares across tenants: the candidates are fetched tenant-scoped
and every lookup filters again.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.models import Document, DocumentProfile, DocumentRelation, RelationType
from app.services.vector_store import DocumentVectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    """Another document of the same tenant, and what it has in common."""

    document_id: object
    similarity: float | None = None
    same_file_hash: bool = False
    same_content_hash: bool = False
    shared_identifiers: list[str] = field(default_factory=list)
    shared_organizations: list[str] = field(default_factory=list)
    shared_persons: list[str] = field(default_factory=list)
    shared_dates: list[str] = field(default_factory=list)
    shared_topics: list[str] = field(default_factory=list)

    @property
    def shared_entities(self) -> int:
        return len(self.shared_organizations) + len(self.shared_persons) + len(self.shared_dates)


@dataclass(frozen=True)
class Decision:
    relation_type: RelationType
    score: float | None
    reason: dict


def _overlap(left: list | None, right: list | None) -> list[str]:
    """Case-insensitive intersection, reported in the left document's spelling."""
    if not left or not right:
        return []
    lowered = {str(value).strip().lower() for value in right if str(value).strip()}
    seen: set[str] = set()
    shared = []
    for value in left:
        key = str(value).strip().lower()
        if key and key in lowered and key not in seen:
            seen.add(key)
            shared.append(str(value).strip())
    return shared


def classify(candidate: Candidate, settings: Settings) -> Decision | None:
    """Pick the relation, most certain first. None means "not related enough".

    Ordering is the whole design: identical bytes are not a similarity
    question, and a shared case number is a stronger claim than a vector score.
    """
    if candidate.same_file_hash:
        # Currently unreachable between two live documents: a partial unique
        # index forbids two of them sharing a file hash within a tenant, and
        # the upload path returns the existing document rather than creating a
        # second. Kept because it is the most certain rule and costs nothing -
        # it is what should fire if the constraint is ever relaxed, or if data
        # arrives around the API.
        return Decision(
            RelationType.exact_duplicate,
            1.0,
            {"signals": ["file_hash"], "explanation": "identical bytes"},
        )

    if candidate.same_content_hash:
        return Decision(
            RelationType.content_duplicate,
            1.0,
            {
                "signals": ["content_hash"],
                "explanation": "identical text after normalization",
            },
        )

    similarity = candidate.similarity

    if (
        similarity is not None
        and similarity >= settings.relation_version_similarity
        and candidate.shared_entities >= settings.relation_min_shared_entities
    ):
        # High similarity alone is not enough: two invoices from one template
        # look alike without being versions of each other. Shared parties or
        # dates are what separate "the same document again" from "the same
        # kind of document".
        return Decision(
            RelationType.possible_version,
            similarity,
            {
                "signals": ["document_vector", "shared_entities"],
                "similarity": round(similarity, 4),
                "threshold": settings.relation_version_similarity,
                "shared_organizations": candidate.shared_organizations,
                "shared_persons": candidate.shared_persons,
                "shared_dates": candidate.shared_dates,
            },
        )

    if len(candidate.shared_identifiers) >= settings.relation_min_shared_identifiers:
        # A case or contract number is deliberate: two documents carrying the
        # same one are about the same matter, whatever they look like.
        return Decision(
            RelationType.same_case,
            similarity,
            {
                "signals": ["shared_identifiers"],
                "shared_identifiers": candidate.shared_identifiers,
                "similarity": round(similarity, 4) if similarity is not None else None,
            },
        )

    if (
        candidate.shared_organizations or candidate.shared_persons
    ) and candidate.shared_entities >= settings.relation_min_shared_entities:
        return Decision(
            RelationType.same_entity,
            similarity,
            {
                "signals": ["shared_entities"],
                "shared_organizations": candidate.shared_organizations,
                "shared_persons": candidate.shared_persons,
                "similarity": round(similarity, 4) if similarity is not None else None,
            },
        )

    if similarity is not None and similarity >= settings.relation_related_similarity:
        return Decision(
            RelationType.related,
            similarity,
            {
                "signals": ["document_vector"],
                "similarity": round(similarity, 4),
                "threshold": settings.relation_related_similarity,
            },
        )

    # Below every threshold and sharing nothing: saying "related" here would
    # make the whole feature noise.
    return None


async def gather_candidates(
    session: AsyncSession,
    vector_store: DocumentVectorStore,
    *,
    tenant_id,
    document_id,
    vector: list[float] | None,
) -> list[Candidate]:
    """Find the tenant's other documents worth comparing, and what they share."""
    settings = get_settings()

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
        return []

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

    similarities: dict = {}
    if vector is not None:
        similarities = dict(
            await vector_store.similar(
                tenant_id=tenant_id,
                vector=vector,
                limit=settings.relation_max_candidates,
                exclude=document_id,
            )
        )

    # Hash matches are found by query rather than by similarity: two documents
    # can be byte-identical and still not be each other's nearest neighbour if
    # the collection is large.
    hash_matches = list(
        (
            await session.execute(
                select(Document.id).where(
                    Document.tenant_id == tenant_id,
                    Document.id != document_id,
                    Document.deleted_at.is_(None),
                    (Document.file_hash == document.file_hash)
                    | (
                        (Document.content_hash == document.content_hash)
                        & Document.content_hash.is_not(None)
                    ),
                )
            )
        ).scalars()
    )

    candidate_ids = set(similarities) | set(hash_matches)
    if not candidate_ids:
        return []

    rows = (
        await session.execute(
            select(Document, DocumentProfile)
            .outerjoin(
                DocumentProfile,
                (DocumentProfile.document_id == Document.id)
                & (DocumentProfile.tenant_id == Document.tenant_id),
            )
            .where(
                Document.tenant_id == tenant_id,
                Document.id.in_(candidate_ids),
                Document.deleted_at.is_(None),
            )
        )
    ).all()

    candidates = []
    for other, other_profile in rows:
        candidates.append(
            Candidate(
                document_id=other.id,
                similarity=similarities.get(other.id),
                same_file_hash=other.file_hash == document.file_hash,
                same_content_hash=bool(
                    document.content_hash and other.content_hash == document.content_hash
                ),
                shared_identifiers=_overlap(
                    profile.identifiers if profile else [],
                    other_profile.identifiers if other_profile else [],
                ),
                shared_organizations=_overlap(
                    profile.organizations if profile else [],
                    other_profile.organizations if other_profile else [],
                ),
                shared_persons=_overlap(
                    profile.persons if profile else [],
                    other_profile.persons if other_profile else [],
                ),
                shared_dates=_overlap(
                    profile.dates if profile else [],
                    other_profile.dates if other_profile else [],
                ),
                shared_topics=_overlap(
                    profile.topics if profile else [],
                    other_profile.topics if other_profile else [],
                ),
            )
        )
    return candidates


async def detect_relations(
    session: AsyncSession,
    vector_store: DocumentVectorStore,
    *,
    tenant_id,
    document_id,
    vector: list[float] | None,
) -> list[DocumentRelation]:
    """Draw this document's relations, replacing the ones it had.

    Relations are written in both directions: "this is a version of that" is
    equally true read the other way, and a reader looking at either document
    should see it without a second query shape.
    """
    settings = get_settings()
    candidates = await gather_candidates(
        session,
        vector_store,
        tenant_id=tenant_id,
        document_id=document_id,
        vector=vector,
    )

    written: list[DocumentRelation] = []
    for candidate in candidates:
        decision = classify(candidate, settings)
        if decision is None:
            continue

        for source, target in (
            (document_id, candidate.document_id),
            (candidate.document_id, document_id),
        ):
            statement = (
                insert(DocumentRelation)
                .values(
                    tenant_id=tenant_id,
                    source_document_id=source,
                    target_document_id=target,
                    relation_type=decision.relation_type,
                    score=decision.score,
                    reason=decision.reason,
                )
                # Re-running detection must update the verdict, not fail on the
                # edge it drew last time.
                .on_conflict_do_update(
                    constraint="uq_document_relations_edge",
                    set_={"score": decision.score, "reason": decision.reason},
                )
                .returning(DocumentRelation)
            )
            relation = (await session.execute(statement)).scalars().first()
            if relation is not None and source == document_id:
                written.append(relation)

    await session.flush()

    logger.info(
        "document relations detected",
        extra={
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "candidates": len(candidates),
            "relations": len(written),
        },
    )
    return written


async def list_relations(
    session: AsyncSession, *, tenant_id, document_id
) -> list[tuple[DocumentRelation, Document]]:
    """This document's relations and the documents they point at."""
    return list(
        (
            await session.execute(
                select(DocumentRelation, Document)
                .join(Document, Document.id == DocumentRelation.target_document_id)
                .where(
                    DocumentRelation.tenant_id == tenant_id,
                    DocumentRelation.source_document_id == document_id,
                    Document.deleted_at.is_(None),
                )
                .order_by(DocumentRelation.score.desc().nullslast())
            )
        ).all()
    )
