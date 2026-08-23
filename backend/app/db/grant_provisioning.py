from dataclasses import dataclass
from re import fullmatch
from typing import Any

from psycopg import Connection, sql

from backend.app.db.role_provisioning import (
    ROLE_NAME_PATTERN,
    RoleConfigurationError,
)

SQL_IDENTIFIER_PATTERN = r"[a-z_][a-z0-9_]{0,62}"

RETAIL_TABLES_WITH_FULL_SELECT = (
    "categories",
    "order_items",
    "orders",
    "payments",
    "products",
    "regions",
    "sales_targets",
)

CUSTOMER_ALLOWED_COLUMNS = (
    "id",
    "external_id",
    "region_id",
    "full_name",
    "segment",
    "active",
    "created_at",
)

CUSTOMER_RESTRICTED_COLUMNS = (
    "email",
    "document_number",
)


@dataclass(frozen=True, slots=True)
class RetailGrantPolicy:
    schema_name: str = "retail"
    column_restricted_table: str = "customers"
    full_select_tables: tuple[str, ...] = RETAIL_TABLES_WITH_FULL_SELECT
    allowed_customer_columns: tuple[str, ...] = CUSTOMER_ALLOWED_COLUMNS
    restricted_customer_columns: tuple[str, ...] = CUSTOMER_RESTRICTED_COLUMNS

    def __post_init__(self) -> None:
        identifiers = (
            self.schema_name,
            self.column_restricted_table,
            *self.full_select_tables,
            *self.allowed_customer_columns,
            *self.restricted_customer_columns,
        )

        invalid_identifiers = [
            identifier
            for identifier in identifiers
            if fullmatch(SQL_IDENTIFIER_PATTERN, identifier) is None
        ]

        if invalid_identifiers:
            raise RoleConfigurationError(
                "Invalid PostgreSQL identifiers: " + ", ".join(invalid_identifiers)
            )

        if self.column_restricted_table in self.full_select_tables:
            raise RoleConfigurationError(
                "Column-restricted table cannot receive table-level SELECT"
            )

        overlap = set(self.allowed_customer_columns) & set(self.restricted_customer_columns)

        if overlap:
            raise RoleConfigurationError(
                "Allowed and restricted customer columns overlap: " + ", ".join(sorted(overlap))
            )


def _validate_role_name(role_name: str) -> None:
    if fullmatch(ROLE_NAME_PATTERN, role_name) is None:
        raise RoleConfigurationError(f"Invalid PostgreSQL role name: {role_name!r}")


def _validate_database_objects(
    connection: Connection[Any],
    policy: RetailGrantPolicy,
    application_role: str,
    analytics_role: str,
) -> None:
    expected_tables = set(policy.full_select_tables) | {policy.column_restricted_table}
    expected_customer_columns = set(policy.allowed_customer_columns) | set(
        policy.restricted_customer_columns
    )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rolname
            FROM pg_roles
            WHERE rolname = ANY(%s)
            """,
            ([application_role, analytics_role],),
        )
        existing_roles = {str(row[0]) for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            """,
            (policy.schema_name,),
        )
        existing_tables = {str(row[0]) for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
            """,
            (
                policy.schema_name,
                policy.column_restricted_table,
            ),
        )
        existing_customer_columns = {str(row[0]) for row in cursor.fetchall()}

    expected_roles = {application_role, analytics_role}

    if existing_roles != expected_roles:
        raise RoleConfigurationError("Runtime database roles do not match the grant policy")

    if existing_tables != expected_tables:
        raise RoleConfigurationError("Retail tables changed and require an explicit policy review")

    if existing_customer_columns != expected_customer_columns:
        raise RoleConfigurationError(
            "Customer columns changed and require an explicit policy review"
        )


def apply_retail_grants(
    connection: Connection[Any],
    application_role: str,
    analytics_role: str,
    policy: RetailGrantPolicy | None = None,
) -> None:
    _validate_role_name(application_role)
    _validate_role_name(analytics_role)

    if application_role == analytics_role:
        raise RoleConfigurationError("Application and analytics roles must be different")

    active_policy = policy or RetailGrantPolicy()

    try:
        _validate_database_objects(
            connection,
            active_policy,
            application_role,
            analytics_role,
        )

        role_targets = sql.SQL(", ").join(
            (
                sql.Identifier(application_role),
                sql.Identifier(analytics_role),
            )
        )

        qualified_tables = sql.SQL(", ").join(
            sql.SQL("{}.{}").format(
                sql.Identifier(active_policy.schema_name),
                sql.Identifier(table_name),
            )
            for table_name in active_policy.full_select_tables
        )

        customer_table = sql.SQL("{}.{}").format(
            sql.Identifier(active_policy.schema_name),
            sql.Identifier(active_policy.column_restricted_table),
        )

        customer_columns = sql.SQL(", ").join(
            sql.Identifier(column_name) for column_name in active_policy.allowed_customer_columns
        )

        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM PUBLIC, {}").format(
                    sql.Identifier(active_policy.schema_name),
                    role_targets,
                )
            )

            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM PUBLIC, {}").format(
                    sql.Identifier(active_policy.schema_name),
                    role_targets,
                )
            )

            cursor.execute(
                sql.SQL(
                    "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {} FROM PUBLIC, {}"
                ).format(
                    sql.Identifier(active_policy.schema_name),
                    role_targets,
                )
            )

            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(active_policy.schema_name),
                    sql.Identifier(analytics_role),
                )
            )

            cursor.execute(
                sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                    qualified_tables,
                    sql.Identifier(analytics_role),
                )
            )

            cursor.execute(
                sql.SQL("GRANT SELECT ({}) ON TABLE {} TO {}").format(
                    customer_columns,
                    customer_table,
                    sql.Identifier(analytics_role),
                )
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
