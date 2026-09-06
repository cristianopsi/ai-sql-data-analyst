"""Permanent tests for the rate limiting module.

Validates:
- RateLimitConfig frozen dataclass
- DEFAULT_LIMITS values
- Sliding window counting (per-minute and per-day)
- 429 exception when per-minute exceeded
- 429 exception when per-day exceeded
- Different roles get different limits
- reset() clears counters
- EXEMPT_PATHS content
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.core.rate_limiter import (
    DEFAULT_LIMITS,
    EXEMPT_PATHS,
    RateLimitConfig,
    RateLimiter,
)


class TestRateLimitConfig:
    def test_frozen(self) -> None:
        config = RateLimitConfig(per_minute=10, per_day=100)
        with pytest.raises((AttributeError, TypeError)):
            config.per_minute = 20

    def test_values(self) -> None:
        config = RateLimitConfig(per_minute=30, per_day=1000)
        assert config.per_minute == 30
        assert config.per_day == 1000


class TestDefaultLimits:
    def test_free_user_limits(self) -> None:
        assert DEFAULT_LIMITS["FREE_USER"].per_minute == 10
        assert DEFAULT_LIMITS["FREE_USER"].per_day == 100

    def test_paid_user_limits(self) -> None:
        assert DEFAULT_LIMITS["PAID_USER"].per_minute == 30
        assert DEFAULT_LIMITS["PAID_USER"].per_day == 1000

    def test_admin_limits(self) -> None:
        assert DEFAULT_LIMITS["ADMIN"].per_minute == 60
        assert DEFAULT_LIMITS["ADMIN"].per_day == 10000


class TestExemptPaths:
    def test_health_exempt(self) -> None:
        assert "/health" in EXEMPT_PATHS

    def test_ready_exempt(self) -> None:
        assert "/ready" in EXEMPT_PATHS

    def test_docs_exempt(self) -> None:
        assert "/docs" in EXEMPT_PATHS

    def test_openapi_exempt(self) -> None:
        assert "/openapi.json" in EXEMPT_PATHS

    def test_redoc_exempt(self) -> None:
        assert "/redoc" in EXEMPT_PATHS


class TestRateLimiter:
    def test_under_limit_no_error(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        for _ in range(5):
            limiter.check_and_record("user-1", ["FREE_USER"], now=now)
        # No exception raised

    def test_per_minute_exceeded(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        for _ in range(10):
            limiter.check_and_record("user-1", ["FREE_USER"], now=now)
        with pytest.raises(HTTPException) as exc_info:
            limiter.check_and_record("user-1", ["FREE_USER"], now=now)
        assert exc_info.value.status_code == 429

    def test_per_day_exceeded(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        # Use PAID_USER: 30/min, 1000/day
        # Make 1000 requests across multiple minutes
        for minute in range(34):
            minute_start = now + (minute * 61)
            for _ in range(29):
                limiter.check_and_record("user-1", ["PAID_USER"], now=minute_start)
        # Now at 29*34=986, add 14 more to hit 1000
        for _ in range(14):
            limiter.check_and_record("user-1", ["PAID_USER"], now=now + 3500)
        with pytest.raises(HTTPException) as exc_info:
            limiter.check_and_record("user-1", ["PAID_USER"], now=now + 3500)
        assert exc_info.value.status_code == 429

    def test_sliding_window_expiry_minute(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        # Make 10 requests (FREE_USER per-minute limit)
        for _ in range(10):
            limiter.check_and_record("user-1", ["FREE_USER"], now=now)
        # 61 seconds later, window expired
        limiter.check_and_record("user-1", ["FREE_USER"], now=now + 61)
        # No exception

    def test_different_keys_independent(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        for _ in range(10):
            limiter.check_and_record("user-a", ["FREE_USER"], now=now)
        # user-b should not be affected
        limiter.check_and_record("user-b", ["FREE_USER"], now=now)

    def test_admin_higher_limits(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        # ADMIN has 60/min — 10 should not trigger
        for _ in range(10):
            limiter.check_and_record("admin-1", ["ADMIN"], now=now)
        # No exception

    def test_paid_user_higher_than_free(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        # PAID_USER has 30/min, FREE_USER has 10/min
        for _ in range(15):
            limiter.check_and_record("user-1", ["PAID_USER"], now=now)
        # No exception — would fail with FREE_USER limits

    def test_multiple_roles_picks_most_permissive(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        # User with both FREE_USER and PAID_USER — should use PAID_USER limits
        for _ in range(20):
            limiter.check_and_record("user-1", ["FREE_USER", "PAID_USER"], now=now)
        # No exception — PAID_USER allows 30/min

    def test_no_roles_uses_free_limits(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        for _ in range(10):
            limiter.check_and_record("user-1", [], now=now)
        with pytest.raises(HTTPException) as exc_info:
            limiter.check_and_record("user-1", [], now=now)
        assert exc_info.value.status_code == 429

    def test_reset_clears_counters(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        for _ in range(10):
            limiter.check_and_record("user-1", ["FREE_USER"], now=now)
        limiter.reset()
        # Should work again after reset
        limiter.check_and_record("user-1", ["FREE_USER"], now=now)

    def test_retry_after_header_per_minute(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        for _ in range(10):
            limiter.check_and_record("user-1", ["FREE_USER"], now=now)
        with pytest.raises(HTTPException) as exc_info:
            limiter.check_and_record("user-1", ["FREE_USER"], now=now)
        assert "Retry-After" in exc_info.value.headers

    def test_unknown_role_uses_free_default(self) -> None:
        limiter = RateLimiter()
        now = 1000.0
        for _ in range(10):
            limiter.check_and_record("user-1", ["UNKNOWN_ROLE"], now=now)
        with pytest.raises(HTTPException) as exc_info:
            limiter.check_and_record("user-1", ["UNKNOWN_ROLE"], now=now)
        assert exc_info.value.status_code == 429

    def test_custom_limits_override(self) -> None:
        custom = {"ADMIN": RateLimitConfig(per_minute=5, per_day=50)}
        limiter = RateLimiter(limits=custom)
        now = 1000.0
        for _ in range(5):
            limiter.check_and_record("admin-1", ["ADMIN"], now=now)
        with pytest.raises(HTTPException) as exc_info:
            limiter.check_and_record("admin-1", ["ADMIN"], now=now)
        assert exc_info.value.status_code == 429
