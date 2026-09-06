"""Rate limiting module with in-memory sliding window.

Provides per-user and per-IP rate limiting with configurable limits
by role. Uses a sliding window algorithm with in-memory storage
(suitable for single-instance deployments).

Limits:
- FREE_USER: 10 req/min, 100 req/day
- PAID_USER: 30 req/min, 1000 req/day
- ADMIN: 60 req/min, 10000 req/day
- No auth (dev/test): unlimited
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from backend.app.core.auth import CurrentUser
from backend.app.core.tenant import get_tenant_id


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate limit configuration per role."""

    per_minute: int
    per_day: int


# Default limits by role name
DEFAULT_LIMITS: dict[str, RateLimitConfig] = {
    "FREE_USER": RateLimitConfig(per_minute=10, per_day=100),
    "PAID_USER": RateLimitConfig(per_minute=30, per_day=1000),
    "ADMIN": RateLimitConfig(per_minute=60, per_day=10000),
}

# Exempt paths (no rate limiting)
EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/ready", "/docs", "/openapi.json", "/redoc"})

# Window durations in seconds
_MINUTE_SECONDS = 60
_DAY_SECONDS = 86400


@dataclass
class _WindowCounter:
    """Sliding window counter for a single key."""

    timestamps: list[float] = field(default_factory=list)

    def _prune(self, window_seconds: float, now: float) -> None:
        """Remove timestamps outside the window."""
        cutoff = now - window_seconds
        self.timestamps = [ts for ts in self.timestamps if ts >= cutoff]

    def count(self, window_seconds: float, now: float) -> int:
        """Count requests within the window."""
        self._prune(window_seconds, now)
        return len(self.timestamps)

    def add(self, now: float) -> None:
        """Record a new request."""
        self.timestamps.append(now)


class RateLimiter:
    """In-memory rate limiter with sliding window per key.

    Each key (tenant_id or IP) has two windows: per-minute and per-day.
    """

    def __init__(self, limits: dict[str, RateLimitConfig] | None = None) -> None:
        self._limits = limits or DEFAULT_LIMITS
        self._minute_counters: dict[str, _WindowCounter] = {}
        self._day_counters: dict[str, _WindowCounter] = {}

    def _get_config(self, roles: list[str]) -> RateLimitConfig:
        """Get the most permissive config from user roles."""
        if not roles:
            return DEFAULT_LIMITS["FREE_USER"]
        # Pick the role with the highest per_day limit
        best = RateLimitConfig(per_minute=0, per_day=0)
        for role in roles:
            cfg = self._limits.get(role)
            if cfg and cfg.per_day > best.per_day:
                best = cfg
        return best if best.per_day > 0 else DEFAULT_LIMITS["FREE_USER"]

    def check_and_record(
        self,
        key: str,
        roles: list[str],
        now: float | None = None,
    ) -> None:
        """Check rate limit and record the request.

        Args:
            key: Identifier (tenant_id or IP address).
            roles: User roles for limit selection.
            now: Current timestamp (for testing).

        Raises:
            HTTPException: 429 if rate limit exceeded.
        """
        if now is None:
            now = time.time()

        config = self._get_config(roles)

        minute_counter = self._minute_counters.setdefault(key, _WindowCounter())
        day_counter = self._day_counters.setdefault(key, _WindowCounter())

        minute_count = minute_counter.count(_MINUTE_SECONDS, now)
        day_count = day_counter.count(_DAY_SECONDS, now)

        if minute_count >= config.per_minute:
            raise HTTPException(
                status_code=HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Too many requests per minute.",
                headers={"Retry-After": str(_MINUTE_SECONDS)},
            )

        if day_count >= config.per_day:
            raise HTTPException(
                status_code=HTTP_429_TOO_MANY_REQUESTS,
                detail="Daily rate limit exceeded.",
                headers={"Retry-After": str(_DAY_SECONDS)},
            )

        minute_counter.add(now)
        day_counter.add(now)

    def reset(self) -> None:
        """Clear all counters (for testing)."""
        self._minute_counters.clear()
        self._day_counters.clear()


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request, respecting X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces rate limiting on non-exempt paths.

    Uses tenant_id when auth is active, falls back to client IP.
    When rate_limit_enabled=False, passes through without checking.
    """

    def __init__(
        self,
        app: ASGIApp,
        rate_limiter: RateLimiter,
        enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self._limiter = rate_limiter
        self._enabled = enabled

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._enabled:
            return await call_next(request)

        path = request.url.path

        if path in EXEMPT_PATHS:
            return await call_next(request)

        tenant_id = get_tenant_id(request)

        key = tenant_id if tenant_id else _get_client_ip(request)
        user: CurrentUser | None = getattr(request.state, "current_user", None)
        roles = user.roles if user else []

        self._limiter.check_and_record(key, roles)

        return await call_next(request)
