"""Adapter interface for Machine Intelligence intake (SIN-100).

Adapters emit typed observations. They do not write canonical engineering
entities; SIN-90+ services resolve observations into those rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

ADAPTER_VERSION = "1.0.0"


class AdapterError(Exception):
    """Intake failed. The message must not contain filenames or content."""


class PackageRejected(AdapterError):
    """The archive or member is unsafe or exceeds intake limits."""


@dataclass(frozen=True)
class Artifact:
    """One stored blob presented to adapters. Paths are hints, never identity."""

    filename: str
    mime_type: str
    content: bytes
    path_hint: str | None = None
    file_hash: str | None = None


@dataclass(frozen=True)
class Detection:
    score: float
    family: str
    reasons: tuple[str, ...]
    adapter_name: str
    adapter_version: str = ADAPTER_VERSION


@dataclass(frozen=True)
class Observation:
    kind: str
    payload: dict[str, Any]
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass(frozen=True)
class ExtractResult:
    observations: tuple[Observation, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdapterCapabilities:
    formats: tuple[str, ...]
    families: tuple[str, ...]
    constructs: tuple[str, ...]
    limitations: tuple[str, ...]


class EngineeringAdapter(ABC):
    """Deterministic detect / extract / validate seam. No vendor SDK."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable adapter identifier stored with observations."""

    @property
    def version(self) -> str:
        return ADAPTER_VERSION

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        """Formats, families and known gaps for this adapter."""

    @abstractmethod
    def detect(self, artifact: Artifact) -> Detection:
        """Score how well this adapter applies. 0 means skip extract."""

    @abstractmethod
    def extract(self, artifact: Artifact) -> ExtractResult:
        """Return observations and warnings. Must be identical for identical input."""

    @abstractmethod
    def validate(self, artifact: Artifact, result: ExtractResult) -> ValidationReport:
        """Deterministic checks on the extract. Must not invent facts."""
