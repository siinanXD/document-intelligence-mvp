"""SIN-100 adapter framework, safe zip unpack and package intake."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from app.adapters.base import PackageRejected
from app.adapters.registry import run_adapters
from app.adapters.stubs import default_adapters
from app.adapters.zip_unpack import safe_unpack
from app.evaluation.machine_intelligence.artifacts import binary_files, minimal_png, source_texts
from app.models import DocumentStatus
from app.providers.local_storage import LocalStorageBackend
from app.services import jobs as jobs_service
from app.services.deletion import delete_document
from app.services.engineering import (
    add_package_document,
    create_package,
    get_package,
    list_package_documents,
    list_packages,
)
from app.services.package_intake import (
    adapter_key_for,
    ingest_artifact,
    is_search_skippable,
    package_slug_for,
)
from app.services.processing import process_job
from app.services.uploads import UnsupportedFileType, validate_upload
from tests.test_processing import _FakeParser


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def test_zip_traversal_and_empty_archives_are_rejected():
    with pytest.raises(PackageRejected, match="escapes"):
        safe_unpack(_zip_bytes({"../secret.txt": b"nope"}))
    with pytest.raises(PackageRejected, match="absolute"):
        info_zip = io.BytesIO()
        with zipfile.ZipFile(info_zip, "w") as archive:
            archive.writestr("/tmp/x.txt", b"nope")
        safe_unpack(info_zip.getvalue())
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w"):
        pass
    with pytest.raises(PackageRejected, match="no files"):
        safe_unpack(empty.getvalue())


def test_nested_zip_is_listed_but_not_unpacked():
    inner = _zip_bytes({"inner.txt": b"hello"})
    members = safe_unpack(_zip_bytes({"outer/nested.zip": inner, "readme.md": b"# hi\n"}))
    nested = next(item for item in members if item.path_hint.endswith("nested.zip"))
    assert nested.nested_archive is True
    assert {item.path_hint for item in members} == {"outer/nested.zip", "readme.md"}


def test_simaticml_and_scl_win_detection():
    texts = source_texts()
    xml = texts["sources/plc/OB1.xml"].encode()
    from app.adapters.base import Artifact

    xml_art = Artifact(filename="OB1.xml", mime_type="application/xml", content=xml)
    detection, extracted, report = run_adapters(xml_art, default_adapters())
    assert detection is not None
    assert detection.adapter_name == "simaticml"
    assert report.ok
    assert any(item.kind == "unsupported" for item in extracted.observations)

    scl = texts["sources/plc/FB_ConveyorCtrl.scl"].encode()
    scl_art = Artifact(filename="FB_ConveyorCtrl.scl", mime_type="text/x-scl", content=scl)
    detection, extracted, report = run_adapters(scl_art)
    assert detection is not None
    assert detection.adapter_name == "scl"
    assert report.ok


def test_identical_input_yields_identical_observations():
    from app.adapters.base import Artifact

    png = minimal_png()
    artifact = Artifact(filename="cab.png", mime_type="image/png", content=png)
    first = run_adapters(artifact)
    second = run_adapters(artifact)
    assert first == second


@pytest.fixture
def storage(tmp_path):
    return LocalStorageBackend(tmp_path)


async def _queued_zip(db_session, storage, tenant, make_document, content: bytes):
    digest = hashlib.sha256(content).hexdigest()
    document = await make_document(
        tenant,
        filename="line.zip",
        mime_type="application/zip",
        file_hash=digest,
    )
    document.storage_key = f"{tenant.id}/{document.id}/line.zip"
    await db_session.flush()
    await storage.put(document.storage_key, content, content_type="application/zip")
    await jobs_service.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)
    claimed = await jobs_service.claim(db_session, worker_id="worker-1")
    return document, claimed[0]


async def test_zip_intake_stores_members_and_skips_docling(
    db_session, storage, tenant, other_tenant, make_document
):
    pdf = b"%PDF-1.7\nmine"
    foreign = b"%PDF-1.7\nmine"
    await make_document(
        other_tenant,
        filename="other.pdf",
        mime_type="application/pdf",
        file_hash=hashlib.sha256(foreign).hexdigest(),
    )
    existing = await make_document(
        tenant,
        filename="already.pdf",
        mime_type="application/pdf",
        file_hash=hashlib.sha256(pdf).hexdigest(),
    )
    xml = source_texts()["sources/plc/OB1.xml"].encode()
    content = _zip_bytes(
        {
            "plc/OB1.xml": xml,
            "notes/readme.md": b"# line\n",
            "dup.pdf": pdf,
        }
    )
    document, job = await _queued_zip(db_session, storage, tenant, make_document, content)
    parser = _FakeParser()
    await process_job(db_session, storage, parser, job=job)
    assert parser.calls == []
    assert document.status == DocumentStatus.ready
    assert document.parser_name == "adapter"
    payload = json.loads(await storage.get(adapter_key_for(document)))
    assert payload["package_id"]
    members = [item for item in payload["observations"] if item["kind"] == "package_member"]
    assert {item["payload"]["path_hint"] for item in members} == {
        "plc/OB1.xml",
        "notes/readme.md",
        "dup.pdf",
    }
    duplicates = [
        item for item in payload["observations"] if item["kind"] == "duplicate_fingerprint"
    ]
    assert len(duplicates) == 1
    assert duplicates[0]["payload"]["existing_document_id"] == str(existing.id)
    assert duplicates[0]["payload"]["merged"] is False
    packages = await list_packages(db_session, tenant_id=tenant.id)
    assert len(packages) == 1
    membership = await list_package_documents(
        db_session, tenant_id=tenant.id, package_id=packages[0].id
    )
    assert [row.document_id for row in membership] == [document.id]
    other_packages = await list_packages(db_session, tenant_id=other_tenant.id)
    assert other_packages == []
    for key in payload["member_storage_keys"]:
        assert await storage.exists(key)
    await delete_document(db_session, storage, tenant_id=tenant.id, document_id=document.id)
    assert await storage.exists(adapter_key_for(document)) is False
    for key in payload["member_storage_keys"]:
        assert await storage.exists(key) is False
    assert await list_packages(db_session, tenant_id=tenant.id) == []


async def test_zip_intake_is_tenant_scoped(
    db_session, storage, tenant, other_tenant, make_document
):
    body = b"FUNCTION_BLOCK FB\nEND_FUNCTION_BLOCK\n"
    content = _zip_bytes({"FB.scl": body})
    document, job = await _queued_zip(db_session, storage, tenant, make_document, content)
    await process_job(db_session, storage, _FakeParser(), job=job)
    payload = json.loads(await storage.get(adapter_key_for(document)))
    other = await make_document(
        other_tenant,
        filename="line.zip",
        mime_type="application/zip",
        file_hash=hashlib.sha256(content).hexdigest(),
    )
    other.storage_key = f"{other_tenant.id}/{other.id}/line.zip"
    await db_session.flush()
    await storage.put(other.storage_key, content)
    result = await ingest_artifact(db_session, storage, document=other, content=content)
    assert result["package_id"] != payload["package_id"]
    other_members = [item for item in result["observations"] if item["kind"] == "package_member"]
    assert other_members[0]["payload"]["duplicate_document_id"] is None


async def test_unsafe_zip_fails_processing(db_session, storage, tenant, make_document):
    content = _zip_bytes({"../escape.txt": b"nope"})
    # zipfile.writestr may already normalize. Force an unsafe name via ZipInfo.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("../escape.txt")
        archive.writestr(info, b"nope")
    content = buffer.getvalue()
    document, job = await _queued_zip(db_session, storage, tenant, make_document, content)
    with pytest.raises(PackageRejected):
        await process_job(db_session, storage, _FakeParser(), job=job)


async def test_reprocessing_a_zip_reuses_the_existing_package(
    db_session, storage, tenant, make_document
):
    content = _zip_bytes({"readme.md": b"# line\n"})
    document, job = await _queued_zip(db_session, storage, tenant, make_document, content)
    await process_job(db_session, storage, _FakeParser(), job=job)
    first = await list_packages(db_session, tenant_id=tenant.id)
    assert len(first) == 1
    await process_job(db_session, storage, _FakeParser(), job=job)
    again = await list_packages(db_session, tenant_id=tenant.id)
    assert [row.id for row in again] == [first[0].id]
    membership = await list_package_documents(
        db_session, tenant_id=tenant.id, package_id=first[0].id
    )
    assert [row.document_id for row in membership] == [document.id]
    assert document.status == DocumentStatus.ready
    payload = json.loads(await storage.get(adapter_key_for(document)))
    assert payload["package_id"] == str(first[0].id)


def test_json_is_rejected_until_an_adapter_exists():
    assert is_search_skippable("application/json") is False
    with pytest.raises(UnsupportedFileType, match="unsupported file extension"):
        validate_upload(
            filename="facts.json",
            declared_mime_type="application/json",
            content=b'{"ok": true}',
            max_bytes=1024,
        )
    with pytest.raises(UnsupportedFileType, match="unsupported file extension"):
        validate_upload(
            filename="facts.json",
            declared_mime_type="application/json",
            content=b"{invalid",
            max_bytes=1024,
        )


async def test_deleting_a_zip_removes_its_generated_package(
    db_session, storage, tenant, other_tenant, make_document
):
    content = _zip_bytes({"readme.md": b"# line\n"})
    document, job = await _queued_zip(db_session, storage, tenant, make_document, content)
    await process_job(db_session, storage, _FakeParser(), job=job)
    other, other_job = await _queued_zip(db_session, storage, other_tenant, make_document, content)
    await process_job(db_session, storage, _FakeParser(), job=other_job)
    assert await list_packages(db_session, tenant_id=tenant.id)
    await delete_document(db_session, storage, tenant_id=tenant.id, document_id=document.id)
    assert await list_packages(db_session, tenant_id=tenant.id) == []
    remaining = await list_packages(db_session, tenant_id=other_tenant.id)
    assert [row.id for row in remaining]
    other_members = await list_package_documents(
        db_session, tenant_id=other_tenant.id, package_id=remaining[0].id
    )
    assert [row.document_id for row in other_members] == [other.id]


async def test_deleting_a_zip_keeps_a_package_that_still_has_members(
    db_session, storage, tenant, make_document
):
    content = _zip_bytes({"readme.md": b"# line\n"})
    document, job = await _queued_zip(db_session, storage, tenant, make_document, content)
    await process_job(db_session, storage, _FakeParser(), job=job)
    packages = await list_packages(db_session, tenant_id=tenant.id)
    extra = await make_document(tenant, filename="notes.md", mime_type="text/markdown")
    await add_package_document(
        db_session,
        tenant_id=tenant.id,
        package_id=packages[0].id,
        document_id=extra.id,
        relative_path="notes.md",
    )
    await delete_document(db_session, storage, tenant_id=tenant.id, document_id=document.id)
    remaining = await list_packages(db_session, tenant_id=tenant.id)
    assert [row.id for row in remaining] == [packages[0].id]
    members = await list_package_documents(
        db_session, tenant_id=tenant.id, package_id=packages[0].id
    )
    assert [row.document_id for row in members] == [extra.id]


async def test_deleting_a_non_zip_does_not_drop_a_package_that_shares_the_generated_slug(
    db_session, storage, tenant, make_document
):
    document = await make_document(tenant, filename="notes.md", mime_type="text/markdown")
    document.storage_key = f"{tenant.id}/{document.id}/notes.md"
    await db_session.flush()
    await storage.put(document.storage_key, b"# notes\n")
    human = await create_package(
        db_session,
        tenant_id=tenant.id,
        slug=package_slug_for(document),
        name="Human package",
    )
    await delete_document(db_session, storage, tenant_id=tenant.id, document_id=document.id)
    kept = await get_package(db_session, tenant_id=tenant.id, package_id=human.id)
    assert kept is not None
    assert kept.id == human.id


async def test_zip_intake_does_not_take_over_a_user_package_with_the_same_slug(
    db_session, storage, tenant, make_document
):
    content = _zip_bytes({"readme.md": b"# line\n"})
    document, job = await _queued_zip(db_session, storage, tenant, make_document, content)
    human = await create_package(
        db_session,
        tenant_id=tenant.id,
        slug=package_slug_for(document),
        name="Human package",
    )
    await process_job(db_session, storage, _FakeParser(), job=job)
    payload = json.loads(await storage.get(adapter_key_for(document)))
    assert payload["package_id"] != str(human.id)
    await delete_document(db_session, storage, tenant_id=tenant.id, document_id=document.id)
    kept = await get_package(db_session, tenant_id=tenant.id, package_id=human.id)
    assert kept is not None
    remaining = await list_packages(db_session, tenant_id=tenant.id)
    assert [row.id for row in remaining] == [human.id]


def test_fixture_package_members_are_valid_uploads():
    files = binary_files()
    archive = _zip_bytes(files)
    upload = validate_upload(
        filename="cl-12.zip",
        declared_mime_type="application/zip",
        content=archive,
        max_bytes=20 * 1024 * 1024,
    )
    assert upload.mime_type == "application/zip"
    members = safe_unpack(archive)
    assert len(members) >= 20
    assert any(item.path_hint.endswith("OB1.xml") for item in members)
    assert any(item.path_hint.endswith("cabinet_photo.png") for item in members)
