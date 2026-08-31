"""Upload validation: what a file claims to be versus what it is."""

import hashlib

import pytest

from app.services.uploads import (
    EmptyFile,
    FileTooLarge,
    UnsupportedFileType,
    extension_of,
    max_request_body_bytes,
    read_upload_bounded,
    receive_upload,
    safe_filename,
    storage_key_for,
    validate_upload,
)

PDF = b"%PDF-1.7\nbody"
DOCX = b"PK\x03\x04" + b"\x00" * 40
MAX = 1024 * 1024


class _FakeUpload:
    """Minimal UploadFile stand-in for streaming tests."""

    def __init__(
        self,
        data: bytes,
        *,
        filename: str = "contract.pdf",
        content_type: str | None = "application/pdf",
        fail_after: int | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self._data = data
        self._pos = 0
        self.filename = filename
        self.content_type = content_type
        self.fail_after = fail_after
        self.close_error = close_error
        self.closed = False
        self.bytes_read = 0

    async def read(self, size: int = -1) -> bytes:
        if self.fail_after is not None and self._pos >= self.fail_after:
            raise RuntimeError("connection reset")
        if size < 0:
            size = len(self._data) - self._pos
        end = self._pos + size
        if self.fail_after is not None:
            end = min(end, self.fail_after)
        end = min(end, len(self._data))
        chunk = self._data[self._pos : end]
        self._pos = end
        self.bytes_read += len(chunk)
        return chunk

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


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


@pytest.mark.asyncio
async def test_streaming_accepts_a_body_exactly_at_the_limit():
    limit = 32
    content = b"%PDF-" + b"x" * (limit - 5)
    assert len(content) == limit

    body, digest = await read_upload_bounded(_FakeUpload(content).read, max_bytes=limit)

    assert body == content
    assert digest == hashlib.sha256(content).hexdigest()


@pytest.mark.asyncio
async def test_streaming_refuses_one_byte_over_the_limit_without_keeping_it():
    limit = 32
    content = b"%PDF-" + b"x" * (limit - 4)  # limit + 1
    assert len(content) == limit + 1
    upload = _FakeUpload(content)

    with pytest.raises(FileTooLarge):
        await read_upload_bounded(upload.read, max_bytes=limit, chunk_size=8)

    # We stop as soon as the overflow byte arrives: never pull the rest.
    assert upload.bytes_read == limit + 1
    assert upload._pos == limit + 1


@pytest.mark.asyncio
async def test_streaming_empty_body_returns_empty_bytes():
    body, digest = await read_upload_bounded(_FakeUpload(b"").read, max_bytes=16)

    assert body == b""
    assert digest == hashlib.sha256(b"").hexdigest()


@pytest.mark.asyncio
async def test_streaming_hash_matches_one_shot_sha256():
    """Duplicate detection must see the same digest as before streaming."""
    content = b"%PDF-1.7\n" + b"clause " * 200
    body, digest = await read_upload_bounded(
        _FakeUpload(content).read, max_bytes=10_000, chunk_size=17
    )

    assert body == content
    assert digest == hashlib.sha256(content).hexdigest()


@pytest.mark.asyncio
async def test_receive_upload_closes_the_stream_after_an_empty_body():
    upload = _FakeUpload(b"")

    with pytest.raises(EmptyFile):
        await receive_upload(upload, max_bytes=64)

    assert upload.closed is True


@pytest.mark.asyncio
async def test_receive_upload_closes_the_stream_after_a_size_rejection():
    upload = _FakeUpload(b"%PDF-" + b"x" * 100)

    with pytest.raises(FileTooLarge):
        await receive_upload(upload, max_bytes=16, chunk_size=4)

    assert upload.closed is True


@pytest.mark.asyncio
async def test_an_interrupted_upload_discards_buffers_and_closes():
    content = b"%PDF-1.7\n" + b"y" * 200
    upload = _FakeUpload(content, fail_after=40)

    with pytest.raises(RuntimeError, match="connection reset"):
        await receive_upload(upload, max_bytes=10_000, chunk_size=16)

    assert upload.closed is True
    # Never held more than the bytes delivered before the failure.
    assert upload.bytes_read <= 40


@pytest.mark.asyncio
async def test_receive_upload_preserves_hash_and_mime_validation():
    upload = _FakeUpload(PDF)
    validated, content = await receive_upload(upload, max_bytes=MAX)

    assert content == PDF
    assert validated.file_hash == hashlib.sha256(PDF).hexdigest()
    assert validated.mime_type == "application/pdf"
    assert upload.closed is True


@pytest.mark.asyncio
async def test_a_close_failure_does_not_hide_a_validation_error():
    upload = _FakeUpload(b"", close_error=OSError("already closed"))

    with pytest.raises(EmptyFile):
        await receive_upload(upload, max_bytes=64)

    assert upload.closed is True


def test_request_body_limit_allows_multipart_overhead():
    """The ASGI body ceiling must sit above MAX_UPLOAD_BYTES for framing."""
    assert max_request_body_bytes(1024) > 1024
    assert max_request_body_bytes(50 * 1024 * 1024) == 50 * 1024 * 1024 + 256 * 1024


@pytest.mark.asyncio
async def test_streaming_spool_matches_one_shot_bytes_for_multi_chunk_bodies():
    """Spooling must not change the bytes or hash handed to storage/duplicates."""
    content = b"%PDF-1.7\n" + (b"block-" * 5000)
    body, digest = await read_upload_bounded(
        _FakeUpload(content).read, max_bytes=len(content), chunk_size=64
    )

    assert body == content
    assert digest == hashlib.sha256(content).hexdigest()
