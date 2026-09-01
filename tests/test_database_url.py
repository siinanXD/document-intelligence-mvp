from app.core.database_url import async_database_url
from app.core.settings import Settings


def test_async_database_url_rewrites_railway_postgres_urls():
    assert (
        async_database_url("postgres://u:p@postgres.railway.internal:5432/railway")
        == "postgresql+asyncpg://u:p@postgres.railway.internal:5432/railway"
    )
    assert (
        async_database_url(
            "postgresql://u:p@postgres.railway.internal:5432/railway?sslmode=require"
        )
        == "postgresql+asyncpg://u:p@postgres.railway.internal:5432/railway?ssl=require"
    )


def test_async_database_url_preserves_certificate_verification_modes():
    assert (
        async_database_url("postgresql://u:p@db:5432/railway?sslmode=verify-full")
        == "postgresql+asyncpg://u:p@db:5432/railway?ssl=verify-full"
    )
    assert (
        async_database_url("postgresql://u:p@db:5432/railway?sslmode=verify-ca")
        == "postgresql+asyncpg://u:p@db:5432/railway?ssl=verify-ca"
    )


def test_async_database_url_leaves_asyncpg_and_other_schemes():
    already = "postgresql+asyncpg://u:p@localhost:5432/db"
    assert async_database_url(already) == already
    assert async_database_url("sqlite+aiosqlite:///tmp.db") == "sqlite+aiosqlite:///tmp.db"


def test_settings_accept_railway_postgres_url():
    settings = Settings(
        database_url="postgres://u:p@db:5432/railway?sslmode=require",
        _env_file=None,
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert "ssl=require" in settings.database_url
    assert "sslmode" not in settings.database_url
