"""JWT authentication middleware for the Bank Offering AI API.

Supports two modes:
1. DEMO_MODE: bypasses auth entirely (returns placeholder ID)
2. Keycloak OIDC: validates RS256 JWTs from Keycloak using JWKS
"""

import logging
import os
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# Legacy HS256 settings (fallback)
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "bank-offering-api")
JWT_ISSUER = os.getenv("JWT_ISSUER", "bank-auth-service")

# Keycloak settings
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "")  # e.g. http://keycloak:8080/auth
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "bankofferai")

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

bearer_scheme = HTTPBearer(auto_error=not DEMO_MODE)

# Cached JWKS keys
_jwks_cache: Optional[dict] = None


async def _get_keycloak_jwks() -> dict:
    """Fetch and cache the Keycloak realm's JWKS (JSON Web Key Set)."""
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache
    jwks_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(jwks_url, timeout=10)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            return _jwks_cache
    except Exception as e:
        logger.warning("Failed to fetch Keycloak JWKS: %s", e)
        return {}


def _decode_keycloak_token(token: str, jwks: dict) -> dict:
    """Decode a Keycloak RS256 JWT using the realm's JWKS."""
    try:
        # Extract key ID from token header
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        # Find matching key
        rsa_key = {}
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                rsa_key = key
                break

        if not rsa_key:
            raise JWTError("No matching key found in JWKS")

        issuer = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience="account",
            issuer=issuer,
            options={"verify_aud": False},  # Keycloak public clients don't always set aud
        )
        return payload
    except JWTError:
        raise
    except Exception as e:
        raise JWTError(str(e))


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token (legacy HS256), returning the claims payload."""
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    customer_id: Optional[str] = payload.get("customer_id")
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required customer_id claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


async def get_current_customer_id(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    """FastAPI dependency that extracts and validates the customer_id from a JWT.

    In DEMO_MODE, authentication is bypassed and a placeholder ID is returned.
    With Keycloak configured, validates RS256 tokens and extracts customer_id claim.
    Also accepts long-lived API tokens (boai_* prefix) for programmatic access.
    """
    if DEMO_MODE:
        return "__demo__"

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # API token check (boai_* prefix)
    if token.startswith("boai_") and request:
        from services.api.routers.api_tokens import validate_api_token

        session_factory = request.app.state.db_session_factory
        token_meta = await validate_api_token(token, session_factory)
        if token_meta:
            return "__api_token__"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Try Keycloak RS256 first if configured
    if KEYCLOAK_URL:
        try:
            jwks = await _get_keycloak_jwks()
            if jwks:
                payload = _decode_keycloak_token(token, jwks)
                # customer_id is a custom claim from our realm mapper
                return payload.get("customer_id", payload.get("sub", "0"))
        except JWTError:
            pass  # Fall through to legacy HS256

    # Legacy HS256 fallback
    payload = decode_token(token)
    return payload["customer_id"]
