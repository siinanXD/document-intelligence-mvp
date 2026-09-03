"""Deterministic parsers for the first supported engineering formats (SIN-91).

Adapters call these to emit observations. They do not write canonical rows.
No vendor SDK: XLSX is read as OOXML with the standard library.
"""

from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

from app.adapters.zip_unpack import assert_zip_directory_within_limits

_S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# Dense-grid caps. A1 references are attacker-controlled; expanding to the
# declared row/column without a bound can OOM the shared ingestion worker.
MAX_SHEET_ROWS = 4_096
MAX_SHEET_COLUMNS = 64
MAX_SHEET_CELLS = 65_536
_MAX_ROW_DIGITS = 5
_MAX_COLUMN_LETTERS = 3


@dataclass(frozen=True)
class TableRow:
    sheet_name: str
    row_number: int
    cell_range: str
    cells: dict[str, str]


def parse_table(content: bytes, filename: str) -> tuple[str, tuple[TableRow, ...]]:
    """Return (sheet_name, data rows). Header row is consumed, not returned."""
    lowered = filename.lower()
    if lowered.endswith(".xlsx"):
        return _parse_xlsx(content)
    if lowered.endswith(".tsv") or lowered.endswith(".csv"):
        delimiter = "\t" if lowered.endswith(".tsv") else ","
        return _parse_delimited(content, delimiter=delimiter, sheet_name=_sheet_from_name(filename))
    return "unknown", ()


def _iter_pdf_strings(content: bytes) -> Iterator[bytes]:
    """Yield fixture-style PDF Tj strings in one bounded linear scan."""
    start: int | None = None
    latest_open: int | None = None
    index = 0
    while index < len(content):
        value = content[index]
        if start is None:
            if value == ord("("):
                start = latest_open = index
            index += 1
            continue
        if value == ord("\\"):
            index += 2
            continue
        if value == ord("("):
            latest_open = index
        elif value == ord(")"):
            if content[index + 1 : index + 4] == b" Tj":
                yield content[start + 1 : index]
                start = latest_open = None
                index += 4
                continue
            start = latest_open if latest_open is not None and latest_open > start else None
            latest_open = start
        index += 1


def extract_pdf_pages(content: bytes) -> tuple[tuple[int, str], ...]:
    """Page-ordered text from the fixture-style PDF string operators."""
    pages: list[list[str]] = []
    current: list[str] = []
    for raw in _iter_pdf_strings(content):
        text = (
            raw.replace(b"\\(", b"(")
            .replace(b"\\)", b")")
            .replace(b"\\\\", b"\\")
            .decode("latin-1")
        )
        if text.startswith("TITLE BLOCK") and current:
            pages.append(current)
            current = [text]
        else:
            current.append(text)
    if current:
        pages.append(current)
    numbered: list[tuple[int, str]] = []
    for index, lines in enumerate(pages, start=1):
        blob = "\n".join(lines)
        match = re.search(r"sheet=(\d+)/", blob)
        page_number = int(match.group(1)) if match else index
        numbered.append((page_number, blob))
    return tuple(numbered)


def extract_text_pages(content: bytes) -> tuple[tuple[int, str], ...]:
    """Form-feed separated pages, or a single page for plain text."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return ()
    parts = re.split(r"\f", text)
    numbered: list[tuple[int, str]] = []
    for index, part in enumerate(parts, start=1):
        if not part.strip():
            continue
        match = re.search(r"sheet=(\d+)/", part)
        page_number = int(match.group(1)) if match else index
        numbered.append((page_number, part))
    return tuple(numbered)


def _sheet_from_name(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem or "Sheet1"


def _parse_delimited(
    content: bytes, *, delimiter: str, sheet_name: str
) -> tuple[str, tuple[TableRow, ...]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return sheet_name, ()
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
    return sheet_name, _rows_from_matrix(rows, sheet_name)


def _parse_xlsx(content: bytes) -> tuple[str, tuple[TableRow, ...]]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert_zip_directory_within_limits(archive)
            workbook = archive.read("xl/workbook.xml")
            sheet_name, sheet_path = _workbook_sheet(archive, workbook)
            strings = (
                _shared_strings(archive.read("xl/sharedStrings.xml"))
                if "xl/sharedStrings.xml" in archive.namelist()
                else []
            )
            matrix = _sheet_matrix(archive.read(sheet_path), strings)
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, ValueError, MemoryError):
        return "unknown", ()
    return sheet_name, _rows_from_matrix(matrix, sheet_name)


def _workbook_sheet(archive: zipfile.ZipFile, workbook_xml: bytes) -> tuple[str, str]:
    root = ElementTree.fromstring(workbook_xml)
    sheet = root.find(f".//{_S_NS}sheet")
    name = sheet.get("name") if sheet is not None else None
    relationship_id = sheet.get(f"{{{_DOC_REL_NS}}}id") if sheet is not None else None
    target = None
    if relationship_id:
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for relationship in rels.findall(f"{_REL_NS}Relationship"):
            if relationship.get("Id") == relationship_id:
                target = relationship.get("Target")
                break
    if not target:
        target = "worksheets/sheet1.xml"
    sheet_path = posixpath.normpath(posixpath.join("xl", target.lstrip("/")))
    if sheet_path not in archive.namelist():
        raise KeyError(sheet_path)
    return name or "Sheet1", sheet_path


def _shared_strings(sst_xml: bytes) -> list[str]:
    root = ElementTree.fromstring(sst_xml)
    strings: list[str] = []
    for item in root.findall(f"{_S_NS}si"):
        strings.append("".join(node.text or "" for node in item.iter(f"{_S_NS}t")))
    return strings


def _a1_column(reference: str) -> int:
    column_match = re.match(r"([A-Z]+)", reference)
    if not column_match:
        return 0
    letters = column_match.group(1)
    if len(letters) > _MAX_COLUMN_LETTERS:
        raise ValueError("sheet column exceeds limit")
    column = 0
    for character in letters:
        column = column * 26 + ord(character) - ord("A") + 1
    if column > MAX_SHEET_COLUMNS:
        raise ValueError("sheet column exceeds limit")
    return column


def _sheet_row_number(raw: str | None, fallback: int) -> int:
    if raw is None:
        number = fallback
    else:
        if not raw.isdigit() or len(raw) > _MAX_ROW_DIGITS:
            raise ValueError("sheet row exceeds limit")
        number = int(raw)
    if number < 1 or number > MAX_SHEET_ROWS:
        raise ValueError("sheet row exceeds limit")
    return number


def _sheet_matrix(sheet_xml: bytes, strings: list[str]) -> list[list[str]]:
    root = ElementTree.fromstring(sheet_xml)
    matrix: list[list[str]] = []
    allocated = 0
    for row in root.iter(f"{_S_NS}row"):
        row_number = _sheet_row_number(row.get("r"), len(matrix) + 1)
        while len(matrix) < row_number:
            matrix.append([])
        values = matrix[row_number - 1]
        for cell in row.findall(f"{_S_NS}c"):
            column = _a1_column(cell.get("r", ""))
            if not column:
                column = len(values) + 1
            if column > MAX_SHEET_COLUMNS:
                raise ValueError("sheet column exceeds limit")
            extra = max(0, column - len(values))
            if allocated + extra > MAX_SHEET_CELLS:
                raise ValueError("sheet cell count exceeds limit")
            while len(values) < column:
                values.append("")
            allocated += extra
            value = cell.find(f"{_S_NS}v")
            if cell.get("t") == "inlineStr":
                values[column - 1] = "".join(node.text or "" for node in cell.iter(f"{_S_NS}t"))
            elif value is None or value.text is None:
                values[column - 1] = ""
            elif cell.get("t") == "s":
                values[column - 1] = strings[int(value.text)]
            else:
                values[column - 1] = value.text
    return matrix


def _rows_from_matrix(matrix: list[list[str]], sheet_name: str) -> tuple[TableRow, ...]:
    if len(matrix) < 2:
        return ()
    header = [cell.strip() for cell in matrix[0]]
    if not any(header):
        return ()
    rows: list[TableRow] = []
    for offset, raw in enumerate(matrix[1:], start=2):
        cells: dict[str, str] = {}
        for index, key in enumerate(header):
            if not key:
                continue
            cells[key] = raw[index].strip() if index < len(raw) else ""
        if not any(cells.values()):
            continue
        rows.append(
            TableRow(
                sheet_name=sheet_name,
                row_number=offset,
                cell_range=f"A{offset}",
                cells=cells,
            )
        )
    return tuple(rows)


def cell_evidence(
    *,
    sheet_name: str,
    cell_range: str,
    path_hint: str,
    filename: str,
) -> dict[str, Any]:
    return {
        "locator_kind": "sheet_cell",
        "sheet_name": sheet_name,
        "cell_range": cell_range,
        "path_hint": path_hint,
        "artifact": filename,
    }
