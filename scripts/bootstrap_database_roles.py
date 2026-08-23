import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from backend.app.db.role_provisioning import (
    RoleConfigurationError,
    build_role_specs,
    provision_database_roles,
    require_value,
    sqlalchemy_to_psycopg_url,
)


def main() -> int:
    env_path = Path(".env")

    if not env_path.is_file():
        print("ROLE_CONFIGURATION_ERROR:.env not found", file=sys.stderr)
        return 2

    values = dotenv_values(env_path)

    try:
        migration_url = require_value(
            values,
            "MIGRATION_DATABASE_URL",
        )
        database_name = require_value(values, "POSTGRES_DB")
        roles = build_role_specs(values)
        psycopg_url = sqlalchemy_to_psycopg_url(migration_url)

        with psycopg.connect(
            psycopg_url,
            connect_timeout=5,
        ) as connection:
            actions = provision_database_roles(
                connection,
                database_name,
                roles,
            )
    except RoleConfigurationError as error:
        print(f"ROLE_CONFIGURATION_ERROR:{error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(
            f"ROLE_PROVISIONING_FAILED:{type(error).__name__}",
            file=sys.stderr,
        )
        return 1

    print("DATABASE_ROLES_PROVISIONED")

    for action in actions:
        print(f"role_action={action}")

    print("database_connect_public_revoked=True")
    print("database_temporary_public_revoked=True")
    print("public_schema_create_public_revoked=True")
    print("credentials_printed=False")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
