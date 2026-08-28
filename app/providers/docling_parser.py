"""Docling implementation of the parser interface.

Docling is imported lazily inside the constructor, so the rest of the
application - and most of the test suite - runs without its dependency tree.

Two configuration choices deserve naming:

* OCR is off by default. It is the expensive path, it pulls further models, and
  scanned documents are not in the MVP's scope. A scanned PDF therefore parses
  to little or no text rather than silently costing minutes per page.
* `artifacts_path` points at pre-fetched model weights. Left unset, Docling
  downloads them on first use - hundreds of megabytes, inside the first request
  that happens to be a PDF, on a container that may be replaced an hour later.
  Deployments set it and bake the models into the image.
"""

import asyncio
import logging
from typing import Any

from app.providers.parsing import (
    DocumentParser,
    ParsedChunk,
    ParsedDocument,
    ParsingError,
    UnsupportedFormat,
)

logger = logging.getLogger(__name__)

SUPPORTED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/html",
        "text/markdown",
        "text/plain",
    }
)

_SUFFIX_FOR_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/html": ".html",
    "text/markdown": ".md",
    "text/plain": ".txt",
}


class DoclingParser(DocumentParser):
    def __init__(
        self,
        *,
        artifacts_path: str | None = None,
        do_ocr: bool = False,
        do_table_structure: bool = True,
        converter: Any | None = None,
        chunker: Any | None = None,
    ) -> None:
        self._converter = converter or self._build_converter(
            artifacts_path=artifacts_path,
            do_ocr=do_ocr,
            do_table_structure=do_table_structure,
        )
        self._chunker = chunker or self._build_chunker()

    @staticmethod
    def _build_converter(*, artifacts_path, do_ocr, do_table_structure):
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions()
        options.do_ocr = do_ocr
        options.do_table_structure = do_table_structure
        if artifacts_path:
            options.artifacts_path = artifacts_path

        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )

    @staticmethod
    def _build_chunker():
        # HybridChunker is Docling's own; we deliberately do not invent a
        # semantic chunker of our own on top of it.
        from docling.chunking import HybridChunker

        return HybridChunker()

    @property
    def name(self) -> str:
        return "docling"

    def supports(self, mime_type: str) -> bool:
        return mime_type in SUPPORTED_MIME_TYPES

    async def parse(self, *, filename: str, mime_type: str, content: bytes) -> ParsedDocument:
        if not self.supports(mime_type):
            raise UnsupportedFormat(f"docling does not handle {mime_type}")

        # Docling is synchronous and CPU-bound; keep it off the event loop.
        return await asyncio.to_thread(self._parse_sync, filename, mime_type, content)

    def _parse_sync(self, filename: str, mime_type: str, content: bytes) -> ParsedDocument:
        from docling.datamodel.base_models import DocumentStream

        suffix = _SUFFIX_FOR_MIME[mime_type]
        stream = DocumentStream(name=f"document{suffix}", stream=_as_stream(content))

        try:
            result = self._converter.convert(stream)
        except Exception as exc:
            # `from None` on purpose: a parser's exception can quote the
            # document it choked on, and a chained traceback would carry that
            # into every log that renders it. Only the type survives.
            raise ParsingError(
                f"docling failed to convert the document ({type(exc).__name__})"
            ) from None

        document = result.document
        try:
            text = document.export_to_markdown()
            serialized = document.model_dump_json().encode("utf-8")
            chunks = self._chunks_from(document)
        except Exception as exc:
            raise ParsingError(
                f"docling produced an unusable document ({type(exc).__name__})"
            ) from None

        return ParsedDocument(text=text, chunks=chunks, serialized=serialized)

    def _chunks_from(self, document) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        for chunk in self._chunker.chunk(dl_doc=document):
            text = (getattr(chunk, "text", "") or "").strip()
            if not text:
                continue
            page, section, provenance = _provenance_of(chunk)
            chunks.append(
                ParsedChunk(
                    ordinal=len(chunks),
                    text=text,
                    page_number=page,
                    section_title=section,
                    provenance=provenance,
                )
            )
        return chunks


def _as_stream(content: bytes):
    from io import BytesIO

    return BytesIO(content)


def _provenance_of(chunk) -> tuple[int | None, str | None, dict]:
    """Pull page, section and bounding boxes out of a Docling chunk.

    Every field is optional and the shapes vary by input format, so this reads
    defensively: missing provenance is recorded as missing, never invented.
    """
    meta = getattr(chunk, "meta", None)
    if meta is None:
        return None, None, {}

    headings = list(getattr(meta, "headings", None) or [])
    section = headings[-1] if headings else None

    pages: list[int] = []
    boxes: list[dict] = []
    charspans: list[list[int]] = []
    item_refs: list[str] = []

    for item in getattr(meta, "doc_items", None) or []:
        ref = getattr(item, "self_ref", None)
        if ref:
            item_refs.append(str(ref))
        for prov in getattr(item, "prov", None) or []:
            page_no = getattr(prov, "page_no", None)
            if isinstance(page_no, int):
                pages.append(page_no)
            bbox = getattr(prov, "bbox", None)
            if bbox is not None:
                boxes.append(
                    {
                        "page": page_no,
                        "l": getattr(bbox, "l", None),
                        "t": getattr(bbox, "t", None),
                        "r": getattr(bbox, "r", None),
                        "b": getattr(bbox, "b", None),
                    }
                )
            charspan = getattr(prov, "charspan", None)
            if charspan is not None:
                charspans.append(list(charspan))

    provenance: dict = {}
    if headings:
        provenance["headings"] = headings
    if boxes:
        provenance["bboxes"] = boxes
    if charspans:
        provenance["charspans"] = charspans
    if item_refs:
        provenance["doc_items"] = item_refs
    if pages:
        provenance["pages"] = sorted(set(pages))

    return (min(pages) if pages else None), section, provenance
