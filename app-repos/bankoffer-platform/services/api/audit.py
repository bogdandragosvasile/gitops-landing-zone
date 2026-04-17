"""Central audit logging utility for BankOffer AI.

Provides write_audit_log() for routers to record state-changing actions,
and AuditMiddleware for automatic POST/PUT/DELETE request logging.

All writes go to the immutable `audit_log` table (protected by trigger).
"""

import logging
import time
import uuid
from typing import Optional

from fastapi import Request
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Methods that mutate state and should always be logged
_MUTATING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Paths to exclude from automatic middleware logging (high-volume, read-like, or handled by routers)
_EXCLUDE_PATHS = {
    "/health", "/metrics", "/openapi.json", "/docs", "/redoc",
}


def _get_client_ip(request: Request) -> Optional[str]:
    """Extract client IP, respecting X-Forwarded-For behind proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _get_actor_from_request(request: Request) -> tuple[str, str]:
    """Best-effort extraction of actor identity from request state or auth header."""
    # Check if a router already set the actor on request state
    if hasattr(request.state, "audit_actor"):
        return request.state.audit_actor, getattr(request.state, "audit_actor_type", "staff")
    # Try to parse from Authorization header (JWT sub claim)
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            import base64
            import json
            token = auth.split(" ", 1)[1]
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            sub = payload.get("sub") or payload.get("email") or payload.get("customer_id", "")
            actor_type = payload.get("type", "staff")
            if sub:
                return str(sub), actor_type
        except Exception:
            pass
    return "anonymous", "system"


async def write_audit_log(
    request: Request,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    actor: Optional[str] = None,
    actor_type: str = "staff",
    changes: Optional[dict] = None,
    http_status: Optional[int] = None,
):
    """Write a single entry to the audit_log table.

    Call this from any router for fine-grained action logging.
    Raises on failure — callers decide whether to swallow or propagate.
    """
    session_factory = request.app.state.db_session_factory
    if actor is None:
        actor, actor_type = _get_actor_from_request(request)
    ip = _get_client_ip(request)
    ua = request.headers.get("user-agent", "")[:500]
    endpoint = f"{request.method} {request.url.path}"
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

    import json
    async with session_factory() as session:
        await session.execute(
            text("""INSERT INTO audit_log
                (request_id, action, actor, actor_type, resource_type, resource_id,
                 changes, ip_address, user_agent, endpoint, http_method, http_status)
                VALUES (CAST(:rid AS uuid), :action, :actor, :actor_type, :rtype, :rid2,
                        CAST(:changes AS jsonb), CAST(:ip AS inet), :ua, :ep, :method, :status)"""),
            {
                "rid": request_id,
                "action": action,
                "actor": actor,
                "actor_type": actor_type,
                "rtype": resource_type,
                "rid2": str(resource_id) if resource_id is not None else None,
                "changes": json.dumps(changes or {}),
                "ip": ip,
                "ua": ua,
                "ep": endpoint,
                "method": request.method,
                "status": http_status,
            },
        )
        await session.commit()


async def write_ai_api_log(
    request: Request,
    provider: str,
    model: str,
    endpoint_url: str = "",
    request_prompt: str = "",
    response_text: str = "",
    request_tokens: int = 0,
    response_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
    http_status: int = 200,
    outcome: str = "success",
    guardrail_action: str = "",
    guardrail_details: Optional[dict] = None,
    error_message: str = "",
):
    """Write an entry to ai_api_call_log for every LLM provider call."""
    session_factory = request.app.state.db_session_factory
    import json
    async with session_factory() as session:
        await session.execute(
            text("""INSERT INTO ai_api_call_log
                (provider, model, endpoint_url, request_prompt, request_tokens,
                 response_text, response_tokens, total_tokens, latency_ms,
                 http_status, outcome, guardrail_action, guardrail_details, error_message)
                VALUES (:provider, :model, :url, :prompt, :req_tok,
                        :resp_text, :resp_tok, :total_tok, :lat,
                        :status, :outcome, :guard_action, CAST(:guard_details AS jsonb), :err)"""),
            {
                "provider": provider,
                "model": model,
                "url": endpoint_url[:500],
                "prompt": request_prompt[:4000],
                "req_tok": request_tokens,
                "resp_text": response_text[:4000],
                "resp_tok": response_tokens,
                "total_tok": total_tokens,
                "lat": latency_ms,
                "status": http_status,
                "outcome": outcome,
                "guard_action": guardrail_action or None,
                "guard_details": json.dumps(guardrail_details or {}),
                "err": error_message[:2000] if error_message else None,
            },
        )
        await session.commit()


async def write_consent_history(
    request: Request,
    customer_id: str,
    consent_type: str,
    old_value: Optional[bool],
    new_value: bool,
    changed_by: str,
    change_reason: str = "",
):
    """Append to consent_history when any consent flag changes."""
    session_factory = request.app.state.db_session_factory
    ip = _get_client_ip(request)
    async with session_factory() as session:
        await session.execute(
            text("""INSERT INTO consent_history
                (customer_id, consent_type, old_value, new_value, changed_by,
                 change_reason, ip_address)
                VALUES (:cid, :ctype, :old, :new, :by, :reason, CAST(:ip AS inet))"""),
            {
                "cid": customer_id,
                "ctype": consent_type,
                "old": old_value,
                "new": new_value,
                "by": changed_by,
                "reason": change_reason or None,
                "ip": ip,
            },
        )
        await session.commit()


class AuditMiddleware(BaseHTTPMiddleware):
    """Middleware that auto-logs every mutating request to audit_log.

    This provides a baseline audit trail. Individual routers add
    fine-grained entries with before/after diffs via write_audit_log().
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Assign a request_id for correlation
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Only log mutating methods
        if request.method not in _MUTATING_METHODS:
            return await call_next(request)

        # Skip excluded paths
        path = request.url.path
        if path in _EXCLUDE_PATHS:
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - start) * 1000)

        # Log asynchronously — don't slow down the response
        try:
            actor, actor_type = _get_actor_from_request(request)
            ip = _get_client_ip(request)
            ua = request.headers.get("user-agent", "")[:500]

            # Derive resource_type from path prefix
            parts = [p for p in path.strip("/").split("/") if p]
            resource_type = parts[0] if parts else "unknown"

            session_factory = request.app.state.db_session_factory
            import json
            async with session_factory() as session:
                await session.execute(
                    text("""INSERT INTO audit_log
                        (request_id, action, actor, actor_type, resource_type, resource_id,
                         changes, ip_address, user_agent, endpoint, http_method, http_status, duration_ms)
                        VALUES (CAST(:rid AS uuid), :action, :actor, :actor_type, :rtype, :rid2,
                                CAST(:changes AS jsonb), CAST(:ip AS inet), :ua, :ep, :method, :status, :dur)"""),
                    {
                        "rid": request_id,
                        "action": request.method.lower(),
                        "actor": actor,
                        "actor_type": actor_type,
                        "rtype": resource_type,
                        "rid2": parts[1] if len(parts) > 1 else None,
                        "changes": json.dumps({"path": path}),
                        "ip": ip,
                        "ua": ua,
                        "ep": f"{request.method} {path}",
                        "method": request.method,
                        "status": response.status_code,
                        "dur": duration_ms,
                    },
                )
                await session.commit()
        except Exception as e:
            logger.warning("Audit middleware logging failed: %s", e)

        return response
