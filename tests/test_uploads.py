"""Upload validation: what a file claims to be versus what it is."""

import hashlib

import pytest

from app.services.uploads import (
    EmptyFile,
    FileTooLarge,
    UnsupportedFileType,
    extension_of,
    safe_filename,
    storage_key_for,
    validate_upload,
)

PDF = b"%PDF-1.7\nbody"
DOCX = b"PK\x03\x04" + b"\x00" * 40
MAX = 1024 * 1024


def _validate(**overrides):
    kwargs = {
        "filename": "contract.pdf",
        "declared_mime_type": "application/pdf",
        "content": PDF,
        "max_bytes": MAX,
    }
    kwargs.update(overrides)
    return validate_upload(**kwargs)


def test_a_valid_pdf_is_accepted_and_hashed():
    result = _validate()

    assert result.filename == "contract.pdf"
    assert result.mime_type == "application/pdf"
    assert result.file_hash == hashlib.sha256(PDF).hexdigest()
    assert result.size == len(PDF)


def test_the_recorded_type_is_canonical_not_the_clients_claim():
    """A client may declare text/plain for Markdown; we record text/markdown."""
    result = _validate(filename="notes.md", declared_mime_type="text/plain", content=b"# Title")

    assert result.mime_type == "text/markdown"


def test_a_declared_type_that_contradicts_the_extension_is_refused():
    with pytest.raises(UnsupportedFileType, match="does not match extension"):
        _validate(filename="contract.pdf", declared_mime_type="text/html")


def test_a_generic_octet_stream_declaration_is_tolerated():
    """Plenty of clients send application/octet-stream for everything."""
    result = _validate(declared_mime_type="application/octet-stream")

    assert result.mime_type == "application/pdf"


def test_a_missing_declaration_is_tolerated():
    assert _validate(declared_mime_type=None).mime_type == "application/pdf"


def test_a_charset_parameter_does_not_break_the_comparison():
    result = _validate(
        filename="page.html", declared_mime_type="text/html; charset=utf-8", content=b"<html>"
    )

    assert result.mime_type == "text/html"


def test_content_that_is_not_really_a_pdf_is_refused():
    """The extension and the declared type both say PDF; the bytes disagree."""
    with pytest.raises(UnsupportedFileType, match="not a valid .pdf"):
        _validate(content=b"<html>not a pdf at all</html>")


def test_an_ooxml_file_must_be_a_zip_container():
    with pytest.raises(UnsupportedFileType):
        _validate(
            filename="report.docx",
            declared_mime_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            content=b"not a zip",
        )


def test_a_real_ooxml_container_is_accepted():
    result = _validate(
        filename="report.docx",
        declared_mime_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        content=DOCX,
    )

    assert result.mime_type.endswith("wordprocessingml.document")


@pytest.mark.parametrize("filename", ["archive.zip", "script.sh", "noextension", "image.png"])
def test_unsupported_extensions_are_refused(filename):
    with pytest.raises(UnsupportedFileType):
        _validate(filename=filename, declared_mime_type=None, content=b"data")


def test_an_empty_file_is_refused():
    with pytest.raises(EmptyFile):
        _validate(content=b"")


def test_a_file_over_the_limit_is_refused():
    with pytest.raises(FileTooLarge):
        _validate(content=PDF + b"x" * MAX, max_bytes=MAX)


def test_a_file_exactly_at_the_limit_is_accepted():
    content = b"%PDF-" + b"x" * (MAX - 5)

    assert _validate(content=content, max_bytes=MAX).size == MAX


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # The basename is taken first, so traversal segments are gone, not escaped.
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\report.pdf", "report.pdf"),
        ("/absolute/path/file.pdf", "file.pdf"),
        ("weird name (final).pdf", "weird_name_final_.pdf"),
        ("...", "upload"),
        ("", "upload"),
    ],
)
def test_filenames_are_reduced_to_something_safe(given, expected):
    assert safe_filename(given) == expected


def test_a_very_long_filename_is_truncated():
    assert len(safe_filename("a" * 500 + ".pdf")) == 200


def test_extension_is_taken_from_the_sanitised_name():
    assert extension_of("REPORT.PDF") == ".pdf"
    assert extension_of("archive.tar.gz") == ".gz"
    assert extension_of("noextension") == ""


def test_a_traversal_filename_cannot_shape_the_storage_key():
    key = storage_key_for(
        tenant_id="11111111-1111-1111-1111-111111111111",
        document_id="22222222-2222-2222-2222-222222222222",
        filename="../../escape.pdf",
    )

    assert key == (
        "11111111-1111-1111-1111-111111111111/22222222-2222-2222-2222-222222222222/escape.pdf"
    )
    assert ".." not in key
