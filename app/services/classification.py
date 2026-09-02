"""Deterministic engineering document classification (SIN-90).

Filename, path, title and identifier metadata outrank adapter family hints.
LLM classification is a later weak signal and is not used here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.engineering_models import AssignmentState, EngineeringDocumentClass

CLASSIFIER_METHOD = "deterministic_metadata"
CLASSIFIER_VERSION = "sin-90.1"

_MACHINE_CODE = re.compile(r"\b([A-Z]{1,4}-\d{1,4})\b")
_ZONE_CODE = re.compile(r"\b(zone-[a-z])\b", re.IGNORECASE)
_MACHINE_NAME = re.compile(r"\b(Conveyor Line [A-Z]{1,4}-\d{1,4})\b", re.IGNORECASE)

_ADAPTER_CLASS = {
    "plc_xml": EngineeringDocumentClass.plc_xml,
    "plc_scl": EngineeringDocumentClass.plc_scl,
    "s5_text": EngineeringDocumentClass.s5_text,
    "photo": EngineeringDocumentClass.photo,
    "package_container": EngineeringDocumentClass.package_container,
}

_PATH_RULES: tuple[tuple[re.Pattern[str], EngineeringDocumentClass, str], ...] = (
    (
        re.compile(r"unrelated|hvac", re.IGNORECASE),
        EngineeringDocumentClass.unrelated,
        "filename-unrelated",
    ),
    (
        re.compile(r"schematic", re.IGNORECASE),
        EngineeringDocumentClass.schematic,
        "filename-schematic",
    ),
    (re.compile(r"(^|/)bom", re.IGNORECASE), EngineeringDocumentClass.bom, "filename-bom"),
    (
        re.compile(r"overview|commissioning|manual|datasheet", re.IGNORECASE),
        EngineeringDocumentClass.manual,
        "filename-manual",
    ),
    (re.compile(r"io[_-]?list", re.IGNORECASE), EngineeringDocumentClass.io_list, "filename-io"),
    (
        re.compile(r"hardware", re.IGNORECASE),
        EngineeringDocumentClass.hardware,
        "filename-hardware",
    ),
    (re.compile(r"cables?", re.IGNORECASE), EngineeringDocumentClass.cables, "filename-cables"),
    (
        re.compile(r"terminals?", re.IGNORECASE),
        EngineeringDocumentClass.terminals,
        "filename-terminals",
    ),
    (re.compile(r"alarms?", re.IGNORECASE), EngineeringDocumentClass.alarms, "filename-alarms"),
    (
        re.compile(r"motor[_-]?drive", re.IGNORECASE),
        EngineeringDocumentClass.motor_drive,
        "filename-motor-drive",
    ),
    (
        re.compile(r"revision", re.IGNORECASE),
        EngineeringDocumentClass.revision,
        "filename-revision",
    ),
    (
        re.compile(r"manufacturer", re.IGNORECASE),
        EngineeringDocumentClass.manufacturer_facts,
        "filename-manufacturer",
    ),
    (
        re.compile(r"cross[_-]?ref", re.IGNORECASE),
        EngineeringDocumentClass.cross_references,
        "filename-cross-ref",
    ),
)


@dataclass(frozen=True)
class ClassificationInput:
    filename: str
    path_hint: str | None = None
    mime_type: str | None = None
    title: str | None = None
    identifiers: tuple[str, ...] = ()
    text_sample: str | None = None
    adapter_class: str | None = None


@dataclass(frozen=True)
class ClassificationDecision:
    document_class: EngineeringDocumentClass
    machine_code: str | None
    machine_name: str | None
    assembly_code: str | None
    confidence: float
    reasons: tuple[str, ...]
    identifiers: tuple[str, ...]
    method: str = CLASSIFIER_METHOD
    method_version: str = CLASSIFIER_VERSION
    state: AssignmentState = AssignmentState.proposed

    @property
    def assign_to_machine(self) -> bool:
        return self.document_class != EngineeringDocumentClass.unrelated


def _blob(item: ClassificationInput) -> str:
    parts = [item.filename]
    if item.path_hint:
        parts.append(item.path_hint)
    if item.title:
        parts.append(item.title)
    if item.identifiers:
        parts.extend(item.identifiers)
    if item.text_sample:
        parts.append(item.text_sample[:4000])
    return "\n".join(parts)


def _extract_identifiers(item: ClassificationInput) -> tuple[str, ...]:
    blob = _blob(item)
    found: list[str] = []
    for match in _MACHINE_CODE.finditer(blob):
        code = match.group(1).upper()
        if code not in found:
            found.append(code)
    for match in _ZONE_CODE.finditer(blob):
        code = match.group(1).lower()
        if code not in found:
            found.append(code)
    for raw in item.identifiers:
        value = raw.strip()
        if value and value not in found:
            found.append(value)
    return tuple(found)


def _machine_name(item: ClassificationInput, machine_code: str | None) -> str | None:
    blob = _blob(item)
    match = _MACHINE_NAME.search(blob)
    if match:
        return match.group(1)
    if machine_code:
        return machine_code
    return None


def _class_from_path(item: ClassificationInput) -> tuple[EngineeringDocumentClass, str] | None:
    haystack = "/".join(part for part in (item.path_hint, item.filename) if part)
    lowered = haystack.lower()
    for pattern, document_class, reason in _PATH_RULES:
        if pattern.search(haystack):
            return document_class, reason
    if lowered.endswith(".scl"):
        return EngineeringDocumentClass.plc_scl, "suffix-scl"
    if lowered.endswith(".awl") or "/s5/" in lowered:
        return EngineeringDocumentClass.s5_text, "suffix-s5"
    if lowered.endswith(".xml") and ("plc/" in lowered or "simatic" in lowered):
        return EngineeringDocumentClass.plc_xml, "path-plc-xml"
    if lowered.endswith((".png", ".jpg", ".jpeg")):
        return EngineeringDocumentClass.photo, "suffix-image"
    if lowered.endswith(".zip") or (item.mime_type == "application/zip"):
        return EngineeringDocumentClass.package_container, "suffix-zip"
    return None


def classify(item: ClassificationInput) -> ClassificationDecision:
    """Classify one artifact from metadata. Identical input yields identical output."""
    reasons: list[str] = []
    identifiers = _extract_identifiers(item)
    path_hit = _class_from_path(item)
    adapter_class = _ADAPTER_CLASS.get(item.adapter_class or "")

    if path_hit is not None:
        document_class, reason = path_hit
        reasons.append(reason)
        confidence = 0.92
    elif adapter_class is not None:
        document_class = adapter_class
        reasons.append(f"adapter-{item.adapter_class}")
        confidence = 0.7
    else:
        document_class = EngineeringDocumentClass.unknown
        reasons.append("no-deterministic-match")
        confidence = 0.3

    if adapter_class is not None and path_hit is not None and adapter_class == document_class:
        reasons.append("adapter-agrees")
        confidence = min(0.97, confidence + 0.03)

    machine_code = next((value for value in identifiers if _MACHINE_CODE.fullmatch(value)), None)
    assembly_code = next((value for value in identifiers if value.startswith("zone-")), None)
    if document_class == EngineeringDocumentClass.unrelated:
        machine_code = None
        assembly_code = None
        reasons.append("unrelated-not-assigned")
        confidence = max(confidence, 0.9)

    state = AssignmentState.proposed
    if document_class == EngineeringDocumentClass.unknown or confidence < 0.6:
        state = AssignmentState.needs_review
        reasons.append("needs-review")

    return ClassificationDecision(
        document_class=document_class,
        machine_code=machine_code,
        machine_name=_machine_name(item, machine_code),
        assembly_code=assembly_code,
        confidence=confidence,
        reasons=tuple(reasons),
        identifiers=identifiers,
        state=state,
    )


def apply_package_identity(
    decisions: list[ClassificationDecision],
) -> list[ClassificationDecision]:
    """Share one consistent machine code across related files in a package."""
    codes = [
        item.machine_code for item in decisions if item.assign_to_machine and item.machine_code
    ]
    unique = list(dict.fromkeys(codes))
    if len(unique) != 1:
        return decisions
    shared = unique[0]
    name = next(
        (
            item.machine_name
            for item in decisions
            if item.machine_code == shared and item.machine_name
        ),
        shared,
    )
    updated: list[ClassificationDecision] = []
    for item in decisions:
        if not item.assign_to_machine:
            updated.append(item)
            continue
        if item.machine_code == shared:
            updated.append(item)
            continue
        updated.append(
            ClassificationDecision(
                document_class=item.document_class,
                machine_code=shared,
                machine_name=name,
                assembly_code=item.assembly_code,
                confidence=min(item.confidence, 0.88),
                reasons=(*item.reasons, "package-machine-identity"),
                identifiers=item.identifiers,
                state=item.state,
            )
        )
    return updated
