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
# Fixture-style PDF scan is linear, but retaining every Tj string can still
# amplify a valid-size member into hundreds of megabytes of Python objects.
MAX_PDF_STRINGS = 65_536
MAX_PDF_PAGES = 4_096
MAX_PDF_CHARS = 1_048_576
MAX_TEXT_PAGES = 4_096
MAX_TEXT_CHARS = 1_048_576
MAX_XLSX_XML_PART = 256 * 1024
_MAX_ROW_DIGITS = 5
_MAX_COLUMN_LETTERS = 3
_XML_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


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
    string_count = 0
    char_count = 0
    try:
        for raw in _iter_pdf_strings(content):
            string_count += 1
            if string_count > MAX_PDF_STRINGS:
                raise ValueError("pdf string count exceeds limit")
            text = (
                raw.replace(b"\\(", b"(")
                .replace(b"\\)", b")")
                .replace(b"\\\\", b"\\")
                .decode("latin-1")
            )
            char_count += len(text)
            if char_count > MAX_PDF_CHARS:
                raise ValueError("pdf text exceeds limit")
            if text.startswith("TITLE BLOCK") and current:
                pages.append(current)
                current = [text]
                if len(pages) + 1 > MAX_PDF_PAGES:
                    raise ValueError("pdf page count exceeds limit")
            else:
                current.append(text)
        if current:
            pages.append(current)
        if len(pages) > MAX_PDF_PAGES:
            raise ValueError("pdf page count exceeds limit")
    except (ValueError, MemoryError):
        return ()
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
    numbered: list[tuple[int, str]] = []
    start = 0
    char_count = 0
    for index in range(1, MAX_TEXT_PAGES + 1):
        separator = text.find("\f", start)
        if separator < 0:
            part = text[start:]
            start = len(text)
        else:
            part = text[start:separator]
            start = separator + 1
        char_count += len(part)
        if char_count > MAX_TEXT_CHARS:
            return ()
        if not part.strip():
            if start == len(text):
                break
            continue
        match = re.search(r"sheet=(\d+)/", part)
        page_number = int(match.group(1)) if match else index
        numbered.append((page_number, part))
        if start == len(text):
            break
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
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows: list[list[str]] = []
        allocated = 0
        for raw in reader:
            if not any(cell.strip() for cell in raw):
                continue
            if len(rows) >= MAX_SHEET_ROWS:
                raise ValueError("sheet row exceeds limit")
            if len(raw) > MAX_SHEET_COLUMNS:
                raise ValueError("sheet column exceeds limit")
            extra = len(raw)
            if allocated + extra > MAX_SHEET_CELLS:
                raise ValueError("sheet cell count exceeds limit")
            allocated += extra
            rows.append([cell.strip() for cell in raw])
    except (ValueError, MemoryError, csv.Error):
        return sheet_name, ()
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
    root = _parse_xml(workbook_xml)
    sheet = root.find(f".//{_S_NS}sheet")
    name = sheet.get("name") if sheet is not None else None
    relationship_id = sheet.get(f"{{{_DOC_REL_NS}}}id") if sheet is not None else None
    target = None
    if relationship_id:
        rels = _parse_xml(archive.read("xl/_rels/workbook.xml.rels"))
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
    root = _parse_xml(sst_xml)
    strings: list[str] = []
    for item in root.findall(f"{_S_NS}si"):
        strings.append("".join(node.text or "" for node in item.iter(f"{_S_NS}t")))
    return strings


def _shared_string_at(strings: list[str], raw: str) -> str:
    try:
        index = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("shared string index is invalid") from exc
    if index < 0 or index >= len(strings):
        raise ValueError("shared string index is invalid")
    return strings[index]


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
    root = _parse_xml(sheet_xml)
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
                values[column - 1] = _shared_string_at(strings, value.text)
            else:
                values[column - 1] = value.text
    return matrix


def _parse_xml(content: bytes) -> ElementTree.Element:
    if len(content) > MAX_XLSX_XML_PART or _XML_DECLARATION.search(content):
        raise ValueError("xlsx XML part is unsafe")
    return ElementTree.fromstring(content)


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
