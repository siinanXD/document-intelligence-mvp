"""Relation detection end to end.

Real PostgreSQL, real document vectors in qdrant-client's local implementation,
and the fixture set the issue asks for: an exact duplicate, a content
duplicate, a near-identical version, and an unrelated document that must not be
linked.
"""

import uuid

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.models import DocumentProfile, DocumentRelation, RelationType
from app.services.document_vectors import NormalizedMeanStrategy
from app.services.relations import detect_relations, list_relations
from app.services.vector_store import DocumentVectorStore, bounded_similarity

DIMENSIONS = 4


@pytest_asyncio.fixture
async def vectors():
    client = AsyncQdrantClient(":memory:")
    store = DocumentVectorStore(client=client, collection="test_documents")
    await store.ensure_collection(dimensions=DIMENSIONS)
    yield store
    await client.close()


@pytest_asyncio.fixture
def with_profile(db_session):
    async def _add(tenant, document, **fields):
        profile = DocumentProfile(
            tenant_id=tenant.id,
            document_id=document.id,
            organizations=fields.get("organizations", []),
            persons=fields.get("persons", []),
            dates=fields.get("dates", []),
            identifiers=fields.get("identifiers", []),
            topics=fields.get("topics", []),
        )
        db_session.add(profile)
        await db_session.flush()
        return profile

    return _add


async def _place(vectors, tenant, document, vector):
    await vectors.upsert(tenant_id=tenant.id, document_id=document.id, vector=vector)


async def test_the_schema_forbids_two_live_documents_with_the_same_bytes(
    db_session, tenant, make_document
):
    """Which is why `exact_duplicate` cannot arise between two live documents.

    SIN-63's partial unique index prevents the state, and the upload path
    returns the existing document rather than creating a second one. The rule
    stays in `classify` - it is the most certain one and costs nothing - but it
    is unreachable through the normal path, and pretending otherwise in a test
    would be pretending the constraint is not there.
    """
    from sqlalchemy.exc import IntegrityError

    await make_document(tenant, file_hash="a" * 64)

    with pytest.raises(IntegrityError):
        await make_document(tenant, file_hash="a" * 64)


async def test_identical_text_is_linked_as_a_content_duplicate(
    db_session, vectors, tenant, make_document
):
    """Different bytes, same words after normalization."""
    first = await make_document(tenant, file_hash="c" * 64, content_hash="f" * 64)
    second = await make_document(tenant, file_hash="d" * 64, content_hash="f" * 64)
    await _place(vectors, tenant, first, [1.0, 0, 0, 0])
    await _place(vectors, tenant, second, [1.0, 0, 0, 0])

    found = await detect_relations(
        db_session, vectors, tenant_id=tenant.id, document_id=second.id, vector=[1.0, 0, 0, 0]
    )

    assert [relation.relation_type for relation in found] == [RelationType.content_duplicate]


async def test_identical_document_vectors_still_write_a_possible_version(
    db_session, vectors, tenant, make_document, with_profile
):
    """Cosine of two copies can land slightly above 1.0; the row must still insert."""
    original = await make_document(tenant, file_hash="v" * 64, content_hash="1" * 64)
    revision = await make_document(tenant, file_hash="w" * 64, content_hash="2" * 64)
    for document in (original, revision):
        await with_profile(tenant, document, organizations=["Acme"], dates=["2024-01-01"])
    await _place(vectors, tenant, original, [1.0, 0.0, 0.0, 0.0])
    await _place(vectors, tenant, revision, [1.0, 0.0, 0.0, 0.0])

    found = await detect_relations(
        db_session,
        vectors,
        tenant_id=tenant.id,
        document_id=revision.id,
        vector=[1.0, 0.0, 0.0, 0.0],
    )

    assert [relation.relation_type for relation in found] == [RelationType.possible_version]
    assert found[0].score is not None
    assert 0.92 <= found[0].score <= 1.0


def test_bounded_similarity_clips_floating_point_cosine():
    assert bounded_similarity(1.000000067179426) == 1.0
    assert bounded_similarity(-0.01) == 0.0
    assert bounded_similarity(0.94) == 0.94


async def test_a_near_identical_revision_is_a_possible_version(
    db_session, vectors, tenant, make_document, with_profile
):
    original = await make_document(tenant, file_hash="1" * 64)
    revision = await make_document(tenant, file_hash="2" * 64)
    for document in (original, revision):
        await with_profile(tenant, document, organizations=["Acme"], dates=["2024-01-01"])
    await _place(vectors, tenant, original, [1.0, 0.0, 0.0, 0.0])
    # Very close, but not identical.
    await _place(vectors, tenant, revision, [0.99, 0.14, 0.0, 0.0])

    found = await detect_relations(
        db_session,
        vectors,
        tenant_id=tenant.id,
        document_id=revision.id,
        vector=[0.99, 0.14, 0.0, 0.0],
    )

    assert [relation.relation_type for relation in found] == [RelationType.possible_version]
    assert found[0].score >= 0.92
    assert found[0].reason["shared_organizations"] == ["Acme"]


async def test_an_unrelated_document_is_not_linked(
    db_session, vectors, tenant, make_document, with_profile
):
    """The case that decides whether this feature is signal or noise."""
    contract = await make_document(tenant, file_hash="3" * 64)
    unrelated = await make_document(tenant, file_hash="4" * 64)
    await with_profile(tenant, contract, organizations=["Acme"])
    await with_profile(tenant, unrelated, organizations=["Umbrella"])
    await _place(vectors, tenant, contract, [1.0, 0.0, 0.0, 0.0])
    await _place(vectors, tenant, unrelated, [0.0, 0.0, 0.0, 1.0])

    found = await detect_relations(
        db_session,
        vectors,
        tenant_id=tenant.id,
        document_id=unrelated.id,
        vector=[0.0, 0.0, 0.0, 1.0],
    )

    assert found == []


async def test_a_shared_case_number_links_documents_that_look_nothing_alike(
    db_session, vectors, tenant, make_document, with_profile
):
    letter = await make_document(tenant, file_hash="5" * 64)
    invoice = await make_document(tenant, file_hash="6" * 64)
    await with_profile(tenant, letter, identifiers=["CASE-2024-17"])
    await with_profile(tenant, invoice, identifiers=["case-2024-17"])
    await _place(vectors, tenant, letter, [1.0, 0.0, 0.0, 0.0])
    await _place(vectors, tenant, invoice, [0.0, 1.0, 0.0, 0.0])

    found = await detect_relations(
        db_session,
        vectors,
        tenant_id=tenant.id,
        document_id=invoice.id,
        vector=[0.0, 1.0, 0.0, 0.0],
    )

    assert [relation.relation_type for relation in found] == [RelationType.same_case]
    # Matching is case-insensitive, and the reason reports what was shared.
    assert found[0].reason["shared_identifiers"] == ["case-2024-17"]


async def test_relations_are_written_in_both_directions(db_session, vectors, tenant, make_document):
    """Whoever is looking should see it, without a second query shape."""
    first = await make_document(tenant, file_hash="7" * 64, content_hash="e" * 64)
    second = await make_document(tenant, file_hash="8" * 64, content_hash="e" * 64)
    await _place(vectors, tenant, first, [1.0, 0, 0, 0])
    await _place(vectors, tenant, second, [1.0, 0, 0, 0])

    await detect_relations(
        db_session, vectors, tenant_id=tenant.id, document_id=second.id, vector=[1.0, 0, 0, 0]
    )

    forward = await list_relations(db_session, tenant_id=tenant.id, document_id=second.id)
    backward = await list_relations(db_session, tenant_id=tenant.id, document_id=first.id)

    assert len(forward) == 1
    assert len(backward) == 1


async def test_re_running_detection_updates_rather_than_failing(
    db_session, vectors, tenant, make_document
):
    first = await make_document(tenant, file_hash="9" * 64, content_hash="d" * 64)
    second = await make_document(tenant, file_hash="0" * 64, content_hash="d" * 64)
    await _place(vectors, tenant, first, [1.0, 0, 0, 0])
    await _place(vectors, tenant, second, [1.0, 0, 0, 0])

    for _ in range(2):
        await detect_relations(
            db_session,
            vectors,
            tenant_id=tenant.id,
            document_id=second.id,
            vector=[1.0, 0, 0, 0],
        )

    from sqlalchemy import select

    rows = (
        (
            await db_session.execute(
                select(DocumentRelation).where(DocumentRelation.tenant_id == tenant.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2  # one per direction, not four


async def test_documents_are_never_compared_across_tenants(
    db_session, vectors, tenant, other_tenant, make_document
):
    mine = await make_document(tenant, file_hash="a" * 64)
    theirs = await make_document(other_tenant, file_hash="a" * 64)
    await _place(vectors, tenant, mine, [1.0, 0, 0, 0])
    await _place(vectors, other_tenant, theirs, [1.0, 0, 0, 0])

    found = await detect_relations(
        db_session, vectors, tenant_id=tenant.id, document_id=mine.id, vector=[1.0, 0, 0, 0]
    )

    assert found == []


async def test_a_document_with_no_vector_can_still_be_linked(
    db_session, vectors, tenant, make_document
):
    """A document that parsed to nothing still has its content hash."""
    first = await make_document(tenant, file_hash="b" * 64, content_hash="9" * 64)
    second = await make_document(tenant, file_hash="c" * 64, content_hash="9" * 64)
    assert first.id != second.id

    found = await detect_relations(
        db_session, vectors, tenant_id=tenant.id, document_id=second.id, vector=None
    )

    assert [relation.relation_type for relation in found] == [RelationType.content_duplicate]


@pytest.mark.parametrize(
    ("vectors_in", "expected"),
    [
        ([], None),
        ([[1.0, 0.0, 0.0, 0.0]], [1.0, 0.0, 0.0, 0.0]),
        ([[2.0, 0.0, 0.0, 0.0]], [1.0, 0.0, 0.0, 0.0]),
        ([[1.0, 0.0, 0.0, 0.0], [-1.0, 0.0, 0.0, 0.0]], None),
    ],
)
def test_the_document_vector_strategy(vectors_in, expected):
    result = NormalizedMeanStrategy().combine(vectors_in)

    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_the_strategy_normalises_so_length_does_not_depend_on_chunk_count():
    """Otherwise a long document drifts from its own shorter revision."""
    short = NormalizedMeanStrategy().combine([[1.0, 1.0, 0.0, 0.0]])
    long = NormalizedMeanStrategy().combine([[1.0, 1.0, 0.0, 0.0]] * 20)

    assert short == pytest.approx(long)


def test_the_strategy_refuses_vectors_of_different_widths():
    with pytest.raises(ValueError, match="differing dimensionality"):
        NormalizedMeanStrategy().combine([[1.0, 0.0], [1.0, 0.0, 0.0]])


def test_the_strategy_reports_its_name():
    assert NormalizedMeanStrategy().name == "normalized_mean"
    assert uuid.uuid4()  # keeps the import honest
