import pytest

from app.core.text import normalize_whitespace


def test_normalize_whitespace_collapses_ascii_runs_and_trims():
    assert normalize_whitespace("  hello   world  ") == "hello world"


def test_normalize_whitespace_collapses_tabs_and_newlines():
    assert normalize_whitespace("a\tb\nc\r\nd") == "a b c d"


def test_normalize_whitespace_collapses_non_breaking_spaces():
    assert normalize_whitespace("a\u00a0\u00a0b") == "a b"


def test_normalize_whitespace_empty_and_whitespace_only():
    assert normalize_whitespace("") == ""
    assert normalize_whitespace("   \n\t  ") == ""


def test_normalize_whitespace_leaves_non_whitespace_untouched():
    assert normalize_whitespace("Grüße, Ärger!") == "Grüße, Ärger!"


def test_normalize_whitespace_rejects_non_str():
    with pytest.raises(TypeError):
        normalize_whitespace(None)
