"""Customer authentication router with GDPR-compliant data anonymization.

Implements:
- SHA-256 email hashing (irreversible pseudonymization, GDPR Art. 5(1)(c))
- PBKDF2-SHA256 password hashing (no external dependencies)
- JWT session tokens
- Automatic anonymization scheduling (GDPR Art. 5(1)(e) storage limitation)
"""

import binascii
import hashlib
import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from jose import jwt
from sqlalchemy import text

from services.api.models import (
    CustomerLoginRequest,
    CustomerLoginResponse,
    CustomerRegisterRequest,
    CustomerRegisterResponse,
)

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

# GDPR Art. 5(1)(e): Storage limitation — auto-anonymize after 2 years
ANONYMIZATION_PERIOD_DAYS = 730

router = APIRouter()


def hash_email(email: str) -> str:
    """One-way SHA-256 hash of email for pseudonymization (GDPR Art. 5(1)(c))."""
    return hashlib.sha256(email.lower().strip().encode()).hexdigest()


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-SHA256 with random salt (100k iterations)."""
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


def create_customer_token(customer_id: str, external_id: str) -> str:
    """Create a signed JWT for customer session."""
    payload = {
        "customer_id": customer_id,
        "external_id": external_id,
        "type": "customer",
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


@router.post(
    "/register",
    response_model=CustomerRegisterResponse,
    summary="Register a new customer account",
    description="Creates a new customer with auto-increment ID and onboarding wizard. "
    "Email is stored as irreversible SHA-256 hash only (GDPR Art. 5(1)(c)). "
    "An auto-anonymization date is set per Art. 5(1)(e).",
)
async def register_customer(body: CustomerRegisterRequest, request: Request):
    if not body.gdpr_consent:
        raise HTTPException(
            status_code=400,
            detail="You must accept the GDPR data processing terms to register.",
        )

    session_factory = request.app.state.db_session_factory
    email_h = hash_email(body.email)

    async with session_factory() as session:
        # Check if email already registered
        existing = await session.execute(
            text("SELECT id FROM customer_auth WHERE email_hash = :eh"),
            {"eh": email_h},
        )
        if existing.fetchone():
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists.",
            )

        # Ensure onboarding_complete column exists
        await session.execute(text(
            "ALTER TABLE customers ADD COLUMN IF NOT EXISTS onboarding_complete BOOLEAN DEFAULT FALSE"
        ))

        # Auto-increment: find next integer customer_id
        next_id_row = await session.execute(text(
            "SELECT COALESCE(MAX(CAST(customer_id AS INTEGER)), 0) + 1 "
            "FROM customers WHERE customer_id ~ '^[0-9]+$'"
        ))
        new_customer_id = str(next_id_row.scalar())

        # Generate a new external UUID
        ext_id_row = await session.execute(text("SELECT gen_random_uuid()"))
        external_id = str(ext_id_row.scalar())

        # Create customer record (onboarding_complete=false, needs wizard)
        await session.execute(
            text(
                "INSERT INTO customers (customer_id, external_id, onboarding_complete) "
                "VALUES (:cid, :eid, FALSE)"
            ),
            {"cid": new_customer_id, "eid": external_id},
        )

        # Create empty customer_features row for profile questionnaire
        await session.execute(
            text(
                "INSERT INTO customer_features (customer_id) VALUES (:cid) "
                "ON CONFLICT DO NOTHING"
            ),
            {"cid": new_customer_id},
        )

        pwd_hash = hash_password(body.password)
        anon_date = datetime.utcnow() + timedelta(days=ANONYMIZATION_PERIOD_DAYS)
        display = body.display_name or "Customer"

        await session.execute(
            text(
                "INSERT INTO customer_auth "
                "(email_hash, password_hash, customer_id, display_name, anonymize_after) "
                "VALUES (:eh, :ph, :cid, :dn, :anon)"
            ),
            {
                "eh": email_h,
                "ph": pwd_hash,
                "cid": new_customer_id,
                "dn": display,
                "anon": anon_date,
            },
        )
        await session.commit()

        token = create_customer_token(new_customer_id, external_id)

        return CustomerRegisterResponse(
            token=token,
            customer_id=new_customer_id,
            external_id=external_id,
            display_name=display,
            anonymize_after=anon_date,
            onboarding_complete=False,
            message="Account created. Please complete the onboarding wizard to receive personalized offers.",
        )


@router.get(
    "/sso-lookup",
    summary="Look up customer by email for SSO login",
    description="Returns customer_id and display_name for a Keycloak-authenticated user. "
    "No password required — caller must already be authenticated via SSO.",
)
async def sso_lookup(email: str, request: Request):
    session_factory = request.app.state.db_session_factory
    email_h = hash_email(email)

    async with session_factory() as session:
        await session.execute(text(
            "ALTER TABLE customers ADD COLUMN IF NOT EXISTS onboarding_complete BOOLEAN DEFAULT FALSE"
        ))
        result = await session.execute(
            text(
                "SELECT ca.customer_id, ca.display_name, c.external_id, ca.anonymize_after, "
                "COALESCE(c.onboarding_complete, FALSE) AS onboarding_complete, "
                "ca.last_login "
                "FROM customer_auth ca "
                "JOIN customers c ON c.customer_id = ca.customer_id "
                "WHERE ca.email_hash = :eh"
            ),
            {"eh": email_h},
        )
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="No customer account linked to this email")

        # SSO login always skips the onboarding wizard
        onboarding_complete = True
        if not row[4]:
            await session.execute(
                text("UPDATE customers SET onboarding_complete = TRUE WHERE customer_id = :cid"),
                {"cid": str(row[0])},
            )
            await session.commit()

        return {
            "customer_id": str(row[0]),
            "display_name": row[1] or "Customer",
            "external_id": str(row[2]),
            "anonymize_after": row[3].isoformat() if row[3] else None,
            "onboarding_complete": onboarding_complete,
        }


@router.post(
    "/login",
    response_model=CustomerLoginResponse,
    summary="Customer login",
    description="Authenticate with email and password. "
    "Returns a session token and pseudonymized identifiers.",
)
async def login_customer(body: CustomerLoginRequest, request: Request):
    session_factory = request.app.state.db_session_factory
    email_h = hash_email(body.email)

    async with session_factory() as session:
        # Ensure onboarding_complete column exists
        await session.execute(text(
            "ALTER TABLE customers ADD COLUMN IF NOT EXISTS onboarding_complete BOOLEAN DEFAULT FALSE"
        ))

        result = await session.execute(
            text(
                "SELECT ca.id, ca.password_hash, ca.customer_id, ca.display_name, "
                "ca.anonymize_after, c.external_id, "
                "COALESCE(c.onboarding_complete, FALSE) AS onboarding_complete, "
                "ca.last_login "
                "FROM customer_auth ca "
                "JOIN customers c ON c.customer_id = ca.customer_id "
                "WHERE ca.email_hash = :eh"
            ),
            {"eh": email_h},
        )
        row = result.fetchone()

        if not row or not verify_password(body.password, row[1]):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        customer_id = row[2]
        external_id = str(row[5])

        # Login always skips the onboarding wizard — only /register
        # routes new users through it.
        onboarding_complete = True
        await session.execute(
            text("UPDATE customers SET onboarding_complete = TRUE WHERE customer_id = :cid AND onboarding_complete = FALSE"),
            {"cid": customer_id},
        )

        # Update last_login
        await session.execute(
            text("UPDATE customer_auth SET last_login = NOW() WHERE id = :id"),
            {"id": row[0]},
        )
        await session.commit()

        token = create_customer_token(customer_id, external_id)

        return CustomerLoginResponse(
            token=token,
            customer_id=customer_id,
            external_id=external_id,
            display_name=row[3] or "Customer",
            anonymize_after=row[4],
            onboarding_complete=onboarding_complete,
        )


@router.get(
    "/onboarding/status/{customer_id}",
    summary="Get onboarding status",
    description="Returns whether a customer has completed the onboarding wizard.",
)
async def get_onboarding_status(customer_id: str, request: Request):
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        await session.execute(text(
            "ALTER TABLE customers ADD COLUMN IF NOT EXISTS onboarding_complete BOOLEAN DEFAULT FALSE"
        ))
        result = await session.execute(
            text(
                "SELECT COALESCE(onboarding_complete, FALSE) FROM customers WHERE customer_id = :cid"
            ),
            {"cid": customer_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"customer_id": customer_id, "onboarding_complete": row[0]}


@router.put(
    "/onboarding/{customer_id}",
    summary="Submit onboarding wizard data",
    description="Saves profile questionnaire answers and consent selections from the onboarding wizard. "
    "Creates/updates customer_features and consent columns, then marks onboarding as complete.",
)
async def submit_onboarding(customer_id: str, request: Request):
    body = await request.json()
    session_factory = request.app.state.db_session_factory

    async with session_factory() as session:
        # Ensure columns exist
        for col, coltype in [
            ("onboarding_complete", "BOOLEAN DEFAULT FALSE"),
            ("profiling_consent", "BOOLEAN DEFAULT FALSE"),
            ("profiling_consent_ts", "TIMESTAMP"),
            ("automated_decision_consent", "BOOLEAN DEFAULT FALSE"),
            ("automated_decision_consent_ts", "TIMESTAMP"),
            ("marketing_push", "BOOLEAN DEFAULT FALSE"),
            ("marketing_push_ts", "TIMESTAMP"),
            ("marketing_email", "BOOLEAN DEFAULT FALSE"),
            ("marketing_email_ts", "TIMESTAMP"),
            ("marketing_sms", "BOOLEAN DEFAULT FALSE"),
            ("marketing_sms_ts", "TIMESTAMP"),
            ("family_context_consent", "BOOLEAN DEFAULT FALSE"),
            ("family_context_consent_ts", "TIMESTAMP"),
        ]:
            await session.execute(text(
                f"ALTER TABLE customers ADD COLUMN IF NOT EXISTS {col} {coltype}"
            ))

        # Verify customer exists
        check = await session.execute(
            text("SELECT customer_id FROM customers WHERE customer_id = :cid"),
            {"cid": customer_id},
        )
        if not check.fetchone():
            raise HTTPException(status_code=404, detail="Customer not found")

        # --- Save consent selections ---
        consent = body.get("consent", {})
        now = datetime.utcnow()
        consent_updates = []
        consent_params = {"cid": customer_id}

        consent_fields = [
            "profiling_consent", "automated_decision_consent",
            "marketing_push", "marketing_email", "marketing_sms",
            "family_context_consent",
        ]
        for field in consent_fields:
            if field in consent:
                val = bool(consent[field])
                consent_updates.append(f"{field} = :{field}")
                consent_updates.append(f"{field}_ts = :ts")
                consent_params[field] = val
                consent_params["ts"] = now

        if consent_updates:
            await session.execute(
                text(f"UPDATE customers SET {', '.join(consent_updates)} WHERE customer_id = :cid"),
                consent_params,
            )

        # --- Save profile questionnaire to customer_features AND customers ---
        profile = body.get("profile", {})
        if profile:
            # Ensure needed columns exist on customer_features
            for col, coltype in [
                ("age", "INTEGER"),
                ("annual_income", "NUMERIC(12,2)"),
                ("dependents", "INTEGER DEFAULT 0"),
                ("risk_tolerance", "VARCHAR(20)"),
                ("homeowner_status", "VARCHAR(20)"),
                ("existing_products", "TEXT"),
                ("employment_status", "VARCHAR(30)"),
            ]:
                await session.execute(text(
                    f"ALTER TABLE customer_features ADD COLUMN IF NOT EXISTS {col} {coltype}"
                ))

            feat_sets = []
            feat_params = {"cid": customer_id}
            for key in ["age", "annual_income", "dependents", "risk_tolerance",
                        "homeowner_status", "existing_products", "employment_status"]:
                if key in profile:
                    feat_sets.append(f"{key} = :{key}")
                    feat_params[key] = profile[key]

            if feat_sets:
                # Try update first, insert if not exists
                result = await session.execute(
                    text(f"UPDATE customer_features SET {', '.join(feat_sets)} WHERE customer_id = :cid"),
                    feat_params,
                )
                if result.rowcount == 0:
                    await session.execute(
                        text(
                            "INSERT INTO customer_features (customer_id, "
                            + ", ".join(k for k in feat_params if k != "cid")
                            + ") VALUES (:cid, "
                            + ", ".join(f":{k}" for k in feat_params if k != "cid")
                            + ")"
                        ),
                        feat_params,
                    )

            # --- Also update the customers table (needed by compliance/portal endpoints) ---
            cust_updates = []
            cust_params = {"cid": customer_id}
            if "age" in profile:
                cust_updates.append("age = :age")
                cust_params["age"] = int(profile["age"])
            if "annual_income" in profile:
                cust_updates.append("income = :income")
                cust_params["income"] = float(profile["annual_income"])
            if "dependents" in profile:
                cust_updates.append("dependents_count = :deps")
                cust_params["deps"] = int(profile["dependents"])
            if "homeowner_status" in profile:
                cust_updates.append("homeowner_status = :ho")
                cust_params["ho"] = profile["homeowner_status"]
            if "existing_products" in profile:
                cust_updates.append("existing_products = :ep")
                cust_params["ep"] = profile["existing_products"]
            # Map risk_tolerance to risk_profile
            if "risk_tolerance" in profile:
                cust_updates.append("risk_profile = :rp")
                cust_params["rp"] = profile["risk_tolerance"]
            if cust_updates:
                await session.execute(
                    text(f"UPDATE customers SET {', '.join(cust_updates)} WHERE customer_id = :cid"),
                    cust_params,
                )

        # --- Generate customer_profiles entry using build_profile ---
        age_val = profile.get("age")
        income_val = profile.get("annual_income")
        if age_val and income_val:
            try:
                from services.worker.profiler import build_profile
                import json

                features = {
                    "age": int(age_val),
                    "annual_income": float(income_val),
                    "dependents": int(profile.get("dependents", 0)),
                    "account_tenure_years": 0,
                    "investment_balance": 0,
                    "savings_ratio": 0.1,
                    "loan_to_income": 0,
                }
                result = build_profile(customer_id, features)
                profile_data = {
                    "customer_id": customer_id,
                    "life_stage": result.life_stage,
                    "risk_score": result.risk_score,
                    "segments": result.segments,
                    "income_bracket": (
                        "low" if float(income_val) < 30000
                        else "medium" if float(income_val) < 70000
                        else "high" if float(income_val) < 150000
                        else "very_high"
                    ),
                    "spending_patterns": [],
                }
                await session.execute(
                    text(
                        "INSERT INTO customer_profiles (customer_id, data) "
                        "VALUES (:cid, :data) "
                        "ON CONFLICT (customer_id) DO UPDATE SET data = :data"
                    ),
                    {"cid": customer_id, "data": json.dumps(profile_data)},
                )
            except Exception as e:
                logger.warning("Failed to generate profile for %s: %s", customer_id, e)

        # --- Mark onboarding complete ---
        await session.execute(
            text("UPDATE customers SET onboarding_complete = TRUE WHERE customer_id = :cid"),
            {"cid": customer_id},
        )
        await session.commit()

        return {
            "customer_id": customer_id,
            "onboarding_complete": True,
            "message": "Onboarding completed successfully. Your profile is ready for personalized offers.",
        }


@router.get(
    "/customers/list",
    summary="List all customer IDs",
    description="Returns a list of all customer IDs for the employee portal.",
)
async def list_customers(request: Request):
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT customer_id FROM customers "
                "WHERE customer_id ~ '^[0-9]+$' "
                "ORDER BY CAST(customer_id AS INTEGER)"
            )
        )
        rows = result.fetchall()
        return {"customer_ids": [r[0] for r in rows]}
