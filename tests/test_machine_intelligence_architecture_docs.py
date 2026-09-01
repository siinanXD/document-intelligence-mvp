"""SIN-88 architecture doc stays complete and classifies every adapter family."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = ROOT / "docs" / "MACHINE_INTELLIGENCE_ARCHITECTURE.md"
README = ROOT / "README.md"

REQUIRED_HEADINGS = (
    "Outcome in one page",
    "What was inspected",
    "Baseline validation",
    "Extension points in this codebase",
    "Parser and standard evaluation matrix",
    "Adapter family classification",
    "Exact TIA/S7 fixture format",
    "EPLAN native-versus-PDF strategy",
    "First vertical slice and acceptance fixture",
    "Deferred formats and features",
    "Canonical objects (SIN-89)",
    "Implementation order after this document merges",
    "Proprietary tooling and licensing",
    "Human decisions",
)

REQUIRED_CLASSIFICATIONS = (
    "supported in first slice",
    "supported via required export",
    "experimental",
    "deferred",
)

REQUIRED_FAMILIES = (
    "Container and manifest",
    "Siemens TIA / S7",
    "Legacy Siemens S5",
    "PLCopen",
    "PDF and document",
    "Spreadsheet",
    "Image and mobile-photo",
    "Electrical schematic",
    "Pneumatic",
)


def test_architecture_doc_covers_required_sections():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    for heading in REQUIRED_HEADINGS:
        assert heading in text, f"missing heading: {heading}"
    for label in REQUIRED_CLASSIFICATIONS:
        assert label in text, f"missing classification: {label}"
    for family in REQUIRED_FAMILIES:
        assert family in text, f"missing adapter family: {family}"
    assert "SimaticML" in text
    assert "SIN-89" in text
    assert "SIN-99" in text
    assert "SIN-100" in text
    assert "TIA Openness" in text
    assert ".S5D" in text
    assert "graph database" in text.lower()


def test_readme_points_at_machine_intelligence_architecture():
    text = README.read_text(encoding="utf-8")
    assert "docs/MACHINE_INTELLIGENCE_ARCHITECTURE.md" in text
