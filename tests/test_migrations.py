"""The migration chain is well-formed and reads its URL from settings."""

from alembic.config import Config
from alembic.script import ScriptDirectory


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config("alembic.ini"))


def test_migration_chain_has_a_single_head():
    assert len(_script_directory().get_heads()) == 1


def test_chain_starts_at_the_baseline_and_every_revision_is_reversible():
    script = _script_directory()
    revisions = list(script.walk_revisions())

    assert revisions[-1].revision == "0001_initial_baseline"
    for revision in revisions:
        module = revision.module
        assert hasattr(module, "upgrade")
        assert hasattr(module, "downgrade")


def test_alembic_ini_holds_no_database_url():
    with open("alembic.ini") as handle:
        content = handle.read()

    assert "sqlalchemy.url" not in content


def test_every_revision_id_fits_alembics_version_column():
    """Alembic stores the revision in a VARCHAR(32).

    A longer id runs its DDL and then fails to record itself, leaving the
    database changed but the version table pointing at the previous revision -
    a state every later upgrade then trips over.
    """
    for revision in _script_directory().walk_revisions():
        assert len(revision.revision) <= 32, (
            f"revision id {revision.revision!r} is {len(revision.revision)} characters; "
            "alembic_version.version_num holds 32"
        )
