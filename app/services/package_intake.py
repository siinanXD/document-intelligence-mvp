"""Package intake on the existing upload/storage/worker path (SIN-100).

Adapters emit observations. Canonical entities are not written here.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import Artifact, PackageRejected
from app.adapters.registry import run_adapters
from app.adapters.zip_unpack import member_storage_key, safe_unpack
from app.engineering_models import PackageStatus
from app.models import Document
from app.providers.storage import ObjectNotFoundError, StorageBackend
from app.services import documents as documents_service
from app.services import engineering
from app.services.uploads import mime_for_extension

SEARCH_SKIPPABLE_MIMES = frozenset(
    {
        "application/zip",
        "image/png",
        "image/jpeg",
        "application/xml",
        "text/xml",
        "text/csv",
        "text/tab-separated-values",
        "text/x-scl",
        "text/x-awl",
    }
)


def adapter_key_for(document: Document) -> str:
    """Where serialized adapter observations live, beside the original bytes."""
    return f"{document.storage_key}.adapter.json"


def package_slug_for(document: Document) -> str:
    """Stable tenant-scoped slug for the package generated from one archive."""
    return f"pkg-{document.id.hex[:12]}"


def is_search_skippable(mime_type: str) -> bool:
    """Structured or raster artifacts are not forced through Docling."""
    return mime_type in SEARCH_SKIPPABLE_MIMES


def _artifact(
    filename: str, mime_type: str, content: bytes, *, path_hint: str | None = None
) -> Artifact:
    return Artifact(filename=filename, mime_type=mime_type, content=content, path_hint=path_hint)


def _observation_dict(observation) -> dict[str, Any]:
    return {
        "kind": observation.kind,
        "payload": observation.payload,
        "evidence": observation.evidence,
        "confidence": observation.confidence,
    }


async def ingest_artifact(
    session: AsyncSession,
    storage: StorageBackend,
    *,
    document: Document,
    content: bytes,
) -> dict[str, Any]:
    """Run adapters and persist observations. Zip members are stored, not merged."""
    payload: dict[str, Any] = {
        "document_id": str(document.id),
        "adapter_version": "1.0.0",
        "observations": [],
        "warnings": [],
        "validation_errors": [],
        "member_storage_keys": [],
        "package_id": None,
    }
    root = _artifact(document.filename, document.mime_type, content)
    detection, extracted, report = run_adapters(root)
    if detection is not None:
        payload["adapter"] = detection.adapter_name
        payload["family"] = detection.family
        payload["detection_reasons"] = list(detection.reasons)
    payload["observations"].extend(_observation_dict(item) for item in extracted.observations)
    payload["warnings"].extend(extracted.warnings)
    if not report.ok:
        payload["validation_errors"].extend(report.errors)
        raise PackageRejected(report.errors[0] if report.errors else "adapter validation failed")

    if document.mime_type == "application/zip":
        await _ingest_zip(session, storage, document=document, content=content, payload=payload)

    encoded = (json.dumps(payload, sort_keys=True, default=str) + "\n").encode()
    await storage.put(adapter_key_for(document), encoded, content_type="application/json")
    if payload["validation_errors"]:
        raise PackageRejected(payload["validation_errors"][0])
    return payload


async def delete_adapter_artifacts(storage: StorageBackend, *, document: Document) -> None:
    """Remove observation JSON and unpacked members. Missing keys are fine."""
    key = adapter_key_for(document)
    try:
        raw = await storage.get(key)
    except ObjectNotFoundError:
        return
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    for member_key in payload.get("member_storage_keys") or []:
        if isinstance(member_key, str):
            await storage.delete(member_key)
    await storage.delete(key)


async def delete_generated_package(session: AsyncSession, *, tenant_id, document: Document) -> None:
    """Remove the archive's generated package when no other members remain."""
    package = await engineering.get_package_by_slug(
        session, tenant_id=tenant_id, slug=package_slug_for(document)
    )
    if package is None:
        return
    remaining = await engineering.list_package_documents(
        session, tenant_id=tenant_id, package_id=package.id
    )
    if remaining:
        return
    await engineering.delete_package(session, tenant_id=tenant_id, package_id=package.id)


async def _ingest_zip(
    session: AsyncSession,
    storage: StorageBackend,
    *,
    document: Document,
    content: bytes,
    payload: dict[str, Any],
) -> None:
    members = safe_unpack(content)
    slug = package_slug_for(document)
    package = await engineering.get_package_by_slug(
        session, tenant_id=document.tenant_id, slug=slug
    )
    if package is None:
        package = await engineering.create_package(
            session,
            tenant_id=document.tenant_id,
            slug=slug,
            name="Uploaded package",
        )
    package.status = PackageStatus.draft
    memberships = await engineering.list_package_documents(
        session, tenant_id=document.tenant_id, package_id=package.id
    )
    if all(row.document_id != document.id for row in memberships):
        await engineering.add_package_document(
            session,
            tenant_id=document.tenant_id,
            package_id=package.id,
            document_id=document.id,
            relative_path="",
        )
    payload["package_id"] = str(package.id)

    for member in members:
        key = member_storage_key(
            tenant_id=document.tenant_id,
            document_id=document.id,
            path_hint=member.path_hint,
        )
        await storage.put(key, member.content, content_type=mime_for_extension(member.path_hint))
        payload["member_storage_keys"].append(key)
        existing = await documents_service.find_by_file_hash(
            session, tenant_id=document.tenant_id, file_hash=member.file_hash
        )
        duplicate_id = (
            str(existing.id) if existing is not None and existing.id != document.id else None
        )
        payload["observations"].append(
            {
                "kind": "package_member",
                "payload": {
                    "path_hint": member.path_hint,
                    "file_hash": member.file_hash,
                    "size": len(member.content),
                    "nested_archive": member.nested_archive,
                    "duplicate_document_id": duplicate_id,
                },
                "evidence": {"locator_kind": "native_id", "path_hint": member.path_hint},
                "confidence": 1.0,
            }
        )
        if duplicate_id is not None:
            payload["observations"].append(
                {
                    "kind": "duplicate_fingerprint",
                    "payload": {
                        "file_hash": member.file_hash,
                        "existing_document_id": duplicate_id,
                        "merged": False,
                    },
                    "evidence": {"locator_kind": "native_id", "path_hint": member.path_hint},
                    "confidence": 1.0,
                }
            )
            payload["warnings"].append("duplicate fingerprint not merged")
        if member.nested_archive:
            payload["warnings"].append("nested zip was not unpacked")
            continue
        child = _artifact(
            member.path_hint.rsplit("/", 1)[-1],
            mime_for_extension(member.path_hint),
            member.content,
            path_hint=member.path_hint,
        )
        _detection, extracted, report = run_adapters(child)
        payload["observations"].extend(_observation_dict(item) for item in extracted.observations)
        payload["warnings"].extend(extracted.warnings)
        if not report.ok:
            payload["validation_errors"].extend(report.errors)
