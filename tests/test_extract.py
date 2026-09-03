"""Deterministic XLSX/CSV parsers used by tabular adapters (SIN-91)."""

from __future__ import annotations

import io
import zipfile

from app.adapters.base import Artifact
from app.adapters.extract import (
    MAX_PDF_STRINGS,
    MAX_SHEET_COLUMNS,
    MAX_SHEET_ROWS,
    extract_pdf_pages,
    parse_table,
)
from app.adapters.stubs import TabularAdapter
from app.adapters.zip_unpack import MAX_COMPRESSION_RATIO
from app.evaluation.machine_intelligence.artifacts import binary_files

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _xlsx(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _workbook(sheet_name: str, relationship_id: str = "rId1") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{_NS}" xmlns:r="{_DOC_REL}">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="{relationship_id}"/>'
        "</sheets></workbook>"
    )


def _workbook_rels(target: str, relationship_id: str = "rId1") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_REL_NS}">'
        f'<Relationship Id="{relationship_id}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="{target}"/></Relationships>'
    )


def _a1_letters(column: int) -> str:
    letters: list[str] = []
    while column:
        column, remainder = divmod(column - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def _inline_sheet(rows: list[list[tuple[str, str]]]) -> str:
    rendered = []
    for cells in rows:
        body = "".join(
            f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>' for ref, value in cells
        )
        row_number = "".join(character for character in cells[0][0] if character.isdigit())
        rendered.append(f'<row r="{row_number}">{body}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{_NS}"><sheetData>{"".join(rendered)}</sheetData></worksheet>'
    )


def test_sparse_xlsx_cells_keep_their_columns():
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook("IO"),
            "xl/_rels/workbook.xml.rels": _workbook_rels("worksheets/sheet1.xml"),
            "xl/worksheets/sheet1.xml": _inline_sheet(
                [
                    [("A1", "name"), ("B1", "address"), ("C1", "comment")],
                    [("A2", "CV01.PEInfeed"), ("C2", "PE infeed")],
                ]
            ),
        }
    )
    sheet_name, rows = parse_table(content, "io_list.xlsx")
    assert sheet_name == "IO"
    assert rows[0].cells == {
        "name": "CV01.PEInfeed",
        "address": "",
        "comment": "PE infeed",
    }


def test_xlsx_without_shared_strings_uses_inline_values_and_rels():
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook("Signals"),
            "xl/_rels/workbook.xml.rels": _workbook_rels("worksheets/data.xml"),
            "xl/worksheets/data.xml": _inline_sheet(
                [
                    [("A1", "name"), ("B1", "address")],
                    [("A2", "CV01.RunCmd"), ("B2", "%Q0.0")],
                ]
            ),
        }
    )
    sheet_name, rows = parse_table(content, "signals.xlsx")
    assert sheet_name == "Signals"
    assert rows[0].cells == {"name": "CV01.RunCmd", "address": "%Q0.0"}


def test_sparse_sheet_rejects_huge_a1_row():
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook("IO"),
            "xl/_rels/workbook.xml.rels": _workbook_rels("worksheets/sheet1.xml"),
            "xl/worksheets/sheet1.xml": _inline_sheet(
                [
                    [("A1", "name")],
                    [(f"A{MAX_SHEET_ROWS + 1}", "bomb")],
                ]
            ),
        }
    )
    sheet_name, rows = parse_table(content, "sparse-row.xlsx")
    assert sheet_name == "unknown"
    assert rows == ()


def test_sparse_sheet_rejects_huge_a1_column():
    wide = "A" * 4
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook("IO"),
            "xl/_rels/workbook.xml.rels": _workbook_rels("worksheets/sheet1.xml"),
            "xl/worksheets/sheet1.xml": _inline_sheet(
                [
                    [("A1", "name"), (f"{wide}1", "wide")],
                    [("A2", "CV01.PEInfeed")],
                ]
            ),
        }
    )
    sheet_name, rows = parse_table(content, "sparse-col.xlsx")
    assert sheet_name == "unknown"
    assert rows == ()


def test_sparse_sheet_rejects_declared_column_past_cap():
    over = _a1_letters(MAX_SHEET_COLUMNS + 1)
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook("IO"),
            "xl/_rels/workbook.xml.rels": _workbook_rels("worksheets/sheet1.xml"),
            "xl/worksheets/sheet1.xml": _inline_sheet(
                [
                    [("A1", "name"), (f"{over}1", "wide")],
                    [("A2", "CV01.PEInfeed")],
                ]
            ),
        }
    )
    sheet_name, rows = parse_table(content, "wide-col.xlsx")
    assert sheet_name == "unknown"
    assert rows == ()


def test_nested_xlsx_zip_bomb_is_not_expanded():
    zeros = b"\x00" * (256 * (MAX_COMPRESSION_RATIO + 1))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", zeros)
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    sheet_name, rows = parse_table(buffer.getvalue(), "bomb.xlsx")
    assert sheet_name == "unknown"
    assert rows == ()


def test_reference_xlsx_still_parses_after_zip_limits():
    files = binary_files()
    sheet_name, rows = parse_table(files["io_list.xlsx"], "io_list.xlsx")
    assert sheet_name == "IO"
    assert any(row.cells.get("name") == "CV01.PEInfeed" for row in rows)


def test_tabular_observations_omit_cell_values():
    files = binary_files()
    result = TabularAdapter().extract(
        Artifact(
            filename="io_list.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=files["io_list.xlsx"],
        )
    )
    row_payloads = [item.payload for item in result.observations if item.kind == "tabular_row"]
    assert row_payloads
    assert all("cells" not in payload for payload in row_payloads)
    assert all("row_number" in payload for payload in row_payloads)


def test_pdf_string_scan_handles_many_unmatched_openers():
    malicious = b"(" * 20_000
    assert extract_pdf_pages(malicious) == ()


def test_pdf_string_scan_caps_accumulated_output():
    payload = b"".join(b"(TITLE BLOCK) Tj" for _ in range(MAX_PDF_STRINGS + 1))
    assert extract_pdf_pages(payload) == ()


def test_delimited_table_rejects_too_many_rows():
    header = "tag,kind,qty,revision,power_kw"
    rows = [header, *[f"CV01-M{index},motor,1,A,1" for index in range(MAX_SHEET_ROWS)]]
    sheet_name, parsed = parse_table("\n".join(rows).encode(), "bom.csv")
    assert sheet_name == "bom"
    assert parsed == ()


def test_xlsx_out_of_range_shared_string_is_empty_parse():
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook("IO"),
            "xl/_rels/workbook.xml.rels": _workbook_rels("worksheets/sheet1.xml"),
            "xl/sharedStrings.xml": (
                f'<?xml version="1.0"?><sst xmlns="{_NS}"><si><t>name</t></si></sst>'
            ),
            "xl/worksheets/sheet1.xml": (
                f'<?xml version="1.0"?><worksheet xmlns="{_NS}"><sheetData>'
                '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                '<row r="2"><c r="A2" t="s"><v>99</v></c></row>'
                "</sheetData></worksheet>"
            ),
        }
    )
    sheet_name, rows = parse_table(content, "io_list.xlsx")
    assert sheet_name == "unknown"
    assert rows == ()


def test_xlsx_negative_shared_string_index_is_empty_parse():
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook("IO"),
            "xl/_rels/workbook.xml.rels": _workbook_rels("worksheets/sheet1.xml"),
            "xl/sharedStrings.xml": (
                f'<?xml version="1.0"?><sst xmlns="{_NS}">'
                "<si><t>name</t></si><si><t>other</t></si></sst>"
            ),
            "xl/worksheets/sheet1.xml": (
                f'<?xml version="1.0"?><worksheet xmlns="{_NS}"><sheetData>'
                '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                '<row r="2"><c r="A2" t="s"><v>-1</v></c></row>'
                "</sheetData></worksheet>"
            ),
        }
    )
    sheet_name, rows = parse_table(content, "io_list.xlsx")
    assert sheet_name == "unknown"
    assert rows == ()
