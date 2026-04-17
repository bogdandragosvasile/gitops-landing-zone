"""Staff authentication router for employee and admin login."""

import binascii
import hashlib
import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from jose import jwt
from sqlalchemy import text

from services.api.audit import write_audit_log
from services.api.models import StaffLoginRequest, StaffLoginResponse

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 8

router = APIRouter()


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-SHA256 with random salt."""
    salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
    return binascii.hexlify(salt).decode() + ":" + binascii.hexlify(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    """Verify password against stored PBKDF2-SHA256 hash."""
    try:
        salt_hex, dk_hex = stored.split(":")
        salt = binascii.unhexlify(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
        return binascii.hexlify(dk).decode() == dk_hex
    except (ValueError, binascii.Error):
        return False


def create_staff_token(email: str, role: str, display_name: str) -> str:
    """Create a signed JWT for staff session."""
    payload = {
        "email": email,
        "role": role,
        "name": display_name,
        "type": "staff",
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


@router.post(
    "/login",
    response_model=StaffLoginResponse,
    summary="Staff login (employee or admin)",
)
async def login_staff(body: StaffLoginRequest, request: Request):
    session_factory = request.app.state.db_session_factory

    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT id, password_hash, display_name, role "
                "FROM staff_auth WHERE email = :email"
            ),
            {"email": body.email.lower().strip()},
        )
        row = result.fetchone()

        if not row or not verify_password(body.password, row[1]):
            try:
                await write_audit_log(
                    request, action="login_failed", resource_type="staff_auth",
                    resource_id=body.email.lower().strip(),
                    actor=body.email.lower().strip(), actor_type="staff",
                    changes={"reason": "invalid_credentials"},
                )
            except Exception:
                pass
            raise HTTPException(status_code=401, detail="Invalid email or password")

        # Update last_login
        await session.execute(
            text("UPDATE staff_auth SET last_login = NOW() WHERE id = :id"),
            {"id": row[0]},
        )
        await session.commit()

        token = create_staff_token(body.email.lower().strip(), row[3], row[2])

        try:
            await write_audit_log(
                request, action="login", resource_type="staff_auth",
                resource_id=body.email.lower().strip(),
                actor=body.email.lower().strip(), actor_type="staff",
                changes={"role": row[3], "display_name": row[2]},
            )
        except Exception:
            pass

        return StaffLoginResponse(
            token=token,
            email=body.email.lower().strip(),
            display_name=row[2],
            role=row[3],
        )
