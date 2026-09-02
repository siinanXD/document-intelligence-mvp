"""SIN-90: deterministic package classification and machine assignment."""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from sqlalchemy import select

from app.engineering_models import (
    AssignmentState,
    EngineeringDocumentClass,
    EvidenceSubjectKind,
)
from app.evaluation.machine_intelligence.classification_fixture import (
    CLASSIFICATION_FIXTURE_MEMBERS,
    EXPECTED_CLASSES,
    EXPECTED_MACHINE,
    classification_fixture_files,
    expected_machine_name,
)
from app.evaluation.machine_intelligence.line import MACHINE_CODE
from app.models import DocumentStatus, IngestionJob
from app.providers.local_storage import LocalStorageBackend
from app.services import engineering
from app.services.classification import (
    CLASSIFIER_METHOD,
    ClassificationInput,
    apply_package_identity,
    classify,
)
from app.services.package_assignment import (
    OVERRIDE_METHOD,
    current_assignments,
    override_assignment,
)
from app.services.processing import process_job
from tests.test_package_intake import _queued_zip
from tests.test_processing import _FakeParser


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def test_filename_rules_cover_schematic_manual_bom_and_plc():
    cases = (
        ("schematic.pdf", "schematic.pdf", EngineeringDocumentClass.schematic),
        ("overview.md", "overview.md", EngineeringDocumentClass.manual),
        ("commissioning.md", "docs/commissioning.md", EngineeringDocumentClass.manual),
        ("bom.xlsx", "bom.xlsx", EngineeringDocumentClass.bom),
        ("OB1.xml", "plc/OB1.xml", EngineeringDocumentClass.plc_xml),
        ("OB1.scl", "plc/OB1.scl", EngineeringDocumentClass.plc_scl),
        ("unrelated_hvac.md", "unrelated_hvac.md", EngineeringDocumentClass.unrelated),
        ("cabinet_photo.png", "cabinet_photo.png", EngineeringDocumentClass.photo),
    )
    for filename, path, expected in cases:
        decision = classify(ClassificationInput(filename=filename, path_hint=path))
        assert decision.document_class == expected
        assert decision.method == CLASSIFIER_METHOD


def test_unrelated_filename_wins_over_machine_identifier_in_text():
    decision = classify(
        ClassificationInput(
            filename="unrelated_hvac.md",
            path_hint="unrelated_hvac.md",
            text_sample="This artifact is deliberately unrelated to Conveyor Line CL-12.",
        )
    )
    assert decision.document_class == EngineeringDocumentClass.unrelated
    assert decision.machine_code is None
    assert decision.assign_to_machine is False


def test_filename_refines_generic_tabular_adapter_class():
    decision = classify(
        ClassificationInput(
            filename="bom.xlsx",
            path_hint="bom.xlsx",
            adapter_class="tabular",
        )
    )
    assert decision.document_class == EngineeringDocumentClass.bom
    assert "filename-bom" in decision.reasons


def test_package_identity_assigns_related_files_to_one_machine():
    overview = classify(
        ClassificationInput(
            filename="overview.md",
            path_hint="overview.md",
            text_sample="# Conveyor Line CL-12\nSynthetic multi-conveyor line `CL-12`.",
        )
    )
    schematic = classify(ClassificationInput(filename="schematic.pdf", path_hint="schematic.pdf"))
    unrelated = classify(
        ClassificationInput(filename="unrelated_hvac.md", path_hint="unrelated_hvac.md")
    )
    combined = apply_package_identity([overview, schematic, unrelated])
    assert combined[0].machine_code == MACHINE_CODE
    assert combined[1].machine_code == MACHINE_CODE
    assert combined[1].document_class == EngineeringDocumentClass.schematic
    assert combined[2].machine_code is None


def test_classification_is_reproducible():
    item = ClassificationInput(filename="schematic.pdf", path_hint="EPLAN/schematic.pdf")
    assert classify(item) == classify(item)


def test_classification_fixture_has_the_sin88_subset():
    files = classification_fixture_files()
    assert set(files) == set(CLASSIFICATION_FIXTURE_MEMBERS)
    assert files["schematic.pdf"].startswith(b"%PDF-")
    assert b"CL-12" in files["overview.md"]
    assert files["bom.xlsx"].startswith(b"PK")
    assert b"SW.Blocks." in files["plc/OB1.xml"] or b"<Engineering" in files["plc/OB1.xml"]


@pytest.fixture
def storage(tmp_path):
    return LocalStorageBackend(tmp_path)


async def _ingest_fixture(db_session, storage, tenant, make_document):
    content = _zip_bytes(classification_fixture_files())
    document, job = await _queued_zip(db_session, storage, tenant, make_document, content)
    await process_job(db_session, storage, _FakeParser(), job=job)
    packages = await engineering.list_packages(db_session, tenant_id=tenant.id)
    assert len(packages) == 1
    return document, packages[0]


async def test_fixture_zip_is_classified_and_assigned(db_session, storage, tenant, make_document):
    document, package = await _ingest_fixture(db_session, storage, tenant, make_document)
    assert document.status == DocumentStatus.ready
    assignments = await current_assignments(db_session, tenant_id=tenant.id, package_id=package.id)
    by_path = {row.relative_path: row for row in assignments}
    assert set(by_path) == set(EXPECTED_CLASSES)
    machines = await engineering.list_machines(
        db_session, tenant_id=tenant.id, package_id=package.id
    )
    assert len(machines) == 1
    assert machines[0].code == MACHINE_CODE
    assert machines[0].name == expected_machine_name()
    for path, expected_class in EXPECTED_CLASSES.items():
        row = by_path[path]
        assert row.document_class == expected_class
        assert row.method == CLASSIFIER_METHOD
        assert 0 <= row.confidence <= 1
        assert row.reason.get("rules")
        evidence = await engineering.list_evidence_for(
            db_session,
            tenant_id=tenant.id,
            subject_kind=EvidenceSubjectKind.package_assignment,
            subject_id=row.id,
        )
        assert evidence
        expected_machine = EXPECTED_MACHINE[path]
        if expected_machine is None:
            assert row.machine_id is None
        else:
            assert row.machine_id == machines[0].id
    assert package.name == expected_machine_name()


async def test_override_keeps_the_original_assignment(db_session, storage, tenant, make_document):
    _document, package = await _ingest_fixture(db_session, storage, tenant, make_document)
    current = await current_assignments(db_session, tenant_id=tenant.id, package_id=package.id)
    schematic = next(row for row in current if row.relative_path == "schematic.pdf")
    replacement = await override_assignment(
        db_session,
        tenant_id=tenant.id,
        assignment_id=schematic.id,
        actor_id="reviewer-1",
        document_class=EngineeringDocumentClass.manual,
    )
    await db_session.refresh(schematic)
    assert schematic.state == AssignmentState.superseded
    assert schematic.document_class == EngineeringDocumentClass.schematic
    assert replacement.document_class == EngineeringDocumentClass.manual
    assert replacement.method == OVERRIDE_METHOD
    assert replacement.state == AssignmentState.accepted
    assert replacement.id != schematic.id
    still = await engineering.get_assignment(
        db_session, tenant_id=tenant.id, assignment_id=schematic.id
    )
    assert still is not None
    assert still.state == AssignmentState.superseded


async def test_reprocess_does_not_replace_a_human_override(
    db_session, storage, tenant, make_document
):
    document, package = await _ingest_fixture(db_session, storage, tenant, make_document)
    current = await current_assignments(db_session, tenant_id=tenant.id, package_id=package.id)
    schematic = next(row for row in current if row.relative_path == "schematic.pdf")
    replacement = await override_assignment(
        db_session,
        tenant_id=tenant.id,
        assignment_id=schematic.id,
        actor_id="reviewer-1",
        document_class=EngineeringDocumentClass.manual,
    )
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
    after = await current_assignments(db_session, tenant_id=tenant.id, package_id=package.id)
    schematic_now = next(row for row in after if row.relative_path == "schematic.pdf")
    assert schematic_now.id == replacement.id
    assert schematic_now.document_class == EngineeringDocumentClass.manual


async def test_assignments_are_invisible_to_another_tenant(
    db_session, storage, tenant, other_tenant, make_document
):
    _document, package = await _ingest_fixture(db_session, storage, tenant, make_document)
    assert (
        await engineering.list_assignments(
            db_session, tenant_id=other_tenant.id, package_id=package.id
        )
        == []
    )
    assert (
        await current_assignments(db_session, tenant_id=other_tenant.id, package_id=package.id)
        == []
    )
    mine = await current_assignments(db_session, tenant_id=tenant.id, package_id=package.id)
    foreign = await engineering.get_assignment(
        db_session, tenant_id=other_tenant.id, assignment_id=mine[0].id
    )
    assert foreign is None
    with pytest.raises(engineering.EngineeringIsolationError):
        await override_assignment(
            db_session,
            tenant_id=other_tenant.id,
            assignment_id=mine[0].id,
            actor_id="intruder",
        )
    other_packages = await engineering.list_packages(db_session, tenant_id=other_tenant.id)
    assert other_packages == []
    other_machines = await engineering.list_machines(
        db_session, tenant_id=other_tenant.id, package_id=package.id
    )
    assert other_machines == []


async def test_two_tenants_can_classify_the_same_bytes(
    db_session, storage, tenant, other_tenant, make_document
):
    content = _zip_bytes(classification_fixture_files())
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
    assert len(mine) == 1 and len(theirs) == 1
    assert mine[0].id != theirs[0].id
    my_assign = await current_assignments(db_session, tenant_id=tenant.id, package_id=mine[0].id)
    their_assign = await current_assignments(
        db_session, tenant_id=other_tenant.id, package_id=theirs[0].id
    )
    assert {row.document_class for row in my_assign} == set(EXPECTED_CLASSES.values())
    assert {row.document_class for row in their_assign} == set(EXPECTED_CLASSES.values())
    assert first.id != other.id
