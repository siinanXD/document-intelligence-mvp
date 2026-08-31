"""Golden corpus shape and fixture round-trip through the evaluation parser."""

from app.evaluation.cases import load_dataset
from app.evaluation.formats import build_file
from app.evaluation.parser import EvaluationParser


def test_retrieval_v1_covers_the_required_categories():
    dataset = load_dataset()
    assert dataset.name == "retrieval-v1"
    assert dataset.version == "1.0.0"
    assert 30 <= len(dataset.cases) <= 50
    categories = {case.category for case in dataset.cases}
    assert categories == {
        "answerable",
        "lexical",
        "unanswerable",
        "multi_document",
        "versioned",
        "conflicting",
        "cross_tenant",
    }
    languages = {case.language for case in dataset.cases}
    assert languages == {"de", "en"}
    modes = {case.retrieval_mode for case in dataset.cases}
    assert modes == {"semantic", "lexical"}
    formats = {item.format for item in dataset.documents}
    assert {"pdf", "docx", "xlsx"}.issubset(formats)
    tenants = {item.tenant for item in dataset.documents}
    assert tenants == {"tenant_a", "tenant_b"}
    assert all(case.track == "retrieval" for case in dataset.cases)


async def test_generated_files_round_trip_through_the_parser():
    dataset = load_dataset()
    parser = EvaluationParser()
    for spec in dataset.documents:
        source = (dataset.root / spec.source).read_text(encoding="utf-8")
        content, mime = build_file(spec.format, source)
        if spec.format == "pdf":
            assert content.startswith(b"%PDF-")
        if spec.format in {"docx", "xlsx"}:
            assert content.startswith(b"PK\x03\x04")
        parsed = await parser.parse(filename=spec.filename, mime_type=mime, content=content)
        assert parsed.chunks
        assert parsed.text.strip()
