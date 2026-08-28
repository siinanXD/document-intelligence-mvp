"""Deterministic text normalization and the content hash.

Two uploads that differ only in encoding, line endings or whitespace are the
same document for our purposes. The normalization below is the definition of
"the same": it must stay deterministic and stable, because `content_hash` is
computed from it and stored.

Changing any rule here changes every hash. That is a migration, not an edit.
"""

import hashlib
import re
import unicodedata

_LINE_ENDINGS = re.compile(r"\r\n?")
_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Reduce text to its canonical form.

    - Unicode NFC, so composed and decomposed forms agree.
    - Line endings become \\n.
    - Runs of spaces and tabs collapse to a single space.
    - Trailing whitespace per line is dropped.
    - Three or more blank lines collapse to one.
    - Leading and trailing whitespace is stripped.

    Case is preserved: a document that differs only in capitalisation is a
    different document, and near-matches are the similarity search's job.
    """
    text = unicodedata.normalize("NFC", text)
    text = _LINE_ENDINGS.sub("\n", text)
    text = _HORIZONTAL_WHITESPACE.sub(" ", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def content_hash(text: str) -> str:
    """SHA-256 of the normalized text."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def source_id_for(*, document_id, ordinal: int) -> str:
    """A citation's stable identifier within a tenant.

    Stable for as long as the document's chunking is: reprocessing replaces a
    document's chunks wholesale, so a citation issued before a re-chunk may
    land on different text. That is why the chunk's own provenance - page and
    section - is stored alongside, rather than being derived from this id.
    """
    return f"{document_id}:{ordinal:05d}"
