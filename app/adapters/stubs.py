"""Capability stubs. Full domain parsers belong to SIN-90 through SIN-96."""

from __future__ import annotations

from xml.etree import ElementTree

from app.adapters.base import (
    ADAPTER_VERSION,
    AdapterCapabilities,
    Artifact,
    Detection,
    EngineeringAdapter,
    ExtractResult,
    Observation,
    ValidationReport,
)
from app.adapters.extract import extract_pdf_pages, extract_text_pages, parse_table

_STUB_LIMIT = "full domain parser is not in SIN-100"


def _detection(name: str, family: str, score: float, *reasons: str) -> Detection:
    return Detection(
        score=score,
        family=family,
        reasons=reasons,
        adapter_name=name,
        adapter_version=ADAPTER_VERSION,
    )


def _stub_extract(adapter_name: str, document_class: str, artifact: Artifact) -> ExtractResult:
    observations = (
        Observation(
            kind="document_class",
            payload={"document_class": document_class, "adapter": adapter_name},
            evidence=_evidence(artifact),
            confidence=1.0 if artifact.path_hint is None else 0.9,
        ),
        Observation(
            kind="unsupported",
            payload={"construct": _STUB_LIMIT, "adapter": adapter_name},
            evidence=_evidence(artifact),
            confidence=1.0,
        ),
    )
    return ExtractResult(observations=observations, warnings=(_STUB_LIMIT,))


def _evidence(artifact: Artifact) -> dict:
    evidence = {"locator_kind": "native_id", "artifact": artifact.filename}
    if artifact.path_hint:
        evidence["path_hint"] = artifact.path_hint
        evidence["locator_kind"] = "native_id"
    suffix = artifact.filename.rsplit(".", 1)[-1].lower() if "." in artifact.filename else ""
    if suffix in {"scl", "awl", "csv", "tsv", "md", "txt", "xml"}:
        evidence["locator_kind"] = "line_range"
        evidence["line_start"] = 1
    elif suffix == "pdf":
        evidence["locator_kind"] = "page"
        evidence["page_number"] = 1
    elif suffix in {"xlsx", "csv", "tsv"}:
        evidence["locator_kind"] = "sheet_cell"
        evidence["sheet_name"] = "unknown"
        evidence["cell_range"] = "A1"
    elif suffix in {"png", "jpg", "jpeg"}:
        evidence["locator_kind"] = "image_region"
        evidence["region"] = {"x": 0, "y": 0, "w": 1, "h": 1}
    return evidence


class ContainerAdapter(EngineeringAdapter):
    @property
    def name(self) -> str:
        return "container"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            formats=("zip",),
            families=("A",),
            constructs=("package_member", "path_hint"),
            limitations=("nested zip is not unpacked", "folder upload is deferred"),
        )

    def detect(self, artifact: Artifact) -> Detection:
        if artifact.mime_type == "application/zip" or artifact.content.startswith(b"PK\x03\x04"):
            if artifact.filename.lower().endswith((".xlsx", ".docx", ".pptx")):
                return _detection(self.name, "A", 0.0, "ooxml-container")
            return _detection(self.name, "A", 1.0, "zip-magic")
        return _detection(self.name, "A", 0.0, "not-zip")

    def extract(self, artifact: Artifact) -> ExtractResult:
        return ExtractResult(
            observations=(
                Observation(
                    kind="document_class",
                    payload={"document_class": "package_container", "adapter": self.name},
                    evidence={"locator_kind": "native_id", "artifact": artifact.filename},
                    confidence=1.0,
                ),
            )
        )

    def validate(self, artifact: Artifact, result: ExtractResult) -> ValidationReport:
        if not artifact.content.startswith(b"PK"):
            return ValidationReport(ok=False, errors=("not a zip archive",))
        return ValidationReport(ok=True)


class SimaticMLAdapter(EngineeringAdapter):
    @property
    def name(self) -> str:
        return "simaticml"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            formats=("xml",),
            families=("B",),
            constructs=("Document", "Engineering", "SW.Blocks.FB", "SW.Blocks.OB"),
            limitations=("not a TIA export parser", _STUB_LIMIT),
        )

    def detect(self, artifact: Artifact) -> Detection:
        if artifact.mime_type not in {"application/xml", "text/xml"} and not (
            artifact.filename.lower().endswith(".xml")
        ):
            return _detection(self.name, "B", 0.0, "not-xml")
        head = artifact.content[:2000]
        if b"SW.Blocks." in head or b"<Engineering" in head:
            return _detection(self.name, "B", 0.95, "simaticml-markers")
        if head.lstrip().startswith(b"<") or head.lstrip().startswith(b"<?xml"):
            return _detection(self.name, "B", 0.2, "xml")
        return _detection(self.name, "B", 0.0, "not-xml")

    def extract(self, artifact: Artifact) -> ExtractResult:
        return _stub_extract(self.name, "plc_xml", artifact)

    def validate(self, artifact: Artifact, result: ExtractResult) -> ValidationReport:
        try:
            ElementTree.fromstring(artifact.content)
        except ElementTree.ParseError:
            return ValidationReport(ok=False, errors=("xml is not well-formed",))
        return ValidationReport(ok=True)


class SclAdapter(EngineeringAdapter):
    @property
    def name(self) -> str:
        return "scl"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            formats=("scl",),
            families=("B",),
            constructs=("FUNCTION_BLOCK", "ORGANIZATION_BLOCK", "FUNCTION"),
            limitations=(_STUB_LIMIT,),
        )

    def detect(self, artifact: Artifact) -> Detection:
        name = artifact.filename.lower()
        if name.endswith(".scl"):
            return _detection(self.name, "B", 0.9, "suffix")
        return _detection(self.name, "B", 0.0, "not-scl")

    def extract(self, artifact: Artifact) -> ExtractResult:
        return _stub_extract(self.name, "plc_scl", artifact)

    def validate(self, artifact: Artifact, result: ExtractResult) -> ValidationReport:
        return ValidationReport(ok=True)


class S5Adapter(EngineeringAdapter):
    @property
    def name(self) -> str:
        return "s5-text"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            formats=("awl", "stl"),
            families=("C",),
            constructs=("instruction-list",),
            limitations=("binary .S5D is deferred", _STUB_LIMIT),
        )

    def detect(self, artifact: Artifact) -> Detection:
        name = artifact.filename.lower()
        if name.endswith((".awl", ".stl")):
            return _detection(self.name, "C", 0.9, "suffix")
        return _detection(self.name, "C", 0.0, "not-s5-text")

    def extract(self, artifact: Artifact) -> ExtractResult:
        return _stub_extract(self.name, "s5_text", artifact)

    def validate(self, artifact: Artifact, result: ExtractResult) -> ValidationReport:
        return ValidationReport(ok=True)


class TabularAdapter(EngineeringAdapter):
    @property
    def name(self) -> str:
        return "tabular"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            formats=("xlsx", "csv", "tsv"),
            families=("F",),
            constructs=("sheet", "row", "cell"),
            limitations=(_STUB_LIMIT,),
        )

    def detect(self, artifact: Artifact) -> Detection:
        name = artifact.filename.lower()
        if name.endswith(".xlsx") or artifact.mime_type.endswith("spreadsheetml.sheet"):
            return _detection(self.name, "F", 0.9, "xlsx")
        if name.endswith(".csv") or artifact.mime_type == "text/csv":
            return _detection(self.name, "F", 0.9, "csv")
        if name.endswith(".tsv") or artifact.mime_type == "text/tab-separated-values":
            return _detection(self.name, "F", 0.9, "tsv")
        return _detection(self.name, "F", 0.0, "not-tabular")

    def extract(self, artifact: Artifact) -> ExtractResult:
        sheet_name, rows = parse_table(artifact.content, artifact.filename)
        observations = [
            Observation(
                kind="document_class",
                payload={"document_class": "tabular", "adapter": self.name},
                evidence=_evidence(artifact),
                confidence=0.9,
            )
        ]
        for row in rows:
            observations.append(
                Observation(
                    kind="tabular_row",
                    payload={
                        "sheet_name": sheet_name,
                        "row_number": row.row_number,
                    },
                    evidence={
                        "locator_kind": "sheet_cell",
                        "sheet_name": sheet_name,
                        "cell_range": row.cell_range,
                        "artifact": artifact.filename,
                        **({"path_hint": artifact.path_hint} if artifact.path_hint else {}),
                    },
                    confidence=1.0,
                )
            )
        warnings: tuple[str, ...] = ()
        if not rows:
            observations.append(
                Observation(
                    kind="unsupported",
                    payload={"construct": _STUB_LIMIT, "adapter": self.name},
                    evidence=_evidence(artifact),
                    confidence=1.0,
                )
            )
            warnings = (_STUB_LIMIT,)
        return ExtractResult(observations=tuple(observations), warnings=warnings)

    def validate(self, artifact: Artifact, result: ExtractResult) -> ValidationReport:
        return ValidationReport(ok=True)


class ImageAdapter(EngineeringAdapter):
    @property
    def name(self) -> str:
        return "image"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            formats=("png", "jpeg"),
            families=("G",),
            constructs=("image_region",),
            limitations=("OCR is experimental", _STUB_LIMIT),
        )

    def detect(self, artifact: Artifact) -> Detection:
        if artifact.mime_type in {"image/png", "image/jpeg"} or artifact.filename.lower().endswith(
            (".png", ".jpg", ".jpeg")
        ):
            return _detection(self.name, "G", 0.9, "raster")
        return _detection(self.name, "G", 0.0, "not-image")

    def extract(self, artifact: Artifact) -> ExtractResult:
        return _stub_extract(self.name, "photo", artifact)

    def validate(self, artifact: Artifact, result: ExtractResult) -> ValidationReport:
        return ValidationReport(ok=True)


class DoclingDocumentAdapter(EngineeringAdapter):
    """Marks PDF/Office as Docling's search path. Does not parse PLC."""

    @property
    def name(self) -> str:
        return "docling-document"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            formats=("pdf", "docx", "pptx", "html", "md", "txt"),
            families=("E", "H"),
            constructs=("page", "section"),
            limitations=("Docling does not parse PLC XML or native EPLAN",),
        )

    def detect(self, artifact: Artifact) -> Detection:
        mime = artifact.mime_type
        if mime in {"application/pdf", "text/html", "text/markdown", "text/plain"} or mime.endswith(
            ("wordprocessingml.document", "presentationml.presentation")
        ):
            return _detection(self.name, "E", 0.8, "document-parser")
        return _detection(self.name, "E", 0.0, "not-document")

    def extract(self, artifact: Artifact) -> ExtractResult:
        observations = [
            Observation(
                kind="document_class",
                payload={"document_class": "prose_document", "adapter": self.name},
                evidence=_evidence(artifact),
                confidence=0.8,
            )
        ]
        pages = ()
        lowered = artifact.filename.lower()
        if lowered.endswith(".pdf"):
            pages = extract_pdf_pages(artifact.content)
        elif lowered.endswith((".md", ".txt")):
            pages = extract_text_pages(artifact.content)
        for page_number, text in pages:
            observations.append(
                Observation(
                    kind="text_page",
                    payload={"page_number": page_number, "char_count": len(text)},
                    evidence=_evidence(artifact)
                    if not lowered.endswith(".pdf")
                    else {
                        "locator_kind": "page",
                        "page_number": page_number,
                        "artifact": artifact.filename,
                        **({"path_hint": artifact.path_hint} if artifact.path_hint else {}),
                    },
                    confidence=0.8,
                )
            )
        return ExtractResult(observations=tuple(observations))

    def validate(self, artifact: Artifact, result: ExtractResult) -> ValidationReport:
        return ValidationReport(ok=True)


def default_adapters() -> tuple[EngineeringAdapter, ...]:
    return (
        ContainerAdapter(),
        SimaticMLAdapter(),
        SclAdapter(),
        S5Adapter(),
        TabularAdapter(),
        ImageAdapter(),
        DoclingDocumentAdapter(),
    )
