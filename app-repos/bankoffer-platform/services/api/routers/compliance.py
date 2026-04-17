"""Compliance router - audit trail, human-in-the-loop feedback, consent management, and AI Act endpoints."""

import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from services.api.audit import write_audit_log, write_consent_history
from services.api.middleware.auth import get_current_customer_id
from services.api.models import (
    AuditEntry,
    AuditListEntry,
    ConsentStatus,
    ConsentUpdate,
    FeedbackRequest,
    FeedbackResponse,
    KillSwitchStatus,
    KillSwitchToggle,
    OverrideRequest,
    OverrideResponse,
    RiskRegisterEntry,
    SuitabilityConfirmRequest,
    SuitabilityConfirmResponse,
)

logger = logging.getLogger(__name__)

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

router = APIRouter()


# ===== Requirement 3: Human-in-the-loop feedback =====

@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="Submit feedback on a recommendation",
    description="Allows customers or employees to accept, reject, flag, or override a recommendation.",
)
async def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Record human feedback on a recommendation (GDPR art.22 + AI Act)."""
    session_factory = request.app.state.db_session_factory

    if body.action not in ("accepted", "rejected", "flagged", "overridden"):
        raise HTTPException(status_code=400, detail="Invalid action")
    if body.actor_type not in ("customer", "employee"):
        raise HTTPException(status_code=400, detail="Invalid actor_type")

    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""INSERT INTO recommendation_feedback
                    (recommendation_id, offer_id, product_id, customer_id,
                     actor_type, actor_id, action, reason)
                    VALUES (:rid, :oid, :pid, :cid, :atype, :aid, :action, :reason)
                    RETURNING id, created_at"""),
                {
                    "rid": body.recommendation_id,
                    "oid": body.offer_id,
                    "pid": body.product_id,
                    "cid": body.customer_id,
                    "atype": body.actor_type,
                    "aid": body.actor_id,
                    "action": body.action,
                    "reason": body.reason,
                },
            )
            row = result.mappings().fetchone()
            await session.commit()

            # Auto-create employee notification when customer accepts/rejects
            if body.actor_type == "customer" and body.action in ("accepted", "rejected"):
                try:
                    # Look up product name from the offer_id or recommendation
                    product_name = body.product_id or body.offer_id
                    pn_result = await session.execute(
                        text("""SELECT final_offers FROM audit_recommendations
                                WHERE recommendation_id = :rid LIMIT 1"""),
                        {"rid": body.recommendation_id},
                    )
                    pn_row = pn_result.fetchone()
                    if pn_row and pn_row[0]:
                        import json
                        offers_data = pn_row[0] if isinstance(pn_row[0], list) else json.loads(pn_row[0])
                        for o in offers_data:
                            if o.get("offer_id") == body.offer_id:
                                product_name = o.get("product_name", product_name)
                                break
                    await session.execute(
                        text("""INSERT INTO notifications
                            (customer_id, offer_id, product_name, action, recommendation_id)
                            VALUES (:cid, :oid, :pname, :action, :rid)"""),
                        {
                            "cid": body.customer_id,
                            "oid": body.offer_id,
                            "pname": product_name,
                            "action": body.action,
                            "rid": body.recommendation_id,
                        },
                    )
                    await session.commit()
                except Exception as notif_err:
                    logger.warning("Failed to create notification: %s", notif_err)

            return FeedbackResponse(
                id=row["id"],
                recommendation_id=body.recommendation_id,
                offer_id=body.offer_id,
                action=body.action,
                reason=body.reason,
                created_at=row["created_at"],
            )
    except Exception as e:
        logger.error("Failed to save feedback: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save feedback")


@router.get(
    "/feedback/{customer_id}",
    response_model=list[FeedbackResponse],
    summary="Get feedback history for a customer",
)
async def get_feedback(
    customer_id: str,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Retrieve all feedback entries for a customer."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""SELECT id, recommendation_id, offer_id, action, reason, created_at
                     FROM recommendation_feedback
                     WHERE customer_id = :cid
                     ORDER BY created_at DESC LIMIT 100"""),
                {"cid": customer_id},
            )
            rows = result.mappings().fetchall()
            return [FeedbackResponse(
                id=r["id"],
                recommendation_id=str(r["recommendation_id"]),
                offer_id=r["offer_id"],
                action=r["action"],
                reason=r["reason"],
                created_at=r["created_at"],
            ) for r in rows]
    except Exception as e:
        logger.error("Failed to fetch feedback: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve feedback")


# ===== Requirement 4: Audit trail =====

@router.get(
    "/audit",
    response_model=list[AuditListEntry],
    summary="List audit trail entries",
    description="Returns paginated list of all recommendation audit entries (EBA Guidelines + AI Act).",
)
async def list_audit(
    request: Request,
    customer_id: str = Query(default=None, description="Filter by customer ID"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """List audit trail entries. Employee access in production."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            if customer_id:
                result = await session.execute(
                    text("""SELECT id, recommendation_id, external_customer_id, customer_id,
                            final_offers, excluded_products, model_version, created_at
                         FROM audit_recommendations
                         WHERE customer_id = :cid
                         ORDER BY created_at DESC LIMIT :lim OFFSET :off"""),
                    {"cid": customer_id, "lim": limit, "off": offset},
                )
            else:
                result = await session.execute(
                    text("""SELECT id, recommendation_id, external_customer_id, customer_id,
                            final_offers, excluded_products, model_version, created_at
                         FROM audit_recommendations
                         ORDER BY created_at DESC LIMIT :lim OFFSET :off"""),
                    {"lim": limit, "off": offset},
                )
            rows = result.mappings().fetchall()
            return [AuditListEntry(
                id=r["id"],
                recommendation_id=str(r["recommendation_id"]),
                external_customer_id=str(r["external_customer_id"]),
                customer_id=r["customer_id"],
                num_offers=len(r["final_offers"]) if isinstance(r["final_offers"], list) else 0,
                num_excluded=len(r["excluded_products"]) if isinstance(r["excluded_products"], list) else 0,
                model_version=r["model_version"],
                created_at=r["created_at"],
            ) for r in rows]
    except Exception as e:
        logger.error("Failed to list audit: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve audit entries")


@router.get(
    "/audit/{recommendation_id}",
    response_model=AuditEntry,
    summary="Get full audit detail for a recommendation",
)
async def get_audit_detail(
    recommendation_id: str,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Retrieve full audit record for a specific recommendation."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""SELECT * FROM audit_recommendations
                     WHERE recommendation_id = CAST(:rid AS uuid)"""),
                {"rid": recommendation_id},
            )
            row = result.mappings().fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Audit entry not found")
            return AuditEntry(
                id=row["id"],
                recommendation_id=str(row["recommendation_id"]),
                external_customer_id=str(row["external_customer_id"]),
                customer_id=row["customer_id"],
                input_features=row["input_features"],
                profile_result=row["profile_result"],
                suitability_checks=row["suitability_checks"],
                scored_products=row["scored_products"],
                final_offers=row["final_offers"],
                excluded_products=row["excluded_products"],
                model_version=row["model_version"],
                consent_snapshot=row["consent_snapshot"],
                created_at=row["created_at"],
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch audit detail: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve audit entry")


@router.get(
    "/action-log",
    summary="List action audit log entries",
    description="Returns the general action audit log (staff actions, product changes, AI calls). AI Act Art. 12.",
)
async def list_action_log(
    request: Request,
    action: str = Query(default=None, description="Filter by action type"),
    resource_type: str = Query(default=None, description="Filter by resource type"),
    actor: str = Query(default=None, description="Filter by actor"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """List general audit log entries from audit_log table."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            filters = []
            params: dict = {"lim": limit, "off": offset}
            if action:
                filters.append("action = :action")
                params["action"] = action
            if resource_type:
                filters.append("resource_type = :resource_type")
                params["resource_type"] = resource_type
            if actor:
                filters.append("actor ILIKE :actor")
                params["actor"] = f"%{actor}%"
            where = ("WHERE " + " AND ".join(filters)) if filters else ""
            result = await session.execute(
                text(f"""SELECT id, request_id, action, actor, actor_type,
                         resource_type, resource_id, changes, ip_address,
                         endpoint, http_method, http_status, duration_ms, created_at
                      FROM audit_log {where}
                      ORDER BY created_at DESC LIMIT :lim OFFSET :off"""),
                params,
            )
            rows = result.mappings().fetchall()
            return [
                {
                    "id": r["id"],
                    "request_id": str(r["request_id"]) if r["request_id"] else None,
                    "action": r["action"],
                    "actor": r["actor"],
                    "actor_type": r["actor_type"],
                    "resource_type": r["resource_type"],
                    "resource_id": r["resource_id"],
                    "changes": r["changes"],
                    "ip_address": str(r["ip_address"]) if r["ip_address"] else None,
                    "endpoint": r["endpoint"],
                    "http_method": r["http_method"],
                    "http_status": r["http_status"],
                    "duration_ms": r["duration_ms"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to fetch action log: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve action log")


# ===== Requirement 5: Consent management (5-tier) =====

CONSENT_FIELDS = {
    "profiling_consent": ("profiling_consent", "profiling_consent_ts"),
    "automated_decision_consent": ("automated_decision_consent", "automated_decision_consent_ts"),
    "marketing_push": ("marketing_push", "marketing_push_ts"),
    "marketing_email": ("marketing_email", "marketing_email_ts"),
    "marketing_sms": ("marketing_sms", "marketing_sms_ts"),
    "family_context_consent": ("family_context_consent", "family_context_consent_ts"),
    "sensitive_data_consent": ("sensitive_data_consent", "sensitive_data_consent_ts"),
}

CONSENT_SELECT = """SELECT customer_id, external_id,
    profiling_consent, profiling_consent_ts,
    automated_decision_consent, automated_decision_consent_ts,
    marketing_push, marketing_push_ts,
    marketing_email, marketing_email_ts,
    marketing_sms, marketing_sms_ts,
    family_context_consent, family_context_consent_ts,
    sensitive_data_consent, sensitive_data_consent_ts
FROM customers WHERE customer_id = :cid"""


def _row_to_consent(row) -> ConsentStatus:
    return ConsentStatus(
        customer_id=row["customer_id"],
        external_id=str(row["external_id"]),
        profiling_consent=bool(row["profiling_consent"]),
        profiling_consent_ts=row["profiling_consent_ts"],
        automated_decision_consent=bool(row["automated_decision_consent"]),
        automated_decision_consent_ts=row["automated_decision_consent_ts"],
        marketing_push=bool(row["marketing_push"]),
        marketing_push_ts=row["marketing_push_ts"],
        marketing_email=bool(row["marketing_email"]),
        marketing_email_ts=row["marketing_email_ts"],
        marketing_sms=bool(row["marketing_sms"]),
        marketing_sms_ts=row["marketing_sms_ts"],
        family_context_consent=bool(row["family_context_consent"]),
        family_context_consent_ts=row["family_context_consent_ts"],
        sensitive_data_consent=bool(row["sensitive_data_consent"]),
        sensitive_data_consent_ts=row["sensitive_data_consent_ts"],
    )


@router.get(
    "/consent/{customer_id}",
    response_model=ConsentStatus,
    summary="Get consent status for a customer",
)
async def get_consent(
    customer_id: str,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Retrieve current consent flags for a customer (all 5 consent types)."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(text(CONSENT_SELECT), {"cid": customer_id})
            row = result.mappings().fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Customer not found")
            return _row_to_consent(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch consent: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve consent status")


@router.put(
    "/consent/{customer_id}",
    response_model=ConsentStatus,
    summary="Update consent flags for a customer",
)
async def update_consent(
    customer_id: str,
    body: ConsentUpdate,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Update any combination of the 5 consent types."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            # Fetch current values for consent history
            old_result = await session.execute(text(CONSENT_SELECT), {"cid": customer_id})
            old_row = old_result.mappings().fetchone()
            if not old_row:
                raise HTTPException(status_code=404, detail="Customer not found")

            updates = []
            params = {"cid": customer_id}
            field_idx = 0
            changed_fields = {}

            for field_name, (col, ts_col) in CONSENT_FIELDS.items():
                val = getattr(body, field_name, None)
                if val is not None:
                    param_key = f"v{field_idx}"
                    updates.append(f"{col} = :{param_key}")
                    updates.append(f"{ts_col} = NOW()")
                    params[param_key] = val
                    old_val = bool(old_row[col]) if old_row[col] is not None else None
                    if old_val != val:
                        changed_fields[field_name] = (old_val, val)
                    field_idx += 1

            # Sync family_context_consent → sensitive_data_consent for backward compat
            if body.family_context_consent is not None and body.sensitive_data_consent is None:
                param_key = f"v{field_idx}"
                updates.append(f"sensitive_data_consent = :{param_key}")
                updates.append("sensitive_data_consent_ts = NOW()")
                params[param_key] = body.family_context_consent
                field_idx += 1

            if not updates:
                raise HTTPException(status_code=400, detail="No consent fields to update")

            await session.execute(
                text(f"UPDATE customers SET {', '.join(updates)} WHERE customer_id = :cid"),
                params,
            )
            await session.commit()

            # Write consent history for each changed field
            actor = authenticated_customer or customer_id
            for field_name, (old_val, new_val) in changed_fields.items():
                try:
                    await write_consent_history(
                        request, customer_id=customer_id, consent_type=field_name,
                        old_value=old_val, new_value=new_val, changed_by=actor,
                    )
                except Exception as e:
                    logger.warning("Failed to write consent history for %s.%s: %s", customer_id, field_name, e)

            # Audit log entry
            try:
                await write_audit_log(
                    request, action="consent_change", resource_type="consent",
                    resource_id=customer_id, actor=actor, actor_type="customer",
                    changes={k: {"old": v[0], "new": v[1]} for k, v in changed_fields.items()},
                )
            except Exception:
                pass

            result = await session.execute(text(CONSENT_SELECT), {"cid": customer_id})
            row = result.mappings().fetchone()
            return _row_to_consent(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update consent: %s", e)
        raise HTTPException(status_code=500, detail="Failed to update consent")


# ===== Consent #5: MiFID II Suitability confirmation =====

@router.post(
    "/suitability-confirm/{customer_id}",
    response_model=SuitabilityConfirmResponse,
    summary="Record suitability confirmation for investment product (MiFID II Art. 25)",
)
async def confirm_suitability(
    customer_id: str,
    body: SuitabilityConfirmRequest,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Client confirms they understand the risk of an investment product."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            # Fetch current risk profile
            r = await session.execute(
                text("SELECT risk_profile FROM customers WHERE customer_id = :cid"),
                {"cid": customer_id},
            )
            cust = r.mappings().fetchone()
            if not cust:
                raise HTTPException(status_code=404, detail="Customer not found")

            result = await session.execute(
                text("""INSERT INTO suitability_confirmations
                    (customer_id, product_id, recommendation_id, risk_profile_at_confirmation, confirmed)
                    VALUES (:cid, :pid, CAST(:rid AS uuid), :rp, :conf)
                    RETURNING id, confirmed_at"""),
                {
                    "cid": customer_id,
                    "pid": body.product_id,
                    "rid": body.recommendation_id,
                    "rp": cust["risk_profile"] or "moderate",
                    "conf": body.confirmed,
                },
            )
            row = result.mappings().fetchone()
            await session.commit()

            return SuitabilityConfirmResponse(
                id=row["id"],
                customer_id=customer_id,
                product_id=body.product_id,
                risk_profile_at_confirmation=cust["risk_profile"] or "moderate",
                confirmed=body.confirmed,
                confirmed_at=row["confirmed_at"],
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to record suitability confirmation: %s", e)
        raise HTTPException(status_code=500, detail="Failed to record suitability confirmation")


@router.get(
    "/suitability-confirm/{customer_id}",
    response_model=list[SuitabilityConfirmResponse],
    summary="Get suitability confirmations for a customer",
)
async def get_suitability_confirmations(
    customer_id: str,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """List all suitability confirmations for a customer."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""SELECT id, customer_id, product_id, risk_profile_at_confirmation,
                        confirmed, confirmed_at
                     FROM suitability_confirmations
                     WHERE customer_id = :cid ORDER BY confirmed_at DESC"""),
                {"cid": customer_id},
            )
            rows = result.mappings().fetchall()
            return [SuitabilityConfirmResponse(**dict(r)) for r in rows]
    except Exception as e:
        logger.error("Failed to fetch suitability confirmations: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch suitability confirmations")


# ===== AI Act Art. 14: Kill-switch =====

@router.get(
    "/kill-switch",
    response_model=KillSwitchStatus,
    summary="Get model kill-switch status",
)
async def get_kill_switch(
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Check if the recommendation model is suspended."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT active, activated_by, reason, activated_at FROM model_kill_switch ORDER BY id DESC LIMIT 1")
            )
            row = result.mappings().fetchone()
            if not row:
                return KillSwitchStatus(active=False)
            return KillSwitchStatus(
                active=bool(row["active"]),
                activated_by=row["activated_by"],
                reason=row["reason"],
                activated_at=row["activated_at"],
            )
    except Exception as e:
        logger.error("Failed to get kill-switch: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get kill-switch status")


@router.post(
    "/kill-switch",
    response_model=KillSwitchStatus,
    summary="Toggle model kill-switch (AI Act Art. 14)",
)
async def toggle_kill_switch(
    body: KillSwitchToggle,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Activate or deactivate the recommendation model kill-switch."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            if body.active:
                await session.execute(
                    text("""INSERT INTO model_kill_switch (active, activated_by, reason, activated_at)
                            VALUES (TRUE, :by, :reason, NOW())"""),
                    {"by": body.activated_by, "reason": body.reason},
                )
            else:
                await session.execute(
                    text("""INSERT INTO model_kill_switch (active, activated_by, reason, deactivated_at)
                            VALUES (FALSE, :by, :reason, NOW())"""),
                    {"by": body.activated_by, "reason": body.reason},
                )
            await session.commit()

            try:
                await write_audit_log(
                    request, action="kill_switch_toggle", resource_type="kill_switch",
                    resource_id="global",
                    actor=body.activated_by or "admin", actor_type="staff",
                    changes={"active": body.active, "reason": body.reason},
                )
            except Exception:
                pass

            return KillSwitchStatus(
                active=body.active,
                activated_by=body.activated_by,
                reason=body.reason,
                activated_at=datetime.utcnow() if body.active else None,
            )
    except Exception as e:
        logger.error("Failed to toggle kill-switch: %s", e)
        raise HTTPException(status_code=500, detail="Failed to toggle kill-switch")


# ===== AI Act Art. 9: Risk Register =====

@router.get(
    "/risk-register",
    response_model=list[RiskRegisterEntry],
    summary="List all risk register entries (AI Act Art. 9)",
)
async def list_risk_register(
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Return all risk register entries."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM ai_act_risk_register ORDER BY severity DESC, created_at")
            )
            rows = result.mappings().fetchall()
            return [RiskRegisterEntry(**dict(r)) for r in rows]
    except Exception as e:
        logger.error("Failed to list risk register: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list risk register")


@router.post(
    "/risk-register",
    response_model=RiskRegisterEntry,
    summary="Add entry to risk register",
)
async def add_risk_register(
    body: RiskRegisterEntry,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Add a new risk entry to the register."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""INSERT INTO ai_act_risk_register
                    (risk_id, category, description, severity, mitigation, status, owner, model_version)
                    VALUES (:rid, :cat, :desc, :sev, :mit, :st, :own, :mv)
                    RETURNING id, created_at, updated_at"""),
                {
                    "rid": body.risk_id, "cat": body.category, "desc": body.description,
                    "sev": body.severity, "mit": body.mitigation, "st": body.status,
                    "own": body.owner, "mv": body.model_version,
                },
            )
            row = result.mappings().fetchone()
            await session.commit()
            body.id = row["id"]
            body.created_at = row["created_at"]
            body.updated_at = row["updated_at"]
            return body
    except Exception as e:
        logger.error("Failed to add risk register entry: %s", e)
        raise HTTPException(status_code=500, detail="Failed to add risk entry")


# ===== AI Act Art. 14: Recommendation overrides =====

@router.post(
    "/override",
    response_model=OverrideResponse,
    summary="Override a recommendation (AI Act Art. 14 human oversight)",
)
async def create_override(
    body: OverrideRequest,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Employee rejects/suppresses/escalates a recommendation."""
    if body.override_type not in ("reject", "suppress", "escalate"):
        raise HTTPException(status_code=400, detail="Invalid override_type")
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""INSERT INTO recommendation_overrides
                    (recommendation_id, customer_id, employee_id, product_id, override_type, reason)
                    VALUES (CAST(:rid AS uuid), :cid, :eid, :pid, :otype, :reason)
                    RETURNING id, created_at"""),
                {
                    "rid": body.recommendation_id, "cid": body.customer_id,
                    "eid": body.employee_id, "pid": body.product_id,
                    "otype": body.override_type, "reason": body.reason,
                },
            )
            row = result.mappings().fetchone()
            await session.commit()
            return OverrideResponse(
                id=row["id"],
                recommendation_id=body.recommendation_id,
                override_type=body.override_type,
                reason=body.reason,
                created_at=row["created_at"],
            )
    except Exception as e:
        logger.error("Failed to create override: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create override")


# ===== AI Act compliance dashboard =====

@router.get(
    "/ai-act-status",
    summary="AI Act compliance dashboard data",
)
async def ai_act_status(
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Return current AI Act compliance status for the admin dashboard."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            # Kill-switch status
            ks = await session.execute(
                text("SELECT active, activated_by, reason, activated_at FROM model_kill_switch ORDER BY id DESC LIMIT 1")
            )
            ks_row = ks.mappings().fetchone()

            # Risk register summary
            rr = await session.execute(
                text("""SELECT status, COUNT(*) as cnt FROM ai_act_risk_register GROUP BY status""")
            )
            risk_summary = {r["status"]: r["cnt"] for r in rr.mappings().fetchall()}

            # Total recommendations
            rc = await session.execute(text("SELECT COUNT(*) as cnt FROM audit_recommendations"))
            total_recs = rc.mappings().fetchone()["cnt"]

            # Total overrides
            oc = await session.execute(text("SELECT COUNT(*) as cnt FROM recommendation_overrides"))
            total_overrides = oc.mappings().fetchone()["cnt"]

            # Total suitability confirmations
            sc = await session.execute(text("SELECT COUNT(*) as cnt FROM suitability_confirmations WHERE confirmed = TRUE"))
            total_suitability = sc.mappings().fetchone()["cnt"]

            # Consent statistics
            cs = await session.execute(
                text("""SELECT
                    COUNT(*) FILTER (WHERE profiling_consent = TRUE) as profiling,
                    COUNT(*) FILTER (WHERE automated_decision_consent = TRUE) as automated,
                    COUNT(*) FILTER (WHERE family_context_consent = TRUE) as family,
                    COUNT(*) FILTER (WHERE marketing_push = TRUE) as mkt_push,
                    COUNT(*) FILTER (WHERE marketing_email = TRUE) as mkt_email,
                    COUNT(*) as total
                FROM customers""")
            )
            consent_stats = dict(cs.mappings().fetchone())

            return {
                "classification": "HIGH-RISK",
                "legal_basis": "EU AI Act Annex III — financial services",
                "model_version": "rules-1.0.0",
                "kill_switch": {
                    "active": bool(ks_row["active"]) if ks_row else False,
                    "activated_by": ks_row["activated_by"] if ks_row else None,
                    "reason": ks_row["reason"] if ks_row else None,
                },
                "risk_register": risk_summary,
                "total_recommendations": total_recs,
                "total_overrides": total_overrides,
                "total_suitability_confirmations": total_suitability,
                "consent_statistics": consent_stats,
                "compliance_checklist": {
                    "risk_management_system": True,       # Art. 9
                    "data_governance": True,               # Art. 10
                    "technical_documentation": True,       # Art. 11
                    "transparency_logging": True,          # Art. 12+13
                    "human_oversight": True,               # Art. 14
                    "accuracy_robustness": True,           # Art. 15
                    "conformity_assessment": False,        # Pending — due Aug 2026
                    "eu_database_registration": False,     # Pending — due Aug 2026
                    "ai_literacy": True,                   # Art. 4 — active since Feb 2025
                },
                "prohibited_practices_check": {
                    "manipulative_ui": "CLEAR — no urgency tactics, no countdown timers",
                    "vulnerable_exploitation": "CLEAR — fragile customers excluded from credit",
                    "social_scoring": "CLEAR — no behavioral social scoring implemented",
                    "protected_characteristics": "CLEAR — city excluded, no sub-category inference",
                    "predatory_targeting": "CLEAR — geography excluded from scoring logic",
                },
                "deadlines": {
                    "practices_banned": {"date": "2025-02-02", "status": "COMPLIANT"},
                    "gpai_obligations": {"date": "2025-08-02", "status": "COMPLIANT"},
                    "high_risk_conformity": {"date": "2026-08-02", "status": "IN_PROGRESS"},
                },
            }
    except Exception as e:
        logger.error("Failed to get AI Act status: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get AI Act status")


# ===== Customer data endpoint (for customer portal) =====

@router.get(
    "/customer/{customer_id}",
    summary="Get customer data for portal",
)
async def get_customer_data(
    customer_id: str,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Return customer info for the customer portal (pseudonymized)."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""SELECT c.customer_id, c.external_id, c.age, c.income,
                        c.risk_profile, c.financial_health,
                        c.dependents_count, c.homeowner_status,
                        c.profiling_consent, c.automated_decision_consent,
                        c.marketing_push, c.marketing_email, c.marketing_sms,
                        c.family_context_consent, c.sensitive_data_consent,
                        cf.annual_income, cf.idle_cash, cf.savings_rate,
                        cf.debt_to_income, cf.balance_trend,
                        cf.dominant_spend_category, cf.investment_gap_flag
                 FROM customers c
                 LEFT JOIN customer_features cf ON c.customer_id = cf.customer_id
                 WHERE c.customer_id = :cid"""),
                {"cid": customer_id},
            )
            row = result.mappings().fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Customer not found")

            return {
                "customer_id": row["customer_id"],
                "external_id": str(row["external_id"]),
                "age": row["age"],
                "income_monthly": float(row["income"]) if row["income"] else 0,
                "risk_profile": row["risk_profile"],
                "financial_health": row["financial_health"],
                "dependents": row["dependents_count"],
                "homeowner_status": row["homeowner_status"],
                "profiling_consent": bool(row["profiling_consent"]),
                "automated_decision_consent": bool(row["automated_decision_consent"]),
                "marketing_push": bool(row["marketing_push"]),
                "marketing_email": bool(row["marketing_email"]),
                "marketing_sms": bool(row["marketing_sms"]),
                "family_context_consent": bool(row["family_context_consent"]),
                "sensitive_data_consent": bool(row["sensitive_data_consent"]),
                "features": {
                    "annual_income": float(row["annual_income"]) if row["annual_income"] else 0,
                    "idle_cash": float(row["idle_cash"]) if row["idle_cash"] else 0,
                    "savings_rate": float(row["savings_rate"]) if row["savings_rate"] else 0,
                    "debt_to_income": float(row["debt_to_income"]) if row["debt_to_income"] else 0,
                    "balance_trend": row["balance_trend"],
                    "spend_category": row["dominant_spend_category"],
                    "investment_gap": bool(row["investment_gap_flag"]),
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch customer data: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve customer data")
