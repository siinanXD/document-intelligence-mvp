"""Pick adapters by deterministic detection score. No LLM routing in CI."""

from __future__ import annotations

from app.adapters.base import (
    Artifact,
    Detection,
    EngineeringAdapter,
    ExtractResult,
    ValidationReport,
)
from app.adapters.stubs import default_adapters

_MIN_SCORE = 0.5


def run_adapters(
    artifact: Artifact,
    adapters: tuple[EngineeringAdapter, ...] | None = None,
) -> tuple[Detection | None, ExtractResult, ValidationReport]:
    """Run the highest-scoring adapter at or above the threshold."""
    chosen: Detection | None = None
    selected: EngineeringAdapter | None = None
    for adapter in adapters or default_adapters():
        detection = adapter.detect(artifact)
        if detection.score < _MIN_SCORE:
            continue
        if chosen is None or detection.score > chosen.score:
            chosen = detection
            selected = adapter
    if selected is None or chosen is None:
        empty = ExtractResult(observations=())
        skipped = ValidationReport(ok=True, warnings=("no adapter claimed this artifact",))
        return None, empty, skipped
    extracted = selected.extract(artifact)
    report = selected.validate(artifact, extracted)
    return chosen, extracted, report
