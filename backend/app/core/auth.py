"""OIDC Resource Server authentication module.

Validates JWT access tokens from an external Identity Provider (IdP)
using RS256 asymmetric signatures and JWKS key discovery.

Security guarantees:
- Only RS256 algorithm accepted (never HS256)
- Claims validated: exp, iat, aud, iss, sub
- JWKS keys cached with configurable TTL
- Tokens never logged (only sub and iss extracted)
- Paths /health and /ready are exempt (health checks)
- AUTH_ENABLED=false disables auth for local development
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jwt
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_ALGORITHMS = ["RS256"]
BEARER_PREFIX = "Bearer "
DEFAULT_JWKS_CACHE_TTL_SECONDS = 3600
DEFAULT_EXEMPT_PATHS = frozenset({"/health", "/ready"})

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CurrentUser(BaseModel):
    """User identity extracted from a validated JWT token."""

    sub: str
    issuer: str = ""
    email: str = ""
    roles: list[str] = []
    raw_claims: dict[str, Any] = {}

    model_config = {"frozen": True}


class AuthConfig(BaseModel):
    """Configuration for OIDC JWT validation."""

    enabled: bool = True
    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    jwks_cache_ttl_seconds: int = DEFAULT_JWKS_CACHE_TTL_SECONDS
    exempt_paths: frozenset[str] = DEFAULT_EXEMPT_PATHS

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# JWKS Client
# ---------------------------------------------------------------------------


class JWKSClient:
    """Fetches and caches JSON Web Key Set from the IdP."""

    def __init__(self, jwks_url: str, cache_ttl_seconds: int) -> None:
        self._jwks_url = jwks_url
        self._cache_ttl = cache_ttl_seconds
        self._keys: dict[str, Any] = {}
        self._last_fetch: float = 0.0

    def _is_cache_valid(self) -> bool:
        return bool(self._keys) and (time.monotonic() - self._last_fetch) < self._cache_ttl

    async def get_key(self, kid: str) -> Any | None:
        """Return the public key for the given key ID, fetching if needed."""
        if self._is_cache_valid() and kid in self._keys:
            return self._keys[kid]
        await self._refresh()
        return self._keys.get(kid)

    async def _refresh(self) -> None:
        """Fetch JWKS from the IdP and update the cache."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(self._jwks_url)
            response.raise_for_status()
        data = response.json()
        self._keys = {}
        for key in data.get("keys", []):
            kid = key.get("kid")
            if kid:
                self._keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(key)
        self._last_fetch = time.monotonic()


# ---------------------------------------------------------------------------
# JWT Validator
# ---------------------------------------------------------------------------


class JWTValidator:
    """Validates JWT access tokens using RS256 and JWKS."""

    def __init__(self, config: AuthConfig) -> None:
        self._config = config
        self._jwks_client = JWKSClient(config.jwks_url, config.jwks_cache_ttl_seconds)

    async def validate(self, token: str) -> CurrentUser:
        """Validate a JWT token and return the authenticated user."""
        unverified_header = jwt.get_unverified_header(token)
        alg = unverified_header.get("alg", "")
        if alg not in ALLOWED_ALGORITHMS:
            raise ValueError(f"Algorithm not allowed: {alg}")
        kid = unverified_header.get("kid", "")
        public_key = await self._jwks_client.get_key(kid)
        if public_key is None:
            raise ValueError(f"Key not found for kid: {kid}")
        payload = jwt.decode(
            token,
            public_key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=self._config.audience,
            issuer=self._config.issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
        return CurrentUser(
            sub=payload["sub"],
            issuer=payload.get("iss", ""),
            email=payload.get("email", ""),
            roles=payload.get("roles", []),
            raw_claims=payload,
        )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that validates Bearer JWT tokens on protected routes."""

    def __init__(self, app: Any, config: AuthConfig) -> None:  # noqa: ANN401
        super().__init__(app)
        self._config = config
        self._validator = JWTValidator(config) if config.enabled else None

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._config.enabled:
            return await call_next(request)

        path = request.url.path
        if path in self._config.exempt_paths:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith(BEARER_PREFIX):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        token = auth_header[len(BEARER_PREFIX) :]
        try:
            assert self._validator is not None
            user = await self._validator.validate(token)
        except Exception:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )

        request.state.current_user = user
        return await call_next(request)
