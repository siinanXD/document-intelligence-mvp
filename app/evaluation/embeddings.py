"""Deterministic bag-of-words embeddings for CI evaluation.

Hashed unigrams and bigrams, no network. The space is stable for a given
`version` so a ranking regression is a real change in retrieval or chunking,
not noise from a paid model. It is not a substitute for a live-provider run.
"""

import hashlib
import math
import re

from app.providers.base import EmbeddingProvider

# Keep identifiers such as 6ES7315-2EH14-0AB0 and PL-04 as single tokens.
_TOKEN = re.compile(r"[0-9A-Za-zÄÖÜäöüß]+(?:[._/-][0-9A-Za-zÄÖÜäöüß]+)*")


class HashingEmbeddings(EmbeddingProvider):
    """Signed hashing-trick embeddings. Deterministic, offline, 64 dimensions."""

    provider = "evaluation"
    model = "hashing-bow"
    version = "v1"
    dimensions = 64

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [_hash_vector(text, self.dimensions) for text in texts]


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN.finditer(text)]


def _hash_vector(text: str, dimensions: int) -> list[float]:
    tokens = tokenize(text)
    features = list(tokens)
    features.extend(f"{left}_{right}" for left, right in zip(tokens, tokens[1:], strict=False))
    if not features:
        vector = [0.0] * dimensions
        vector[-1] = 1.0
        return vector

    values = [0.0] * dimensions
    for feature in features:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        values[index] += sign
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]
