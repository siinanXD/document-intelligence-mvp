"""Provider interfaces and the OpenAI implementations.

Every external call is faked; no test may reach the network.
"""

from dataclasses import dataclass

import pytest
from app.providers.base import EmbeddingProvider, LLMProvider
from app.providers.openai_provider import (
    OpenAIEmbeddingProvider,
    OpenAILLMProvider,
    ProviderResponseError,
)
from app.providers.registry import (
    ProviderConfigurationError,
    get_embedding_provider,
    get_llm_provider,
)
from app.providers.storage import StorageBackend
from pydantic import BaseModel


@dataclass
class _Embedding:
    index: int
    embedding: list[float]


class _FakeEmbeddings:
    def __init__(self, out_of_order: bool = False) -> None:
        self.calls: list[dict] = []
        self._out_of_order = out_of_order

    async def create(self, model: str, input: list[str]):
        self.calls.append({"model": model, "input": input})
        items = [_Embedding(index=i, embedding=[float(i), 0.5]) for i in range(len(input))]
        if self._out_of_order:
            items.reverse()
        return type("Response", (), {"data": items})()


class _FakeOpenAIClient:
    def __init__(self, embeddings=None) -> None:
        self.embeddings = embeddings or _FakeEmbeddings()


def test_interfaces_cannot_be_instantiated():
    for interface in (EmbeddingProvider, LLMProvider, StorageBackend):
        with pytest.raises(TypeError):
            interface()


async def test_embedding_provider_returns_one_vector_per_input():
    client = _FakeOpenAIClient()
    provider = OpenAIEmbeddingProvider(client=client)

    vectors = await provider.embed(["alpha", "beta", "gamma"])

    assert len(vectors) == 3
    assert client.embeddings.calls == [
        {"model": "text-embedding-3-small", "input": ["alpha", "beta", "gamma"]}
    ]


async def test_embedding_provider_restores_input_order():
    client = _FakeOpenAIClient(embeddings=_FakeEmbeddings(out_of_order=True))
    provider = OpenAIEmbeddingProvider(client=client)

    vectors = await provider.embed(["a", "b", "c"])

    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]


async def test_embedding_provider_skips_the_call_for_no_input():
    client = _FakeOpenAIClient()

    assert await OpenAIEmbeddingProvider(client=client).embed([]) == []
    assert client.embeddings.calls == []


def test_embedding_provider_exposes_its_identity():
    provider = OpenAIEmbeddingProvider(client=_FakeOpenAIClient(), version="v2")

    assert provider.provider == "openai"
    assert provider.model == "text-embedding-3-small"
    assert provider.version == "v2"
    assert provider.dimensions == 1536


def test_embedding_provider_rejects_an_unknown_model():
    with pytest.raises(ValueError, match="unknown embedding model"):
        OpenAIEmbeddingProvider(client=_FakeOpenAIClient(), model="not-a-model")


class _Profile(BaseModel):
    title: str | None = None


class _FakeCompletions:
    def __init__(self, content: str = "answer", parsed=None) -> None:
        self.calls: list[dict] = []
        self._content = content
        self._parsed = parsed

    def _response(self, payload):
        message = type("Message", (), payload)()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()

    async def create(self, model, messages, **kwargs):
        self.calls.append({"model": model, "messages": messages})
        return self._response({"content": self._content})

    async def parse(self, model, messages, response_format, **kwargs):
        self.calls.append({"model": model, "messages": messages, "schema": response_format})
        return self._response({"parsed": self._parsed})


class _FakeChatClient:
    def __init__(self, completions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()


async def test_llm_provider_returns_completion_text():
    completions = _FakeCompletions(content="grounded answer")
    provider = OpenAILLMProvider(client=_FakeChatClient(completions))

    assert await provider.complete("sys", "user") == "grounded answer"
    assert completions.calls[0]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]


async def test_llm_provider_returns_empty_string_for_empty_content():
    provider = OpenAILLMProvider(client=_FakeChatClient(_FakeCompletions(content=None)))

    assert await provider.complete("sys", "user") == ""


async def test_llm_provider_parses_structured_output():
    completions = _FakeCompletions(parsed=_Profile(title="Contract"))
    provider = OpenAILLMProvider(client=_FakeChatClient(completions))

    result = await provider.complete_structured("sys", "user", _Profile)

    assert result == _Profile(title="Contract")
    assert completions.calls[0]["schema"] is _Profile


def test_llm_provider_exposes_its_identity():
    provider = OpenAILLMProvider(client=_FakeChatClient(_FakeCompletions()), model="gpt-4o")

    assert provider.provider == "openai"
    assert provider.model == "gpt-4o"


def test_registry_refuses_to_build_providers_without_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        get_embedding_provider()
    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        get_llm_provider()


def test_registry_builds_configured_providers_without_calling_out(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-secret")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")

    embeddings = get_embedding_provider()
    llm = get_llm_provider()

    assert embeddings.model == "text-embedding-3-large"
    assert embeddings.dimensions == 3072
    assert llm.model == "gpt-4o"


async def test_structured_output_raises_instead_of_returning_none():
    """A refusal arrives as parsed=None; returning it defers the failure."""
    completions = _FakeCompletions(parsed=None)
    provider = OpenAILLMProvider(client=_FakeChatClient(completions), model="gpt-4o")

    with pytest.raises(ProviderResponseError, match="no parsable _Profile"):
        await provider.complete_structured("sys", "user", _Profile)
