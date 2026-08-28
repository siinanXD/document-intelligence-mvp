"""The real Docling parser.

Every other test in the suite drives a fake parser, which cannot notice Docling
changing its API or its output shape. These use the genuine library on real
fixture files.

Two things this environment cannot cover, and neither can be worked around from
here:

* PDF parsing needs Docling's layout model, and `HybridChunker` needs a
  tokenizer. Both are fetched from huggingface.co, which the build environment's
  egress policy denies. The PDF test therefore only runs where the models are
  available, and the chunker tests inject `HierarchicalChunker`, which needs
  none - so what is verified here is the conversion, serialisation and
  provenance mapping, not HybridChunker's own splitting.
* That is also the reason a deployment must pre-fetch both. See the parser's
  docstring and `docling_artifacts_path`.
"""

from pathlib import Path

import pytest

docling = pytest.importorskip(
    "docling", reason="install the `parsing` extra to exercise the real parser"
)

from docling_core.transforms.chunker.hierarchical_chunker import (  # noqa: E402
    HierarchicalChunker,
)

from app.providers.docling_parser import DoclingParser  # noqa: E402
from app.providers.parsing import ParsingError, UnsupportedFormat  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(scope="module")
def parser():
    """A parser whose chunker needs no downloaded tokenizer."""
    return DoclingParser(chunker=HierarchicalChunker())


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


async def test_a_docx_parses_to_text_and_chunks(parser):
    parsed = await parser.parse(
        filename="sample.docx", mime_type=DOCX, content=_read("sample.docx")
    )

    assert "Service Agreement" in parsed.text
    assert "EUR 1000" in parsed.text
    assert len(parsed.chunks) >= 2
    assert all(chunk.text.strip() for chunk in parsed.chunks)


async def test_markdown_headings_become_section_titles(parser):
    parsed = await parser.parse(
        filename="sample.md", mime_type="text/markdown", content=_read("sample.md")
    )

    sections = {chunk.section_title for chunk in parsed.chunks}
    assert "Contract" in sections
    assert "Term" in sections


async def test_html_parses_and_records_its_headings(parser):
    parsed = await parser.parse(
        filename="sample.html", mime_type="text/html", content=_read("sample.html")
    )

    assert parsed.chunks
    assert parsed.chunks[0].section_title == "Contract"
    assert parsed.chunks[0].provenance["headings"] == ["Contract"]


async def test_chunk_ordinals_are_dense_and_ordered(parser):
    parsed = await parser.parse(
        filename="sample.docx", mime_type=DOCX, content=_read("sample.docx")
    )

    assert [chunk.ordinal for chunk in parsed.chunks] == list(range(len(parsed.chunks)))


async def test_every_chunk_carries_a_reference_back_to_the_document(parser):
    parsed = await parser.parse(
        filename="sample.docx", mime_type=DOCX, content=_read("sample.docx")
    )

    assert all(chunk.provenance.get("doc_items") for chunk in parsed.chunks)


async def test_the_parsed_representation_round_trips_as_json(parser):
    import json

    parsed = await parser.parse(
        filename="sample.md", mime_type="text/markdown", content=_read("sample.md")
    )

    restored = json.loads(parsed.serialized)
    assert isinstance(restored, dict)
    assert parsed.serialized_media_type == "application/json"


async def test_an_unsupported_media_type_is_refused(parser):
    with pytest.raises(UnsupportedFormat):
        await parser.parse(
            filename="archive.zip", mime_type="application/zip", content=b"PK\x03\x04"
        )


async def test_corrupt_content_raises_without_quoting_the_document(parser):
    """A parser's own message can echo the bytes it choked on."""
    secret = b"CONFIDENTIAL Acme pays EUR 1000"

    with pytest.raises(ParsingError) as caught:
        await parser.parse(filename="broken.docx", mime_type=DOCX, content=secret)

    assert "CONFIDENTIAL" not in str(caught.value)
    assert "Acme" not in str(caught.value)


def test_the_parser_reports_what_it_supports(parser):
    assert parser.name == "docling"
    assert parser.supports("application/pdf")
    assert parser.supports(DOCX)
    assert not parser.supports("application/zip")


@pytest.mark.skipif(
    "not config.getoption('--run-docling-models')",
    reason="needs Docling's layout model; pass --run-docling-models where it can be fetched",
)
async def test_a_pdf_parses_when_the_models_are_available():
    """The one path this environment cannot reach: PDF needs the layout model."""
    parser = DoclingParser()

    parsed = await parser.parse(
        filename="sample.pdf", mime_type="application/pdf", content=_read("sample.pdf")
    )

    assert "Service Agreement" in parsed.text
    assert parsed.chunks
    assert parsed.chunks[0].page_number == 1
