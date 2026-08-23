from collections.abc import Mapping
from logging.config import fileConfig
from typing import Any

from alembic import context
from dotenv import dotenv_values
from sqlalchemy import Connection, engine_from_config, pool

import backend.app.db.models  # noqa: F401
from backend.app.db.base import metadata

config = context.config
target_metadata = metadata


def get_migration_database_url() -> str:
    values = dotenv_values(".env")
    database_url = values.get("MIGRATION_DATABASE_URL")

    if not database_url:
        raise RuntimeError("MIGRATION_DATABASE_URL must be configured")

    return database_url


def include_name(
    name: str | None,
    type_: str,
    parent_names: Mapping[str, str | None],
) -> bool:
    if type_ == "schema":
        return name in {None, "retail"}

    if type_ == "table":
        return parent_names.get("schema_name") == "retail"

    return True


def configure_context(
    connection: Connection | None = None,
) -> None:
    options: dict[str, Any] = {
        "target_metadata": target_metadata,
        "include_schemas": True,
        "include_name": include_name,
        "compare_type": True,
        "compare_server_default": True,
        "transaction_per_migration": True,
    }

    if connection is None:
        context.configure(
            url=get_migration_database_url(),
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            **options,
        )
    else:
        context.configure(
            connection=connection,
            **options,
        )


def run_migrations_offline() -> None:
    configure_context()

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    database_url = get_migration_database_url()
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("%", "%%"),
    )

    configuration = config.get_section(config.config_ini_section)

    if configuration is None:
        raise RuntimeError("Alembic configuration section was not found")

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        configure_context(connection)

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
