"""Role-Based Access Control (RBAC) module.

Defines user roles and provides dependency-based authorization
for FastAPI endpoints.

Roles:
- ADMIN: Full access to all endpoints (bypass)
- PAID_USER: Access to all analytical endpoints
- FREE_USER: Access to catalog, grounding, and SQL generation only

Usage:
    @router.post("/analytics", dependencies=[Depends(require_roles({Role.PAID_USER}))])
    async def analytics(...):
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from fastapi import HTTPException, Request
from starlette.status import HTTP_403_FORBIDDEN

from backend.app.core.auth import CurrentUser


class Role(StrEnum):
    """User roles for RBAC."""

    ADMIN = "ADMIN"
    PAID_USER = "PAID_USER"
    FREE_USER = "FREE_USER"


def require_roles(
    allowed_roles: set[Role],
) -> Callable[[Request], Awaitable[CurrentUser | None]]:
    """Create a FastAPI dependency that checks user roles.

    Args:
        allowed_roles: Set of roles permitted to access the endpoint.

    Returns:
        Dependency function that returns the current user if authorized,
        None if auth is disabled, or raises 403 if insufficient permissions.

    Security:
        - ADMIN role always bypasses (full access)
        - When auth is disabled (no user in request.state), returns None
        - Error message is sanitized (no role names leaked)
    """

    async def dependency(request: Request) -> CurrentUser | None:
        user: CurrentUser | None = getattr(request.state, "current_user", None)
        if user is None:
            return None
        user_roles = set(user.roles)
        if Role.ADMIN in user_roles:
            return user
        if not user_roles.intersection(allowed_roles):
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for this resource",
            )
        return user

    return dependency
