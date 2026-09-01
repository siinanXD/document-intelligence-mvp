"""CLI flags for live generation evaluation stay opt-in and bounded."""

from app.evaluation.__main__ import _apply_live_bounds, build_parser
from app.evaluation.generation_runner import LIVE_DEFAULT_MAX_CASES, LIVE_DEFAULT_MAX_COST_USD
from app.evaluation.judge import JudgeVerdict, LiveJudge, NullJudge
from app.services.qa import AskResult


def test_scripted_run_does_not_imply_live_bounds():
    args = build_parser().parse_args(["--track", "generation"])
    _apply_live_bounds(args)
    assert args.llm == "scripted"
    assert args.judge == "none"
    assert args.max_cases is None
    assert args.max_cost_usd is None
    assert args.no_compare is False


def test_live_llm_applies_case_and_cost_caps_and_skips_scripted_baseline():
    args = build_parser().parse_args(["--track", "generation", "--llm", "live"])
    _apply_live_bounds(args)
    assert args.max_cases == LIVE_DEFAULT_MAX_CASES
    assert args.max_cost_usd == LIVE_DEFAULT_MAX_COST_USD
    assert args.no_compare is True


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
