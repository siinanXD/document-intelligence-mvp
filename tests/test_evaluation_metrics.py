"""Pure metric calculations for the retrieval evaluator."""

import pytest

from app.evaluation.metrics import (
    mean_reciprocal_rank,
    precision_at_k,
    recall_at_k,
    score_case,
)


def test_recall_and_precision_at_k():
    relevant = {"a", "b"}
    retrieved = ["a", "x", "b"]

    assert recall_at_k(relevant, retrieved, 1) == 0.5
    assert recall_at_k(relevant, retrieved, 3) == 1.0
    assert precision_at_k(relevant, retrieved, 1) == 1.0
    assert precision_at_k(relevant, retrieved, 3) == pytest.approx(2 / 3)
    assert precision_at_k(relevant, retrieved, 5) == pytest.approx(2 / 5)


def test_duplicates_do_not_inflate_scores():
    relevant = {"a"}
    retrieved = ["a", "a", "a"]

    assert recall_at_k(relevant, retrieved, 3) == 1.0
    assert precision_at_k(relevant, retrieved, 3) == pytest.approx(1 / 3)
    assert mean_reciprocal_rank(relevant, retrieved) == 1.0


def test_mrr_is_reciprocal_of_first_relevant_rank():
    relevant = {"b"}
    assert mean_reciprocal_rank(relevant, ["a", "b"]) == 0.5
    assert mean_reciprocal_rank(relevant, ["x", "y"]) == 0.0


def test_no_relevant_sources_are_not_scored_as_correct():
    with pytest.raises(ValueError):
        recall_at_k(set(), ["a"], 1)
    score = score_case(case_id="u", relevant=set(), retrieved=["a", "b"], leaked=[])
    assert score.recall_at_5 is None
    assert score.precision_at_5 is None
    assert score.mrr is None
    assert score.no_relevant_hit is False
    assert score.retrieved == ["a", "b"]


def test_cross_tenant_leakage_is_recorded_on_the_case():
    score = score_case(
        case_id="x",
        relevant={"a"},
        retrieved=["a"],
        leaked=["foreign:00000"],
    )
    assert score.leaked_sources == ["foreign:00000"]
    assert score.recall_at_1 == 1.0
