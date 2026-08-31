"""Ranking metrics for one retrieved list.

Recall, precision and MRR are defined on source ids. Duplicate hits collapse
to the first occurrence so a repeated passage cannot inflate the scores.
Cases with no relevant sources are not scored here: they have no denominator
for recall, and treating an arbitrary retrieved chunk as correct would hide
unanswerable failures.
"""

from dataclasses import dataclass


def _unique(retrieved: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in retrieved:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def recall_at_k(relevant: set[str], retrieved: list[str], k: int) -> float:
    """Fraction of relevant sources appearing in the first `k` unique hits."""
    if not relevant:
        raise ValueError("recall is undefined when there are no relevant sources")
    if k < 1:
        raise ValueError("k must be at least 1")
    hits = set(_unique(retrieved)[:k])
    return len(relevant & hits) / len(relevant)


def precision_at_k(relevant: set[str], retrieved: list[str], k: int) -> float:
    """Fraction of the first `k` unique hits that are relevant.

    The denominator is `k` even when fewer than `k` results were returned, so
    a short list cannot look more precise than a padded one.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    hits = _unique(retrieved)[:k]
    if not hits:
        return 0.0
    return len(relevant & set(hits)) / k


def mean_reciprocal_rank(relevant: set[str], retrieved: list[str]) -> float:
    """1 / rank of the first relevant unique hit, or 0 if none appear."""
    if not relevant:
        raise ValueError("MRR is undefined when there are no relevant sources")
    for rank, item in enumerate(_unique(retrieved), start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    relevant_count: int
    retrieved: list[str]
    recall_at_1: float | None
    recall_at_3: float | None
    recall_at_5: float | None
    precision_at_1: float | None
    precision_at_3: float | None
    precision_at_5: float | None
    mrr: float | None
    leaked_sources: list[str]
    no_relevant_hit: bool

    def as_dict(self) -> dict:
        def r(value: float | None) -> float | None:
            return None if value is None else round(value, 4)

        return {
            "case_id": self.case_id,
            "relevant_count": self.relevant_count,
            "retrieved": list(self.retrieved),
            "recall_at_1": r(self.recall_at_1),
            "recall_at_3": r(self.recall_at_3),
            "recall_at_5": r(self.recall_at_5),
            "precision_at_1": r(self.precision_at_1),
            "precision_at_3": r(self.precision_at_3),
            "precision_at_5": r(self.precision_at_5),
            "mrr": r(self.mrr),
            "leaked_sources": list(self.leaked_sources),
            "no_relevant_hit": self.no_relevant_hit,
        }


def score_case(
    *,
    case_id: str,
    relevant: set[str],
    retrieved: list[str],
    leaked: list[str],
) -> CaseScore:
    unique = _unique(retrieved)
    if relevant:
        return CaseScore(
            case_id=case_id,
            relevant_count=len(relevant),
            retrieved=unique,
            recall_at_1=recall_at_k(relevant, unique, 1),
            recall_at_3=recall_at_k(relevant, unique, 3),
            recall_at_5=recall_at_k(relevant, unique, 5),
            precision_at_1=precision_at_k(relevant, unique, 1),
            precision_at_3=precision_at_k(relevant, unique, 3),
            precision_at_5=precision_at_k(relevant, unique, 5),
            mrr=mean_reciprocal_rank(relevant, unique),
            leaked_sources=list(leaked),
            no_relevant_hit=not bool(relevant & set(unique)),
        )
    return CaseScore(
        case_id=case_id,
        relevant_count=0,
        retrieved=unique,
        recall_at_1=None,
        recall_at_3=None,
        recall_at_5=None,
        precision_at_1=None,
        precision_at_3=None,
        precision_at_5=None,
        mrr=None,
        leaked_sources=list(leaked),
        no_relevant_hit=False,
    )


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
