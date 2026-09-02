"""Small, dependency-free string helpers."""


def normalize_whitespace(value: str) -> str:
    """Collapse Unicode whitespace runs to a single ASCII space and trim.

    Every run of one or more Unicode whitespace characters is replaced with a
    single ASCII space. Leading and trailing whitespace is stripped. Empty or
    whitespace-only input returns an empty string. Non-whitespace characters
    are left unchanged: no case folding, Unicode normalization, or punctuation
    handling. Raises TypeError if value is not a str.
    """
    if not isinstance(value, str):
        raise TypeError("value must be a str")
    return " ".join(value.split())
