"""Schema constraints, exercised against a real PostgreSQL.

These assert the guarantees the database itself enforces. A constraint that
only lives in application code is a convention; one the database rejects is a
guarantee, and that difference is what keeps two tenants apart.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import Chunk, DocumentRelation, DocumentStatus, RelationType

SHA = "a" * 64


async def test_migration_creates_every_mvp_table(db_session):
    result = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    )
    tables = {row[0] for row in result}

    assert {
        "tenants",
        "documents",
        "chunks",
        "document_profiles",
        "document_relations",
        "ingestion_jobs",
        "machine_packages",
        "machines",
        "assemblies",
        "package_documents",
        "package_assignments",
        "engineering_entities",
        "engineering_entity_candidates",
        "engineering_relations",
        "engineering_relation_candidates",
        "evidence_references",
        "evidence_bindings",
        "plc_programs",
        "plc_blocks",
        "plc_variables",
        "plc_references",
        "engineering_conflicts",
        "unsupported_constructs",
        "human_overrides",
        "derived_behavior_claims",
    } <= tables


async def test_models_and_migrations_do_not_drift(db_engine):
    """The migration must produce exactly what the models declare.

    Without this, a model change that nobody wrote a migration for passes every
    other test and then fails on a real deployment.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.core.db import Base

    async with db_engine.connect() as connection:
        diffs = await connection.run_sync(
            lambda sync_connection: compare_metadata(
                MigrationContext.configure(sync_connection), Base.metadata
            )
        )

    assert diffs == [], f"models and migrations disagree: {diffs}"


async def test_same_bytes_cannot_be_stored_twice_for_one_tenant(db_session, tenant, make_document):
    await make_document(tenant, file_hash=SHA)

    with pytest.raises(IntegrityError):
        await make_document(tenant, file_hash=SHA)


async def test_two_tenants_may_hold_the_same_bytes(db_session, tenant, other_tenant, make_document):
    """Identical uploads by different tenants are not duplicates of each other."""
    first = await make_document(tenant, file_hash=SHA)
    second = await make_document(other_tenant, file_hash=SHA)

    assert first.id != second.id
    assert first.tenant_id != second.tenant_id


async def test_a_soft_deleted_document_frees_its_hash(db_session, tenant, make_document):
    """Deleting and re-uploading the same file must be possible."""
    from datetime import UTC, datetime

    original = await make_document(tenant, file_hash=SHA)
    original.deleted_at = datetime.now(UTC)
    await db_session.flush()

    replacement = await make_document(tenant, file_hash=SHA)

    assert replacement.id != original.id


async def test_file_hash_must_be_a_sha256(db_session, tenant, make_document):
    with pytest.raises(IntegrityError):
        await make_document(tenant, file_hash="too-short")


async def test_chunk_ordinals_are_unique_per_document(db_session, tenant, make_document):
    document = await make_document(tenant)
    db_session.add(
        Chunk(
            tenant_id=tenant.id,
            document_id=document.id,
            ordinal=0,
            text="first",
            source_id=f"{document.id}:0",
        )
    )
    await db_session.flush()

    db_session.add(
        Chunk(
            tenant_id=tenant.id,
            document_id=document.id,
            ordinal=0,
            text="collides",
            source_id=f"{document.id}:collides",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_source_ids_are_unique_per_tenant(db_session, tenant, make_document):
    """A citation must resolve to exactly one chunk within a tenant."""
    first = await make_document(tenant)
    second = await make_document(tenant)
    for document in (first, second):
        db_session.add(
            Chunk(
                tenant_id=tenant.id,
                document_id=document.id,
                ordinal=0,
                text="body",
                source_id="shared-source-id",
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_deleting_a_document_deletes_its_chunks(db_session, tenant, make_document):
    document = await make_document(tenant)
    db_session.add(
        Chunk(
            tenant_id=tenant.id,
            document_id=document.id,
            ordinal=0,
            text="body",
            source_id=f"{document.id}:0",
        )
    )
    await db_session.flush()

    await db_session.delete(document)
    await db_session.flush()

    remaining = await db_session.execute(
        text("SELECT count(*) FROM chunks WHERE document_id = :id"), {"id": document.id}
    )
    assert remaining.scalar_one() == 0


async def test_a_document_cannot_relate_to_itself(db_session, tenant, make_document):
    document = await make_document(tenant)
    db_session.add(
        DocumentRelation(
            tenant_id=tenant.id,
            source_document_id=document.id,
            target_document_id=document.id,
            relation_type=RelationType.related,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_relation_scores_stay_within_zero_and_one(db_session, tenant, make_document):
    source = await make_document(tenant)
    target = await make_document(tenant)
    db_session.add(
        DocumentRelation(
            tenant_id=tenant.id,
            source_document_id=source.id,
            target_document_id=target.id,
            relation_type=RelationType.related,
            score=1.5,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_a_document_cannot_belong_to_a_missing_tenant(db_session, make_document):
    class _Ghost:
        id = uuid.uuid4()

    with pytest.raises(IntegrityError):
        await make_document(_Ghost())


async def test_document_status_rejects_an_unknown_value(db_session, tenant, make_document):
    document = await make_document(tenant)

    with pytest.raises((DBAPIError, IntegrityError)):
        await db_session.execute(
            text("UPDATE documents SET status = 'not-a-status' WHERE id = :id"),
            {"id": document.id},
        )


async def test_default_status_is_queued(db_session, tenant, make_document):
    document = await make_document(tenant)

    assert document.status is DocumentStatus.queued


async def test_chunk_tenant_must_match_its_document(
    db_session, tenant, other_tenant, make_document
):
    document = await make_document(tenant)
    db_session.add(
        Chunk(
            tenant_id=other_tenant.id,
            document_id=document.id,
            ordinal=0,
            text="must not cross tenants",
            source_id=f"{document.id}:0",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_profile_tenant_must_match_its_document(
    db_session, tenant, other_tenant, make_document
):
    from app.models import DocumentProfile

    document = await make_document(tenant)
    db_session.add(DocumentProfile(tenant_id=other_tenant.id, document_id=document.id))

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_job_tenant_must_match_its_document(db_session, tenant, other_tenant, make_document):
    from app.models import IngestionJob

    document = await make_document(tenant)
    db_session.add(IngestionJob(tenant_id=other_tenant.id, document_id=document.id))

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_both_relation_documents_must_belong_to_the_relation_tenant(
    db_session, tenant, other_tenant, make_document
):
    source = await make_document(tenant)
    target = await make_document(other_tenant)
    db_session.add(
        DocumentRelation(
            tenant_id=tenant.id,
            source_document_id=source.id,
            target_document_id=target.id,
            relation_type=RelationType.related,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()
