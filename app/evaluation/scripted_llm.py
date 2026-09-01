"""Deterministic LLM for generation evaluation in ordinary CI.

The scripted provider answers from gold labels **only when the matching
evidence was actually supplied in the prompt**. It never invents source ids
that were not given, and it never uses tenant-B facts for a tenant-A case.
This exercises the real `/ask` path without a paid provider.

It is not a quality target: live-provider runs use the configured LLM.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from app.evaluation.cases import GenerationCase
from app.providers.base import LLMProvider, SchemaT
from app.providers.generation import GenerationResult, generation_from_prompt
from app.providers.prompts import Prompt
from app.services.qa import NO_EVIDENCE, GroundedAnswer

_SOURCE_BLOCK = re.compile(
    r"\[source_id: (?P<source_id>[^\]]+)\] (?P<header>[^\n]*)\n(?P<text>.*?)(?=\n\[source_id: |\Z)",
    re.DOTALL,
)


@dataclass(frozen=True)
class _SuppliedSource:
    source_id: str
    filename: str
    text: str


class ScriptedLLM(LLMProvider):
    """Oracle constrained to retrieved passages. No network."""

    provider = "evaluation"
    model = "scripted-grounded"

    def __init__(self, cases: tuple[GenerationCase, ...]) -> None:
        self._by_query = {case.query.strip(): case for case in cases}
        self.calls: list[str] = []

    async def complete(self, prompt: Prompt, user: str) -> GenerationResult[str]:
        raise AssertionError("generation evaluation must use the structured path")

    async def complete_structured(
        self, prompt: Prompt, user: str, schema: type[SchemaT]
    ) -> GenerationResult[SchemaT]:
        started = time.perf_counter()
        self.calls.append(prompt.name)
        if schema is not GroundedAnswer:
            raise AssertionError("scripted LLM only answers GroundedAnswer")
        question = _question_from_user(user)
        case = self._by_query.get(question)
        supplied = parse_supplied_sources(user)
        content = schema.model_validate(_answer_case(case, supplied).model_dump())
        latency_ms = (time.perf_counter() - started) * 1000
        return generation_from_prompt(
            content,
            prompt,
            provider=self.provider,
            model=self.model,
            latency_ms=latency_ms,
            finish_reason="stop",
            trace_id="eval-scripted",
        )


def parse_supplied_sources(user: str) -> list[_SuppliedSource]:
    sources: list[_SuppliedSource] = []
    for match in _SOURCE_BLOCK.finditer(user):
        header = match.group("header").strip()
        filename = header.split(" (", 1)[0].strip()
        sources.append(
            _SuppliedSource(
                source_id=match.group("source_id").strip(),
                filename=filename,
                text=match.group("text").strip(),
            )
        )
    return sources


def _question_from_user(user: str) -> str:
    marker = "\n\nQuestion: "
    index = user.rfind(marker)
    if index == -1:
        return user.strip()
    return user[index + len(marker) :].strip()


def _answer_case(case: GenerationCase | None, supplied: list[_SuppliedSource]) -> GroundedAnswer:
    if case is None or not case.answerable:
        return GroundedAnswer(
            answer=NO_EVIDENCE,
            source_ids=[],
            has_sufficient_evidence=False,
            conflicting=False,
        )

    cited: list[str] = []
    matched_contains: set[str] = set()
    for judgment in case.relevant:
        for source in supplied:
            if judgment.contains in source.text and source.source_id not in cited:
                cited.append(source.source_id)
                matched_contains.add(judgment.contains)
                break

    if len(matched_contains) < len(case.relevant):
        return GroundedAnswer(
            answer=NO_EVIDENCE,
            source_ids=[],
            has_sufficient_evidence=False,
            conflicting=False,
        )

    if case.expected_conflict:
        return GroundedAnswer(
            answer="The sources disagree: " + "; ".join(case.expected_facts),
            source_ids=cited,
            has_sufficient_evidence=True,
            conflicting=True,
        )

    return GroundedAnswer(
        answer=" ".join(case.expected_facts),
        source_ids=cited,
        has_sufficient_evidence=True,
        conflicting=False,
    )
