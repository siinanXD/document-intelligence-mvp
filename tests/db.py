"""Shared helpers for the tests that need a real PostgreSQL.

The schema is created by running the real Alembic migrations rather than
`create_all`, so every run also exercises the migration path a deployment
takes. Each test then works inside a transaction that is rolled back, which
keeps them isolated without re-migrating per test.
"""

import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/document_intelligence_test",
)

# In CI a missing database is a failure, not a reason to quietly pass. Locally
# it is a skip, so contributors without a running PostgreSQL can still work.
REQUIRE_DB = os.environ.get("REQUIRE_DB") == "1"
