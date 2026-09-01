"""Machine Intelligence evaluation fixture (SIN-99).

This package authors the conveyor-line dataset and oracle. It does not parse
production packages and it does not call paid providers.
"""

from app.evaluation.machine_intelligence.artifacts import DATASET_ROOT, write_dataset
from app.evaluation.machine_intelligence.line import (
    DATASET_NAME,
    GENERATOR_VERSION,
    build_line,
    ci_subset,
)
from app.evaluation.machine_intelligence.oracle import build_oracle, facts_missing_evidence

__all__ = [
    "DATASET_NAME",
    "DATASET_ROOT",
    "GENERATOR_VERSION",
    "build_line",
    "build_oracle",
    "ci_subset",
    "facts_missing_evidence",
    "write_dataset",
]
