"""Tracing adapters: no-op, fail-open, optional Langfuse."""

from types import SimpleNamespace

from app.core.settings import Settings
from app.providers.generation import RetrievalSourceTrace, RetrievalTrace, generation_from_prompt
from app.providers.langfuse_adapter import LangfuseTracingAdapter
from app.providers.openai_provider import OpenAILLMProvider
from app.providers.prompts import ASK_GROUNDED
from app.providers.tracing import (
    FailOpenTracingAdapter,
    NullTracingAdapter,
    TracingAdapter,
    build_tracing_adapter,
    get_tracing_adapter,
)
from tests.test_providers import _TEST_PROMPT, _FakeChatClient, _FakeCompletions


class _RecordingTracer(TracingAdapter):
    def __init__(self, *, capture: bool = False, error: Exception | None = None) -> None:
        self.generations: list[dict] = []
        self.retrievals: list[dict] = []
        self._capture = capture
        self._error = error

    @property
    def captures_content(self) -> bool:
        return self._capture

    async def record_generation(
        self, result, *, extra=None, input_payload=None, output_payload=None
    ):
        if self._error:
            raise self._error
        self.generations.append(
            {
                "metadata": result.safe_metadata(),
                "extra": extra,
                "input_payload": input_payload,
                "output_payload": output_payload,
            }
        )

    async def record_retrieval(self, retrieval, *, extra=None):
        if self._error:
            raise self._error
        self.retrievals.append({"metadata": retrieval.safe_metadata(), "extra": extra})


class _FakeLangfuse:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start_as_current_observation(self, **kwargs):
        self.calls.append(kwargs)
        return _NullSpan()


class _NullSpan:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def update(self, **kwargs):
        return None


class _FakePropagate:
    """Stand-in for `langfuse.propagate_attributes`: records the attributes
    it was asked to propagate and behaves as a context manager."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return _NullSpan()


class _TimeoutTracer(_RecordingTracer):
    async def record_generation(
        self, result, *, extra=None, input_payload=None, output_payload=None
    ):
        raise TimeoutError("langfuse hung")


async def test_null_tracer_is_a_noop():
    tracer = NullTracingAdapter()
    result = generation_from_prompt("secret-answer", _TEST_PROMPT, provider="fake", model="fake")
    await tracer.record_generation(result, input_payload="should-not-matter", output_payload="nope")
    await tracer.record_retrieval(
        RetrievalTrace(mode="semantic", candidate_count=0, supplied_count=0)
    )
    assert tracer.captures_content is False


def test_disabled_tracing_builds_a_null_adapter():
    adapter = build_tracing_adapter(Settings(tracing_provider="none", _env_file=None))
    assert isinstance(adapter, NullTracingAdapter)


def test_langfuse_without_credentials_is_a_noop():
    adapter = build_tracing_adapter(Settings(tracing_provider="langfuse", _env_file=None))
    assert isinstance(adapter, NullTracingAdapter)


def test_langfuse_without_the_package_is_a_noop(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "langfuse" or name.startswith("langfuse."):
            raise ImportError("langfuse is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)
    adapter = LangfuseTracingAdapter.from_settings(
        SimpleNamespace(
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            langfuse_host=None,
            langfuse_base_url=None,
            tracing_capture_content=False,
        )
    )
    assert adapter is None


async def test_generation_succeeds_when_the_tracer_raises():
    completions = _FakeCompletions(content="still works")
    provider = OpenAILLMProvider(
        client=_FakeChatClient(completions),
        tracer=_RecordingTracer(error=RuntimeError("tracer exploded")),
    )

    result = await provider.complete(_TEST_PROMPT, "user")

    assert result.content == "still works"


async def test_generation_succeeds_when_the_tracer_times_out():
    completions = _FakeCompletions(content="still works")
    provider = OpenAILLMProvider(client=_FakeChatClient(completions), tracer=_TimeoutTracer())

    result = await provider.complete(_TEST_PROMPT, "user")

    assert result.content == "still works"


async def test_fail_open_wrapper_swallows_adapter_errors():
    inner = _RecordingTracer(error=RuntimeError("nope"))
    adapter = FailOpenTracingAdapter(inner)
    result = generation_from_prompt("x", _TEST_PROMPT, provider="fake", model="fake")
    await adapter.record_generation(result)
    await adapter.record_retrieval(
        RetrievalTrace(mode="semantic", candidate_count=1, supplied_count=1)
    )


async def test_langfuse_adapter_sends_metadata_not_content_by_default():
    client = _FakeLangfuse()
    adapter = LangfuseTracingAdapter(client, capture_content=False)
    result = generation_from_prompt(
        "SECRET_MODEL_ANSWER",
        ASK_GROUNDED,
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=3,
        output_tokens=2,
        total_tokens=5,
    )

    await adapter.record_generation(
        result,
        extra={"request_id": "req-1"},
        input_payload=[{"role": "user", "content": "SECRET_QUESTION"}],
        output_payload="SECRET_MODEL_ANSWER",
    )

    assert client.calls
    payload = client.calls[0]
    blob = str(payload)
    assert "SECRET_QUESTION" not in blob
    assert "SECRET_MODEL_ANSWER" not in blob
    assert payload["name"] == "ask_grounded"
    assert payload["model"] == "gpt-4o-mini"
    assert payload["usage_details"] == {"input": 3, "output": 2, "total": 5}
    assert "input" not in payload
    assert "output" not in payload


async def test_langfuse_adapter_includes_content_only_when_explicitly_enabled():
    client = _FakeLangfuse()
    adapter = LangfuseTracingAdapter(client, capture_content=True)
    result = generation_from_prompt(
        "visible-answer", ASK_GROUNDED, provider="openai", model="gpt-4o-mini"
    )

    await adapter.record_generation(
        result,
        input_payload=[{"role": "user", "content": "visible-question"}],
        output_payload="visible-answer",
    )

    assert client.calls[0]["input"] == [{"role": "user", "content": "visible-question"}]
    assert client.calls[0]["output"] == "visible-answer"


async def test_langfuse_retrieval_span_has_no_chunk_text():
    client = _FakeLangfuse()
    adapter = LangfuseTracingAdapter(client)
    retrieval = RetrievalTrace(
        mode="semantic",
        candidate_count=1,
        supplied_count=1,
        sources=(RetrievalSourceTrace(source_id="d1:00000", document_id="d1", rank=1, score=0.9),),
    )

    await adapter.record_retrieval(retrieval)

    blob = str(client.calls[0])
    assert "chunk" not in blob.lower() or "text" not in client.calls[0]["metadata"]["sources"][0]
    assert "text" not in client.calls[0]["metadata"]["sources"][0]
    assert client.calls[0]["name"] == "retrieval"


async def test_generation_propagates_session_and_user_v4():
    client = _FakeLangfuse()
    propagate = _FakePropagate()
    adapter = LangfuseTracingAdapter(client, propagate=propagate)
    result = generation_from_prompt(
        "answer",
        ASK_GROUNDED,
        provider="openai",
        model="gpt-4o-mini",
        trace_id="req-9",
    )

    await adapter.record_generation(result, extra={"request_id": "req-9", "tenant_id": "tenant-1"})

    # The cost-bearing generation carries the session id (v4 model), and the
    # observation is still created inside that propagation scope.
    assert propagate.calls == [{"session_id": "req-9", "user_id": "tenant-1"}]
    assert client.calls[0]["name"] == "ask_grounded"


async def test_retrieval_propagates_session_and_user_v4():
    client = _FakeLangfuse()
    propagate = _FakePropagate()
    adapter = LangfuseTracingAdapter(client, propagate=propagate)
    retrieval = RetrievalTrace(mode="semantic", candidate_count=1, supplied_count=1)

    await adapter.record_retrieval(
        retrieval, extra={"tenant_id": "tenant-1", "request_id": "req-9"}
    )

    # Retrieval shares the request's session so it groups with its generation.
    assert propagate.calls == [{"session_id": "req-9", "user_id": "tenant-1"}]
    assert client.calls[0]["name"] == "retrieval"


async def test_observation_created_without_propagation_when_unavailable():
    # Unit path with no injected propagate (SDK not installed): still records.
    client = _FakeLangfuse()
    adapter = LangfuseTracingAdapter(client)
    result = generation_from_prompt("a", ASK_GROUNDED, provider="openai", model="gpt-4o-mini")

    await adapter.record_generation(result)

    assert client.calls[0]["name"] == "ask_grounded"


def test_from_settings_uses_v4_base_url_and_environment(monkeypatch):
    import sys
    import types

    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    def _fake_propagate(**kwargs):
        return _NullSpan()

    fake_module = types.ModuleType("langfuse")
    fake_module.Langfuse = _FakeClient
    fake_module.propagate_attributes = _fake_propagate
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    adapter = LangfuseTracingAdapter.from_settings(
        SimpleNamespace(
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            langfuse_host="http://langfuse:3000",
            langfuse_base_url=None,
            environment="staging",
            tracing_capture_content=False,
        )
    )

    assert adapter is not None
    # v4 constructor contract: server URL is `base_url`, not the legacy `host`
    # kwarg, and the deployment environment is stamped on the client.
    assert captured["base_url"] == "http://langfuse:3000"
    assert captured["environment"] == "staging"
    assert "host" not in captured
    assert captured["public_key"] == "pk-test"
    assert captured["secret_key"] == "sk-test"


def test_get_tracing_adapter_defaults_to_null():
    assert isinstance(get_tracing_adapter(), NullTracingAdapter)


def test_services_do_not_import_langfuse():
    from pathlib import Path

    roots = [Path("app/services"), Path("app/api"), Path("app/providers/openai_provider.py")]
    files = []
    for root in roots:
        if root.is_file():
            files.append(root)
        else:
            files.extend(root.rglob("*.py"))
    offenders = [path for path in files if "langfuse" in path.read_text(encoding="utf-8").lower()]
    assert offenders == []
