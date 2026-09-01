"""Normalise DATABASE_URL values from hosted Postgres providers.

Railway's plugin emits `postgres://` or `postgresql://` with `sslmode=require`.
SQLAlchemy's async engine needs `postgresql+asyncpg://`. The `sslmode` query
parameter is renamed to `ssl` for asyncpg without changing the requested mode.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_ASYNC_PREFIX = "postgresql+asyncpg://"


def async_database_url(url: str) -> str:
    """Return a SQLAlchemy asyncpg URL. Unrecognised schemes are left alone."""
    if url.startswith(_ASYNC_PREFIX):
        scheme_url = url
    elif url.startswith("postgres://"):
        scheme_url = _ASYNC_PREFIX + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        scheme_url = _ASYNC_PREFIX + url.removeprefix("postgresql://")
    else:
        return url

    parts = urlsplit(scheme_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    sslmode = query.pop("sslmode", None)
    if sslmode and "ssl" not in query:
        # Rename only. Do not collapse verify-ca / verify-full to require.
        query["ssl"] = sslmode
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
