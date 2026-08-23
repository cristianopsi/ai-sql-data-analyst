from typing import NoReturn

import pytest

from scripts.seed_database import (
    build_seed_config,
    main,
)


def test_full_profile_uses_official_volume() -> None:
    config = build_seed_config("full")

    assert config.customer_count == 5_000
    assert config.product_count == 1_000
    assert config.order_count == 50_000
    assert config.seed == 20260823


def test_smoke_profile_uses_small_volume() -> None:
    config = build_seed_config("smoke")

    assert config.customer_count == 60
    assert config.product_count == 36
    assert config.order_count == 200


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Unsupported seed profile",
    ):
        build_seed_config("unexpected")


def test_dry_run_never_connects_to_database(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_if_called(
        *args: object,
        **kwargs: object,
    ) -> NoReturn:
        raise AssertionError("Database connection attempted during dry-run")

    monkeypatch.setattr(
        "scripts.seed_database.psycopg.connect",
        fail_if_called,
    )

    exit_code = main(["--profile", "smoke", "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert "SEED_DATASET_DRY_RUN_VALID" in captured.out
    assert "profile=smoke" in captured.out
    assert "table=orders rows=200" in captured.out
    assert "database_modified=False" in captured.out
    assert "credentials_printed=False" in captured.out
