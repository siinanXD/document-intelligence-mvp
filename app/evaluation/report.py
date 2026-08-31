"""Machine-readable evaluation reports with stable key order."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.evaluation.metrics import CaseScore, mean


def git_commit(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def round4(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)


def aggregate_scores(scores: list[CaseScore]) -> dict[str, Any]:
    evaluated = [score for score in scores if score.relevant_count]
    no_evidence = [score for score in scores if score.relevant_count == 0]
    leaked = [source for score in scores for source in score.leaked_sources]
    failed = sorted(
        score.case_id
        for score in scores
        if score.leaked_sources or (score.relevant_count and score.no_relevant_hit)
    )
    return {
        "total_cases": len(scores),
        "evaluated_cases": len(evaluated),
        "no_evidence_cases": len(no_evidence),
        "no_evidence_with_hits": sum(1 for score in no_evidence if score.retrieved),
        "cases_with_zero_relevant_retrieval": sum(
            1 for score in evaluated if score.no_relevant_hit
        ),
        "cross_tenant_leakage": len(leaked),
        "leaked_sources": sorted(leaked),
        "failed_case_ids": failed,
        "recall_at_1": round4(mean([score.recall_at_1 or 0.0 for score in evaluated])),
        "recall_at_3": round4(mean([score.recall_at_3 or 0.0 for score in evaluated])),
        "recall_at_5": round4(mean([score.recall_at_5 or 0.0 for score in evaluated])),
        "precision_at_1": round4(mean([score.precision_at_1 or 0.0 for score in evaluated])),
        "precision_at_3": round4(mean([score.precision_at_3 or 0.0 for score in evaluated])),
        "precision_at_5": round4(mean([score.precision_at_5 or 0.0 for score in evaluated])),
        "mrr": round4(mean([score.mrr or 0.0 for score in evaluated])),
        "by_category": {},
    }


def attach_categories(
    summary: dict[str, Any], scores: list[CaseScore], categories: dict[str, str]
) -> None:
    grouped: dict[str, list[CaseScore]] = {}
    for score in scores:
        grouped.setdefault(categories[score.case_id], []).append(score)
    summary["by_category"] = {
        name: {
            "total_cases": len(group),
            "evaluated_cases": sum(1 for item in group if item.relevant_count),
            "recall_at_5": round4(
                mean([item.recall_at_5 or 0.0 for item in group if item.relevant_count])
            ),
            "precision_at_5": round4(
                mean([item.precision_at_5 or 0.0 for item in group if item.relevant_count])
            ),
            "mrr": round4(mean([item.mrr or 0.0 for item in group if item.relevant_count])),
        }
        for name, group in sorted(grouped.items())
    }


def build_report(
    *,
    dataset_name: str,
    dataset_version: str,
    commit: str,
    embeddings,
    modes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "dataset": dataset_name,
        "dataset_version": dataset_version,
        "track": "retrieval",
        "code_commit": commit,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "embedding_provider": embeddings.provider,
        "embedding_model": embeddings.model,
        "embedding_version": embeddings.version,
        "embedding_dimensions": embeddings.dimensions,
        "modes": modes,
    }


def dump_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def without_run_local_ids(report: dict[str, Any]) -> dict[str, Any]:
    """Drop source ids that change every ingest so baselines can be diffed."""
    copy = json.loads(json.dumps(report))
    for mode in copy.get("modes", {}).values():
        for case in mode.get("cases") or []:
            case.pop("retrieved", None)
            case.pop("leaked_sources", None)
    return copy


def format_summary(report: dict[str, Any]) -> str:
    lines = [
        f"{report['dataset']} {report['dataset_version']}  commit={report['code_commit']}",
        (
            f"embeddings {report['embedding_provider']}/"
            f"{report['embedding_model']}@{report['embedding_version']} "
            f"dim={report['embedding_dimensions']}"
        ),
    ]
    for mode, body in sorted(report["modes"].items()):
        lines.append(
            f"{mode}: n={body['evaluated_cases']}/{body['total_cases']} "
            f"R@1={body['recall_at_1']} R@3={body['recall_at_3']} "
            f"R@5={body['recall_at_5']} "
            f"P@1={body['precision_at_1']} P@3={body['precision_at_3']} "
            f"P@5={body['precision_at_5']} "
            f"MRR={body['mrr']} leakage={body['cross_tenant_leakage']} "
            f"zero_hit={body['cases_with_zero_relevant_retrieval']}"
        )
        if body.get("failed_case_ids"):
            lines.append("  failed: " + ", ".join(body["failed_case_ids"]))
    return "\n".join(lines)
