"""Typed engineering mentions from the first supported formats (SIN-91).

Deterministic parser output and exact identifiers only. LLM extraction is not
used: absent identifiers stay absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.adapters.extract import (
    TableRow,
    cell_evidence,
    extract_pdf_pages,
    extract_text_pages,
    parse_table,
)
from app.engineering_models import EngineeringDocumentClass, EntityKind

EXTRACTOR_METHOD = "deterministic_identifiers"
EXTRACTOR_VERSION = "sin-91.1"

_SIGNAL = re.compile(r"\b((?:Line|CV\d{2})\.[A-Za-z][A-Za-z0-9]*)\b")
_COMPONENT = re.compile(r"\b(CV\d{2}-[BMUS]\d+|CL12-[A-Z0-9-]+)\b")
_TERMINAL = re.compile(r"\b(X[AB]:\d+\.\d+)\b")
_CABLE = re.compile(r"\b(W-CV\d{2}-[A-Z]+)\b")

_BOM_HEADERS = ("tag", "kind", "qty", "revision", "power_kw")
_IO_HEADERS = ("name", "address", "data_type", "direction", "comment", "conveyor")
_TERMINAL_HEADERS = ("id", "role", "cable")
_CABLE_HEADERS = ("id", "from", "to")
_HARDWARE_HEADERS = ("id", "name", "role", "location", "class")
_DRIVE_HEADERS = ("motor", "drive", "kind", "power_kw")
_XREF_HEADERS = ("block", "variable", "kind", "network")

_SKIP_CLASSES = frozenset(
    {
        EngineeringDocumentClass.unrelated,
        EngineeringDocumentClass.photo,
        EngineeringDocumentClass.package_container,
        EngineeringDocumentClass.unknown,
    }
)


@dataclass(frozen=True)
class ExtractedMention:
    entity_kind: EntityKind
    name: str
    aliases: tuple[str, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    document_class: EngineeringDocumentClass = EngineeringDocumentClass.unknown
    confidence: float = 0.9
    revision_role: str = "current"


def extract_mentions(
    *,
    content: bytes,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
) -> tuple[ExtractedMention, ...]:
    """Extract typed mentions. Identical bytes and class yield identical output."""
    if document_class in _SKIP_CLASSES:
        return ()
    lowered = (path_hint or filename).lower()
    revision_role = "superseded" if "rev_old" in lowered or "bom_old" in lowered else "current"
    if lowered.endswith((".xlsx", ".csv", ".tsv")):
        return _from_table(
            content,
            filename=filename,
            path_hint=path_hint,
            document_class=document_class,
            revision_role=revision_role,
        )
    if lowered.endswith(".pdf"):
        pages = extract_pdf_pages(content)
        return _from_pages(
            pages,
            filename=filename,
            path_hint=path_hint,
            document_class=document_class,
            locator_kind="page",
        )
    if lowered.endswith((".md", ".txt", ".scl", ".awl", ".xml")):
        pages = extract_text_pages(content)
        locator_kind = (
            "line_range" if lowered.endswith((".scl", ".awl", ".xml", ".txt", ".md")) else "page"
        )
        return _from_pages(
            pages,
            filename=filename,
            path_hint=path_hint,
            document_class=document_class,
            locator_kind=locator_kind,
        )
    return ()


def _headers(row: TableRow) -> tuple[str, ...]:
    return tuple(key.strip().lower() for key in row.cells)


def _header_match(row: TableRow, expected: tuple[str, ...]) -> bool:
    present = set(_headers(row))
    return set(expected).issubset(present)


def _cell(row: TableRow, *names: str) -> str:
    lowered = {key.lower(): value for key, value in row.cells.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return value
    return ""


def _from_table(
    content: bytes,
    *,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    revision_role: str,
) -> tuple[ExtractedMention, ...]:
    sheet_name, rows = parse_table(content, filename)
    if not rows:
        return ()
    sample = rows[0]
    mentions: list[ExtractedMention] = []
    if _header_match(sample, _BOM_HEADERS):
        mentions.extend(
            _bom_mentions(
                rows,
                filename=filename,
                path_hint=path_hint,
                document_class=document_class,
                revision_role=revision_role,
                sheet_name=sheet_name,
            )
        )
    elif _header_match(sample, _IO_HEADERS):
        mentions.extend(
            _io_mentions(
                rows,
                filename=filename,
                path_hint=path_hint,
                document_class=document_class,
                sheet_name=sheet_name,
            )
        )
    elif (
        _header_match(sample, _TERMINAL_HEADERS)
        and document_class != EngineeringDocumentClass.cables
    ):
        mentions.extend(
            _terminal_mentions(
                rows,
                filename=filename,
                path_hint=path_hint,
                document_class=document_class,
                sheet_name=sheet_name,
            )
        )
    elif _header_match(sample, _CABLE_HEADERS):
        mentions.extend(
            _cable_mentions(
                rows,
                filename=filename,
                path_hint=path_hint,
                document_class=document_class,
                sheet_name=sheet_name,
            )
        )
    elif _header_match(sample, _HARDWARE_HEADERS):
        mentions.extend(
            _hardware_mentions(
                rows,
                filename=filename,
                path_hint=path_hint,
                document_class=document_class,
                sheet_name=sheet_name,
            )
        )
    elif _header_match(sample, _DRIVE_HEADERS):
        mentions.extend(
            _drive_mentions(
                rows,
                filename=filename,
                path_hint=path_hint,
                document_class=document_class,
                sheet_name=sheet_name,
            )
        )
    elif _header_match(sample, _XREF_HEADERS):
        mentions.extend(
            _xref_mentions(
                rows,
                filename=filename,
                path_hint=path_hint,
                document_class=document_class,
                sheet_name=sheet_name,
            )
        )
    return tuple(mentions)


def _mention(
    *,
    kind: EntityKind,
    name: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    evidence: dict[str, Any],
    aliases: tuple[str, ...] = (),
    attributes: dict[str, Any] | None = None,
    confidence: float = 0.95,
    revision_role: str = "current",
) -> ExtractedMention:
    return ExtractedMention(
        entity_kind=kind,
        name=name,
        aliases=aliases,
        attributes=attributes or {},
        evidence=evidence,
        source_path=path_hint,
        document_class=document_class,
        confidence=confidence,
        revision_role=revision_role,
    )


def _bom_mentions(
    rows: tuple[TableRow, ...],
    *,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    revision_role: str,
    sheet_name: str,
) -> list[ExtractedMention]:
    mentions: list[ExtractedMention] = []
    for row in rows:
        name = _cell(row, "tag")
        if not name:
            continue
        kind = _cell(row, "kind")
        attributes: dict[str, Any] = {}
        if kind:
            attributes["role"] = kind
        qty = _cell(row, "qty")
        if qty:
            attributes["qty"] = _as_number(qty)
        revision = _cell(row, "revision")
        if revision:
            attributes["revision"] = revision
        power = _cell(row, "power_kw")
        if power:
            attributes["power_kw"] = _as_number(power)
        mentions.append(
            _mention(
                kind=EntityKind.component,
                name=name,
                path_hint=path_hint,
                document_class=document_class,
                evidence=cell_evidence(
                    sheet_name=sheet_name or row.sheet_name,
                    cell_range=row.cell_range,
                    path_hint=path_hint,
                    filename=filename,
                ),
                attributes=attributes,
                revision_role=revision_role,
                confidence=0.97 if revision_role == "current" else 0.7,
            )
        )
    return mentions


def _io_mentions(
    rows: tuple[TableRow, ...],
    *,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    sheet_name: str,
) -> list[ExtractedMention]:
    mentions: list[ExtractedMention] = []
    for row in rows:
        name = _cell(row, "name")
        if not name:
            continue
        attributes: dict[str, Any] = {}
        address = _cell(row, "address")
        if address:
            attributes["address"] = address
        data_type = _cell(row, "data_type")
        if data_type:
            attributes["data_type"] = data_type
        direction = _cell(row, "direction")
        if direction:
            attributes["io_direction"] = direction
        comment = _cell(row, "comment")
        if comment:
            attributes["comment"] = comment
        conveyor = _cell(row, "conveyor")
        if conveyor:
            attributes["conveyor"] = conveyor
        mentions.append(
            _mention(
                kind=EntityKind.signal,
                name=name,
                path_hint=path_hint,
                document_class=document_class,
                evidence=cell_evidence(
                    sheet_name=sheet_name or row.sheet_name,
                    cell_range=row.cell_range,
                    path_hint=path_hint,
                    filename=filename,
                ),
                attributes=attributes,
                confidence=0.98,
            )
        )
    return mentions


def _terminal_mentions(
    rows: tuple[TableRow, ...],
    *,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    sheet_name: str,
) -> list[ExtractedMention]:
    mentions: list[ExtractedMention] = []
    for row in rows:
        name = _cell(row, "id")
        if not name:
            continue
        attributes: dict[str, Any] = {}
        role = _cell(row, "role")
        if role:
            attributes["role"] = role
        cable = _cell(row, "cable")
        if cable:
            attributes["cable"] = cable
        mentions.append(
            _mention(
                kind=EntityKind.terminal,
                name=name,
                path_hint=path_hint,
                document_class=document_class,
                evidence=cell_evidence(
                    sheet_name=sheet_name or row.sheet_name,
                    cell_range=row.cell_range,
                    path_hint=path_hint,
                    filename=filename,
                ),
                attributes=attributes,
            )
        )
    return mentions


def _cable_mentions(
    rows: tuple[TableRow, ...],
    *,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    sheet_name: str,
) -> list[ExtractedMention]:
    mentions: list[ExtractedMention] = []
    for row in rows:
        name = _cell(row, "id")
        if not name:
            continue
        attributes: dict[str, Any] = {}
        source = _cell(row, "from")
        target = _cell(row, "to")
        if source:
            attributes["from"] = source
        if target:
            attributes["to"] = target
        mentions.append(
            _mention(
                kind=EntityKind.cable,
                name=name,
                path_hint=path_hint,
                document_class=document_class,
                evidence=cell_evidence(
                    sheet_name=sheet_name or row.sheet_name,
                    cell_range=row.cell_range,
                    path_hint=path_hint,
                    filename=filename,
                ),
                attributes=attributes,
            )
        )
    return mentions


def _hardware_mentions(
    rows: tuple[TableRow, ...],
    *,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    sheet_name: str,
) -> list[ExtractedMention]:
    mentions: list[ExtractedMention] = []
    for row in rows:
        name = _cell(row, "id")
        if not name:
            continue
        display = _cell(row, "name")
        aliases = (display,) if display and display != name else ()
        attributes: dict[str, Any] = {}
        role = _cell(row, "role")
        location = _cell(row, "location")
        klass = _cell(row, "class")
        if role:
            attributes["role"] = role
        if location:
            attributes["location"] = location
        if klass:
            attributes["class"] = klass
        mentions.append(
            _mention(
                kind=EntityKind.component,
                name=name,
                path_hint=path_hint,
                document_class=document_class,
                evidence=cell_evidence(
                    sheet_name=sheet_name or row.sheet_name,
                    cell_range=row.cell_range,
                    path_hint=path_hint,
                    filename=filename,
                ),
                aliases=aliases,
                attributes=attributes,
            )
        )
    return mentions


def _drive_mentions(
    rows: tuple[TableRow, ...],
    *,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    sheet_name: str,
) -> list[ExtractedMention]:
    mentions: list[ExtractedMention] = []
    for row in rows:
        evidence = cell_evidence(
            sheet_name=sheet_name or row.sheet_name,
            cell_range=row.cell_range,
            path_hint=path_hint,
            filename=filename,
        )
        motor = _cell(row, "motor")
        drive = _cell(row, "drive")
        kind = _cell(row, "kind")
        power = _cell(row, "power_kw")
        if motor:
            attributes: dict[str, Any] = {"role": "motor"}
            if power:
                attributes["power_kw"] = _as_number(power)
            mentions.append(
                _mention(
                    kind=EntityKind.component,
                    name=motor,
                    path_hint=path_hint,
                    document_class=document_class,
                    evidence=evidence,
                    attributes=attributes,
                    confidence=0.9,
                )
            )
        if drive and drive.upper() != "DOL":
            mentions.append(
                _mention(
                    kind=EntityKind.component,
                    name=drive,
                    path_hint=path_hint,
                    document_class=document_class,
                    evidence=evidence,
                    attributes={"role": kind or "vfd"},
                    confidence=0.93,
                )
            )
    return mentions


def _xref_mentions(
    rows: tuple[TableRow, ...],
    *,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    sheet_name: str,
) -> list[ExtractedMention]:
    mentions: list[ExtractedMention] = []
    for row in rows:
        name = _cell(row, "variable")
        if not name or not _SIGNAL.fullmatch(name):
            continue
        mentions.append(
            _mention(
                kind=EntityKind.signal,
                name=name,
                path_hint=path_hint,
                document_class=document_class,
                evidence=cell_evidence(
                    sheet_name=sheet_name or row.sheet_name,
                    cell_range=row.cell_range,
                    path_hint=path_hint,
                    filename=filename,
                ),
                attributes={"mention": True},
                confidence=0.8,
                revision_role="mention",
            )
        )
    return mentions


def _from_pages(
    pages: tuple[tuple[int, str], ...],
    *,
    filename: str,
    path_hint: str,
    document_class: EngineeringDocumentClass,
    locator_kind: str,
) -> tuple[ExtractedMention, ...]:
    mentions: list[ExtractedMention] = []
    seen: set[tuple[EntityKind, str, int]] = set()
    for page_number, text in pages:
        for kind, pattern in (
            (EntityKind.signal, _SIGNAL),
            (EntityKind.component, _COMPONENT),
            (EntityKind.terminal, _TERMINAL),
            (EntityKind.cable, _CABLE),
        ):
            for match in pattern.finditer(text):
                name = match.group(1)
                key = (kind, name, page_number)
                if key in seen:
                    continue
                seen.add(key)
                if locator_kind == "page":
                    evidence = {
                        "locator_kind": "page",
                        "page_number": page_number,
                        "path_hint": path_hint,
                        "artifact": filename,
                    }
                else:
                    line_start = _line_of(text, match.start())
                    evidence = {
                        "locator_kind": "line_range",
                        "line_start": line_start,
                        "line_end": line_start,
                        "path_hint": path_hint,
                        "artifact": filename,
                    }
                mentions.append(
                    _mention(
                        kind=kind,
                        name=name,
                        path_hint=path_hint,
                        document_class=document_class,
                        evidence=evidence,
                        attributes={"mention": True},
                        confidence=0.72,
                        revision_role="mention",
                    )
                )
    return tuple(mentions)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _as_number(value: str) -> int | float | str:
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return int(number)
    return number
