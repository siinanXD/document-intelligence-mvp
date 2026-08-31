"""Parse evaluation fixtures into citable chunks.

Production ingest uses Docling. The golden corpus is authored with explicit
`##` sections (and TSV rows for spreadsheets) so CI can chunk without the
parsing extra, while still sending real PDF/DOCX/XLSX/Markdown bytes through
storage, `process_job`, indexing and retrieval.

Optional live runs may swap this for Docling; source-id mapping then still
goes through the `contains` judgments rather than assuming a chunk layout.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

from app.providers.parsing import DocumentParser, ParsedChunk, ParsedDocument, ParsingError

_HEADING = re.compile(r"(?m)^## ")
_PDF_STRING = re.compile(rb"\((?:\\.|[^\\)])*\) Tj")
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class EvaluationParser(DocumentParser):
    name = "evaluation"

    def supports(self, mime_type: str) -> bool:
        return True

    async def parse(self, *, filename: str, mime_type: str, content: bytes) -> ParsedDocument:
        try:
            text = _extract(filename, content)
        except Exception:
            raise ParsingError("evaluation parser failed to extract fixture text") from None
        chunks = _chunk(text)
        if not chunks:
            raise ParsingError("evaluation fixture produced no chunks")
        return ParsedDocument(
            text=text,
            chunks=chunks,
            serialized=text.encode("utf-8"),
            serialized_media_type="text/plain; charset=utf-8",
        )


def _extract(filename: str, content: bytes) -> str:
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        return _extract_pdf(content)
    if lowered.endswith(".docx"):
        return _extract_docx(content)
    if lowered.endswith(".xlsx"):
        return _extract_xlsx(content)
    if lowered.endswith(".html") or lowered.endswith(".htm"):
        return re.sub(r"<[^>]+>", "\n", content.decode("utf-8"))
    return content.decode("utf-8")


def _extract_pdf(content: bytes) -> str:
    lines = []
    for match in _PDF_STRING.finditer(content):
        raw = match.group(0)[1:-4]  # drop "(" and ") Tj"
        text = (
            raw.replace(b"\\(", b"(")
            .replace(b"\\)", b")")
            .replace(b"\\\\", b"\\")
            .decode("latin-1")
        )
        lines.append(text)
    return "\n".join(lines)


def _extract_docx(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    lines = []
    for paragraph in root.iter(f"{_W_NS}p"):
        pieces = [node.text or "" for node in paragraph.iter(f"{_W_NS}t")]
        lines.append("".join(pieces))
    return "\n".join(lines)


def _extract_xlsx(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sst_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        sheet_root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    strings = []
    for si in sst_root.findall(f"{_S_NS}si"):
        strings.append("".join(node.text or "" for node in si.iter(f"{_S_NS}t")))
    rows: list[list[str]] = []
    for row in sheet_root.iter(f"{_S_NS}row"):
        values = []
        for cell in row.findall(f"{_S_NS}c"):
            value = cell.find(f"{_S_NS}v")
            if value is None or value.text is None:
                values.append("")
                continue
            values.append(strings[int(value.text)])
        rows.append(values)
    if not rows:
        return ""
    header, *body = rows
    lines = ["## Parts list"]
    for row in body:
        pairs = [
            f"{header[index]} {row[index]}"
            for index in range(min(len(header), len(row)))
            if row[index]
        ]
        if pairs:
            lines.append("## " + (row[0] if row else "row"))
            lines.append("; ".join(pairs))
    return "\n".join(lines)


def _chunk(text: str) -> list[ParsedChunk]:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    parts = _HEADING.split(text)
    preamble = parts[0].strip()
    headings = _HEADING.findall(text)
    chunks: list[ParsedChunk] = []
    ordinal = 0
    if preamble:
        title = preamble.splitlines()[0].lstrip("# ").strip() or "Preamble"
        chunks.append(ParsedChunk(ordinal=ordinal, text=preamble, section_title=title[:120]))
        ordinal += 1
    for heading_token, body in zip(headings, parts[1:], strict=False):
        section = (heading_token.replace("##", "", 1) + body).strip()
        if not section:
            continue
        title = section.splitlines()[0].lstrip("# ").strip() or f"Section {ordinal}"
        chunks.append(ParsedChunk(ordinal=ordinal, text=section, section_title=title[:120]))
        ordinal += 1
    if chunks:
        return chunks
    return [ParsedChunk(ordinal=0, text=text, section_title=text.splitlines()[0][:120])]
