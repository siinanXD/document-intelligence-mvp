"""Report serialisation and baseline comparison."""

import json
from pathlib import Path

from app.evaluation.compare import compare_reports
from app.evaluation.embeddings import HashingEmbeddings
from app.evaluation.report import build_report, dump_report, format_summary, load_report


def _mode(*, recall=1.0, leakage=0, cases=None):
    return {
        "total_cases": 1,
        "evaluated_cases": 1,
        "no_evidence_cases": 0,
        "no_evidence_with_hits": 0,
        "cases_with_zero_relevant_retrieval": 0,
        "cross_tenant_leakage": leakage,
        "leaked_sources": [],
        "failed_case_ids": [],
        "recall_at_1": recall,
        "recall_at_3": recall,
        "recall_at_5": recall,
        "precision_at_1": recall,
        "precision_at_3": recall,
        "precision_at_5": recall,
        "mrr": recall,
        "by_category": {},
        "cases": cases or [],
    }


def test_report_dump_is_deterministic(tmp_path: Path):
    embeddings = HashingEmbeddings()
    first = build_report(
        dataset_name="retrieval-v1",
        dataset_version="1.0.0",
        commit="abc",
        embeddings=embeddings,
        modes={"lexical": _mode(), "semantic": _mode()},
    )
    first["generated_at"] = "2026-01-01T00:00:00+00:00"
    path = tmp_path / "report.json"
    dump_report(first, path)
    dump_report(first, path)
    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == first
    assert list(json.loads(text)["modes"]) == ["lexical", "semantic"]
    assert "Recall@5" not in format_summary(first) or "R@5" in format_summary(first)


def test_baseline_comparison_detects_regression_and_leakage(tmp_path: Path):
    embeddings = HashingEmbeddings()
    good = build_report(
        dataset_name="retrieval-v1",
        dataset_version="1.0.0",
        commit="abc",
        embeddings=embeddings,
        modes={
            "semantic": _mode(
                recall=0.8,
                cases=[{"case_id": "c1", "recall_at_5": 1.0, "mrr": 1.0, "retrieved": ["a"]}],
            )
        },
    )
    bad = build_report(
        dataset_name="retrieval-v1",
        dataset_version="1.0.0",
        commit="def",
        embeddings=embeddings,
        modes={
            "semantic": _mode(
                recall=0.4,
                leakage=1,
                cases=[{"case_id": "c1", "recall_at_5": 0.0, "mrr": 0.0, "retrieved": ["z"]}],
            )
        },
    )
    baseline = {
        "thresholds": {
            "semantic": {
                "recall_at_5": 0.8,
                "mrr": 0.8,
                "cross_tenant_leakage": 0,
            }
        },
        "report": good,
    }
    path = tmp_path / "baseline.json"
    dump_report(baseline, path)
    comparison = compare_reports(bad, load_report(path))
    assert comparison.leakage == 1
    assert comparison.passed is False
    assert any("recall_at_5" in item for item in comparison.threshold_failures)
    assert any(change["case_id"] == "c1" for change in comparison.changed_cases)


def test_identical_reports_pass_thresholds():
    embeddings = HashingEmbeddings()
    report = build_report(
        dataset_name="retrieval-v1",
        dataset_version="1.0.0",
        commit="abc",
        embeddings=embeddings,
        modes={"semantic": _mode(recall=0.9)},
    )
    baseline = {
        "thresholds": {"semantic": {"recall_at_5": 0.9, "mrr": 0.9, "cross_tenant_leakage": 0}},
        "report": report,
    }
    assert compare_reports(report, baseline).passed is True
