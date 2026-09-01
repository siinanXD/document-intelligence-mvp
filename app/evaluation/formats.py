"""Build the synthetic evaluation files from checked-in text.

The golden corpus is authored as UTF-8 so it can be reviewed. Ingest still
uses real PDF, DOCX, XLSX and Markdown bytes, produced here without extra
dependencies.
"""

from __future__ import annotations

import csv
import io
import zipfile
from xml.sax.saxutils import escape

from app.services.uploads import SUPPORTED_TYPES


def build_file(fmt: str, source: str, *, sheet_name: str = "BOM") -> tuple[bytes, str]:
    """Return (bytes, canonical mime type) for one corpus document."""
    if fmt == "md":
        return source.encode("utf-8"), SUPPORTED_TYPES[".md"][0]
    if fmt == "txt":
        return source.encode("utf-8"), SUPPORTED_TYPES[".txt"][0]
    if fmt == "html":
        return _html(source), SUPPORTED_TYPES[".html"][0]
    if fmt == "pdf":
        return _pdf(source), SUPPORTED_TYPES[".pdf"][0]
    if fmt == "docx":
        return _docx(source), SUPPORTED_TYPES[".docx"][0]
    if fmt == "xlsx":
        return _xlsx(source, sheet_name=sheet_name), SUPPORTED_TYPES[".xlsx"][0]
    raise ValueError(f"unsupported evaluation format: {fmt}")


def _html(source: str) -> bytes:
    body = escape(source).replace("\n", "<br/>\n")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/></head>"
        f"<body><pre>{body}</pre></body></html>"
    ).encode()


def _pdf_escape(line: str) -> str:
    latin = line.encode("latin-1", "replace").decode("latin-1")
    return latin.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(source: str) -> bytes:
    lines = source.splitlines() or [""]
    operations = ["BT", "/F1 11 Tf", "50 780 Td"]
    y_steps = 0
    for index, line in enumerate(lines):
        if y_steps >= 52:
            operations.extend(["ET", "BT", "/F1 11 Tf", "50 780 Td"])
            y_steps = 0
        elif index:
            operations.append("0 -14 Td")
        operations.append(f"({_pdf_escape(line[:110])}) Tj")
        y_steps += 1
    operations.append("ET")
    stream = "\n".join(operations).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pieces = [b"%PDF-1.4\n"]
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(sum(len(piece) for piece in pieces))
        pieces.append(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = sum(len(piece) for piece in pieces)
    xref = [b"xref\n", f"0 {len(objects) + 1}\n".encode(), b"0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref.append(f"{offset:010d} 00000 n \n".encode())
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return b"".join(pieces + xref + [trailer])


def _docx(source: str) -> bytes:
    paragraphs = []
    for line in source.splitlines() or [""]:
        paragraphs.append(f'<w:p><w:r><w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>')
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(paragraphs) + "</w:body></w:document>"
    )
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _xlsx(source: str, *, sheet_name: str = "BOM") -> bytes:
    rows = list(csv.reader(io.StringIO(source), delimiter="\t"))
    if not rows:
        rows = [["empty"]]
    strings: list[str] = []
    index_of: dict[str, int] = {}

    def intern(value: str) -> int:
        if value not in index_of:
            index_of[value] = len(strings)
            strings.append(value)
        return index_of[value]

    sheet_rows = []
    for r_index, row in enumerate(rows, start=1):
        cells = []
        for c_index, value in enumerate(row):
            ref = f"{chr(ord('A') + c_index)}{r_index}"
            cells.append(f'<c r="{ref}" t="s"><v>{intern(value)}</v></c>')
        sheet_rows.append(f'<row r="{r_index}">{"".join(cells)}</row>')
    shared = "".join(f'<si><t xml:space="preserve">{escape(item)}</t></si>' for item in strings)
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">{shared}</sst>'
    )
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", shared_xml)
    return buffer.getvalue()
