"""Upload validation: what a file claims to be versus what it is.

A client-provided content type is a hint, never evidence. Every accepted type
is checked against the filename extension *and* the leading bytes, and a file
is only accepted when all three agree on something we support. Docling remains
the final authority on whether a file can actually be parsed; this layer only
refuses what is obviously wrong, cheaply, before anything expensive happens.

HTTP uploads are read in bounded chunks via `receive_upload`: the size limit is
enforced while streaming, SHA-256 is computed in the same pass, and accepted
bytes are assembled via a spool so we do not keep both a chunk list and a
joined copy in memory. Callers that already hold bytes (evaluation fixtures,
tests) still use `validate_upload` directly.

The ASGI stack also applies Starlette's ``RequestBodyLimitMiddleware`` so an
oversized request is cut off before multipart parsing spools the whole part.
"""

import hashlib
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile

logger = logging.getLogger(__name__)

# How much of the body we pull per await. Small enough that an oversized upload
# is refused after roughly one chunk past the limit, not after the whole body.
_READ_CHUNK_SIZE = 64 * 1024

# Multipart framing (boundaries, Content-Disposition) sits outside the file
# bytes. The request-body limit must allow that headroom so a file at exactly
# MAX_UPLOAD_BYTES is accepted, while still refusing arbitrarily large bodies
# before Starlette spools them to disk.
MULTIPART_BODY_OVERHEAD_BYTES = 256 * 1024


def max_request_body_bytes(max_upload_bytes: int) -> int:
    """Ceiling for the raw HTTP body, including multipart overhead."""
    return max_upload_bytes + MULTIPART_BODY_OVERHEAD_BYTES


# extension -> (canonical mime type, accepted client-declared mime types)
SUPPORTED_TYPES: dict[str, tuple[str, frozenset[str]]] = {
    ".pdf": ("application/pdf", frozenset({"application/pdf"})),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        frozenset({"application/vnd.openxmlformats-officedocument.presentationml.presentation"}),
    ),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    ),
    ".html": ("text/html", frozenset({"text/html"})),
    ".htm": ("text/html", frozenset({"text/html"})),
    ".md": ("text/markdown", frozenset({"text/markdown", "text/plain"})),
    ".txt": ("text/plain", frozenset({"text/plain"})),
    ".zip": (
        "application/zip",
        frozenset({"application/zip", "application/x-zip-compressed"}),
    ),
    ".xml": ("application/xml", frozenset({"application/xml", "text/xml"})),
    ".csv": ("text/csv", frozenset({"text/csv", "text/plain"})),
    ".tsv": (
        "text/tab-separated-values",
        frozenset({"text/tab-separated-values", "text/plain"}),
    ),
    ".scl": ("text/x-scl", frozenset({"text/x-scl", "text/plain"})),
    ".awl": ("text/x-awl", frozenset({"text/x-awl", "text/plain"})),
    ".json": ("application/json", frozenset({"application/json", "text/plain"})),
    ".png": ("image/png", frozenset({"image/png"})),
    ".jpg": ("image/jpeg", frozenset({"image/jpeg"})),
    ".jpeg": ("image/jpeg", frozenset({"image/jpeg"})),
}

# Leading bytes that must be present for the formats that have a signature.
_MAGIC: dict[str, bytes] = {
    ".pdf": b"%PDF-",
    # OOXML files are ZIP containers.
    ".docx": b"PK\x03\x04",
    ".pptx": b"PK\x03\x04",
    ".xlsx": b"PK\x03\x04",
    ".zip": b"PK\x03\x04",
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
}

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class UnsupportedFileType(ValueError):
    """The file is not a type this service accepts."""


class FileTooLarge(ValueError):
    """The upload exceeds the configured limit."""


class EmptyFile(ValueError):
    """The upload contains no bytes."""


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    mime_type: str
    file_hash: str
    size: int


def safe_filename(filename: str) -> str:
    """Reduce a client filename to something safe to store and display.

    The result is never used as a path on its own - object keys are built from
    identifiers - but it still must not carry separators, control characters or
    surprises through logs and responses.
    """
    # Take the basename under both separators: a Windows client sends
    # backslashes, which posixpath would leave in place.
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFKD", base)
    base = _UNSAFE_FILENAME_CHARS.sub("_", base).strip("._")
    if not base:
        base = "upload"
    return base[:200]


def mime_for_extension(filename: str) -> str:
    """Canonical MIME for a filename suffix, or octet-stream when unknown."""
    extension = extension_of(filename)
    spec = SUPPORTED_TYPES.get(extension)
    if spec is None:
        return "application/octet-stream"
    return spec[0]


def _is_utf8_text(content: bytes) -> bool:
    if b"\x00" in content[:8192]:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _stripped_head(content: bytes) -> bytes:
    return content.lstrip(b"\xef\xbb\xbf \t\r\n")


def _contents_match(extension: str, content: bytes) -> bool:
    magic = _MAGIC.get(extension)
    if magic and not content.startswith(magic):
        return False
    if extension == ".xml":
        head = _stripped_head(content)
        return head.startswith(b"<?xml") or head.startswith(b"<")
    if extension == ".json":
        head = _stripped_head(content)
        return head.startswith(b"{") or head.startswith(b"[")
    if extension in {".csv", ".tsv", ".scl", ".awl"}:
        return _is_utf8_text(content)
    return True


def extension_of(filename: str) -> str:
    name = safe_filename(filename).lower()
    _, separator, extension = name.rpartition(".")
    return f".{extension}" if separator else ""


async def read_upload_bounded(
    read: Callable[[int], Awaitable[bytes]],
    *,
    max_bytes: int,
    chunk_size: int = _READ_CHUNK_SIZE,
) -> tuple[bytes, str]:
    """Read an upload in chunks, hashing as we go, stopping at `max_bytes`.

    `read` is typically `UploadFile.read`. Raises `FileTooLarge` as soon as one
    more byte would push the total over the limit; the spool is discarded before
    the exception leaves. An empty body returns ``(b"", <hash of empty>)`` so the
    caller can raise `EmptyFile`.

    Chunks spill to a temporary file once they exceed `chunk_size`, so an
    accepted near-limit upload is not held twice in memory (chunk list plus a
    joined copy) the way an unbounded `list[bytes]` + `b"".join` would.
    """
    hasher = hashlib.sha256()
    total = 0
    # Context manager keeps ruff happy and always closes the spool, including
    # on FileTooLarge / interrupted reads.
    with SpooledTemporaryFile(max_size=chunk_size) as spool:
        while True:
            # Ask for at most one byte past the remaining budget so we can refuse
            # without pulling a full extra chunk into memory.
            remaining = max_bytes - total
            chunk = await read(min(chunk_size, remaining + 1))
            if not chunk:
                break
            if total + len(chunk) > max_bytes:
                raise FileTooLarge(f"the uploaded file exceeds {max_bytes} bytes")
            total += len(chunk)
            hasher.update(chunk)
            spool.write(chunk)
        spool.seek(0)
        return spool.read(), hasher.hexdigest()


async def receive_upload(
    file,
    *,
    max_bytes: int,
    chunk_size: int = _READ_CHUNK_SIZE,
) -> tuple[ValidatedUpload, bytes]:
    """Stream an `UploadFile`, enforce the size limit, hash and validate it.

    The underlying stream is closed on every path - success, rejection, or
    mid-read failure - so a partial spool does not linger after we refuse.
    Nothing here writes to object storage or the database; a rejected upload
    therefore leaves neither a row nor a stored object.
    """
    try:
        content, file_hash = await read_upload_bounded(
            file.read, max_bytes=max_bytes, chunk_size=chunk_size
        )
        upload = validate_upload(
            filename=file.filename or "upload",
            declared_mime_type=file.content_type,
            content=content,
            max_bytes=max_bytes,
            file_hash=file_hash,
        )
        return upload, content
    finally:
        try:
            await file.close()
        except Exception:
            # Closing a half-read multipart part must not mask the original error.
            logger.debug("upload stream close failed", exc_info=True)


def validate_upload(
    *,
    filename: str,
    declared_mime_type: str | None,
    content: bytes,
    max_bytes: int,
    file_hash: str | None = None,
) -> ValidatedUpload:
    """Validate an upload and return what we will actually record about it.

    Raises EmptyFile, FileTooLarge or UnsupportedFileType. The returned
    mime_type is the canonical one for the extension, not the client's claim.

    `file_hash` is the SHA-256 hex digest of `content`. When the caller already
    computed it while streaming (see `receive_upload`), pass it through so the
    digest stays identical without a second pass over the bytes.
    """
    if not content:
        raise EmptyFile("the uploaded file is empty")
    if len(content) > max_bytes:
        raise FileTooLarge(f"the uploaded file exceeds {max_bytes} bytes")

    name = safe_filename(filename)
    extension = extension_of(name)
    if extension not in SUPPORTED_TYPES:
        raise UnsupportedFileType(f"unsupported file extension: {extension or '(none)'}")

    canonical_mime, accepted = SUPPORTED_TYPES[extension]

    # A declared type that contradicts the extension is a refusal, not a
    # correction: we do not know which of the two is the lie.
    if declared_mime_type:
        declared = declared_mime_type.split(";", 1)[0].strip().lower()
        if declared and declared != "application/octet-stream" and declared not in accepted:
            raise UnsupportedFileType(
                f"declared content type {declared} does not match extension {extension}"
            )

    magic = _MAGIC.get(extension)
    if magic and not content.startswith(magic):
        raise UnsupportedFileType(f"file contents are not a valid {extension} document")
    if not _contents_match(extension, content):
        raise UnsupportedFileType(f"file contents are not a valid {extension} document")

    return ValidatedUpload(
        filename=name,
        mime_type=canonical_mime,
        file_hash=file_hash if file_hash is not None else hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def storage_key_for(*, tenant_id, document_id, filename: str) -> str:
    """Build an object key from identifiers, never from client input alone.

    The filename is appended only so a human browsing the bucket can tell what
    an object is; it is sanitised, and the path segments that matter are UUIDs.
    """
    return f"{tenant_id}/{document_id}/{safe_filename(filename)}"
