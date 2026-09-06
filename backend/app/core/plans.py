"""Plan and quota management module.

Defines plan configurations (FREE, PAID, ADMIN) with dataset row limits
and daily token budgets. Provides in-memory token counting and quota
checking. Includes idempotent billing webhook processor.

Plans:
- FREE_USER: 1000 max_dataset_rows, 10000 daily_token_budget
- PAID_USER: 100000 max_dataset_rows, 100000 daily_token_budget
- ADMIN: 1000000 max_dataset_rows, 1000000 daily_token_budget
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import HTTPException
from starlette.status import HTTP_429_TOO_MANY_REQUESTS


@dataclass(frozen=True)
class PlanConfig:
    """Plan configuration with resource limits."""

    max_dataset_rows: int
    daily_token_budget: int


PLAN_CONFIGS: dict[str, PlanConfig] = {
    "FREE_USER": PlanConfig(max_dataset_rows=1000, daily_token_budget=10000),
    "PAID_USER": PlanConfig(max_dataset_rows=100000, daily_token_budget=100000),
    "ADMIN": PlanConfig(max_dataset_rows=1000000, daily_token_budget=1000000),
}

_DAY_SECONDS = 86400


def get_plan_config(roles: list[str]) -> PlanConfig:
    """Get the most permissive plan config from user roles.

    Args:
        roles: User roles from JWT claims.

    Returns:
        PlanConfig for the most permissive role, or FREE_USER default.
    """
    if not roles:
        return PLAN_CONFIGS["FREE_USER"]
    best = PLAN_CONFIGS["FREE_USER"]
    for role in roles:
        cfg = PLAN_CONFIGS.get(role)
        if cfg and cfg.daily_token_budget > best.daily_token_budget:
            best = cfg
    return best


def get_row_limit(roles: list[str]) -> int:
    """Get the maximum dataset row limit for the user's roles.

    Args:
        roles: User roles from JWT claims.

    Returns:
        Maximum number of rows the user can retrieve in a single query.
    """
    return get_plan_config(roles).max_dataset_rows


@dataclass
class TokenCounter:
    """In-memory daily token counter per tenant.

    Tracks token consumption per tenant_id per day.
    Resets when the day changes (UTC).
    """

    _daily_totals: dict[str, float] = field(default_factory=dict)
    _daily_dates: dict[str, str] = field(default_factory=dict)

    def _get_date_key(self, now: float) -> str:
        """Get the date string for the current timestamp."""
        return datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")

    def get_usage(self, tenant_id: str, now: float | None = None) -> int:
        """Get current daily token usage for a tenant.

        Returns 0 if the day has rolled over.
        """
        if now is None:
            now = time.time()
        date_key = self._get_date_key(now)
        stored_date = self._daily_dates.get(tenant_id)
        if stored_date != date_key:
            return 0
        return int(self._daily_totals.get(tenant_id, 0))

    def check_quota(
        self,
        tenant_id: str,
        roles: list[str],
        tokens_needed: int = 0,
        now: float | None = None,
    ) -> None:
        """Check if the tenant has enough token quota.

        Args:
            tenant_id: Tenant identifier.
            roles: User roles for budget selection.
            tokens_needed: Tokens that will be consumed.
            now: Current timestamp (for testing).

        Raises:
            HTTPException: 429 if token quota exceeded.
        """
        if not tenant_id:
            return
        if now is None:
            now = time.time()
        config = get_plan_config(roles)
        current_usage = self.get_usage(tenant_id, now)
        if current_usage + tokens_needed > config.daily_token_budget:
            raise HTTPException(
                status_code=HTTP_429_TOO_MANY_REQUESTS,
                detail="Token quota exceeded for today.",
                headers={"Retry-After": str(_DAY_SECONDS)},
            )

    def record_usage(
        self,
        tenant_id: str,
        tokens_used: int,
        now: float | None = None,
    ) -> None:
        """Record token usage for a tenant.

        Args:
            tenant_id: Tenant identifier.
            tokens_used: Number of tokens consumed.
            now: Current timestamp (for testing).
        """
        if not tenant_id or tokens_used <= 0:
            return
        if now is None:
            now = time.time()
        date_key = self._get_date_key(now)
        stored_date = self._daily_dates.get(tenant_id)
        if stored_date != date_key:
            self._daily_dates[tenant_id] = date_key
            self._daily_totals[tenant_id] = 0.0
        self._daily_totals[tenant_id] += tokens_used

    def reset(self) -> None:
        """Clear all counters (for testing)."""
        self._daily_totals.clear()
        self._daily_dates.clear()


class QuotaChecker:
    """Combines token counter with plan configs for quota enforcement.

    Usage:
        quota_checker = QuotaChecker()
        quota_checker.check(tenant_id, roles, tokens_needed=500)
        # ... process request ...
        quota_checker.record(tenant_id, tokens_used=450)
    """

    def __init__(self) -> None:
        self._counter = TokenCounter()

    @property
    def counter(self) -> TokenCounter:
        """Direct access to the token counter."""
        return self._counter

    def check(
        self,
        tenant_id: str | None,
        roles: list[str],
        tokens_needed: int = 0,
        now: float | None = None,
    ) -> None:
        """Check quota before processing a request.

        Args:
            tenant_id: Tenant identifier (None = no quota check).
            roles: User roles for budget selection.
            tokens_needed: Estimated tokens to be consumed.
            now: Current timestamp (for testing).

        Raises:
            HTTPException: 429 if token quota exceeded.
        """
        if tenant_id is None:
            return
        self._counter.check_quota(tenant_id, roles, tokens_needed, now)

    def record(
        self,
        tenant_id: str | None,
        tokens_used: int,
        now: float | None = None,
    ) -> None:
        """Record actual token usage after processing.

        Args:
            tenant_id: Tenant identifier (None = no recording).
            tokens_used: Actual tokens consumed.
            now: Current timestamp (for testing).
        """
        if tenant_id is None:
            return
        self._counter.record_usage(tenant_id, tokens_used, now)

    def reset(self) -> None:
        """Clear all counters (for testing)."""
        self._counter.reset()


@dataclass
class BillingWebhookProcessor:
    """Idempotent billing webhook processor.

    Processes billing events (e.g., plan upgrades) with idempotency
    guarantee via event_id deduplication.

    In production, this would integrate with Stripe webhooks.
    For MVP, this is a mock that logs and acknowledges events.
    """

    _processed_events: set[str] = field(default_factory=set)

    def process(
        self,
        event_id: str,
        event_type: str,
        tenant_id: str,
        new_plan: str,
    ) -> dict[str, str]:
        """Process a billing webhook event idempotently.

        Args:
            event_id: Unique event identifier for idempotency.
            event_type: Event type (e.g., "subscription.created").
            tenant_id: Tenant identifier.
            new_plan: New plan name (e.g., "PAID_USER").

        Returns:
            Dict with processing status.

        Raises:
            ValueError: If event_id is empty.
        """
        if not event_id:
            raise ValueError("event_id is required")

        if event_id in self._processed_events:
            return {
                "status": "duplicate",
                "event_id": event_id,
                "message": "Event already processed",
            }

        self._processed_events.add(event_id)

        return {
            "status": "processed",
            "event_id": event_id,
            "event_type": event_type,
            "tenant_id": tenant_id,
            "new_plan": new_plan,
            "message": "Plan change acknowledged",
        }

    def is_processed(self, event_id: str) -> bool:
        """Check if an event has already been processed."""
        return event_id in self._processed_events

    def reset(self) -> None:
        """Clear processed events (for testing)."""
        self._processed_events.clear()
