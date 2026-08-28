"""Text normalization and the content hash.

The hash is stored, so these rules are a contract: changing one changes every
hash that was ever computed.
"""

import pytest

from app.services.normalization import content_hash, normalize_text, source_id_for


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("a\r\nb", "a\nb"),
        ("a\rb", "a\nb"),
        ("a    b", "a b"),
        ("a\t\tb", "a b"),
        ("trailing   \nnext", "trailing\nnext"),
        ("a\n\n\n\n\nb", "a\n\nb"),
        ("  padded  ", "padded"),
        ("keep\n\nparagraphs", "keep\n\nparagraphs"),
    ],
)
def test_normalization_rules(given, expected):
    assert normalize_text(given) == expected


def test_composed_and_decomposed_unicode_agree():
    composed = "Mañana"  # ñ as one code point
    decomposed = "Mañana"  # n + combining tilde

    assert normalize_text(composed) == normalize_text(decomposed)
    assert content_hash(composed) == content_hash(decomposed)


def test_documents_differing_only_in_whitespace_hash_the_same():
    assert content_hash("Acme  pays\r\n\r\n\r\nEUR 1000") == content_hash("Acme pays\n\nEUR 1000")


def test_case_is_preserved():
    """A document that differs in capitalisation is a different document."""
    assert content_hash("Acme") != content_hash("acme")


def test_the_hash_is_a_sha256():
    digest = content_hash("anything")

    assert len(digest) == 64
    assert int(digest, 16) >= 0


def test_the_hash_is_stable_for_a_known_input():
    """Pins the rules: this changing means every stored hash is invalidated.

    The expected value is the SHA-256 of the NFC-normalized string, which for
    this input the rules leave otherwise untouched - derived independently,
    not copied from the implementation's output.
    """
    assert content_hash("Acme agrees to pay EUR 1000 monthly.") == (
        "3d4c5cca17b0514f67188174dda2a9d6a2db9784e54379e962aa4a48dc14d410"
    )


def test_normalization_is_idempotent():
    once = normalize_text("a  \r\n\r\n\r\n  b  ")

    assert normalize_text(once) == once


def test_source_ids_sort_in_chunk_order():
    ids = [source_id_for(document_id="doc", ordinal=n) for n in (0, 1, 2, 10, 100)]

    assert ids == sorted(ids)
    assert ids[0] == "doc:00000"
