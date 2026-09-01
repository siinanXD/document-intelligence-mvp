"""Optional LLM-judge for groundedness and completeness.

The judge never decides a release. Deterministic citation, leakage,
no-evidence and conflict checks are the gate. A live judge is opt-in,
fail-open, bounded by the caller, and is not used in ordinary CI.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.providers.base import LLMProvider
from app.providers.prompts import GENERATION_EVAL_JUDGE
from app.services.qa import AskResult


class JudgeVerdict(BaseModel):
    groundedness: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class JudgeScore:
    groundedness: float | None
    completeness: float | None
    error: str | None = None


class NullJudge:
    """No-op judge used in ordinary CI."""

    name = "none"

    async def score(
        self,
        *,
        question: str,
        result: AskResult,
        expected_facts: tuple[str, ...],
        forbidden_claims: tuple[str, ...],
    ) -> JudgeScore:
        del question, result, expected_facts, forbidden_claims
        return JudgeScore(groundedness=None, completeness=None)


class LiveJudge:
    """Structured judge behind LLMProvider. Fail-open on provider errors."""

    name = "live"

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def score(
        self,
        *,
        question: str,
        result: AskResult,
        expected_facts: tuple[str, ...],
        forbidden_claims: tuple[str, ...],
    ) -> JudgeScore:
        passages = (
            "\n\n".join(f"[{hit.source_id}] {hit.text}" for hit in result.sources)
            or "(no cited passages)"
        )
        user = (
            f"Question: {question}\n"
            f"Expected facts: {list(expected_facts)}\n"
            f"Forbidden claims: {list(forbidden_claims)}\n"
            f"Sufficient evidence flag: {result.has_sufficient_evidence}\n"
            f"Conflict flag: {result.conflicting}\n"
            f"Cited passages:\n{passages}\n"
            f"Answer: {result.answer}"
        )
        try:
            generation = await self._llm.complete_structured(
                GENERATION_EVAL_JUDGE, user, JudgeVerdict
            )
        except Exception as exc:
            return JudgeScore(
                groundedness=None,
                completeness=None,
                error=type(exc).__name__,
            )
        verdict = generation.content
        if not isinstance(verdict, JudgeVerdict):
            return JudgeScore(groundedness=None, completeness=None, error="invalid_verdict")
        return JudgeScore(
            groundedness=verdict.groundedness,
            completeness=verdict.completeness,
        )
