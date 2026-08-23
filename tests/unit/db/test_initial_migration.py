from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

EXPECTED_REVISION = "4b9f67039f0b"


def get_initial_migration_source() -> str:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision(EXPECTED_REVISION)

    assert revision is not None
    assert revision.path is not None

    return Path(revision.path).read_text(encoding="utf-8")


def test_initial_migration_is_the_only_head() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision(EXPECTED_REVISION)

    assert scripts.get_heads() == [EXPECTED_REVISION]
    assert revision is not None
    assert revision.down_revision is None


def test_schema_is_created_before_retail_tables() -> None:
    source = get_initial_migration_source()

    assert source.index("CREATE SCHEMA") < source.index("op.create_table")


def test_schema_is_dropped_after_retail_tables() -> None:
    source = get_initial_migration_source()

    assert source.rindex("DROP SCHEMA") > source.rindex("op.drop_table")


def test_migration_contains_eight_reversible_tables() -> None:
    source = get_initial_migration_source()
    downgrade_source = source[source.index("def downgrade") :]

    assert source.count("op.create_table(") == 8
    assert source.count("op.drop_table(") == 8
    assert "CASCADE" not in downgrade_source
