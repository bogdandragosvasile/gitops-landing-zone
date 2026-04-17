"""Workflow router - Employee ↔ Customer communication (notifications + application forms)."""

import json
import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from services.api.middleware.auth import get_current_customer_id
from services.api.models import FormCreate, FormOut, FormSubmission, NotificationOut

logger = logging.getLogger(__name__)

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

router = APIRouter()


# ===== Notifications (Employee-facing) =====

@router.get(
    "/notifications",
    response_model=list[NotificationOut],
    summary="List employee notifications",
    description="Returns unread and recent notifications for the employee dashboard.",
)
async def list_notifications(
    request: Request,
    unread_only: bool = Query(False, description="Only return unread notifications"),
    limit: int = Query(50, ge=1, le=200),
):
    """Fetch notifications (offer acceptances/rejections from customers)."""
    session_factory = request.app.state.db_session_factory

    where = "WHERE n.read = FALSE" if unread_only else ""
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(f"""SELECT n.id, n.customer_id, n.offer_id, n.product_name,
                            n.action, n.recommendation_id, n.read, n.created_at
                     FROM notifications n
                     {where}
                     ORDER BY n.created_at DESC
                     LIMIT :lim"""),
                {"lim": limit},
            )
            rows = result.fetchall()
            return [
                NotificationOut(
                    id=r[0], customer_id=r[1], offer_id=r[2], product_name=r[3],
                    action=r[4], recommendation_id=str(r[5]) if r[5] else None,
                    read=r[6], created_at=r[7],
                )
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to fetch notifications: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch notifications")


@router.get(
    "/notifications/count",
    summary="Get unread notification count",
)
async def notification_count(request: Request):
    """Quick count for the badge in the UI."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT count(*) FROM notifications WHERE read = FALSE")
            )
            return {"unread": result.scalar() or 0}
    except Exception as e:
        logger.error("Notification count error: %s", e)
        return {"unread": 0}


@router.put(
    "/notifications/{notification_id}/read",
    summary="Mark notification as read",
)
async def mark_notification_read(notification_id: int, request: Request):
    """Mark a single notification as read."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            await session.execute(
                text("UPDATE notifications SET read = TRUE WHERE id = :nid"),
                {"nid": notification_id},
            )
            await session.commit()
            return {"ok": True}
    except Exception as e:
        logger.error("Mark read error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to mark as read")


@router.put(
    "/notifications/read-all",
    summary="Mark all notifications as read",
)
async def mark_all_read(request: Request):
    """Bulk mark all notifications as read."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            await session.execute(text("UPDATE notifications SET read = TRUE WHERE read = FALSE"))
            await session.commit()
            return {"ok": True}
    except Exception as e:
        logger.error("Mark all read error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to mark all as read")


# ===== Application Forms (Employee sends → Customer fills) =====

@router.post(
    "/forms",
    response_model=FormOut,
    summary="Send application form to customer",
    description="Employee creates and sends a form for the customer to fill out.",
)
async def create_form(body: FormCreate, request: Request):
    """Employee sends an application form to a customer."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""INSERT INTO application_forms
                    (customer_id, employee_id, notification_id, product_name,
                     offer_id, form_type, fields, status)
                    VALUES (:cid, :eid, :nid, :pname, :oid, :ftype, :fields, 'pending')
                    RETURNING id, created_at"""),
                {
                    "cid": body.customer_id,
                    "eid": body.employee_id,
                    "nid": body.notification_id,
                    "pname": body.product_name,
                    "oid": body.offer_id,
                    "ftype": body.form_type,
                    "fields": json.dumps(body.fields),
                },
            )
            row = result.fetchone()
            await session.commit()

            # Mark linked notification as read
            if body.notification_id:
                await session.execute(
                    text("UPDATE notifications SET read = TRUE WHERE id = :nid"),
                    {"nid": body.notification_id},
                )
                await session.commit()

            return FormOut(
                id=row[0],
                customer_id=body.customer_id,
                employee_id=body.employee_id,
                notification_id=body.notification_id,
                product_name=body.product_name,
                offer_id=body.offer_id,
                form_type=body.form_type,
                fields=body.fields,
                status="pending",
                submitted_data=None,
                submitted_at=None,
                created_at=row[1],
            )
    except Exception as e:
        logger.error("Failed to create form: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create form")


@router.get(
    "/forms",
    response_model=list[FormOut],
    summary="List all forms (employee view)",
)
async def list_all_forms(
    request: Request,
    status: str = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
):
    """Employee view of all forms they've sent."""
    session_factory = request.app.state.db_session_factory
    where = "WHERE f.status = :st" if status else ""
    params: dict = {"lim": limit}
    if status:
        params["st"] = status
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(f"""SELECT f.id, f.customer_id, f.employee_id, f.notification_id,
                            f.product_name, f.offer_id, f.form_type, f.fields,
                            f.status, f.submitted_data, f.submitted_at, f.created_at
                     FROM application_forms f
                     {where}
                     ORDER BY f.created_at DESC
                     LIMIT :lim"""),
                params,
            )
            rows = result.fetchall()
            return [
                FormOut(
                    id=r[0], customer_id=r[1], employee_id=r[2], notification_id=r[3],
                    product_name=r[4], offer_id=r[5], form_type=r[6],
                    fields=r[7] if isinstance(r[7], list) else json.loads(r[7]) if r[7] else [],
                    status=r[8], submitted_data=r[9], submitted_at=r[10], created_at=r[11],
                )
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to list forms: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list forms")


@router.get(
    "/forms/customer/{customer_id}",
    response_model=list[FormOut],
    summary="Get forms for a customer",
    description="Customer retrieves forms sent to them by employees.",
)
async def get_customer_forms(
    customer_id: str,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Customer-facing endpoint to retrieve their pending/submitted forms."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""SELECT f.id, f.customer_id, f.employee_id, f.notification_id,
                            f.product_name, f.offer_id, f.form_type, f.fields,
                            f.status, f.submitted_data, f.submitted_at, f.created_at
                     FROM application_forms f
                     WHERE f.customer_id = :cid
                     ORDER BY f.created_at DESC"""),
                {"cid": customer_id},
            )
            rows = result.fetchall()
            return [
                FormOut(
                    id=r[0], customer_id=r[1], employee_id=r[2], notification_id=r[3],
                    product_name=r[4], offer_id=r[5], form_type=r[6],
                    fields=r[7] if isinstance(r[7], list) else json.loads(r[7]) if r[7] else [],
                    status=r[8], submitted_data=r[9], submitted_at=r[10], created_at=r[11],
                )
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to get customer forms: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get customer forms")


@router.put(
    "/forms/{form_id}/submit",
    response_model=FormOut,
    summary="Customer submits a filled form",
)
async def submit_form(
    form_id: int,
    body: FormSubmission,
    request: Request,
    authenticated_customer: str = Depends(get_current_customer_id),
):
    """Customer fills and submits an application form."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            # Verify form exists and is pending
            check = await session.execute(
                text("SELECT status FROM application_forms WHERE id = :fid"),
                {"fid": form_id},
            )
            row = check.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Form not found")
            if row[0] != "pending":
                raise HTTPException(status_code=400, detail=f"Form already {row[0]}")

            # Update form with submitted data
            result = await session.execute(
                text("""UPDATE application_forms
                        SET status = 'submitted',
                            submitted_data = :data,
                            submitted_at = NOW()
                        WHERE id = :fid
                        RETURNING id, customer_id, employee_id, notification_id,
                                  product_name, offer_id, form_type, fields,
                                  status, submitted_data, submitted_at, created_at"""),
                {"fid": form_id, "data": json.dumps(body.data)},
            )
            r = result.fetchone()
            await session.commit()

            # Create notification for the employee that form was submitted
            await session.execute(
                text("""INSERT INTO notifications
                    (customer_id, offer_id, product_name, action, recommendation_id)
                    VALUES (:cid, :oid, :pname, 'submitted', NULL)"""),
                {"cid": r[1], "oid": r[5] or '', "pname": r[4]},
            )
            await session.commit()

            return FormOut(
                id=r[0], customer_id=r[1], employee_id=r[2], notification_id=r[3],
                product_name=r[4], offer_id=r[5], form_type=r[6],
                fields=r[7] if isinstance(r[7], list) else json.loads(r[7]) if r[7] else [],
                status=r[8], submitted_data=r[9], submitted_at=r[10], created_at=r[11],
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to submit form: %s", e)
        raise HTTPException(status_code=500, detail="Failed to submit form")
