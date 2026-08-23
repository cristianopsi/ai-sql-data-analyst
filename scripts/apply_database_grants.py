import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from backend.app.db.grant_provisioning import (
    RetailGrantPolicy,
    apply_retail_grants,
)
from backend.app.db.role_provisioning import (
    RoleConfigurationError,
    require_value,
    sqlalchemy_to_psycopg_url,
)


def main() -> int:
    env_path = Path(".env")

    if not env_path.is_file():
        print("GRANT_CONFIGURATION_ERROR:.env not found", file=sys.stderr)
        return 2

    values = dotenv_values(env_path)

    try:
        migration_url = require_value(
            values,
            "MIGRATION_DATABASE_URL",
        )
        application_role = require_value(
            values,
            "APP_DATABASE_USER",
        )
        analytics_role = require_value(
            values,
            "ANALYTICS_DATABASE_USER",
        )
        psycopg_url = sqlalchemy_to_psycopg_url(migration_url)

        with psycopg.connect(
            psycopg_url,
            connect_timeout=5,
        ) as connection:
            apply_retail_grants(
                connection,
                application_role,
                analytics_role,
            )
    except RoleConfigurationError as error:
        print(f"GRANT_CONFIGURATION_ERROR:{error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(
            f"GRANT_PROVISIONING_FAILED:{type(error).__name__}",
            file=sys.stderr,
        )
        return 1

    policy = RetailGrantPolicy()

    print("RETAIL_GRANTS_APPLIED")
    print(f"analytics_role={analytics_role}")
    print(f"full_select_table_count={len(policy.full_select_tables)}")
    print(f"customer_allowed_column_count={len(policy.allowed_customer_columns)}")
    print(f"customer_restricted_column_count={len(policy.restricted_customer_columns)}")
    print("application_retail_access=False")
    print("future_objects_automatically_exposed=False")
    print("credentials_printed=False")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
