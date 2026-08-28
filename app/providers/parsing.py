"""Document parsing interface.

The parser is the seam between "bytes we stored" and "text with provenance we
can cite". Only implementations of this interface may import a parsing library,
which is what keeps the worker testable without a 5 GB dependency tree.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ParsingError(Exception):
    """The document could not be parsed.

    Carries a short diagnostic only: never the document's content, and never a
    library traceback that might quote it.
    """


class UnsupportedFormat(ParsingError):
    """This parser cannot handle the given media type."""


@dataclass(frozen=True)
class ParsedChunk:
    """One citable passage, with whatever provenance the parser could recover."""

    ordinal: int
    text: str
    page_number: int | None = None
    section_title: str | None = None
    # bbox, charspan and document-item references, whatever the parser offers.
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    chunks: list[ParsedChunk]
    # The parser's own structured representation, stored so a later reindex can
    # re-chunk without parsing the original again.
    serialized: bytes
    serialized_media_type: str = "application/json"


class DocumentParser(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Stable parser identifier, recorded with the derived data."""

    @abstractmethod
    def supports(self, mime_type: str) -> bool:
        """Whether this parser handles the given media type."""

    @abstractmethod
    async def parse(self, *, filename: str, mime_type: str, content: bytes) -> ParsedDocument:
        """Parse bytes into text and chunks.

        Raises ParsingError - or UnsupportedFormat - and never lets a parsing
        library's exception escape carrying document content.
        """
