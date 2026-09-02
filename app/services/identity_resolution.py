"""Resolve extracted mentions into canonical entities (SIN-91).

Exact identifiers merge. Comments, types and reused labels never merge on
their own. Ambiguous cases stay as separate entities plus an explicit conflict.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.engineering_models import (
    ConflictKind,
    EngineeringDocumentClass,
    EntityKind,
    EvidenceLocatorKind,
    EvidenceSubjectKind,
    IdentityStatus,
)
from app.models import Document
from app.providers.storage import ObjectNotFoundError, StorageBackend
from app.services import engineering
from app.services.entity_extract import (
    EXTRACTOR_METHOD,
    EXTRACTOR_VERSION,
    ExtractedMention,
    extract_mentions,
)
from app.services.package_assignment import (
    _load_payload,
    _member_key,
    current_assignments,
)

logger = logging.getLogger(__name__)

RESOLVER_METHOD = "deterministic_identity"
RESOLVER_VERSION = "sin-91.1"

_CONVEYOR = re.compile(r"CV(\d{2})")
_TERMINAL_NUM = re.compile(r"^X[AB]:(\d+)")

_ATTR_PRECEDENCE = {
    "current": 5,
    "mention": 1,
    "superseded": 0,
}

_CLASS_PRECEDENCE = {
    EngineeringDocumentClass.io_list: 8,
    EngineeringDocumentClass.bom: 7,
    EngineeringDocumentClass.hardware: 6,
    EngineeringDocumentClass.motor_drive: 6,
    EngineeringDocumentClass.terminals: 5,
    EngineeringDocumentClass.cables: 5,
    EngineeringDocumentClass.schematic: 3,
    EngineeringDocumentClass.cross_references: 2,
    EngineeringDocumentClass.manual: 2,
    EngineeringDocumentClass.revision: 1,
}


async def resolve_package_identities(
    session: AsyncSession,
    storage: StorageBackend,
    *,
    document: Document,
) -> list:
    """Extract and resolve identities for the archive's machine package."""
    payload = await _load_payload(storage, document)
    raw_package_id = payload.get("package_id")
    if not raw_package_id:
        return []
    package = await engineering.get_package(
        session, tenant_id=document.tenant_id, package_id=raw_package_id
    )
    if package is None:
        return []

    assignments = await current_assignments(
        session, tenant_id=document.tenant_id, package_id=package.id
    )
    class_by_path = {
        row.relative_path: row.document_class for row in assignments if row.relative_path
    }
    machine_id = next((row.machine_id for row in assignments if row.machine_id), None)
    mentions = await _mentions_for_members(
        storage,
        payload=payload,
        class_by_path=class_by_path,
    )
    await engineering.clear_package_identities(
        session,
        tenant_id=document.tenant_id,
        package_id=package.id,
        methods=(RESOLVER_METHOD, EXTRACTOR_METHOD),
    )
    if not mentions:
        logger.info(
            "package identities empty",
            extra={
                "tenant_id": str(document.tenant_id),
                "document_id": str(document.id),
                "package_id": str(package.id),
            },
        )
        return []

    entities = await _persist_resolution(
        session,
        tenant_id=document.tenant_id,
        package_id=package.id,
        document_id=document.id,
        machine_id=machine_id,
        mentions=mentions,
    )
    logger.info(
        "package identities resolved",
        extra={
            "tenant_id": str(document.tenant_id),
            "document_id": str(document.id),
            "package_id": str(package.id),
            "entity_count": len(entities),
        },
    )
    return entities


async def _mentions_for_members(
    storage: StorageBackend,
    *,
    payload: dict[str, Any],
    class_by_path: dict[str | None, EngineeringDocumentClass],
) -> list[ExtractedMention]:
    mentions: list[ExtractedMention] = []
    members = [
        item for item in payload.get("observations") or [] if item.get("kind") == "package_member"
    ]
    for member in members:
        path_hint = (member.get("payload") or {}).get("path_hint") or ""
        document_class = class_by_path.get(path_hint)
        if document_class is None:
            continue
        key = _member_key(payload, path_hint)
        if not key:
            continue
        try:
            content = await storage.get(key)
        except ObjectNotFoundError:
            continue
        filename = path_hint.rsplit("/", 1)[-1]
        mentions.extend(
            extract_mentions(
                content=content,
                filename=filename,
                path_hint=path_hint,
                document_class=document_class,
            )
        )
    mentions.sort(
        key=lambda item: (
            item.entity_kind.value,
            item.name,
            item.source_path,
            str(item.evidence.get("cell_range") or item.evidence.get("page_number") or 0),
        )
    )
    return mentions


async def _persist_resolution(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    document_id,
    machine_id,
    mentions: list[ExtractedMention],
) -> list:
    groups: dict[tuple[EntityKind, str], list[ExtractedMention]] = defaultdict(list)
    for mention in mentions:
        groups[(mention.entity_kind, mention.name)].append(mention)

    candidates_by_key: dict[tuple[EntityKind, str], list] = {}
    for key, group in groups.items():
        stored = []
        for mention in group:
            evidence = await _record_mention_evidence(
                session, tenant_id=tenant_id, document_id=document_id, mention=mention
            )
            candidate = await engineering.create_entity_candidate(
                session,
                tenant_id=tenant_id,
                package_id=package_id,
                entity_kind=mention.entity_kind,
                proposed_name=mention.name,
                evidence_ids=[evidence.id],
                aliases=list(mention.aliases),
                confidence=mention.confidence,
                method=EXTRACTOR_METHOD,
                method_version=EXTRACTOR_VERSION,
                attributes={
                    **mention.attributes,
                    "source_path": mention.source_path,
                    "revision_role": mention.revision_role,
                },
            )
            stored.append((mention, candidate, evidence))
        candidates_by_key[key] = stored

    entities_by_key = {}
    for key, stored in candidates_by_key.items():
        entity = await _promote_group(
            session,
            tenant_id=tenant_id,
            package_id=package_id,
            machine_id=machine_id,
            stored=stored,
        )
        entities_by_key[key] = entity

    await _record_identity_conflicts(
        session,
        tenant_id=tenant_id,
        package_id=package_id,
        entities_by_key=entities_by_key,
        candidates_by_key=candidates_by_key,
    )
    return list(entities_by_key.values())


async def _promote_group(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    machine_id,
    stored: list,
) -> Any:
    current = [item for item in stored if item[0].revision_role != "superseded"]
    primary_pool = current or stored
    primary_mention = max(primary_pool, key=_mention_rank)[0]
    aliases: list[str] = []
    for mention, _candidate, _evidence in stored:
        for alias in mention.aliases:
            if alias != primary_mention.name and alias not in aliases:
                aliases.append(alias)
    attributes = _merged_attributes(stored)
    supporting = sorted(
        {
            mention.source_path
            for mention, _candidate, _evidence in stored
            if mention.revision_role != "superseded"
        }
    )
    contradicting = sorted(
        {
            mention.source_path
            for mention, _candidate, _evidence in stored
            if mention.revision_role == "superseded"
        }
    )
    attributes["identity_resolution"] = {
        "decision": "resolved",
        "reason": "exact-identifier",
        "supporting_paths": supporting,
        "contradicting_paths": contradicting,
        "candidate_count": len(stored),
    }
    evidence_ids = [evidence.id for _mention, _candidate, evidence in stored]
    assembly_id = None
    assembly_code = assembly_code_for(primary_mention.name)
    if machine_id is not None and assembly_code:
        assembly = await _ensure_assembly(
            session,
            tenant_id=tenant_id,
            package_id=package_id,
            machine_id=machine_id,
            code=assembly_code,
        )
        assembly_id = assembly.id
    confidence = max(mention.confidence for mention, _candidate, _evidence in primary_pool)
    entity = await engineering.create_canonical_entity(
        session,
        tenant_id=tenant_id,
        package_id=package_id,
        entity_kind=primary_mention.entity_kind,
        canonical_name=primary_mention.name,
        evidence_ids=evidence_ids,
        machine_id=machine_id,
        assembly_id=assembly_id,
        aliases=aliases,
        identity_status=IdentityStatus.resolved,
        confidence=confidence,
        method=RESOLVER_METHOD,
        method_version=RESOLVER_VERSION,
        attributes=attributes,
    )
    for mention, candidate, _evidence in stored:
        candidate.canonical_entity_id = entity.id
        candidate.identity_status = (
            IdentityStatus.conflicted
            if mention.revision_role == "superseded"
            else IdentityStatus.resolved
        )
    await session.flush()
    return entity


def _mention_rank(item: tuple[ExtractedMention, Any, Any]) -> tuple[int, int, float]:
    mention = item[0]
    return (
        _ATTR_PRECEDENCE.get(mention.revision_role, 0),
        _CLASS_PRECEDENCE.get(mention.document_class, 0),
        mention.confidence,
    )


def _merged_attributes(stored: list) -> dict[str, Any]:
    ranked = sorted(stored, key=_mention_rank, reverse=True)
    merged: dict[str, Any] = {}
    for mention, _candidate, _evidence in ranked:
        for key, value in mention.attributes.items():
            if key == "mention":
                continue
            if key not in merged and value not in (None, ""):
                merged[key] = value
    return merged


async def _record_identity_conflicts(
    session: AsyncSession,
    *,
    tenant_id,
    package_id,
    entities_by_key: dict,
    candidates_by_key: dict,
) -> None:
    comment_groups: dict[str, list] = defaultdict(list)
    address_groups: dict[str, list] = defaultdict(list)
    for key, entity in entities_by_key.items():
        kind, _name = key
        if kind != EntityKind.signal:
            continue
        comment = (entity.attributes or {}).get("comment")
        address = (entity.attributes or {}).get("address")
        if isinstance(comment, str) and comment:
            comment_groups[comment].append(entity)
        if isinstance(address, str) and address:
            address_groups[address].append(entity)

    for group in (*comment_groups.values(), *address_groups.values()):
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda row: row.canonical_name)
        left, right = ordered[0], ordered[1]
        evidence_rows = await engineering.list_evidence_for(
            session,
            tenant_id=tenant_id,
            subject_kind=EvidenceSubjectKind.entity,
            subject_id=left.id,
        )
        extra = await engineering.list_evidence_for(
            session,
            tenant_id=tenant_id,
            subject_kind=EvidenceSubjectKind.entity,
            subject_id=right.id,
        )
        evidence_ids = [row.id for row in evidence_rows[:1] + extra[:1]]
        if not evidence_ids:
            continue
        await engineering.record_conflict(
            session,
            tenant_id=tenant_id,
            package_id=package_id,
            conflict_kind=ConflictKind.identity,
            left_subject_kind="entity",
            left_subject_id=left.id,
            right_subject_kind="entity",
            right_subject_id=right.id,
            evidence_ids=evidence_ids,
            method=RESOLVER_METHOD,
            method_version=RESOLVER_VERSION,
        )

    for stored in candidates_by_key.values():
        current = [item for item in stored if item[0].revision_role == "current"]
        superseded = [item for item in stored if item[0].revision_role == "superseded"]
        if not current or not superseded:
            continue
        left = current[0]
        right = superseded[0]
        if _same_domain_attrs(left[0].attributes, right[0].attributes):
            continue
        await engineering.record_conflict(
            session,
            tenant_id=tenant_id,
            package_id=package_id,
            conflict_kind=ConflictKind.revision,
            left_subject_kind="entity_candidate",
            left_subject_id=left[1].id,
            right_subject_kind="entity_candidate",
            right_subject_id=right[1].id,
            evidence_ids=[left[2].id, right[2].id],
            method=RESOLVER_METHOD,
            method_version=RESOLVER_VERSION,
        )


def _same_domain_attrs(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = {"role", "qty", "revision", "power_kw", "address", "comment"}
    return {key: left.get(key) for key in keys} == {key: right.get(key) for key in keys}


async def _record_mention_evidence(session: AsyncSession, *, tenant_id, document_id, mention):
    return await engineering.record_evidence(
        session,
        tenant_id=tenant_id,
        **_locator_kwargs(mention.evidence, document_id, mention.source_path),
    )


def _locator_kwargs(evidence: dict[str, Any], document_id, fallback_path: str) -> dict[str, Any]:
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


def assembly_code_for(name: str) -> str | None:
    match = _CONVEYOR.search(name)
    if match is None:
        match = _TERMINAL_NUM.match(name)
    if match is None:
        return None
    number = int(match.group(1))
    if 1 <= number <= 3:
        return "zone-a"
    if 4 <= number <= 6:
        return "zone-b"
    if 7 <= number <= 9:
        return "zone-c"
    if 10 <= number <= 12:
        return "zone-d"
    return None


async def _ensure_assembly(session: AsyncSession, *, tenant_id, package_id, machine_id, code: str):
    existing = await engineering.get_assembly_by_code(
        session, tenant_id=tenant_id, package_id=package_id, code=code
    )
    if existing is not None:
        return existing
    return await engineering.create_assembly(
        session,
        tenant_id=tenant_id,
        package_id=package_id,
        machine_id=machine_id,
        name=code,
        code=code,
    )
