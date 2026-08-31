"""Generation envelope, settings wiring, tokens, cost, timeout and retries."""

import httpx
import pytest
from openai import APITimeoutError, BadRequestError
from pydantic import BaseModel, ValidationError

from app.core.correlation import bind_request_id, reset_request_id
from app.core.settings import Settings, get_settings
from app.evaluation.generation import generation_eval_record
from app.providers.generation import GenerationResult, estimate_cost_usd
from app.providers.openai_provider import OpenAILLMProvider, ProviderResponseError
from app.providers.prompts import ASK_GROUNDED
from app.services.qa import AskResult, GroundedAnswer
from tests.test_providers import _TEST_PROMPT, _FakeChatClient, _FakeCompletions


class _Usage:
    def __init__(self, prompt_tokens, completion_tokens, total_tokens=None) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


def _timeout():
    return APITimeoutError(
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    )


def _bad_request():
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return BadRequestError("bad request", response=httpx.Response(400, request=request), body=None)


class _FlakyCompletions(_FakeCompletions):
    def __init__(self, fail_times: int, factory, **kwargs) -> None:
        super().__init__(**kwargs)
        self._fail_times = fail_times
        self._factory = factory
        self.attempts = 0

    async def create(self, **kwargs):
        self.attempts += 1
        self.calls.append(kwargs)
        if self.attempts <= self._fail_times:
            raise self._factory()
        return self._response({"content": self._content})

    async def parse(self, **kwargs):
        self.attempts += 1
        self.calls.append(kwargs)
        if self.attempts <= self._fail_times:
            raise self._factory()
        return self._response({"parsed": self._parsed})


def test_generation_settings_have_explicit_deterministic_defaults():
    settings = Settings(_env_file=None)

    assert settings.llm_timeout_seconds == 30.0
    assert settings.llm_max_retries == 2
    assert settings.llm_max_output_tokens == 1024
    assert settings.llm_temperature == 0.0
    assert settings.llm_input_usd_per_million is None
    assert settings.llm_output_usd_per_million is None
    assert settings.tracing_provider == "none"
    assert settings.tracing_capture_content is False


def test_generation_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(llm_timeout_seconds=0, _env_file=None)


def test_debug_log_level_does_not_enable_content_capture(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = get_settings()
    assert settings.log_level == "DEBUG"
    assert settings.tracing_capture_content is False


def test_unknown_cost_stays_unknown():
    assert (
        estimate_cost_usd(
            input_tokens=10,
            output_tokens=20,
            input_usd_per_million=None,
            output_usd_per_million=None,
        )
        is None
    )
    assert (
        estimate_cost_usd(
            input_tokens=None,
            output_tokens=20,
            input_usd_per_million=0.15,
            output_usd_per_million=0.6,
        )
        is None
    )


def test_known_cost_is_calculated_from_configured_prices():
    cost = estimate_cost_usd(
        input_tokens=1_000_000,
        output_tokens=500_000,
        input_usd_per_million=0.15,
        output_usd_per_million=0.6,
    )
    assert cost == 0.45


async def test_plain_completion_captures_usage_and_leaves_cost_unknown():
    usage = _Usage(11, 7, 18)
    completions = _FakeCompletions(content="hello", usage=usage)
    provider = OpenAILLMProvider(client=_FakeChatClient(completions))

    result = await provider.complete(_TEST_PROMPT, "user")

    assert result.content == "hello"
    assert result.input_tokens == 11
    assert result.output_tokens == 7
    assert result.total_tokens == 18
    assert result.estimated_cost_usd is None
    assert result.finish_reason == "stop"
    assert result.retry_count == 0
    assert result.provider == "openai"


async def test_structured_completion_keeps_pydantic_content_and_derives_total_tokens():
    class Item(BaseModel):
        title: str

    usage = _Usage(4, 2, None)
    completions = _FakeCompletions(parsed=Item(title="Invoice"), usage=usage)
    provider = OpenAILLMProvider(
        client=_FakeChatClient(completions),
        input_usd_per_million=1.0,
        output_usd_per_million=2.0,
    )

    result = await provider.complete_structured(_TEST_PROMPT, "user", Item)

    assert isinstance(result, GenerationResult)
    assert result.content == Item(title="Invoice")
    assert result.total_tokens == 6
    assert result.estimated_cost_usd == 0.000008
    assert result.prompt_name == "test_prompt"


async def test_retries_transient_timeouts_and_records_the_count():
    completions = _FlakyCompletions(fail_times=2, factory=_timeout, content="recovered")
    provider = OpenAILLMProvider(client=_FakeChatClient(completions), max_retries=2)

    result = await provider.complete(_TEST_PROMPT, "user")

    assert result.content == "recovered"
    assert result.retry_count == 2
    assert completions.attempts == 3


async def test_retry_budget_is_bounded():
    completions = _FlakyCompletions(fail_times=9, factory=_timeout, content="nope")
    provider = OpenAILLMProvider(client=_FakeChatClient(completions), max_retries=2)

    with pytest.raises(APITimeoutError):
        await provider.complete(_TEST_PROMPT, "user")

    assert completions.attempts == 3


async def test_deterministic_client_errors_are_not_retried():
    completions = _FlakyCompletions(fail_times=9, factory=_bad_request, content="nope")
    provider = OpenAILLMProvider(client=_FakeChatClient(completions), max_retries=5)

    with pytest.raises(BadRequestError):
        await provider.complete(_TEST_PROMPT, "user")

    assert completions.attempts == 1


async def test_unparseable_structured_output_is_not_retried():
    completions = _FakeCompletions(parsed=None)
    provider = OpenAILLMProvider(client=_FakeChatClient(completions), max_retries=5)

    with pytest.raises(ProviderResponseError):
        await provider.complete_structured(_TEST_PROMPT, "user", GroundedAnswer)

    assert len(completions.calls) == 1


async def test_generation_carries_the_http_request_id():
    completions = _FakeCompletions(content="ok")
    provider = OpenAILLMProvider(client=_FakeChatClient(completions))
    token = bind_request_id("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    try:
        result = await provider.complete(_TEST_PROMPT, "user")
    finally:
        reset_request_id(token)

    assert result.request_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert result.trace_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_sin74_record_shape_is_stable():
    answer = GroundedAnswer(
        answer="Within thirty days.", source_ids=["s1"], has_sufficient_evidence=True
    )
    generation = GenerationResult(
        content=answer,
        provider="openai",
        model="gpt-4o-mini",
        prompt_name=ASK_GROUNDED.name,
        prompt_version=ASK_GROUNDED.version,
        latency_ms=12.5,
        retry_count=0,
        trace_id="trace-1",
        input_tokens=10,
        output_tokens=4,
        total_tokens=14,
        estimated_cost_usd=None,
        finish_reason="stop",
        request_id="req-1",
    )
    result = AskResult(
        answer=answer.answer,
        has_sufficient_evidence=True,
        conflicting=False,
        considered=3,
        generation=generation,
    )
    record = generation_eval_record(case_id="case-1", result=result)

    assert record["dataset_case_id"] == "case-1"
    assert record["prompt_name"] == "ask_grounded"
    assert record["prompt_version"] == "v1"
    assert record["provider"] == "openai"
    assert record["model"] == "gpt-4o-mini"
    assert record["input_tokens"] == 10
    assert record["output_tokens"] == 4
    assert record["total_tokens"] == 14
    assert record["latency_ms"] == 12.5
    assert record["estimated_cost_usd"] is None
    assert record["finish_reason"] == "stop"
    assert record["trace_id"] == "trace-1"
    assert record["request_id"] == "req-1"
    assert "answer" not in record
    assert "question" not in record
