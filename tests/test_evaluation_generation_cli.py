"""CLI flags for live generation evaluation stay opt-in and bounded."""

import pytest

from app.core.settings import get_settings
from app.evaluation.__main__ import _apply_live_bounds, _reranker, build_parser
from app.evaluation.generation_runner import (
    LIVE_DEFAULT_MAX_CASES,
    LIVE_DEFAULT_MAX_COST_USD,
    account_case_cost,
)
from app.evaluation.judge import JudgeVerdict, LiveJudge, NullJudge
from app.providers.generation import GenerationResult
from app.providers.registry import ProviderConfigurationError
from app.providers.prompts import GENERATION_EVAL_JUDGE
from app.services.qa import AskResult


def test_scripted_run_does_not_imply_live_bounds():
    args = build_parser().parse_args(["--track", "generation"])
    _apply_live_bounds(args)
    assert args.llm == "scripted"
    assert args.judge == "none"
    assert args.reranker == "none"
    assert args.max_cases is None
    assert args.max_cost_usd is None
    assert args.no_compare is False


def test_configured_reranker_is_opt_in_and_bounded(monkeypatch):
    marker = object()
    monkeypatch.setattr("app.providers.registry.get_reranker", lambda: marker)
    args = build_parser().parse_args(["--reranker", "configured"])

    _apply_live_bounds(args)

    assert args.max_cases == LIVE_DEFAULT_MAX_CASES
    assert _reranker(args.reranker) is marker


def test_configured_reranker_requires_an_enabled_provider(monkeypatch):
    monkeypatch.setattr("app.providers.registry.get_reranker", lambda: None)

    with pytest.raises(ProviderConfigurationError, match="RERANKER_PROVIDER"):
        _reranker("configured")


def test_live_llm_caps_cases_and_skips_scripted_baseline_without_prices():
    args = build_parser().parse_args(["--track", "generation", "--llm", "live"])
    _apply_live_bounds(args)
    assert args.max_cases == LIVE_DEFAULT_MAX_CASES
    assert args.max_cost_usd is None
    assert args.no_compare is True


def test_live_llm_defaults_dollar_cap_when_prices_are_configured(monkeypatch):
    monkeypatch.setenv("LLM_INPUT_USD_PER_MILLION", "0.15")
    monkeypatch.setenv("LLM_OUTPUT_USD_PER_MILLION", "0.60")
    get_settings.cache_clear()
    args = build_parser().parse_args(["--track", "generation", "--llm", "live"])
    _apply_live_bounds(args)
    assert args.max_cost_usd == LIVE_DEFAULT_MAX_COST_USD


def test_explicit_max_cost_without_prices_exits():
    args = build_parser().parse_args(
        ["--track", "generation", "--llm", "live", "--max-cost-usd", "0.25"]
    )
    with pytest.raises(SystemExit) as exc:
        _apply_live_bounds(args)
    assert exc.value.code == 2


def test_account_case_cost_requires_generation_usage_when_capped():
    total, reason = account_case_cost(
        generation_cost=None,
        judge_cost=0.01,
        cost_sum=0.0,
        max_cost_usd=0.50,
        require_generation_cost=True,
    )
    assert total == 0.0
    assert reason == "missing_generation_cost"


def test_account_case_cost_includes_judge_usage():
    total, reason = account_case_cost(
        generation_cost=0.40,
        judge_cost=0.15,
        cost_sum=0.0,
        max_cost_usd=0.50,
        require_generation_cost=True,
    )
    assert total == pytest.approx(0.55)
    assert reason == "max_cost_usd"


async def test_live_judge_is_fail_open():
    class _Boom:
        async def complete_structured(self, prompt, user, schema):
            raise RuntimeError("provider down")

    judge = LiveJudge(_Boom())
    score = await judge.score(
        question="q",
        result=AskResult(answer="a", has_sufficient_evidence=False, conflicting=False),
        expected_facts=(),
        forbidden_claims=(),
    )
    assert score.groundedness is None
    assert score.completeness is None
    assert score.error == "RuntimeError"


async def test_null_judge_returns_no_scores():
    score = await NullJudge().score(
        question="q",
        result=AskResult(answer="a", has_sufficient_evidence=True, conflicting=False),
        expected_facts=("500",),
        forbidden_claims=(),
    )
    assert score.groundedness is None
    assert score.completeness is None
    assert score.error is None


def test_judge_verdict_is_bounded():
    verdict = JudgeVerdict(groundedness=0.5, completeness=1.0)
    assert verdict.groundedness == 0.5


async def test_live_judge_records_estimated_cost():
    class _Ok:
        async def complete_structured(self, prompt, user, schema):
            del user, schema
            assert prompt is GENERATION_EVAL_JUDGE
            return GenerationResult(
                content=JudgeVerdict(groundedness=1.0, completeness=0.5),
                provider="evaluation",
                model="judge",
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                latency_ms=1.0,
                retry_count=0,
                trace_id="t",
                estimated_cost_usd=0.02,
            )

    judge = LiveJudge(_Ok())
    score = await judge.score(
        question="q",
        result=AskResult(answer="a", has_sufficient_evidence=True, conflicting=False),
        expected_facts=("500",),
        forbidden_claims=(),
    )
    assert score.groundedness == 1.0
    assert score.completeness == 0.5
    assert score.estimated_cost_usd == 0.02
    assert score.error is None
