import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter

import psycopg
from dotenv import dotenv_values

from backend.app.db.role_provisioning import (
    RoleConfigurationError,
    require_value,
    sqlalchemy_to_psycopg_url,
)
from backend.app.db.seed_generation import (
    GeneratedDataset,
    SeedConfig,
    generate_dataset,
)
from backend.app.db.seed_loading import (
    DatasetLoadError,
    load_dataset,
    validate_dataset_contract,
)

SEED_PROFILES = ("smoke", "full")


def build_seed_config(profile: str) -> SeedConfig:
    if profile == "smoke":
        return SeedConfig(
            customer_count=60,
            product_count=36,
            order_count=200,
        )

    if profile == "full":
        return SeedConfig()

    raise ValueError(f"Unsupported seed profile: {profile}")


def parse_arguments(
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Generate and optionally load the deterministic retail analytics dataset."),
    )
    parser.add_argument(
        "--profile",
        choices=SEED_PROFILES,
        default="full",
        help="Dataset size profile. Default: full.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=("Generate and validate the dataset without connecting to PostgreSQL."),
    )

    return parser.parse_args(argv)


def print_dataset_summary(
    dataset: GeneratedDataset,
    profile: str,
    *,
    dry_run: bool,
    elapsed_seconds: float,
) -> None:
    status = "SEED_DATASET_DRY_RUN_VALID" if dry_run else "SEED_DATASET_LOADED"

    print(status)
    print(f"profile={profile}")
    print(f"seed={dataset.seed}")
    print(f"start_date={dataset.start_date}")
    print(f"reference_date={dataset.reference_date}")

    for table_name, row_count in dataset.row_counts.items():
        print(f"table={table_name} rows={row_count}")

    print(f"elapsed_seconds={elapsed_seconds:.3f}")
    print(f"database_modified={not dry_run}")
    print("credentials_printed=False")


def main(
    argv: Sequence[str] | None = None,
) -> int:
    arguments = parse_arguments(argv)
    profile = str(arguments.profile)
    dry_run = bool(arguments.dry_run)
    started_at = perf_counter()

    try:
        config = build_seed_config(profile)
        dataset = generate_dataset(config)
        validate_dataset_contract(dataset)

        if dry_run:
            print_dataset_summary(
                dataset,
                profile,
                dry_run=True,
                elapsed_seconds=(perf_counter() - started_at),
            )
            return 0

        env_path = Path(".env")

        if not env_path.is_file():
            raise RoleConfigurationError(".env not found")

        values = dotenv_values(env_path)
        migration_url = require_value(
            values,
            "MIGRATION_DATABASE_URL",
        )
        psycopg_url = sqlalchemy_to_psycopg_url(migration_url)

        with psycopg.connect(
            psycopg_url,
            connect_timeout=5,
        ) as connection:
            loaded_counts = load_dataset(
                connection,
                dataset,
            )

        if loaded_counts != dataset.row_counts:
            raise DatasetLoadError("Returned load counts do not match the generated dataset")

    except (
        DatasetLoadError,
        RoleConfigurationError,
        ValueError,
    ) as error:
        print(
            f"SEED_CONFIGURATION_ERROR:{error}",
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        print(
            f"SEED_DATABASE_FAILED:{type(error).__name__}",
            file=sys.stderr,
        )
        return 1

    print_dataset_summary(
        dataset,
        profile,
        dry_run=False,
        elapsed_seconds=(perf_counter() - started_at),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
