"""Permanent tests for the OIDC authentication module.

Validates:
- JWT token validation with RS256
- Algorithm restriction (RS256 only, HS256 rejected)
- Claims validation (exp, iat, aud, iss, sub)
- Exempt paths bypass auth
- AUTH_ENABLED=false disables auth
- Malformed tokens return 401
- Missing Authorization header returns 401
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.app.core.auth import (
    AuthConfig,
    AuthMiddleware,
    CurrentUser,
    JWTValidator,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rsa_keypair() -> dict[str, str]:
    """Generate an RSA keypair for testing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return {"private": private_pem, "public": public_pem}


@pytest.fixture
def auth_config(rsa_keypair: dict[str, str]) -> AuthConfig:
    """AuthConfig with test issuer/audience."""
    return AuthConfig(
        enabled=True,
        issuer="https://test-idp.example.com",
        audience="ai-sql-data-analyst",
        jwks_url="https://test-idp.example.com/.well-known/jwks.json",
        jwks_cache_ttl_seconds=300,
        exempt_paths=frozenset({"/health", "/ready"}),
    )


def _make_token(
    rsa_keypair: dict[str, str],
    *,
    issuer: str = "https://test-idp.example.com",
    audience: str = "ai-sql-data-analyst",
    exp_delta: int = 3600,
    alg: str = "RS256",
) -> str:
    """Create a signed JWT for testing."""
    now = datetime.now(UTC)
    payload = {
        "sub": "user-123",
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + exp_delta,
        "email": "user@test.com",
        "roles": ["PAID_USER"],
    }
    return jwt.encode(payload, rsa_keypair["private"], algorithm=alg)


# ---------------------------------------------------------------------------
# CurrentUser
# ---------------------------------------------------------------------------


class TestCurrentUser:
    def test_frozen_model(self) -> None:
        user = CurrentUser(sub="user-123")
        with pytest.raises((TypeError, AttributeError, ValueError)):
            user.sub = "user-456"  # type: ignore[misc]

    def test_defaults(self) -> None:
        user = CurrentUser(sub="abc")
        assert user.issuer == ""
        assert user.email == ""
        assert user.roles == []
        assert user.raw_claims == {}


# ---------------------------------------------------------------------------
# JWTValidator
# ---------------------------------------------------------------------------


class TestJWTValidator:
    @pytest.mark.asyncio
    async def test_valid_token(
        self,
        auth_config: AuthConfig,
        rsa_keypair: dict[str, str],
    ) -> None:
        token = _make_token(rsa_keypair)
        validator = JWTValidator(auth_config)
        with patch.object(
            validator._jwks_client,
            "get_key",
            new_callable=AsyncMock,
            return_value=rsa_keypair["public"],
        ):
            user = await validator.validate(token)
        assert user.sub == "user-123"
        assert user.email == "user@test.com"
        assert user.roles == ["PAID_USER"]
        assert user.issuer == "https://test-idp.example.com"

    @pytest.mark.asyncio
    async def test_expired_token(
        self,
        auth_config: AuthConfig,
        rsa_keypair: dict[str, str],
    ) -> None:
        token = _make_token(rsa_keypair, exp_delta=-10)
        validator = JWTValidator(auth_config)
        with (
            patch.object(
                validator._jwks_client,
                "get_key",
                new_callable=AsyncMock,
                return_value=rsa_keypair["public"],
            ),
            pytest.raises(jwt.ExpiredSignatureError),
        ):
            await validator.validate(token)

    @pytest.mark.asyncio
    async def test_wrong_audience(
        self,
        auth_config: AuthConfig,
        rsa_keypair: dict[str, str],
    ) -> None:
        token = _make_token(rsa_keypair, audience="wrong-audience")
        validator = JWTValidator(auth_config)
        with (
            patch.object(
                validator._jwks_client,
                "get_key",
                new_callable=AsyncMock,
                return_value=rsa_keypair["public"],
            ),
            pytest.raises(jwt.InvalidAudienceError),
        ):
            await validator.validate(token)

    @pytest.mark.asyncio
    async def test_wrong_issuer(
        self,
        auth_config: AuthConfig,
        rsa_keypair: dict[str, str],
    ) -> None:
        token = _make_token(rsa_keypair, issuer="https://wrong-issuer.com")
        validator = JWTValidator(auth_config)
        with (
            patch.object(
                validator._jwks_client,
                "get_key",
                new_callable=AsyncMock,
                return_value=rsa_keypair["public"],
            ),
            pytest.raises(jwt.InvalidIssuerError),
        ):
            await validator.validate(token)

    @pytest.mark.asyncio
    async def test_hs256_rejected(
        self,
        auth_config: AuthConfig,
        rsa_keypair: dict[str, str],
    ) -> None:
        secret = "test-secret"
        now = datetime.now(UTC)
        payload = {
            "sub": "user-123",
            "iss": auth_config.issuer,
            "aud": auth_config.audience,
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 3600,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")
        validator = JWTValidator(auth_config)
        with pytest.raises(ValueError, match="Algorithm not allowed"):
            await validator.validate(token)

    @pytest.mark.asyncio
    async def test_missing_kid(
        self,
        auth_config: AuthConfig,
        rsa_keypair: dict[str, str],
    ) -> None:
        token = _make_token(rsa_keypair)
        validator = JWTValidator(auth_config)
        with (
            patch.object(
                validator._jwks_client,
                "get_key",
                new_callable=AsyncMock,
                return_value=None,
            ),
            pytest.raises(ValueError, match="Key not found"),
        ):
            await validator.validate(token)


# ---------------------------------------------------------------------------
# AuthMiddleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    def _make_app(self, config: AuthConfig) -> Starlette:
        async def handler(request: Request) -> JSONResponse:
            user = getattr(request.state, "current_user", None)
            return JSONResponse(
                {
                    "authenticated": user is not None,
                    "sub": user.sub if user else None,
                }
            )

        app = Starlette(routes=[Route("/api/test", handler, methods=["GET"])])
        app.add_middleware(AuthMiddleware, config=config)
        return app

    def test_auth_disabled_passes(self, auth_config: AuthConfig) -> None:
        config = auth_config.model_copy(update={"enabled": False})
        app = self._make_app(config)
        with TestClient(app) as client:
            response = client.get("/api/test")
        assert response.status_code == 200

    def test_exempt_path_passes(self, auth_config: AuthConfig) -> None:
        async def health_handler(_request: Request) -> JSONResponse:
            return JSONResponse({"status": "ok"})

        app = Starlette(routes=[Route("/health", health_handler, methods=["GET"])])
        app.add_middleware(AuthMiddleware, config=auth_config)
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200

    def test_missing_authorization_header(self, auth_config: AuthConfig) -> None:
        app = self._make_app(auth_config)
        with TestClient(app) as client:
            response = client.get("/api/test")
        assert response.status_code == 401
        assert "Missing" in response.json()["detail"]

    def test_invalid_bearer_prefix(self, auth_config: AuthConfig) -> None:
        app = self._make_app(auth_config)
        with TestClient(app) as client:
            response = client.get(
                "/api/test",
                headers={"Authorization": "Basic abc123"},
            )
        assert response.status_code == 401

    def test_valid_token_passes(
        self,
        auth_config: AuthConfig,
        rsa_keypair: dict[str, str],
    ) -> None:
        token = _make_token(rsa_keypair)
        user = CurrentUser(
            sub="user-123",
            issuer=auth_config.issuer,
            email="user@test.com",
            roles=["PAID_USER"],
        )

        async def mock_validate(self: JWTValidator, token: str) -> CurrentUser:
            return user

        app = self._make_app(auth_config)
        with (
            patch.object(JWTValidator, "validate", mock_validate),
            TestClient(app) as client,
        ):
            response = client.get(
                "/api/test",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["authenticated"] is True
        assert response.json()["sub"] == "user-123"

    def test_malformed_token_returns_401(self, auth_config: AuthConfig) -> None:
        app = self._make_app(auth_config)
        with TestClient(app) as client:
            response = client.get(
                "/api/test",
                headers={"Authorization": "Bearer not-a-jwt"},
            )
        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]
