"""Permanent tests for the RBAC authorization module.

Validates:
- Role enum values
- require_roles with authorized user (returns user)
- require_roles with unauthorized user (raises 403)
- require_roles with no user (auth disabled, returns None)
- ADMIN bypass (always authorized regardless of allowed_roles)
- Error message sanitization (no role names leaked)
- Multiple roles on user
- Empty roles on user
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.core.auth import CurrentUser
from backend.app.core.roles import Role, require_roles

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(user: CurrentUser | None) -> Request:
    """Create a Request with optional current_user in state."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    if user is not None:
        request.state.current_user = user
    return request


# ---------------------------------------------------------------------------
# TestRole
# ---------------------------------------------------------------------------


class TestRole:
    def test_admin_value(self) -> None:
        assert Role.ADMIN == "ADMIN"

    def test_paid_user_value(self) -> None:
        assert Role.PAID_USER == "PAID_USER"

    def test_free_user_value(self) -> None:
        assert Role.FREE_USER == "FREE_USER"

    def test_str_enum_behavior(self) -> None:
        assert isinstance(Role.ADMIN, str)


# ---------------------------------------------------------------------------
# TestRequireRoles
# ---------------------------------------------------------------------------


class TestRequireRoles:
    @pytest.mark.asyncio
    async def test_authorized_user_returns_user(self) -> None:
        user = CurrentUser(sub="user-1", roles=["PAID_USER"])
        request = _make_request(user)
        dep = require_roles({Role.PAID_USER, Role.ADMIN})
        result = await dep(request)
        assert result is user

    @pytest.mark.asyncio
    async def test_unauthorized_user_raises_403(self) -> None:
        user = CurrentUser(sub="user-1", roles=["FREE_USER"])
        request = _make_request(user)
        dep = require_roles({Role.PAID_USER})
        with pytest.raises(HTTPException) as exc_info:
            await dep(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_user_returns_none(self) -> None:
        request = _make_request(None)
        dep = require_roles({Role.PAID_USER})
        result = await dep(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_admin_bypass(self) -> None:
        user = CurrentUser(sub="admin-1", roles=["ADMIN"])
        request = _make_request(user)
        dep = require_roles({Role.PAID_USER})
        result = await dep(request)
        assert result is user

    @pytest.mark.asyncio
    async def test_admin_bypass_empty_allowed_roles(self) -> None:
        user = CurrentUser(sub="admin-1", roles=["ADMIN"])
        request = _make_request(user)
        dep = require_roles(set())
        result = await dep(request)
        assert result is user

    @pytest.mark.asyncio
    async def test_multiple_roles_on_user(self) -> None:
        user = CurrentUser(sub="user-1", roles=["PAID_USER", "FREE_USER"])
        request = _make_request(user)
        dep = require_roles({Role.FREE_USER})
        result = await dep(request)
        assert result is user

    @pytest.mark.asyncio
    async def test_error_message_sanitized(self) -> None:
        user = CurrentUser(sub="user-1", roles=["FREE_USER"])
        request = _make_request(user)
        dep = require_roles({Role.PAID_USER, Role.ADMIN})
        with pytest.raises(HTTPException) as exc_info:
            await dep(request)
        assert "PAID_USER" not in exc_info.value.detail
        assert "ADMIN" not in exc_info.value.detail
        assert "FREE_USER" not in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_user_with_no_roles(self) -> None:
        user = CurrentUser(sub="user-1", roles=[])
        request = _make_request(user)
        dep = require_roles({Role.PAID_USER})
        with pytest.raises(HTTPException) as exc_info:
            await dep(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_free_user_access_to_free_endpoint(self) -> None:
        user = CurrentUser(sub="user-1", roles=["FREE_USER"])
        request = _make_request(user)
        dep = require_roles({Role.FREE_USER, Role.PAID_USER, Role.ADMIN})
        result = await dep(request)
        assert result is user

    @pytest.mark.asyncio
    async def test_free_user_blocked_from_paid_endpoint(self) -> None:
        user = CurrentUser(sub="user-1", roles=["FREE_USER"])
        request = _make_request(user)
        dep = require_roles({Role.PAID_USER, Role.ADMIN})
        with pytest.raises(HTTPException) as exc_info:
            await dep(request)
        assert exc_info.value.status_code == 403
