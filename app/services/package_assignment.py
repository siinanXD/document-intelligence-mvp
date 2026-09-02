"""Resolve adapter observations into package assignments (SIN-90).

Adapters stay observation-only. This service writes PackageAssignment rows
with method, version, confidence and evidence. Human overrides supersede a
prediction without deleting it.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.engineering_models import (
    AssignmentState,
    EngineeringDocumentClass,
    EvidenceLocatorKind,
    EvidenceSubjectKind,
    IdentityStatus,
    PackageAssignment,
)
from app.models import Document
from app.providers.storage import ObjectNotFoundError, StorageBackend
from app.services import engineering
from app.services.classification import (
    CLASSIFIER_METHOD,
    ClassificationDecision,
    ClassificationInput,
    apply_package_identity,
    classify,
)
from app.services.package_intake import adapter_key_for

logger = logging.getLogger(__name__)

OVERRIDE_METHOD = "human_override"
OVERRIDE_VERSION = "sin-90.1"

_TEXT_SUFFIXES = (".md", ".txt", ".xml", ".scl", ".awl", ".csv", ".tsv")


def _payload_dict(raw: bytes) -> dict[str, Any]:
    import json

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _load_payload(storage: StorageBackend, document: Document) -> dict[str, Any]:
    try:
        raw = await storage.get(adapter_key_for(document))
    except ObjectNotFoundError:
        return {}
    return _payload_dict(raw)


def _member_key(payload: dict[str, Any], path_hint: str) -> str | None:
    for key, path in zip(
        payload.get("member_storage_keys") or [],
        [
            item.get("payload", {}).get("path_hint")
            for item in payload.get("observations") or []
            if item.get("kind") == "package_member"
        ],
        strict=False,
    ):
        if path == path_hint and isinstance(key, str):
            return key
    return None


def _adapter_class_for(payload: dict[str, Any], path_hint: str | None, filename: str) -> str | None:
    for item in payload.get("observations") or []:
        if item.get("kind") != "document_class":
            continue
        evidence = item.get("evidence") or {}
        payload_class = (item.get("payload") or {}).get("document_class")
        if not payload_class:
            continue
        if path_hint and evidence.get("path_hint") == path_hint:
            return payload_class
        if evidence.get("artifact") == filename:
            return payload_class
    return None


def _text_sample(content: bytes) -> str | None:
    if not content:
        return None
    try:
        return content[:4000].decode("utf-8")
    except UnicodeDecodeError:
        return None


def _locator_from_observation(evidence: dict[str, Any], document_id, fallback_path: str):
    kind_raw = evidence.get("locator_kind") or "native_id"
    try:
        kind = EvidenceLocatorKind(kind_raw)
    except ValueError:
        kind = EvidenceLocatorKind.native_id
    kwargs: dict[str, Any] = {"locator_kind": kind, "document_id": document_id}
    if kind == EvidenceLocatorKind.page:
        kwargs["page_number"] = evidence.get("page_number") or 1
    elif kind == EvidenceLocatorKind.sheet_cell:
        kwargs["sheet_name"] = evidence.get("sheet_name") or "unknown"
        kwargs["cell_range"] = evidence.get("cell_range") or "A1"
    elif kind == EvidenceLocatorKind.image_region:
        kwargs["region"] = evidence.get("region") or {"x": 0, "y": 0, "w": 1, "h": 1}
    elif kind == EvidenceLocatorKind.xml_path:
        kwargs["xml_path"] = evidence.get("xml_path") or "/Document"
    elif kind == EvidenceLocatorKind.line_range:
        kwargs["line_start"] = (
            evidence.get("line_start") if evidence.get("line_start") is not None else 1
        )
        kwargs["line_end"] = evidence.get("line_end")
    else:
        kwargs["locator_kind"] = EvidenceLocatorKind.native_id
        kwargs["native_object_id"] = (
            evidence.get("native_object_id") or evidence.get("path_hint") or fallback_path
        )
    return kwargs


async def _ensure_machine(
    session: AsyncSession, *, tenant_id, package_id, decision: ClassificationDecision
):
    if not decision.assign_to_machine or not decision.machine_code:
        return None
    existing = await engineering.get_machine_by_code(
        session, tenant_id=tenant_id, package_id=package_id, code=decision.machine_code
    )
    if existing is not None:
        return existing
    machine = await engineering.create_machine(
        session,
        tenant_id=tenant_id,
        package_id=package_id,
        name=decision.machine_name or decision.machine_code,
        code=decision.machine_code,
        revision="A",
    )
    machine.identity_status = IdentityStatus.resolved
    return machine


async def _ensure_assembly(
    session: AsyncSession, *, tenant_id, package_id, machine_id, decision: ClassificationDecision
):
    if machine_id is None or not decision.assembly_code:
        return None
    existing = await engineering.get_assembly_by_code(
        session, tenant_id=tenant_id, package_id=package_id, code=decision.assembly_code
    )
    if existing is not None:
        return existing
    return await engineering.create_assembly(
        session,
        tenant_id=tenant_id,
        package_id=package_id,
        machine_id=machine_id,
        name=decision.assembly_code,
        code=decision.assembly_code,
    )


def _current_for_path(assignments, relative_path: str | None):
    current = [
        row
        for row in assignments
        if row.relative_path == relative_path and row.state != AssignmentState.superseded
    ]
    if not current:
        return None
    return max(current, key=lambda row: row.created_at)


async def classify_and_assign_package(
    session: AsyncSession,
    storage: StorageBackend,
    *,
    document: Document,
) -> list:
    """Classify the archive and each member. Missing packages are a no-op."""
    payload = await _load_payload(storage, document)
    raw_package_id = payload.get("package_id")
    if not raw_package_id:
        return []
    package = await engineering.get_package(
        session, tenant_id=document.tenant_id, package_id=raw_package_id
    )
    if package is None:
        return []

    members = [
        item for item in payload.get("observations") or [] if item.get("kind") == "package_member"
    ]
    inputs: list[tuple[str | None, ClassificationInput]] = [
        (
            "",
            ClassificationInput(
                filename=document.filename,
                path_hint="",
                mime_type=document.mime_type,
                adapter_class=payload.get("adapter") and "package_container",
            ),
        )
    ]
    for member in members:
        path_hint = (member.get("payload") or {}).get("path_hint") or ""
        filename = path_hint.rsplit("/", 1)[-1]
        text_sample = None
        if path_hint.lower().endswith(_TEXT_SUFFIXES):
            key = _member_key(payload, path_hint)
            if key:
                try:
                    text_sample = _text_sample(await storage.get(key))
                except ObjectNotFoundError:
                    text_sample = None
        inputs.append(
            (
                path_hint,
                ClassificationInput(
                    filename=filename,
                    path_hint=path_hint,
                    adapter_class=_adapter_class_for(payload, path_hint, filename),
                    text_sample=text_sample,
                ),
            )
        )

    decisions = apply_package_identity([classify(item) for _path, item in inputs])
    existing = await engineering.list_assignments(
        session, tenant_id=document.tenant_id, package_id=package.id
    )
    created = []
    package_machine = None
    for (relative_path, _item), decision in zip(inputs, decisions, strict=True):
        path_value = relative_path or ""
        current = _current_for_path(existing, path_value)
        if current is not None and current.method == OVERRIDE_METHOD:
            continue
        machine = await _ensure_machine(
            session, tenant_id=document.tenant_id, package_id=package.id, decision=decision
        )
        if machine is not None:
            package_machine = machine
        assembly = await _ensure_assembly(
            session,
            tenant_id=document.tenant_id,
            package_id=package.id,
            machine_id=None if machine is None else machine.id,
            decision=decision,
        )
        if (
            current is not None
            and current.document_class == decision.document_class
            and current.machine_id == (None if machine is None else machine.id)
            and current.assembly_id == (None if assembly is None else assembly.id)
            and current.method == CLASSIFIER_METHOD
        ):
            continue
        if current is not None and current.method == CLASSIFIER_METHOD:
            current.state = AssignmentState.superseded
        evidence = await engineering.record_evidence(
            session,
            tenant_id=document.tenant_id,
            **_locator_from_observation(
                next(
                    (
                        item.get("evidence") or {}
                        for item in payload.get("observations") or []
                        if item.get("kind") == "document_class"
                        and (
                            (item.get("evidence") or {}).get("path_hint") == relative_path
                            or (item.get("evidence") or {}).get("artifact") == _item.filename
                        )
                    ),
                    {"locator_kind": "native_id", "path_hint": path_value or document.filename},
                ),
                document.id,
                path_value or document.filename,
            ),
        )
        assignment = await engineering.create_assignment(
            session,
            tenant_id=document.tenant_id,
            package_id=package.id,
            document_id=document.id,
            evidence_ids=[evidence.id],
            machine_id=None if machine is None else machine.id,
            assembly_id=None if assembly is None else assembly.id,
            relative_path=path_value,
            document_class=decision.document_class,
            state=decision.state,
            confidence=decision.confidence,
            method=decision.method,
            method_version=decision.method_version,
            reason={
                "rules": list(decision.reasons),
                "identifiers": list(decision.identifiers),
                "adapter_class": _item.adapter_class,
            },
        )
        created.append(assignment)
        existing.append(assignment)

    if package_machine is not None and package.name == "Uploaded package":
        package.name = package_machine.name
    logger.info(
        "package classified",
        extra={
            "tenant_id": str(document.tenant_id),
            "document_id": str(document.id),
            "package_id": str(package.id),
            "assignment_count": len(created),
        },
    )
    return created


async def override_assignment(
    session: AsyncSession,
    *,
    tenant_id,
    assignment_id,
    actor_id: str,
    document_class: EngineeringDocumentClass | None = None,
    machine_id=None,
    assembly_id=None,
) -> PackageAssignment:
    """Correct one assignment. The original row stays, marked superseded."""
    current = await engineering.get_assignment(
        session, tenant_id=tenant_id, assignment_id=assignment_id
    )
    if current is None:
        raise engineering.EngineeringIsolationError("assignment not found")
    previous = {
        "document_class": current.document_class.value,
        "machine_id": None if current.machine_id is None else str(current.machine_id),
        "assembly_id": None if current.assembly_id is None else str(current.assembly_id),
        "state": current.state.value,
        "relative_path": current.relative_path,
    }
    new_class = document_class or current.document_class
    new_machine = current.machine_id if machine_id is None else machine_id
    new_assembly = current.assembly_id if assembly_id is None else assembly_id
    if machine_id is False:
        new_machine = None
    if assembly_id is False:
        new_assembly = None
    current.state = AssignmentState.superseded
    evidence_rows = await engineering.list_evidence_for(
        session,
        tenant_id=tenant_id,
        subject_kind=EvidenceSubjectKind.package_assignment,
        subject_id=current.id,
    )
    evidence_ids = [row.id for row in evidence_rows]
    if not evidence_ids:
        raise engineering.EngineeringEvidenceError("override requires the original evidence")
    replacement = await engineering.create_assignment(
        session,
        tenant_id=tenant_id,
        package_id=current.package_id,
        document_id=current.document_id,
        evidence_ids=evidence_ids,
        machine_id=new_machine,
        assembly_id=new_assembly,
        relative_path=current.relative_path,
        document_class=new_class,
        state=AssignmentState.accepted,
        confidence=1.0,
        method=OVERRIDE_METHOD,
        method_version=OVERRIDE_VERSION,
        reason={"rules": ["human-override"], "previous_assignment_id": str(current.id)},
    )
    await engineering.record_override(
        session,
        tenant_id=tenant_id,
        package_id=current.package_id,
        subject_kind=EvidenceSubjectKind.package_assignment,
        subject_id=current.id,
        actor_id=actor_id,
        previous_payload=previous,
        new_payload={
            "document_class": new_class.value,
            "machine_id": None if new_machine is None else str(new_machine),
            "assembly_id": None if new_assembly is None else str(new_assembly),
            "assignment_id": str(replacement.id),
        },
    )
    return replacement


async def current_assignments(session: AsyncSession, *, tenant_id, package_id) -> list:
    """Non-superseded assignments for one tenant's package."""
    rows = await engineering.list_assignments(session, tenant_id=tenant_id, package_id=package_id)
    latest: dict[str | None, Any] = {}
    for row in rows:
        if row.state == AssignmentState.superseded:
            continue
        latest[row.relative_path] = row
    return list(latest.values())
