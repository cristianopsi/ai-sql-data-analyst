"""Permanent tests for the tenant isolation module.

Validates:
- get_tenant_id returns user.sub when authenticated
- get_tenant_id returns None when auth disabled (no user)
- get_tenant_id returns None when user has no sub
- tenant_id is immutable per user (different users → different tenant_ids)
- tenant_id is a string (not an int or other type)
"""

from __future__ import annotations

from starlette.requests import Request

from backend.app.core.auth import CurrentUser
from backend.app.core.tenant import get_tenant_id


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


class TestGetTenantId:
    def test_returns_sub_when_authenticated(self) -> None:
        user = CurrentUser(sub="user-abc-123", roles=["PAID_USER"])
        request = _make_request(user)
        tenant_id = get_tenant_id(request)
        assert tenant_id == "user-abc-123"

    def test_returns_none_when_no_user(self) -> None:
        request = _make_request(None)
        tenant_id = get_tenant_id(request)
        assert tenant_id is None

    def test_returns_none_when_no_current_user_attr(self) -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        tenant_id = get_tenant_id(request)
        assert tenant_id is None

    def test_different_users_different_tenant_ids(self) -> None:
        user_a = CurrentUser(sub="user-a", roles=["PAID_USER"])
        user_b = CurrentUser(sub="user-b", roles=["PAID_USER"])
        request_a = _make_request(user_a)
        request_b = _make_request(user_b)
        assert get_tenant_id(request_a) != get_tenant_id(request_b)

    def test_tenant_id_is_string(self) -> None:
        user = CurrentUser(sub="user-123", roles=["FREE_USER"])
        request = _make_request(user)
        tenant_id = get_tenant_id(request)
        assert isinstance(tenant_id, str)

    def test_tenant_id_with_empty_sub(self) -> None:
        user = CurrentUser(sub="", roles=["ADMIN"])
        request = _make_request(user)
        tenant_id = get_tenant_id(request)
        assert tenant_id == ""

    def test_tenant_id_propagates_from_admin(self) -> None:
        user = CurrentUser(sub="admin-001", roles=["ADMIN"])
        request = _make_request(user)
        tenant_id = get_tenant_id(request)
        assert tenant_id == "admin-001"

    def test_tenant_id_consistent_across_calls(self) -> None:
        user = CurrentUser(sub="user-xyz", roles=["PAID_USER"])
        request = _make_request(user)
        first = get_tenant_id(request)
        second = get_tenant_id(request)
        assert first == second
