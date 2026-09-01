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
    "Family classification table",
    "Exact TIA/S7 fixture format",
    "SimaticML dialect conformance",
    "EPLAN native-versus-PDF strategy",
    "First vertical slice and acceptance fixture",
    "Deferred formats and features",
    "Canonical objects (SIN-89)",
    "Implementation order after this document merges",
    "Proprietary tooling and licensing",
    "Human decisions",
)

# Each adapter family has exactly one classification. Nested notes (OCR, S5
# binary) must not appear here as a second class for the same family.
FAMILY_CLASSIFICATIONS = (
    ("A", "Container and manifest", "supported in first slice"),
    ("B", "Siemens TIA / S7", "supported via required export"),
    ("C", "Legacy Siemens S5 / STEP 5", "supported via required export"),
    ("D", "Generic IEC 61131-3 / PLCopen XML", "experimental"),
    ("E", "PDF and document", "supported in first slice"),
    ("F", "Spreadsheet and tabular", "supported in first slice"),
    ("G", "Image and mobile-photo", "supported in first slice"),
    ("H", "Electrical schematic", "supported via required export"),
    ("I", "Pneumatic / hydraulic", "deferred"),
)

ALLOWED_CLASSIFICATIONS = {row[2] for row in FAMILY_CLASSIFICATIONS}


def _classification_table_rows(text: str) -> list[tuple[str, str, str]]:
    marker = "### Family classification table"
    start = text.index(marker)
    rest = text[start:]
    end = rest.index("\n### A.")
    table = rest[:end]
    rows: list[tuple[str, str, str]] = []
    for line in table.splitlines():
        if not line.startswith("| ") or line.startswith("| Family id") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 3:
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


def test_architecture_doc_covers_required_sections():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    for heading in REQUIRED_HEADINGS:
        assert heading in text, f"missing heading: {heading}"
    assert "SimaticML" in text
    assert "SIN-89" in text
    assert "SIN-99" in text
    assert "SIN-100" in text
    assert "TIA Openness" in text
    assert ".S5D" in text
    assert "graph database" in text.lower()
    assert "conformance sample" in text.lower()


def test_each_adapter_family_has_exactly_one_classification():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    rows = _classification_table_rows(text)
    assert rows == list(FAMILY_CLASSIFICATIONS)
    ids = [row[0] for row in rows]
    families = [row[1] for row in rows]
    assert len(ids) == len(set(ids))
    assert len(families) == len(set(families))
    assert {row[2] for row in rows} <= ALLOWED_CLASSIFICATIONS


def test_simaticml_conformance_is_required_for_tia_compatibility():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "conformance sample (required)" in lowered
    assert "synthetic package (required)" in lowered
    assert "do not install tia" in lowered or "does not install tia" in lowered
    assert "stop before sin-93" in lowered


def test_readme_points_at_machine_intelligence_architecture():
    text = README.read_text(encoding="utf-8")
    assert "docs/MACHINE_INTELLIGENCE_ARCHITECTURE.md" in text
