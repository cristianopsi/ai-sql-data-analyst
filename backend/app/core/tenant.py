"""Tenant isolation module.

Propagates tenant_id (extracted from CurrentUser.sub) through the
request pipeline and into observability logs.

This module implements stateless tenant isolation:
- No persistence of history (deferred to Etapa 2.5)
- tenant_id is propagated in request.state and observability
- When auth is disabled, tenant_id is None (no isolation needed)
"""

from __future__ import annotations

from starlette.requests import Request

from backend.app.core.auth import CurrentUser

def get_tenant_id(request: Request) -> str | None:
    """Extract tenant_id from the authenticated user on the request.

    Args:
        request: The incoming Starlette request.

    Returns:
        The user's sub as tenant_id, or None if auth is disabled.
    """
    user: CurrentUser | None = getattr(request.state, "current_user", None)
    if user is None:
        return None
    return user.sub