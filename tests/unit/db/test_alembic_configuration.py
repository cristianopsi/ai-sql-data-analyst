from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_configuration_uses_project_migrations() -> None:
    config = Config("alembic.ini")

    assert config.get_main_option("script_location") == "migrations"
    assert config.get_main_option("prepend_sys_path") == "."
    assert config.get_main_option("sqlalchemy.url") == ""


def test_migration_environment_files_exist() -> None:
    expected_files = {
        Path("alembic.ini"),
        Path("migrations/env.py"),
        Path("migrations/script.py.mako"),
        Path("migrations/README"),
        Path("migrations/versions/.gitkeep"),
    }

    assert all(path.is_file() for path in expected_files)


def test_script_directory_contains_initial_revision() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    revisions = list(scripts.walk_revisions())

    assert scripts.get_heads() == ["4b9f67039f0b"]
    assert [revision.revision for revision in revisions] == ["4b9f67039f0b"]
    assert revisions[0].down_revision is None
