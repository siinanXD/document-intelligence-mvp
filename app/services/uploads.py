"""Upload validation: what a file claims to be versus what it is.

A client-provided content type is a hint, never evidence. Every accepted type
is checked against the filename extension *and* the leading bytes, and a file
is only accepted when all three agree on something we support. Docling remains
the final authority on whether a file can actually be parsed; this layer only
refuses what is obviously wrong, cheaply, before anything expensive happens.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass

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
}

# Leading bytes that must be present for the formats that have a signature.
_MAGIC: dict[str, bytes] = {
    ".pdf": b"%PDF-",
    # OOXML files are ZIP containers.
    ".docx": b"PK\x03\x04",
    ".pptx": b"PK\x03\x04",
    ".xlsx": b"PK\x03\x04",
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


def extension_of(filename: str) -> str:
    name = safe_filename(filename).lower()
    _, separator, extension = name.rpartition(".")
    return f".{extension}" if separator else ""


def validate_upload(
    *, filename: str, declared_mime_type: str | None, content: bytes, max_bytes: int
) -> ValidatedUpload:
    """Validate an upload and return what we will actually record about it.

    Raises EmptyFile, FileTooLarge or UnsupportedFileType. The returned
    mime_type is the canonical one for the extension, not the client's claim.
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

    return ValidatedUpload(
        filename=name,
        mime_type=canonical_mime,
        file_hash=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def storage_key_for(*, tenant_id, document_id, filename: str) -> str:
    """Build an object key from identifiers, never from client input alone.

    The filename is appended only so a human browsing the bucket can tell what
    an object is; it is sanitised, and the path segments that matter are UUIDs.
    """
    return f"{tenant_id}/{document_id}/{safe_filename(filename)}"
