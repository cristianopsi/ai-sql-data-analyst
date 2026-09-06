"""Permanent tests for the plan and quota management module.

Validates:
- PlanConfig frozen dataclass
- PLAN_CONFIGS values for FREE_USER, PAID_USER, ADMIN
- get_plan_config: most permissive role, no roles, unknown role
- get_row_limit: per-role limits
- TokenCounter: under budget, over budget, daily reset, different tenants
- QuotaChecker: check and record, quota exceeded, None tenant_id
- BillingWebhookProcessor: idempotent processing, duplicate detection
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.core.plans import (
    PLAN_CONFIGS,
    BillingWebhookProcessor,
    PlanConfig,
    QuotaChecker,
    TokenCounter,
    get_plan_config,
    get_row_limit,
)


class TestPlanConfig:
    def test_frozen(self) -> None:
        config = PlanConfig(max_dataset_rows=1000, daily_token_budget=10000)
        with pytest.raises((AttributeError, TypeError)):
            config.max_dataset_rows = 2000

    def test_values(self) -> None:
        config = PlanConfig(max_dataset_rows=500, daily_token_budget=5000)
        assert config.max_dataset_rows == 500
        assert config.daily_token_budget == 5000


class TestPlanConfigs:
    def test_free_user_config(self) -> None:
        config = PLAN_CONFIGS["FREE_USER"]
        assert config.max_dataset_rows == 1000
        assert config.daily_token_budget == 10000

    def test_paid_user_config(self) -> None:
        config = PLAN_CONFIGS["PAID_USER"]
        assert config.max_dataset_rows == 100000
        assert config.daily_token_budget == 100000

    def test_admin_config(self) -> None:
        config = PLAN_CONFIGS["ADMIN"]
        assert config.max_dataset_rows == 1000000
        assert config.daily_token_budget == 1000000

    def test_paid_higher_than_free(self) -> None:
        paid = PLAN_CONFIGS["PAID_USER"]
        free = PLAN_CONFIGS["FREE_USER"]
        assert paid.daily_token_budget > free.daily_token_budget
        assert paid.max_dataset_rows > free.max_dataset_rows

    def test_admin_higher_than_paid(self) -> None:
        admin = PLAN_CONFIGS["ADMIN"]
        paid = PLAN_CONFIGS["PAID_USER"]
        assert admin.daily_token_budget > paid.daily_token_budget
        assert admin.max_dataset_rows > paid.max_dataset_rows


class TestGetPlanConfig:
    def test_free_user(self) -> None:
        config = get_plan_config(["FREE_USER"])
        assert config.max_dataset_rows == 1000

    def test_paid_user(self) -> None:
        config = get_plan_config(["PAID_USER"])
        assert config.max_dataset_rows == 100000

    def test_admin(self) -> None:
        config = get_plan_config(["ADMIN"])
        assert config.max_dataset_rows == 1000000

    def test_multiple_roles_most_permissive(self) -> None:
        config = get_plan_config(["FREE_USER", "PAID_USER"])
        assert config.max_dataset_rows == 100000

    def test_no_roles_uses_free(self) -> None:
        config = get_plan_config([])
        assert config.max_dataset_rows == 1000

    def test_unknown_role_uses_free(self) -> None:
        config = get_plan_config(["UNKNOWN_ROLE"])
        assert config.max_dataset_rows == 1000


class TestGetRowLimit:
    def test_free_user(self) -> None:
        assert get_row_limit(["FREE_USER"]) == 1000

    def test_paid_user(self) -> None:
        assert get_row_limit(["PAID_USER"]) == 100000

    def test_admin(self) -> None:
        assert get_row_limit(["ADMIN"]) == 1000000

    def test_no_roles(self) -> None:
        assert get_row_limit([]) == 1000


class TestTokenCounter:
    def test_under_budget_no_error(self) -> None:
        counter = TokenCounter()
        counter.record_usage("tenant-1", 500, now=1000.0)
        counter.check_quota("tenant-1", ["FREE_USER"], tokens_needed=100, now=1000.0)

    def test_over_budget_raises_429(self) -> None:
        counter = TokenCounter()
        counter.record_usage("tenant-1", 9900, now=1000.0)
        with pytest.raises(HTTPException) as exc_info:
            counter.check_quota("tenant-1", ["FREE_USER"], tokens_needed=200, now=1000.0)
        assert exc_info.value.status_code == 429

    def test_different_tenants_independent(self) -> None:
        counter = TokenCounter()
        counter.record_usage("tenant-a", 9900, now=1000.0)
        counter.check_quota("tenant-b", ["FREE_USER"], tokens_needed=500, now=1000.0)

    def test_reset_clears_counters(self) -> None:
        counter = TokenCounter()
        counter.record_usage("tenant-1", 500, now=1000.0)
        counter.reset()
        assert counter.get_usage("tenant-1", now=1000.0) == 0

    def test_daily_reset(self) -> None:
        counter = TokenCounter()
        counter.record_usage("tenant-1", 9900, now=1000.0)
        next_day = 1000.0 + 86400 + 3600
        assert counter.get_usage("tenant-1", now=next_day) == 0

    def test_paid_user_higher_budget(self) -> None:
        counter = TokenCounter()
        counter.record_usage("tenant-1", 50000, now=1000.0)
        counter.check_quota("tenant-1", ["PAID_USER"], tokens_needed=1000, now=1000.0)

    def test_empty_tenant_id_no_check(self) -> None:
        counter = TokenCounter()
        counter.check_quota("", ["FREE_USER"], tokens_needed=1000000, now=1000.0)

    def test_record_zero_tokens(self) -> None:
        counter = TokenCounter()
        counter.record_usage("tenant-1", 0, now=1000.0)
        assert counter.get_usage("tenant-1", now=1000.0) == 0

    def test_record_negative_tokens(self) -> None:
        counter = TokenCounter()
        counter.record_usage("tenant-1", -100, now=1000.0)
        assert counter.get_usage("tenant-1", now=1000.0) == 0

    def test_get_usage_no_record(self) -> None:
        counter = TokenCounter()
        assert counter.get_usage("unknown-tenant", now=1000.0) == 0


class TestQuotaChecker:
    def test_check_and_record(self) -> None:
        checker = QuotaChecker()
        checker.check("tenant-1", ["FREE_USER"], tokens_needed=500, now=1000.0)
        checker.record("tenant-1", 450, now=1000.0)

    def test_quota_exceeded(self) -> None:
        checker = QuotaChecker()
        checker.record("tenant-1", 9900, now=1000.0)
        with pytest.raises(HTTPException) as exc_info:
            checker.check("tenant-1", ["FREE_USER"], tokens_needed=200, now=1000.0)
        assert exc_info.value.status_code == 429

    def test_none_tenant_id_no_check(self) -> None:
        checker = QuotaChecker()
        checker.check(None, ["FREE_USER"], tokens_needed=1000000, now=1000.0)

    def test_none_tenant_id_no_record(self) -> None:
        checker = QuotaChecker()
        checker.record(None, 500, now=1000.0)

    def test_reset(self) -> None:
        checker = QuotaChecker()
        checker.record("tenant-1", 500, now=1000.0)
        checker.reset()
        checker.check("tenant-1", ["FREE_USER"], tokens_needed=10000, now=1000.0)

    def test_paid_user_budget(self) -> None:
        checker = QuotaChecker()
        checker.record("tenant-1", 50000, now=1000.0)
        checker.check("tenant-1", ["PAID_USER"], tokens_needed=1000, now=1000.0)


class TestBillingWebhookProcessor:
    def test_process_new_event(self) -> None:
        processor = BillingWebhookProcessor()
        result = processor.process(
            event_id="evt-001",
            event_type="subscription.created",
            tenant_id="user-123",
            new_plan="PAID_USER",
        )
        assert result["status"] == "processed"
        assert result["event_id"] == "evt-001"
        assert result["tenant_id"] == "user-123"
        assert result["new_plan"] == "PAID_USER"

    def test_process_duplicate_event(self) -> None:
        processor = BillingWebhookProcessor()
        processor.process(
            event_id="evt-001",
            event_type="subscription.created",
            tenant_id="user-123",
            new_plan="PAID_USER",
        )
        result = processor.process(
            event_id="evt-001",
            event_type="subscription.created",
            tenant_id="user-123",
            new_plan="PAID_USER",
        )
        assert result["status"] == "duplicate"
        assert result["message"] == "Event already processed"

    def test_process_different_events(self) -> None:
        processor = BillingWebhookProcessor()
        result1 = processor.process(
            event_id="evt-001",
            event_type="subscription.created",
            tenant_id="user-123",
            new_plan="PAID_USER",
        )
        result2 = processor.process(
            event_id="evt-002",
            event_type="subscription.updated",
            tenant_id="user-456",
            new_plan="FREE_USER",
        )
        assert result1["status"] == "processed"
        assert result2["status"] == "processed"
        assert result1["event_id"] != result2["event_id"]

    def test_is_processed(self) -> None:
        processor = BillingWebhookProcessor()
        assert not processor.is_processed("evt-001")
        processor.process(
            event_id="evt-001",
            event_type="subscription.created",
            tenant_id="user-123",
            new_plan="PAID_USER",
        )
        assert processor.is_processed("evt-001")

    def test_empty_event_id_raises(self) -> None:
        processor = BillingWebhookProcessor()
        with pytest.raises(ValueError, match="event_id is required"):
            processor.process(
                event_id="",
                event_type="subscription.created",
                tenant_id="user-123",
                new_plan="PAID_USER",
            )

    def test_reset(self) -> None:
        processor = BillingWebhookProcessor()
        processor.process(
            event_id="evt-001",
            event_type="subscription.created",
            tenant_id="user-123",
            new_plan="PAID_USER",
        )
        processor.reset()
        assert not processor.is_processed("evt-001")

    def test_multiple_plan_changes_same_tenant(self) -> None:
        processor = BillingWebhookProcessor()
        result1 = processor.process(
            event_id="evt-001",
            event_type="subscription.created",
            tenant_id="user-123",
            new_plan="PAID_USER",
        )
        result2 = processor.process(
            event_id="evt-002",
            event_type="subscription.deleted",
            tenant_id="user-123",
            new_plan="FREE_USER",
        )
        assert result1["status"] == "processed"
        assert result2["status"] == "processed"
        assert result1["new_plan"] == "PAID_USER"
        assert result2["new_plan"] == "FREE_USER"
