"""The Qdrant client and the Qdrant server image must stay compatible.

Qdrant refuses a client whose major version differs from the server's, or whose
minor differs by more than one. The client only warns at runtime, so nothing
would fail until a real server is reached - which is exactly the kind of drift
that surfaces in production rather than in CI.
"""

import re
from importlib.metadata import version
from pathlib import Path


def _compose_qdrant_tag() -> tuple[int, int]:
    compose = Path("docker-compose.yml").read_text()
    match = re.search(r"image:\s*qdrant/qdrant:v(\d+)\.(\d+)", compose)
    assert match, "no pinned qdrant/qdrant image found in docker-compose.yml"
    return int(match.group(1)), int(match.group(2))


def _installed_client_version() -> tuple[int, int]:
    major, minor = version("qdrant-client").split(".")[:2]
    return int(major), int(minor)


def test_client_and_server_majors_match():
    assert _installed_client_version()[0] == _compose_qdrant_tag()[0]


def test_client_and_server_minors_differ_by_at_most_one():
    client_minor = _installed_client_version()[1]
    server_minor = _compose_qdrant_tag()[1]

    assert abs(client_minor - server_minor) <= 1, (
        f"qdrant-client {client_minor} and qdrant/qdrant image {server_minor} "
        "are more than one minor apart; bump the image tag or the pin together"
    )
