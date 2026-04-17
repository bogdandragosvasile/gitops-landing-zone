"""API token management router for programmatic API access.

Admins can generate long-lived API tokens (Bearer tokens) that allow
external systems to call the BankOffer AI API without SSO or user sessions.

Tokens are stored as SHA-256 hashes — the plaintext token is shown only once
at generation time and cannot be recovered.
"""

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter()

TOKEN_PREFIX = "boai_"
TOKEN_BYTE_LENGTH = 32  # 256-bit entropy


class GenerateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable label")
    expires_in_days: Optional[int] = Field(
        None, ge=1, le=365, description="Days until expiry (null = never expires)"
    )
    scopes: str = Field(
        default="read", description="Comma-separated scopes: read, write, admin"
    )


class GenerateTokenResponse(BaseModel):
    id: int
    name: str
    token: str = Field(..., description="Plaintext token — shown only once!")
    token_prefix: str = Field(..., description="First 12 chars for identification")
    scopes: str
    expires_at: Optional[datetime]
    created_at: datetime


class TokenListItem(BaseModel):
    id: int
    name: str
    token_prefix: str
    scopes: str
    created_by: str
    last_used: Optional[datetime]
    expires_at: Optional[datetime]
    created_at: datetime
    is_active: bool


def _hash_token(token: str) -> str:
    """SHA-256 hash of the plaintext token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_token() -> str:
    """Generate a cryptographically secure random API token."""
    return TOKEN_PREFIX + secrets.token_hex(TOKEN_BYTE_LENGTH)


async def _ensure_table(session):
    """Create the api_tokens table if it doesn't exist."""
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS api_tokens (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            token_prefix VARCHAR(16) NOT NULL,
            scopes VARCHAR(200) NOT NULL DEFAULT 'read',
            created_by VARCHAR(200) NOT NULL,
            last_used TIMESTAMP,
            expires_at TIMESTAMP,
            revoked BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await session.commit()


@router.post(
    "",
    response_model=GenerateTokenResponse,
    summary="Generate a new API token",
    description="Creates a long-lived Bearer token for programmatic API access. "
    "The plaintext token is returned only once — store it securely.",
)
async def generate_token(body: GenerateTokenRequest, request: Request):
    session_factory = request.app.state.db_session_factory

    plaintext = _generate_token()
    token_hash = _hash_token(plaintext)
    token_prefix = plaintext[:16]
    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=body.expires_in_days)

    created_by = "admin"  # In production, extract from JWT claims

    async with session_factory() as session:
        await _ensure_table(session)

        result = await session.execute(
            text(
                "INSERT INTO api_tokens (name, token_hash, token_prefix, scopes, created_by, expires_at) "
                "VALUES (:name, :hash, :prefix, :scopes, :by, :exp) "
                "RETURNING id, created_at"
            ),
            {
                "name": body.name,
                "hash": token_hash,
                "prefix": token_prefix,
                "scopes": body.scopes,
                "by": created_by,
                "exp": expires_at,
            },
        )
        row = result.fetchone()
        await session.commit()

        return GenerateTokenResponse(
            id=row[0],
            name=body.name,
            token=plaintext,
            token_prefix=token_prefix,
            scopes=body.scopes,
            expires_at=expires_at,
            created_at=row[1],
        )


@router.get(
    "",
    response_model=list[TokenListItem],
    summary="List all API tokens",
    description="Returns metadata for all tokens. The plaintext token is never stored or returned.",
)
async def list_tokens(request: Request):
    session_factory = request.app.state.db_session_factory

    async with session_factory() as session:
        await _ensure_table(session)

        result = await session.execute(
            text(
                "SELECT id, name, token_prefix, scopes, created_by, last_used, "
                "expires_at, created_at, revoked "
                "FROM api_tokens ORDER BY created_at DESC"
            )
        )
        rows = result.fetchall()

        now = datetime.utcnow()
        return [
            TokenListItem(
                id=r[0],
                name=r[1],
                token_prefix=r[2],
                scopes=r[3],
                created_by=r[4],
                last_used=r[5],
                expires_at=r[6],
                created_at=r[7],
                is_active=not r[8] and (r[6] is None or r[6] > now),
            )
            for r in rows
        ]


@router.delete(
    "/{token_id}",
    summary="Revoke an API token",
    description="Permanently revokes a token. It can no longer be used for authentication.",
)
async def revoke_token(token_id: int, request: Request):
    session_factory = request.app.state.db_session_factory

    async with session_factory() as session:
        await _ensure_table(session)

        result = await session.execute(
            text("UPDATE api_tokens SET revoked = TRUE WHERE id = :id RETURNING id"),
            {"id": token_id},
        )
        row = result.fetchone()
        await session.commit()

        if not row:
            raise HTTPException(status_code=404, detail="Token not found")

        return {"status": "revoked", "token_id": token_id}


async def validate_api_token(token: str, session_factory) -> Optional[dict]:
    """Validate an API token against the database.

    Returns token metadata dict if valid, None otherwise.
    Updates last_used timestamp on successful validation.
    """
    token_hash = _hash_token(token)

    async with session_factory() as session:
        # Ensure table exists (first call may be before admin creates tokens)
        try:
            result = await session.execute(
                text(
                    "SELECT id, name, scopes, expires_at, revoked "
                    "FROM api_tokens WHERE token_hash = :hash"
                ),
                {"hash": token_hash},
            )
        except Exception:
            return None

        row = result.fetchone()
        if not row:
            return None

        token_id, name, scopes, expires_at, revoked = row

        if revoked:
            return None
        if expires_at and expires_at < datetime.utcnow():
            return None

        # Update last_used
        await session.execute(
            text("UPDATE api_tokens SET last_used = NOW() WHERE id = :id"),
            {"id": token_id},
        )
        await session.commit()

        return {
            "token_id": token_id,
            "name": name,
            "scopes": scopes,
            "type": "api_token",
        }
