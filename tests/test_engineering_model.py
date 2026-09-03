"""SIN-89 canonical engineering model: constraints, evidence and tenant isolation."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.engineering_models import (
    BehaviorClaimKind,
    ConflictKind,
    EngineeringRelationKind,
    EntityKind,
    EvidenceLocatorKind,
    EvidenceReference,
    EvidenceSubjectKind,
    PackageDocument,
)
from app.models import Chunk
from app.providers.local_storage import LocalStorageBackend
from app.services import engineering
from app.services.deletion import delete_document
from app.services.engineering import EngineeringEvidenceError


async def _package_with_doc(db_session, tenant, make_document):
    package = await engineering.create_package(
        db_session, tenant_id=tenant.id, slug=f"line-{uuid.uuid4().hex[:8]}", name="Line"
    )
    document = await make_document(tenant)
    await engineering.add_package_document(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        document_id=document.id,
        relative_path="exports/tags.xlsx",
    )
    evidence = await engineering.record_evidence(
        db_session,
        tenant_id=tenant.id,
        locator_kind=EvidenceLocatorKind.sheet_cell,
        document_id=document.id,
        sheet_name="I/O",
        cell_range="B12",
    )
    return package, document, evidence


async def test_two_tenants_may_reuse_the_same_package_slug(db_session, tenant, other_tenant):
    first = await engineering.create_package(
        db_session, tenant_id=tenant.id, slug="pl-04", name="A"
    )
    second = await engineering.create_package(
        db_session, tenant_id=other_tenant.id, slug="pl-04", name="B"
    )
    assert first.slug == second.slug
    assert first.tenant_id != second.tenant_id


async def test_package_slug_is_unique_inside_one_tenant(db_session, tenant):
    await engineering.create_package(db_session, tenant_id=tenant.id, slug="pl-04", name="A")
    with pytest.raises(IntegrityError):
        await engineering.create_package(db_session, tenant_id=tenant.id, slug="pl-04", name="B")


async def test_a_package_is_invisible_to_another_tenant(db_session, tenant, other_tenant):
    package = await engineering.create_package(
        db_session, tenant_id=tenant.id, slug="secret", name="Secret"
    )
    assert (
        await engineering.get_package(db_session, tenant_id=other_tenant.id, package_id=package.id)
        is None
    )
    assert await engineering.list_packages(db_session, tenant_id=other_tenant.id) == []


async def test_listing_packages_stays_inside_the_asking_tenant(db_session, tenant, other_tenant):
    mine = await engineering.create_package(
        db_session, tenant_id=tenant.id, slug="mine", name="Mine"
    )
    await engineering.create_package(
        db_session, tenant_id=other_tenant.id, slug="theirs", name="Theirs"
    )
    visible = await engineering.list_packages(db_session, tenant_id=tenant.id)
    assert [row.id for row in visible] == [mine.id]


async def test_package_document_tenant_must_match_the_document(
    db_session, tenant, other_tenant, make_document
):
    package = await engineering.create_package(
        db_session, tenant_id=tenant.id, slug="line", name="Line"
    )
    foreign = await make_document(other_tenant)
    db_session.add(
        PackageDocument(tenant_id=tenant.id, package_id=package.id, document_id=foreign.id)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_clear_package_identities_drops_orphaned_evidence(db_session, tenant, make_document):
    package, document, evidence = await _package_with_doc(db_session, tenant, make_document)
    kept = await engineering.record_evidence(
        db_session,
        tenant_id=tenant.id,
        locator_kind=EvidenceLocatorKind.sheet_cell,
        document_id=document.id,
        sheet_name="I/O",
        cell_range="C4",
    )
    derived = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="CV01-M1",
        evidence_ids=[evidence.id],
        method="deterministic_identity",
        method_version="sin-91.1",
    )
    manual = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="CL12-CPU",
        evidence_ids=[kept.id],
    )
    await engineering.clear_package_identities(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        methods=("deterministic_identity", "deterministic_identifiers"),
    )
    remaining = (
        (
            await db_session.execute(
                select(EvidenceReference).where(EvidenceReference.tenant_id == tenant.id)
            )
        )
        .scalars()
        .all()
    )
    assert {row.id for row in remaining} == {kept.id}
    assert (
        await engineering.get_entity(db_session, tenant_id=tenant.id, entity_id=derived.id) is None
    )
    assert (
        await engineering.get_entity(db_session, tenant_id=tenant.id, entity_id=manual.id)
        is not None
    )


async def test_canonical_entity_requires_evidence(db_session, tenant, make_document):
    package, _document, _evidence = await _package_with_doc(db_session, tenant, make_document)
    with pytest.raises(EngineeringEvidenceError):
        await engineering.create_canonical_entity(
            db_session,
            tenant_id=tenant.id,
            package_id=package.id,
            entity_kind=EntityKind.component,
            canonical_name="M1",
            evidence_ids=[],
        )


async def test_canonical_entity_and_relation_keep_resolvable_evidence(
    db_session, tenant, make_document
):
    package, document, evidence = await _package_with_doc(db_session, tenant, make_document)
    chunk = Chunk(
        tenant_id=tenant.id,
        document_id=document.id,
        ordinal=0,
        text="motor M1",
        source_id=f"{document.id}:00000",
    )
    db_session.add(chunk)
    await db_session.flush()
    page_evidence = await engineering.record_evidence(
        db_session,
        tenant_id=tenant.id,
        locator_kind=EvidenceLocatorKind.chunk,
        document_id=document.id,
        source_id=chunk.source_id,
        page_number=3,
    )
    motor = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="M1",
        evidence_ids=[evidence.id],
        attributes={"rated_power_kw": 5.5},
    )
    sensor = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="B1",
        evidence_ids=[page_evidence.id],
    )
    relation = await engineering.create_canonical_relation(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        relation_kind=EngineeringRelationKind.connected_to,
        source_entity_id=sensor.id,
        target_entity_id=motor.id,
        evidence_ids=[evidence.id],
    )
    motor_evidence = await engineering.list_evidence_for(
        db_session,
        tenant_id=tenant.id,
        subject_kind=EvidenceSubjectKind.entity,
        subject_id=motor.id,
    )
    assert [row.id for row in motor_evidence] == [evidence.id]
    assert motor_evidence[0].sheet_name == "I/O"
    assert motor_evidence[0].cell_range == "B12"
    fetched = await engineering.get_relation(
        db_session, tenant_id=tenant.id, relation_id=relation.id
    )
    assert fetched is not None
    assert fetched.source_entity_id == sensor.id


async def test_entities_are_invisible_to_another_tenant(
    db_session, tenant, other_tenant, make_document
):
    package, _document, evidence = await _package_with_doc(db_session, tenant, make_document)
    entity = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.signal,
        canonical_name="I0.0",
        evidence_ids=[evidence.id],
    )
    assert (
        await engineering.get_entity(db_session, tenant_id=other_tenant.id, entity_id=entity.id)
        is None
    )
    other_package = await engineering.create_package(
        db_session, tenant_id=other_tenant.id, slug="other", name="Other"
    )
    assert (
        await engineering.list_entities(
            db_session, tenant_id=other_tenant.id, package_id=other_package.id
        )
        == []
    )
    assert (
        await engineering.list_evidence_for(
            db_session,
            tenant_id=other_tenant.id,
            subject_kind=EvidenceSubjectKind.entity,
            subject_id=entity.id,
        )
        == []
    )


async def test_foreign_evidence_cannot_ground_this_tenants_entity(
    db_session, tenant, other_tenant, make_document
):
    package, _document, _evidence = await _package_with_doc(db_session, tenant, make_document)
    other_package, _other_doc, foreign_evidence = await _package_with_doc(
        db_session, other_tenant, make_document
    )
    with pytest.raises(engineering.EngineeringIsolationError):
        await engineering.create_canonical_entity(
            db_session,
            tenant_id=tenant.id,
            package_id=package.id,
            entity_kind=EntityKind.component,
            canonical_name="stolen",
            evidence_ids=[foreign_evidence.id],
        )


async def test_confidence_stays_within_zero_and_one(db_session, tenant, make_document):
    package, _document, evidence = await _package_with_doc(db_session, tenant, make_document)
    with pytest.raises(IntegrityError):
        await engineering.create_canonical_entity(
            db_session,
            tenant_id=tenant.id,
            package_id=package.id,
            entity_kind=EntityKind.component,
            canonical_name="M1",
            evidence_ids=[evidence.id],
            confidence=1.5,
        )


async def test_self_relation_is_rejected(db_session, tenant, make_document):
    package, _document, evidence = await _package_with_doc(db_session, tenant, make_document)
    entity = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="M1",
        evidence_ids=[evidence.id],
    )
    with pytest.raises(IntegrityError):
        await engineering.create_canonical_relation(
            db_session,
            tenant_id=tenant.id,
            package_id=package.id,
            relation_kind=EngineeringRelationKind.related,
            source_entity_id=entity.id,
            target_entity_id=entity.id,
            evidence_ids=[evidence.id],
        )


async def test_override_does_not_delete_the_original_entity(db_session, tenant, make_document):
    package, _document, evidence = await _package_with_doc(db_session, tenant, make_document)
    entity = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="M1",
        evidence_ids=[evidence.id],
    )
    await engineering.record_override(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        subject_kind=EvidenceSubjectKind.entity,
        subject_id=entity.id,
        actor_id="reviewer-1",
        previous_payload={"canonical_name": "M1"},
        new_payload={"canonical_name": "M1-A"},
    )
    still = await engineering.get_entity(db_session, tenant_id=tenant.id, entity_id=entity.id)
    assert still is not None
    assert still.canonical_name == "M1"


async def test_conflict_and_unsupported_construct_persist(db_session, tenant, make_document):
    package, document, evidence = await _package_with_doc(db_session, tenant, make_document)
    left = await engineering.create_entity_candidate(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        proposed_name="M1",
        evidence_ids=[evidence.id],
    )
    right = await engineering.create_entity_candidate(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        proposed_name="M1-dup",
        evidence_ids=[evidence.id],
    )
    conflict = await engineering.record_conflict(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        conflict_kind=ConflictKind.identity,
        left_subject_kind="entity_candidate",
        left_subject_id=left.id,
        right_subject_kind="entity_candidate",
        right_subject_id=right.id,
        evidence_ids=[evidence.id],
    )
    unsupported = await engineering.record_unsupported_construct(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        construct_code="tia.unknown_instruction",
        evidence_ids=[evidence.id],
        document_id=document.id,
    )
    assert conflict.status.value == "open"
    assert unsupported.construct_code == "tia.unknown_instruction"


async def test_plc_rows_cannot_attach_to_another_tenants_program(
    db_session, tenant, other_tenant, make_document
):
    package, _document, _evidence = await _package_with_doc(db_session, tenant, make_document)
    program = await engineering.create_plc_program(
        db_session, tenant_id=tenant.id, package_id=package.id, name="CPU"
    )
    with pytest.raises(engineering.EngineeringIsolationError):
        await engineering.create_plc_block(
            db_session,
            tenant_id=other_tenant.id,
            program_id=program.id,
            name="OB1",
            block_type="OB",
        )


async def test_behavior_claim_stores_identifier_path(db_session, tenant, make_document):
    package, _document, evidence = await _package_with_doc(db_session, tenant, make_document)
    motor = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="M1",
        evidence_ids=[evidence.id],
    )
    claim = await engineering.record_behavior_claim(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        claim_kind=BehaviorClaimKind.sequence,
        dependency_path=[{"kind": "entity", "id": str(motor.id)}],
        evidence_ids=[evidence.id],
    )
    assert claim.dependency_path[0]["id"] == str(motor.id)


async def test_deleting_a_document_drops_package_membership(
    db_session, tenant, make_document, tmp_path
):
    package, document, _evidence = await _package_with_doc(db_session, tenant, make_document)
    storage = LocalStorageBackend(tmp_path)
    await storage.put(document.storage_key, b"%PDF-1.4 fake")
    await delete_document(db_session, storage, tenant_id=tenant.id, document_id=document.id)
    remaining = await engineering.list_package_documents(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    assert remaining == []
    still_package = await engineering.get_package(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    assert still_package is not None


async def test_incomplete_evidence_locator_is_rejected(db_session, tenant, make_document):
    _package, document, _evidence = await _package_with_doc(db_session, tenant, make_document)
    with pytest.raises(EngineeringEvidenceError):
        await engineering.record_evidence(
            db_session,
            tenant_id=tenant.id,
            locator_kind=EvidenceLocatorKind.page,
            document_id=document.id,
        )
    db_session.add(
        EvidenceReference(
            tenant_id=tenant.id,
            locator_kind=EvidenceLocatorKind.page,
            document_id=document.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_relations_cannot_cross_packages_in_the_same_tenant(
    db_session, tenant, make_document
):
    package_a, _doc_a, evidence_a = await _package_with_doc(db_session, tenant, make_document)
    package_b, _doc_b, evidence_b = await _package_with_doc(db_session, tenant, make_document)
    motor_a = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package_a.id,
        entity_kind=EntityKind.component,
        canonical_name="M1",
        evidence_ids=[evidence_a.id],
    )
    motor_b = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package_b.id,
        entity_kind=EntityKind.component,
        canonical_name="M2",
        evidence_ids=[evidence_b.id],
    )
    with pytest.raises(engineering.EngineeringIsolationError):
        await engineering.create_canonical_relation(
            db_session,
            tenant_id=tenant.id,
            package_id=package_a.id,
            relation_kind=EngineeringRelationKind.connected_to,
            source_entity_id=motor_a.id,
            target_entity_id=motor_b.id,
            evidence_ids=[evidence_a.id],
        )


async def test_deleting_a_document_drops_facts_that_lose_their_last_evidence(
    db_session, tenant, make_document, tmp_path
):
    package, document, evidence = await _package_with_doc(db_session, tenant, make_document)
    other = await make_document(tenant)
    other_evidence = await engineering.record_evidence(
        db_session,
        tenant_id=tenant.id,
        locator_kind=EvidenceLocatorKind.sheet_cell,
        document_id=other.id,
        sheet_name="BOM",
        cell_range="A1",
    )
    only_here = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="M1",
        evidence_ids=[evidence.id],
    )
    also_elsewhere = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="M2",
        evidence_ids=[evidence.id, other_evidence.id],
    )
    storage = LocalStorageBackend(tmp_path)
    await storage.put(document.storage_key, b"%PDF-1.4 fake")
    await delete_document(db_session, storage, tenant_id=tenant.id, document_id=document.id)
    assert (
        await engineering.get_entity(db_session, tenant_id=tenant.id, entity_id=only_here.id)
        is None
    )
    kept = await engineering.get_entity(
        db_session, tenant_id=tenant.id, entity_id=also_elsewhere.id
    )
    assert kept is not None
    leftover = await engineering.list_evidence_for(
        db_session,
        tenant_id=tenant.id,
        subject_kind=EvidenceSubjectKind.entity,
        subject_id=also_elsewhere.id,
    )
    assert [row.id for row in leftover] == [other_evidence.id]


async def test_deleting_a_document_with_chunk_evidence_succeeds(
    db_session, tenant, make_document, tmp_path
):
    package, document, _sheet = await _package_with_doc(db_session, tenant, make_document)
    chunk = Chunk(
        tenant_id=tenant.id,
        document_id=document.id,
        ordinal=0,
        text="motor M1",
        source_id=f"{document.id}:00000",
    )
    db_session.add(chunk)
    await db_session.flush()
    chunk_evidence = await engineering.record_evidence(
        db_session,
        tenant_id=tenant.id,
        locator_kind=EvidenceLocatorKind.chunk,
        document_id=document.id,
        source_id=chunk.source_id,
        page_number=1,
    )
    entity = await engineering.create_canonical_entity(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        entity_kind=EntityKind.component,
        canonical_name="M1",
        evidence_ids=[chunk_evidence.id],
    )
    storage = LocalStorageBackend(tmp_path)
    await storage.put(document.storage_key, b"%PDF-1.4 fake")
    outcome = await delete_document(
        db_session, storage, tenant_id=tenant.id, document_id=document.id
    )
    assert outcome is not None
    assert (
        await engineering.get_entity(db_session, tenant_id=tenant.id, entity_id=entity.id) is None
    )
