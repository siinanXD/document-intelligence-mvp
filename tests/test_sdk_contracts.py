"""Contract tests against the real vendor SDKs.

Every other provider test drives a fake client, which cannot notice an SDK
moving or renaming a method. These construct a genuine client with a dummy key
and assert only that the call paths the providers use still exist. No request
is made, so they are safe in CI.
"""

import pytest
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient


def _resolve(root, path: str):
    obj = root
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


@pytest.mark.parametrize(
    "path",
    ["embeddings.create", "chat.completions.create", "chat.completions.parse"],
)
def test_openai_client_exposes_the_paths_the_provider_calls(path):
    """`chat.completions.parse` only exists from openai 1.99 onward.

    Before that it lived under `beta`, so a looser floor in pyproject.toml
    would install an SDK where structured output fails at runtime.
    """
    client = AsyncOpenAI(api_key="dummy-key-no-request-is-made")

    assert _resolve(client, path) is not None, f"openai SDK no longer exposes {path}"


@pytest.mark.parametrize("name", ["get_collections", "close"])
def test_qdrant_client_exposes_the_methods_the_service_calls(name):
    # check_compatibility=False keeps construction from probing a server.
    client = AsyncQdrantClient(url="http://127.0.0.1:1", timeout=1, check_compatibility=False)

    assert hasattr(client, name), f"qdrant-client no longer exposes {name}"
