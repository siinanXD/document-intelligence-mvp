"""SIN-91: extract and resolve components, signals and cross-document identities."""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from sqlalchemy import select

from app.engineering_models import ConflictKind, EntityKind, EvidenceSubjectKind, IdentityStatus
from app.evaluation.machine_intelligence.artifacts import binary_files
from app.evaluation.machine_intelligence.compare import score_ids
from app.evaluation.machine_intelligence.line import MACHINE_CODE
from app.evaluation.machine_intelligence.oracle import build_oracle
from app.models import IngestionJob
from app.providers.local_storage import LocalStorageBackend
from app.services import engineering
from app.services.identity_resolution import (
    RESOLVER_METHOD,
    assembly_code_for,
    resolve_package_identities,
)
from app.services.package_assignment import _load_payload, current_assignments, override_assignment
from app.services.processing import process_job
from tests.test_package_intake import _queued_zip
from tests.test_processing import _FakeParser


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


@pytest.fixture
def storage(tmp_path):
    return LocalStorageBackend(tmp_path)


def test_assembly_codes_follow_conveyor_zones():
    assert assembly_code_for("CV01-M1") == "zone-a"
    assert assembly_code_for("CV08.PEInfeed") == "zone-c"
    assert assembly_code_for("XA:1.1") == "zone-a"
    assert assembly_code_for("CL12-CPU") is None


async def _ingest_package(db_session, storage, tenant, make_document, files: dict[str, bytes]):
    content = _zip_bytes(files)
    document, job = await _queued_zip(db_session, storage, tenant, make_document, content)
    await process_job(db_session, storage, _FakeParser(), job=job)
    packages = await engineering.list_packages(db_session, tenant_id=tenant.id)
    assert len(packages) == 1
    return document, packages[0]


async def test_reference_package_yields_stable_canonical_entities(
    db_session, storage, tenant, make_document
):
    document, package = await _ingest_package(
        db_session, storage, tenant, make_document, binary_files()
    )
    entities = await engineering.list_entities(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    by_name = {row.canonical_name: row for row in entities}
    oracle = build_oracle()
    ci_ids = set(oracle["profiles"]["ci"]["entity_ids"])
    predicted = set(by_name)
    scores = score_ids(predicted & ci_ids, ci_ids)
    assert scores["recall"] == 1.0
    assert by_name["CV01.PEInfeed"].entity_kind == EntityKind.signal
    assert by_name["CV01.PEInfeed"].identity_status == IdentityStatus.resolved
    assert by_name["CV01.PEInfeed"].attributes["address"] == "%I0.7"
    assert by_name["CV01-M1"].entity_kind == EntityKind.component
    assert by_name["CV01-M1"].attributes["power_kw"] == 5.5
    assert by_name["CL12-CPU"].aliases == ["CPU-CL12"]
    assert by_name["CV01-B1"].entity_kind == EntityKind.component
    assert by_name["XA:1.1"].entity_kind == EntityKind.terminal
    assert by_name["W-CV01-RUN"].entity_kind == EntityKind.cable
    assert by_name["CV01.PEInfeed"].id != by_name["CV08.PEInfeed"].id
    assert "DAT-9" not in by_name
    assert "SF-9" not in by_name
    machines = await engineering.list_machines(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    assert machines[0].code == MACHINE_CODE
    assert by_name["CV01-M1"].machine_id == machines[0].id
    assert by_name["CV01-M1"].assembly_id is not None
    evidence = await engineering.list_evidence_for(
        db_session,
        tenant_id=tenant.id,
        subject_kind=EvidenceSubjectKind.entity,
        subject_id=by_name["CV01.PEInfeed"].id,
    )
    assert evidence
    assert any(row.sheet_name == "IO" for row in evidence)
    resolution = by_name["CV01-M1"].attributes["identity_resolution"]
    assert resolution["reason"] == "exact-identifier"
    assert "bom.xlsx" in resolution["supporting_paths"]
    assert "schematic.pdf" in resolution["supporting_paths"]
    assert "bom_rev_old.xlsx" in resolution["contradicting_paths"]
    candidates = await engineering.list_entity_candidates(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    motor_candidates = [row for row in candidates if row.proposed_name == "CV01-M1"]
    assert len(motor_candidates) >= 2
    assert all(row.canonical_entity_id == by_name["CV01-M1"].id for row in motor_candidates)
    assert document.id


async def test_shared_comment_does_not_merge_signals(db_session, storage, tenant, make_document):
    _document, package = await _ingest_package(
        db_session, storage, tenant, make_document, binary_files()
    )
    entities = await engineering.list_entities(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    pe01 = next(row for row in entities if row.canonical_name == "CV01.PEInfeed")
    pe08 = next(row for row in entities if row.canonical_name == "CV08.PEInfeed")
    assert pe01.attributes["comment"] == pe08.attributes["comment"] == "PE infeed"
    assert pe01.attributes["address"] != pe08.attributes["address"]
    conflicts = await engineering.list_conflicts(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    identity = [
        row
        for row in conflicts
        if row.conflict_kind == ConflictKind.identity
        and {row.left_subject_id, row.right_subject_id} == {pe01.id, pe08.id}
    ]
    assert identity
    revision = [row for row in conflicts if row.conflict_kind == ConflictKind.revision]
    assert revision


async def test_resolution_is_reproducible_and_idempotent(
    db_session, storage, tenant, make_document
):
    document, package = await _ingest_package(
        db_session, storage, tenant, make_document, binary_files()
    )
    first = await engineering.list_entities(db_session, tenant_id=tenant.id, package_id=package.id)
    first_names = sorted(row.canonical_name for row in first)
    claimed = (
        (
            await db_session.execute(
                select(IngestionJob).where(
                    IngestionJob.tenant_id == tenant.id, IngestionJob.document_id == document.id
                )
            )
        )
        .scalars()
        .first()
    )
    assert claimed is not None
    await process_job(db_session, storage, _FakeParser(), job=claimed)
    second = await engineering.list_entities(db_session, tenant_id=tenant.id, package_id=package.id)
    assert sorted(row.canonical_name for row in second) == first_names
    assert {row.method for row in second} == {RESOLVER_METHOD}


async def test_missing_member_blobs_do_not_erase_identities(
    db_session, storage, tenant, make_document
):
    document, package = await _ingest_package(
        db_session, storage, tenant, make_document, binary_files()
    )
    first = await engineering.list_entities(db_session, tenant_id=tenant.id, package_id=package.id)
    assert first
    payload = await _load_payload(storage, document)
    for key in payload.get("member_storage_keys") or []:
        await storage.delete(key)
    await resolve_package_identities(db_session, storage, document=document)
    second = await engineering.list_entities(db_session, tenant_id=tenant.id, package_id=package.id)
    assert {row.id for row in second} == {row.id for row in first}
    assert {row.canonical_name for row in second} == {row.canonical_name for row in first}


async def test_partial_missing_member_blob_does_not_erase_identities(
    db_session, storage, tenant, make_document
):
    document, package = await _ingest_package(
        db_session, storage, tenant, make_document, binary_files()
    )
    first = await engineering.list_entities(db_session, tenant_id=tenant.id, package_id=package.id)
    assert first
    payload = await _load_payload(storage, document)
    keys = [key for key in (payload.get("member_storage_keys") or []) if key]
    assert len(keys) > 1
    await storage.delete(keys[0])
    await resolve_package_identities(db_session, storage, document=document)
    second = await engineering.list_entities(db_session, tenant_id=tenant.id, package_id=package.id)
    assert {row.id for row in second} == {row.id for row in first}
    assert {row.canonical_name for row in second} == {row.canonical_name for row in first}


async def test_mentions_resolve_against_their_assigned_machine(
    db_session, storage, tenant, make_document
):
    files = {
        "bom_cv01.csv": b"tag,kind,qty,revision,power_kw\nCV01-M1,motor,1,A,5.5,CL-12\n",
        "bom_cv08.csv": b"tag,kind,qty,revision,power_kw\nCV08-M1,motor,1,A,4.0,CL-12\n",
    }
    document, package = await _ingest_package(db_session, storage, tenant, make_document, files)
    machines = await engineering.list_machines(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    assert machines
    first_machine = machines[0]
    second_machine = await engineering.create_machine(
        db_session,
        tenant_id=tenant.id,
        package_id=package.id,
        name="other-line",
        code="other-line",
    )
    assignments = await current_assignments(db_session, tenant_id=tenant.id, package_id=package.id)
    cv08 = next(row for row in assignments if row.relative_path == "bom_cv08.csv")
    await override_assignment(
        db_session,
        tenant_id=tenant.id,
        assignment_id=cv08.id,
        actor_id="reviewer-1",
        machine_id=second_machine.id,
    )
    await resolve_package_identities(db_session, storage, document=document)
    entities = await engineering.list_entities(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    by_name = {row.canonical_name: row for row in entities}
    assert by_name["CV01-M1"].machine_id == first_machine.id
    assert by_name["CV08-M1"].machine_id == second_machine.id


async def test_entities_are_invisible_to_another_tenant(
    db_session, storage, tenant, other_tenant, make_document
):
    _document, package = await _ingest_package(
        db_session, storage, tenant, make_document, binary_files()
    )
    mine = await engineering.list_entities(db_session, tenant_id=tenant.id, package_id=package.id)
    assert mine
    assert (
        await engineering.list_entities(
            db_session, tenant_id=other_tenant.id, package_id=package.id
        )
        == []
    )
    assert (
        await engineering.list_entity_candidates(
            db_session, tenant_id=other_tenant.id, package_id=package.id
        )
        == []
    )
    assert (
        await engineering.list_conflicts(
            db_session, tenant_id=other_tenant.id, package_id=package.id
        )
        == []
    )
    assert (
        await engineering.get_entity(db_session, tenant_id=other_tenant.id, entity_id=mine[0].id)
        is None
    )


async def test_two_tenants_resolve_the_same_bytes_separately(
    db_session, storage, tenant, other_tenant, make_document
):
    content = _zip_bytes(binary_files())
    first, first_job = await _queued_zip(db_session, storage, tenant, make_document, content)
    await process_job(db_session, storage, _FakeParser(), job=first_job)
    digest = hashlib.sha256(content).hexdigest()
    other = await make_document(
        other_tenant,
        filename="line.zip",
        mime_type="application/zip",
        file_hash=digest,
    )
    other.storage_key = f"{other_tenant.id}/{other.id}/line.zip"
    await db_session.flush()
    await storage.put(other.storage_key, content, content_type="application/zip")
    from app.services import jobs as jobs_service

    await jobs_service.enqueue(db_session, tenant_id=other_tenant.id, document_id=other.id)
    claimed = await jobs_service.claim(db_session, worker_id="worker-2")
    await process_job(db_session, storage, _FakeParser(), job=claimed[0])
    mine = await engineering.list_packages(db_session, tenant_id=tenant.id)
    theirs = await engineering.list_packages(db_session, tenant_id=other_tenant.id)
    my_entities = await engineering.list_entities(
        db_session, tenant_id=tenant.id, package_id=mine[0].id
    )
    their_entities = await engineering.list_entities(
        db_session, tenant_id=other_tenant.id, package_id=theirs[0].id
    )
    assert {row.canonical_name for row in my_entities} == {
        row.canonical_name for row in their_entities
    }
    assert {row.id for row in my_entities}.isdisjoint({row.id for row in their_entities})
    assert first.id != other.id
    assignments = await current_assignments(db_session, tenant_id=tenant.id, package_id=mine[0].id)
    assert assignments
