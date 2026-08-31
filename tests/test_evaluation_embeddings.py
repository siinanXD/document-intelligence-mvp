"""Hashing embeddings must actually rank, not assign a constant vector."""

from app.evaluation.embeddings import HashingEmbeddings


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


async def test_hashing_embeddings_rank_a_matching_query_higher():
    embeddings = HashingEmbeddings()
    query, close, far = await embeddings.embed(
        [
            "How often must motor M3 be inspected?",
            "Motor M3 is inspected every 500 operating hours.",
            "Invoice INV-2024-0042 is due within thirty days.",
        ]
    )
    assert _cosine(query, close) > _cosine(query, far)
    assert embeddings.provider == "evaluation"
    assert embeddings.dimensions == 64
