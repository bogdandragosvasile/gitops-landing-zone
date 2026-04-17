"""Connectors router - manage third-party service integrations (AI, Cloud, Ads, etc.)."""

import json
import logging
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from services.api.audit import write_audit_log
from services.api.models import ConnectorApproval, ConnectorCreate, ConnectorOut, ConnectorUpdate

logger = logging.getLogger(__name__)

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

router = APIRouter()

_COLUMNS = """id, name, category, provider, description, icon,
              config_schema, config_values, status, suggested_by,
              approved_by, approved_at, created_at, updated_at"""


def _row_to_connector(r) -> ConnectorOut:
    return ConnectorOut(
        id=r[0], name=r[1], category=r[2], provider=r[3],
        description=r[4], icon=r[5] or "plug",
        config_schema=r[6] if isinstance(r[6], list) else json.loads(r[6]) if r[6] else [],
        config_values=r[7] if isinstance(r[7], dict) else json.loads(r[7]) if r[7] else {},
        status=r[8], suggested_by=r[9], approved_by=r[10],
        approved_at=r[11], created_at=r[12], updated_at=r[13],
    )


@router.get(
    "",
    response_model=list[ConnectorOut],
    summary="List all connectors",
)
async def list_connectors(
    request: Request,
    category: str = Query(None, description="Filter by category"),
    status: str = Query(None, description="Filter by status"),
):
    """List all connectors, optionally filtered by category or status."""
    session_factory = request.app.state.db_session_factory
    conditions = []
    params: dict = {}
    if category:
        conditions.append("category = :cat")
        params["cat"] = category
    if status:
        conditions.append("status = :st")
        params["st"] = status
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(f"SELECT {_COLUMNS} FROM connectors {where} ORDER BY category, name"),
                params,
            )
            return [_row_to_connector(r) for r in result.fetchall()]
    except Exception as e:
        logger.error("Failed to list connectors: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list connectors")


@router.get(
    "/{connector_id}",
    response_model=ConnectorOut,
    summary="Get a single connector",
)
async def get_connector(connector_id: int, request: Request):
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(f"SELECT {_COLUMNS} FROM connectors WHERE id = :cid"),
                {"cid": connector_id},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Connector not found")
            return _row_to_connector(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get connector: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get connector")


@router.post(
    "",
    response_model=ConnectorOut,
    summary="Create or suggest a connector",
)
async def create_connector(body: ConnectorCreate, request: Request):
    """Create a new connector (status=available) or AI-suggested (status=pending)."""
    session_factory = request.app.state.db_session_factory
    initial_status = "pending" if body.suggested_by and body.suggested_by.startswith("ai") else "available"
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(f"""INSERT INTO connectors
                    (name, category, provider, description, icon, config_schema, status, suggested_by)
                    VALUES (:name, :cat, :prov, :desc, :icon, :schema, :status, :by)
                    RETURNING {_COLUMNS}"""),
                {
                    "name": body.name,
                    "cat": body.category,
                    "prov": body.provider,
                    "desc": body.description,
                    "icon": body.icon,
                    "schema": json.dumps(body.config_schema),
                    "status": initial_status,
                    "by": body.suggested_by,
                },
            )
            row = result.fetchone()
            await session.commit()
            try:
                await write_audit_log(
                    request, action="create", resource_type="connector",
                    resource_id=str(row[0]),
                    actor=body.suggested_by or "admin", actor_type="staff",
                    changes={"name": body.name, "category": body.category, "provider": body.provider, "status": initial_status},
                )
            except Exception:
                pass
            return _row_to_connector(row)
    except Exception as e:
        logger.error("Failed to create connector: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create connector")


@router.put(
    "/{connector_id}/configure",
    response_model=ConnectorOut,
    summary="Update connector configuration",
)
async def configure_connector(connector_id: int, body: ConnectorUpdate, request: Request):
    """Save configuration values for a connector."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            # Auto-promote available connectors to approved when an api_key is provided
            has_api_key = bool(body.config_values.get("api_key", "").strip())
            result = await session.execute(
                text(f"""UPDATE connectors
                        SET config_values = :vals, updated_at = NOW(),
                            status = CASE
                                WHEN status = 'available' AND :has_key THEN 'approved'
                                ELSE status
                            END
                        WHERE id = :cid
                        RETURNING {_COLUMNS}"""),
                {"cid": connector_id, "vals": json.dumps(body.config_values), "has_key": has_api_key},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Connector not found")
            await session.commit()
            try:
                await write_audit_log(
                    request, action="update", resource_type="connector",
                    resource_id=str(connector_id),
                    changes={"action": "configure", "fields_updated": list(body.config_values.keys())},
                )
            except Exception:
                pass
            return _row_to_connector(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to configure connector: %s", e)
        raise HTTPException(status_code=500, detail="Failed to configure connector")


@router.put(
    "/{connector_id}/approve",
    response_model=ConnectorOut,
    summary="Approve or reject a connector",
)
async def approve_connector(connector_id: int, body: ConnectorApproval, request: Request):
    """Admin approves or rejects a connector."""
    session_factory = request.app.state.db_session_factory
    new_status = "approved" if body.action == "approve" else "rejected"
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(f"""UPDATE connectors
                        SET status = :st, approved_by = :by, approved_at = NOW(), updated_at = NOW()
                        WHERE id = :cid
                        RETURNING {_COLUMNS}"""),
                {"cid": connector_id, "st": new_status, "by": body.approved_by},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Connector not found")
            await session.commit()
            try:
                await write_audit_log(
                    request, action="approve" if body.action == "approve" else "reject",
                    resource_type="connector", resource_id=str(connector_id),
                    actor=body.approved_by, actor_type="staff",
                    changes={"new_status": new_status, "approved_by": body.approved_by},
                )
            except Exception:
                pass
            return _row_to_connector(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to approve connector: %s", e)
        raise HTTPException(status_code=500, detail="Failed to approve connector")


@router.put(
    "/{connector_id}/toggle",
    response_model=ConnectorOut,
    summary="Activate or disable a connector",
)
async def toggle_connector(connector_id: int, request: Request):
    """Toggle connector between active and disabled."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            # Get current status
            check = await session.execute(
                text("SELECT status FROM connectors WHERE id = :cid"),
                {"cid": connector_id},
            )
            row = check.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Connector not found")
            current = row[0]
            if current == "active":
                new_status = "disabled"
            elif current in ("approved", "disabled", "available"):
                new_status = "active"
            else:
                raise HTTPException(status_code=400, detail=f"Cannot toggle from status '{current}'")

            result = await session.execute(
                text(f"""UPDATE connectors
                        SET status = :st, updated_at = NOW()
                        WHERE id = :cid
                        RETURNING {_COLUMNS}"""),
                {"cid": connector_id, "st": new_status},
            )
            row = result.fetchone()
            await session.commit()
            try:
                await write_audit_log(
                    request, action="toggle", resource_type="connector",
                    resource_id=str(connector_id),
                    changes={"old_status": current, "new_status": new_status},
                )
            except Exception:
                pass
            return _row_to_connector(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to toggle connector: %s", e)
        raise HTTPException(status_code=500, detail="Failed to toggle connector")


@router.delete(
    "/{connector_id}",
    summary="Delete a connector",
)
async def delete_connector(connector_id: int, request: Request):
    """Remove a connector entirely."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("DELETE FROM connectors WHERE id = :cid RETURNING id"),
                {"cid": connector_id},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Connector not found")
            await session.commit()
            try:
                await write_audit_log(
                    request, action="delete", resource_type="connector",
                    resource_id=str(connector_id),
                    changes={"deleted": True},
                )
            except Exception:
                pass
            return {"ok": True, "deleted": connector_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete connector: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete connector")


@router.post(
    "/ai-suggest",
    response_model=list[ConnectorOut],
    summary="AI suggests connectors based on platform needs",
)
async def ai_suggest_connectors(request: Request):
    """AI analyzes the platform and suggests relevant connectors for admin approval."""
    session_factory = request.app.state.db_session_factory

    # AI-curated suggestions based on the BankOffer AI platform needs
    suggestions = [
        {
            "name": "Claude API (Anthropic)",
            "category": "ai",
            "provider": "Anthropic",
            "description": "Power offer explanations and customer communication with Claude AI models. Enhances GDPR Art. 22 transparency with natural language explanations.",
            "icon": "brain",
            "config_schema": [
                {"name": "api_key", "label": "API Key", "type": "password", "required": True, "placeholder": "sk-ant-..."},
                {"name": "model", "label": "Model", "type": "select", "required": True, "options": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"]},
                {"name": "max_tokens", "label": "Max Tokens", "type": "number", "required": False, "placeholder": "1024"},
            ],
        },
        {
            "name": "AWS SageMaker",
            "category": "cloud",
            "provider": "Amazon Web Services",
            "description": "Deploy ML scoring models at scale. Replace rules-based engine with trained XGBoost/neural models for better offer relevance.",
            "icon": "cloud",
            "config_schema": [
                {"name": "aws_access_key", "label": "Access Key ID", "type": "password", "required": True},
                {"name": "aws_secret_key", "label": "Secret Access Key", "type": "password", "required": True},
                {"name": "region", "label": "Region", "type": "select", "required": True, "options": ["eu-central-1", "eu-west-1", "us-east-1"]},
                {"name": "endpoint_name", "label": "Endpoint Name", "type": "text", "required": True, "placeholder": "offer-scoring-prod"},
            ],
        },
        {
            "name": "Google Analytics 4",
            "category": "analytics",
            "provider": "Google",
            "description": "Track offer impressions, click-through rates, and conversion funnels. Measure which AI recommendations drive the highest engagement.",
            "icon": "chart",
            "config_schema": [
                {"name": "measurement_id", "label": "Measurement ID", "type": "text", "required": True, "placeholder": "G-XXXXXXXXXX"},
                {"name": "api_secret", "label": "API Secret", "type": "password", "required": True},
            ],
        },
        {
            "name": "Twilio",
            "category": "messaging",
            "provider": "Twilio",
            "description": "Send offer notifications via SMS. Integrates with consent-based marketing preferences (ePrivacy compliance).",
            "icon": "message",
            "config_schema": [
                {"name": "account_sid", "label": "Account SID", "type": "text", "required": True},
                {"name": "auth_token", "label": "Auth Token", "type": "password", "required": True},
                {"name": "from_number", "label": "From Number", "type": "tel", "required": True, "placeholder": "+40..."},
            ],
        },
        {
            "name": "Salesforce CRM",
            "category": "crm",
            "provider": "Salesforce",
            "description": "Sync customer profiles and offer history with Salesforce. Enables relationship managers to see AI recommendations in their CRM.",
            "icon": "users",
            "config_schema": [
                {"name": "instance_url", "label": "Instance URL", "type": "url", "required": True, "placeholder": "https://yourorg.salesforce.com"},
                {"name": "client_id", "label": "Client ID", "type": "text", "required": True},
                {"name": "client_secret", "label": "Client Secret", "type": "password", "required": True},
            ],
        },
    ]

    created = []
    try:
        async with session_factory() as session:
            for s in suggestions:
                # Skip if already exists
                exists = await session.execute(
                    text("SELECT id FROM connectors WHERE provider = :prov AND name = :name"),
                    {"prov": s["provider"], "name": s["name"]},
                )
                if exists.fetchone():
                    continue

                result = await session.execute(
                    text(f"""INSERT INTO connectors
                        (name, category, provider, description, icon, config_schema, status, suggested_by)
                        VALUES (:name, :cat, :prov, :desc, :icon, :schema, 'pending', 'ai-engine')
                        RETURNING {_COLUMNS}"""),
                    {
                        "name": s["name"],
                        "cat": s["category"],
                        "prov": s["provider"],
                        "desc": s["description"],
                        "icon": s["icon"],
                        "schema": json.dumps(s["config_schema"]),
                    },
                )
                row = result.fetchone()
                created.append(_row_to_connector(row))
            await session.commit()
            return created
    except Exception as e:
        logger.error("AI suggest failed: %s", e)
        raise HTTPException(status_code=500, detail="AI suggestion failed")
