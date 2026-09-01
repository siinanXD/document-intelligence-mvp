"""Compare a run against a checked-in baseline and its thresholds."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

METRIC_KEYS = (
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "precision_at_1",
    "precision_at_3",
    "precision_at_5",
    "mrr",
)


@dataclass
class Comparison:
    leakage: int
    threshold_failures: list[str] = field(default_factory=list)
    changed_cases: list[dict[str, Any]] = field(default_factory=list)
    deltas: dict[str, dict[str, float | None]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.leakage == 0 and not self.threshold_failures


def compare_reports(current: dict[str, Any], baseline: dict[str, Any]) -> Comparison:
    if current.get("track") == "generation":
        return compare_generation_reports(current, baseline)
    return compare_retrieval_reports(current, baseline)


def compare_retrieval_reports(current: dict[str, Any], baseline: dict[str, Any]) -> Comparison:
    thresholds = baseline.get("thresholds") or {}
    leakage = 0
    failures: list[str] = []
    deltas: dict[str, dict[str, float | None]] = {}
    changed: list[dict[str, Any]] = []

    for mode, body in sorted(current.get("modes", {}).items()):
        leakage += int(body.get("cross_tenant_leakage") or 0)
        previous = (baseline.get("report") or baseline).get("modes", {}).get(mode) or {}
        mode_thresholds = thresholds.get(mode) or {}
        mode_deltas: dict[str, float | None] = {}
        for key in METRIC_KEYS:
            now = body.get(key)
            then = previous.get(key)
            if now is not None and then is not None:
                mode_deltas[key] = round(float(now) - float(then), 4)
            else:
                mode_deltas[key] = None
            floor = mode_thresholds.get(key)
            if floor is not None and now is not None and float(now) + 1e-9 < float(floor):
                failures.append(f"{mode}.{key}={now} below threshold {floor}")
        leak_count = int(body.get("cross_tenant_leakage") or 0)
        leak_floor = int(mode_thresholds.get("cross_tenant_leakage") or 0)
        if leak_count > leak_floor:
            failures.append(f"{mode}.cross_tenant_leakage={leak_count} exceeds {leak_floor}")
        deltas[mode] = mode_deltas
        changed.extend(_changed_cases(mode, body, previous))

    if leakage:
        failures.append(f"cross_tenant_leakage={leakage} (hard invariant is 0)")

    return Comparison(
        leakage=leakage, threshold_failures=failures, changed_cases=changed, deltas=deltas
    )


GENERATION_QUALITY_KEYS = (
    "citation_precision",
    "unanswerable_correct_rate",
    "conflict_correct_rate",
    "factual_citation_rate",
)

GENERATION_HARD_KEYS = (
    "cross_tenant_leakage",
    "foreign_source_ids",
    "unresolvable_citations",
    "forbidden_claim_cases",
)


def compare_generation_reports(current: dict[str, Any], baseline: dict[str, Any]) -> Comparison:
    thresholds = baseline.get("thresholds") or {}
    body = current.get("summary") or {}
    previous = (baseline.get("report") or baseline).get("summary") or {}
    leakage = int(body.get("cross_tenant_leakage") or 0)
    failures: list[str] = []
    deltas: dict[str, float | None] = {}

    if current.get("stopped_reason"):
        failures.append(f"stopped_reason={current['stopped_reason']}")

    now_total = int(body.get("total_cases") or 0)
    then_total = int(previous.get("total_cases") or 0)
    if now_total != then_total:
        failures.append(f"total_cases={now_total} != baseline {then_total}")

    now_ids = {item["case_id"] for item in body.get("cases") or []}
    then_ids = {item["case_id"] for item in previous.get("cases") or []}
    if now_ids != then_ids:
        missing = sorted(then_ids - now_ids)
        extra = sorted(now_ids - then_ids)
        if missing:
            failures.append(f"missing_cases={','.join(missing)}")
        if extra:
            failures.append(f"extra_cases={','.join(extra)}")

    for key in GENERATION_HARD_KEYS:
        now = int(body.get(key) or 0)
        floor = int(thresholds.get(key) or 0)
        if now > floor:
            failures.append(f"{key}={now} exceeds {floor}")

    for key in GENERATION_QUALITY_KEYS:
        now = body.get(key)
        then = previous.get(key)
        if now is not None and then is not None:
            deltas[key] = round(float(now) - float(then), 4)
        else:
            deltas[key] = None
        floor = thresholds.get(key)
        if floor is not None and now is None:
            failures.append(f"{key} is missing; threshold is {floor}")
        elif floor is not None and float(now) + 1e-9 < float(floor):
            failures.append(f"{key}={now} below threshold {floor}")

    if leakage:
        failures.append(f"citation_leakage={leakage} (hard invariant is 0)")

    return Comparison(
        leakage=leakage,
        threshold_failures=failures,
        changed_cases=_changed_generation_cases(body, previous),
        deltas={"generation": deltas},
    )


def _changed_generation_cases(current: dict[str, Any], previous: dict[str, Any]) -> list[dict]:
    now_cases = {item["case_id"]: item for item in current.get("cases") or []}
    then_cases = {item["case_id"]: item for item in previous.get("cases") or []}
    changed = []
    fields = (
        "retrieval_failed",
        "generation_failed",
        "no_evidence_correct",
        "conflict_correct",
        "factual_with_resolvable_citations",
    )
    for case_id in sorted(set(now_cases) | set(then_cases)):
        now = now_cases.get(case_id)
        then = then_cases.get(case_id)
        if now is None or then is None:
            changed.append({"mode": "generation", "case_id": case_id, "change": "added_or_removed"})
            continue
        for name in fields:
            if now.get(name) != then.get(name):
                changed.append(
                    {
                        "mode": "generation",
                        "case_id": case_id,
                        "field": name,
                        "before": then.get(name),
                        "after": now.get(name),
                    }
                )
    return changed


def _changed_cases(mode: str, current: dict[str, Any], previous: dict[str, Any]) -> list[dict]:
    now_cases = {item["case_id"]: item for item in current.get("cases") or []}
    then_cases = {item["case_id"]: item for item in previous.get("cases") or []}
    changed = []
    for case_id in sorted(set(now_cases) | set(then_cases)):
        now = now_cases.get(case_id)
        then = then_cases.get(case_id)
        if now is None or then is None:
            changed.append({"mode": mode, "case_id": case_id, "change": "added_or_removed"})
            continue
        for name in ("recall_at_5", "mrr", "no_relevant_hit"):
            if now.get(name) != then.get(name):
                changed.append(
                    {
                        "mode": mode,
                        "case_id": case_id,
                        "field": name,
                        "before": then.get(name),
                        "after": now.get(name),
                    }
                )
    return changed
