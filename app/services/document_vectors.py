"""Reducing a document's chunk vectors to one vector for the document.

Behind a strategy interface, because the mean is a starting point rather than
an answer: it is cheap, deterministic and good enough to find near-duplicates,
and it flattens a long document with several distinct sections into something
that resembles none of them. Replacing it should not touch anything else.
"""

import math
from abc import ABC, abstractmethod


class DocumentVectorStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier, recorded with the vectors it produced."""

    @abstractmethod
    def combine(self, vectors: list[list[float]]) -> list[float] | None:
        """Reduce chunk vectors to one, or None when there is nothing to reduce."""


class NormalizedMeanStrategy(DocumentVectorStrategy):
    """The component-wise mean, normalised to unit length.

    Normalising matters: with cosine distance an unnormalised mean makes a
    document's similarity depend on how many chunks it happens to have, so a
    long document and its own shorter revision would drift apart for a reason
    that has nothing to do with what they say.
    """

    @property
    def name(self) -> str:
        return "normalized_mean"

    def combine(self, vectors: list[list[float]]) -> list[float] | None:
        if not vectors:
            return None

        width = len(vectors[0])
        if any(len(vector) != width for vector in vectors):
            raise ValueError("cannot combine vectors of differing dimensionality")

        totals = [0.0] * width
        for vector in vectors:
            for index, value in enumerate(vector):
                totals[index] += value

        count = len(vectors)
        mean = [total / count for total in totals]

        norm = math.sqrt(sum(value * value for value in mean))
        if norm == 0.0:
            # Opposing vectors that cancel out exactly. There is no meaningful
            # direction to report, and a zero vector would score 0 against
            # everything including itself.
            return None
        return [value / norm for value in mean]


def get_document_vector_strategy() -> DocumentVectorStrategy:
    return NormalizedMeanStrategy()
