"""Read-only cockpit metadata.

Provider names, dependency health and checked-in evaluation summaries. Never
returns secrets, database URLs, storage credentials or evaluation passages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import __version__
from app.core.settings import get_settings
from app.evaluation.compare import compare_reports
from app.services.health import check_dependencies

REPO_ROOT = Path(__file__).resolve().parents[2]
RETRIEVAL_BASELINE = REPO_ROOT / "evaluation" / "baselines" / "retrieval-v1.json"
GENERATION_BASELINE = REPO_ROOT / "evaluation" / "baselines" / "generation-v1.json"


def _strip_cases(payload: dict[str, Any]) -> dict[str, Any]:
    copy = json.loads(json.dumps(payload))
    for mode in (copy.get("modes") or {}).values():
        mode.pop("cases", None)
        mode.pop("failed_case_ids", None)
    summary = copy.get("summary")
    if isinstance(summary, dict):
        summary.pop("cases", None)
        summary.pop("retrieval_failed_case_ids", None)
        summary.pop("generation_failed_case_ids", None)
    return copy


def _evaluation_track(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    report = payload.get("report") or payload
    comparison = compare_reports(report, payload)
    public = _strip_cases(report)
    return {
        "dataset": public.get("dataset"),
        "dataset_version": public.get("dataset_version"),
        "track": public.get("track"),
        "embedding_provider": public.get("embedding_provider"),
        "embedding_model": public.get("embedding_model"),
        "embedding_version": public.get("embedding_version"),
        "llm_provider": public.get("llm_provider"),
        "llm_model": public.get("llm_model"),
        "prompt_name": public.get("prompt_name"),
        "prompt_version": public.get("prompt_version"),
        "gate_passed": comparison.passed,
        "leakage": comparison.leakage,
        "summary": public.get("summary") or public.get("modes"),
        "thresholds": payload.get("thresholds") or {},
    }


def provider_identity() -> dict[str, Any]:
    settings = get_settings()
    return {
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_version": settings.embedding_version,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "embedding_configured": bool(settings.openai_api_key),
        "llm_configured": bool(settings.openai_api_key),
    }


async def cockpit_snapshot() -> dict[str, Any]:
    settings = get_settings()
    statuses = await check_dependencies()
    checks = {item.name: "up" if item.healthy else "down" for item in statuses}
    ready = all(item.healthy for item in statuses)
    return {
        "status": "ready" if ready else "degraded",
        "version": __version__,
        "environment": settings.environment,
        "checks": checks,
        "providers": provider_identity(),
        "evaluation": {
            "retrieval": _evaluation_track(RETRIEVAL_BASELINE),
            "generation": _evaluation_track(GENERATION_BASELINE),
        },
    }
