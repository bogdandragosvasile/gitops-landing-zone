"""Product catalog management router.

Full CRUD for the product catalog. Changes here are reflected immediately
in the recommendation engine (offers endpoint reads from DB on each request).
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from services.api.audit import write_audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


# ===== Pydantic models =====

class ProductCreate(BaseModel):
    product_id: str = Field(..., min_length=1, max_length=100, description="Unique product ID (e.g. prod_savings_plus)")
    name: str = Field(..., min_length=1, max_length=200, description="Display name")
    type: str = Field(..., min_length=1, max_length=100, description="Product type (investment, savings, insurance, etc.)")
    risk_level: str = Field(default="moderate", description="Risk level: low, moderate, high")
    short_description: Optional[str] = Field(None, max_length=500)
    category: Optional[str] = Field(None, max_length=100)
    channel: Optional[str] = Field(None, max_length=100)
    priority: Optional[str] = Field(None, max_length=50)
    lifecycle_stage: Optional[str] = Field(None, max_length=100)
    when_to_recommend: Optional[str] = Field(None, max_length=500)
    is_credit_product: bool = Field(default=False, description="True for loans, mortgages, credit cards")
    active: bool = Field(default=True, description="Whether this product is offered")


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    type: Optional[str] = Field(None, max_length=100)
    risk_level: Optional[str] = None
    short_description: Optional[str] = None
    category: Optional[str] = None
    channel: Optional[str] = None
    priority: Optional[str] = None
    lifecycle_stage: Optional[str] = None
    when_to_recommend: Optional[str] = None
    is_credit_product: Optional[bool] = None
    active: Optional[bool] = None


class ProductResponse(BaseModel):
    product_id: str
    name: str
    type: str
    risk_level: str
    short_description: Optional[str]
    category: Optional[str]
    channel: Optional[str]
    priority: Optional[str]
    lifecycle_stage: Optional[str]
    when_to_recommend: Optional[str]
    is_credit_product: bool
    active: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


async def _ensure_table(session):
    """Extend the products table with columns needed for CRUD management."""
    # Add new columns if they don't exist (safe for existing data)
    for col_def in [
        "product_id VARCHAR(100)",
        "type VARCHAR(100)",
        "risk_level VARCHAR(20) DEFAULT 'moderate'",
        "is_credit_product BOOLEAN DEFAULT FALSE",
        "active BOOLEAN DEFAULT TRUE",
        "updated_at TIMESTAMP DEFAULT NOW()",
    ]:
        col_name = col_def.split()[0]
        try:
            await session.execute(text(
                f"ALTER TABLE products ADD COLUMN IF NOT EXISTS {col_def}"
            ))
        except Exception:
            pass  # Column may already exist
    await session.commit()

    # Backfill product_id from product_name for existing rows using canonical prod_* IDs
    _PRODUCT_NAME_TO_ID = {
        "ETF Starter Portfolio": "prod_etf_starter",
        "ETF Growth Portfolio": "prod_etf_growth",
        "Mutual Funds": "prod_mutual_funds",
        "Managed Portfolio": "prod_managed_portfolio",
        "State Bonds / Treasury Bills": "prod_state_bonds",
        "Savings Deposit": "prod_savings_deposit",
        "Private Pension": "prod_private_pension",
        "Personal Loan": "prod_personal_loan",
        "Mortgage": "prod_mortgage",
        "Credit Card": "prod_credit_card",
        "Life Insurance": "prod_life_insurance",
        "Travel Insurance": "prod_travel_insurance",
    }
    for pname, pid in _PRODUCT_NAME_TO_ID.items():
        await session.execute(
            text("UPDATE products SET product_id = :pid WHERE product_name = :pname AND (product_id IS NULL OR product_id = '' OR product_id != :pid)"),
            {"pid": pid, "pname": pname},
        )
    # Fallback for any unknown products
    await session.execute(text("""
        UPDATE products SET product_id = 'prod_' || LOWER(REPLACE(REPLACE(product_name, ' ', '_'), '-', '_'))
        WHERE product_id IS NULL OR product_id = ''
    """))
    # Backfill type from category
    await session.execute(text("""
        UPDATE products SET type = LOWER(category)
        WHERE type IS NULL OR type = ''
    """))
    # Backfill risk_level
    await session.execute(text("""
        UPDATE products SET risk_level = 'moderate'
        WHERE risk_level IS NULL OR risk_level = ''
    """))
    await session.commit()


def _row_to_product(row) -> dict:
    """Convert a DB row mapping to a ProductResponse dict."""
    return {
        "product_id": row.get("product_id") or "",
        "name": row.get("product_name") or row.get("name") or "",
        "type": row.get("type") or row.get("category") or "",
        "risk_level": row.get("risk_level") or "moderate",
        "short_description": row.get("short_description"),
        "category": row.get("category"),
        "channel": row.get("channel"),
        "priority": row.get("priority"),
        "lifecycle_stage": row.get("lifecycle_stage"),
        "when_to_recommend": row.get("when_to_recommend"),
        "is_credit_product": bool(row.get("is_credit_product")),
        "active": row.get("active") if row.get("active") is not None else True,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get(
    "",
    response_model=list[ProductResponse],
    summary="List all products in the catalog",
)
async def list_products(request: Request, active_only: bool = False):
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        await _ensure_table(session)
        query = "SELECT * FROM products"
        if active_only:
            query += " WHERE active = TRUE"
        query += " ORDER BY product_name"
        result = await session.execute(text(query))
        rows = result.mappings().fetchall()
        return [_row_to_product(r) for r in rows]


@router.get("/performance", summary="Product P&L and performance metrics")
async def get_product_performance(
    request: Request,
    days: int = Query(default=30, ge=7, le=365, description="Lookback window in days"),
):
    """
    Aggregated per-product metrics: volume funnel, financial P&L, compliance,
    composite Business/Risk scores. Powers the Product P&L dashboard.
    """
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        rows = await session.execute(text("""
            WITH
            offers_gen AS (
                SELECT
                    offer->>'product_name'                        AS product_name,
                    COUNT(*)                                      AS offers_generated
                FROM audit_recommendations,
                     jsonb_array_elements(final_offers) AS offer
                WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                GROUP BY 1
            ),
            excluded AS (
                SELECT
                    exc->>'product_name'   AS product_name,
                    COUNT(*)               AS blocked_count
                FROM audit_recommendations,
                     jsonb_array_elements(
                         CASE WHEN jsonb_array_length(excluded_products) > 0
                              THEN excluded_products ELSE '[]'::jsonb END
                     ) AS exc
                WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                GROUP BY 1
            ),
            notif AS (
                SELECT
                    product_name,
                    COUNT(*)                                      AS offers_sent,
                    COUNT(*) FILTER (WHERE read = true)           AS offers_opened,
                    COUNT(*) FILTER (WHERE action = 'submitted')  AS journey_started
                FROM notifications
                WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                GROUP BY 1
            ),
            apps AS (
                SELECT
                    product_name,
                    COUNT(*) FILTER (WHERE status IN ('submitted','approved','rejected'))
                                                                  AS applications_submitted,
                    COUNT(*) FILTER (WHERE status = 'approved')   AS contracts_activated
                FROM application_forms
                WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                GROUP BY 1
            ),
            ovr AS (
                SELECT
                    ro.product_id                                 AS product_id,
                    COUNT(*)                                      AS override_count
                FROM recommendation_overrides ro
                WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                GROUP BY 1
            ),
            complaints AS (
                SELECT
                    rf.product_id                                 AS product_id,
                    COUNT(*) FILTER (WHERE rf.action = 'flagged') AS complaint_count
                FROM recommendation_feedback rf
                WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                GROUP BY 1
            )
            SELECT
                p.product_name,
                p.product_id,
                p.category,
                p.type,
                p.risk_level,
                COALESCE(og.offers_generated, 0)    AS offers_generated,
                COALESCE(n.offers_sent, 0)          AS offers_sent,
                COALESCE(n.offers_opened, 0)        AS offers_opened,
                COALESCE(n.journey_started, 0)      AS journey_started,
                COALESCE(a.applications_submitted,0)AS applications_submitted,
                COALESCE(a.contracts_activated, 0)  AS contracts_activated,
                COALESCE(exc.blocked_count, 0)      AS blocked_by_compliance,
                COALESCE(ov.override_count, 0)      AS manual_overrides,
                COALESCE(c.complaint_count, 0)      AS complaints_30d,
                COALESCE(pe.avg_ticket_value, 0)                   AS avg_ticket_value,
                COALESCE(pe.fee_rate, 0)                           AS fee_rate,
                COALESCE(pe.distribution_cost_per_activation, 0)   AS dist_cost_per_act,
                COALESCE(pe.risk_cost_rate, 0)                     AS risk_cost_rate
            FROM products p
            LEFT JOIN offers_gen og  ON og.product_name  = p.product_name
            LEFT JOIN excluded exc   ON exc.product_name = p.product_name
            LEFT JOIN notif n        ON n.product_name   = p.product_name
            LEFT JOIN apps a         ON a.product_name   = p.product_name
            LEFT JOIN ovr ov         ON ov.product_id    = p.product_id
            LEFT JOIN complaints c   ON c.product_id     = p.product_id
            LEFT JOIN product_economics pe ON pe.product_name = p.product_name
            WHERE p.active = true
            ORDER BY COALESCE(og.offers_generated, 0) DESC
        """), {"days": str(days)})

        raw = rows.mappings().fetchall()

    def _pct(num, den):  # noqa: E306
        return round(num / den * 100, 2) if den else 0.0

    def _div(num, den):
        return round(num / den, 4) if den else 0.0

    products_out = []
    for r in raw:
        gen      = int(r["offers_generated"])
        sent     = int(r["offers_sent"])
        opened   = int(r["offers_opened"])
        journey  = int(r["journey_started"])
        apps_sub = int(r["applications_submitted"])
        activated = int(r["contracts_activated"])
        blocked   = int(r["blocked_by_compliance"])
        overrides = int(r["manual_overrides"])
        complaints = int(r["complaints_30d"])

        avg_ticket    = float(r["avg_ticket_value"])
        fee_rate      = float(r["fee_rate"])
        dist_cost_act = float(r["dist_cost_per_act"])
        risk_rate     = float(r["risk_cost_rate"])

        gross_revenue    = round(avg_ticket * fee_rate * activated, 2)
        dist_cost        = round(dist_cost_act * activated, 2)
        risk_cost        = round(gross_revenue * risk_rate, 2)
        net_contribution = round(gross_revenue - dist_cost - risk_cost, 2)

        ctr             = _pct(opened, sent)
        journey_rate    = _pct(journey, opened)
        app_rate        = _pct(apps_sub, journey)
        activation_rate = _pct(activated, apps_sub)
        e2e_conversion  = _pct(activated, sent) if sent else _pct(activated, gen)

        revenue_per_sent  = _div(gross_revenue, sent) if sent else _div(gross_revenue, gen)
        cpa               = _div(dist_cost, activated)
        net_per_activated = _div(net_contribution, activated)
        roi               = _div(net_contribution, dist_cost)

        total_scored         = gen + blocked
        compliance_pass_rate = _pct(gen, total_scored)
        override_rate        = _pct(overrides, gen)
        complaint_rate       = _pct(complaints, activated)

        products_out.append({
            "product_name": r["product_name"],
            "product_id":   r["product_id"],
            "category":     r["category"],
            "type":         r["type"],
            "risk_level":   r["risk_level"],
            "offers_generated":       gen,
            "offers_sent":            sent,
            "offers_opened":          opened,
            "journey_started":        journey,
            "applications_submitted": apps_sub,
            "contracts_activated":    activated,
            "ctr":              ctr,
            "journey_rate":     journey_rate,
            "app_rate":         app_rate,
            "activation_rate":  activation_rate,
            "e2e_conversion":   e2e_conversion,
            "blocked_by_compliance":  blocked,
            "manual_overrides":       overrides,
            "complaints_30d":         complaints,
            "compliance_pass_rate":   compliance_pass_rate,
            "override_rate":          override_rate,
            "complaint_rate":         complaint_rate,
            "avg_ticket_value":       avg_ticket,
            "fee_rate":               fee_rate,
            "gross_revenue":          gross_revenue,
            "distribution_cost":      dist_cost,
            "risk_cost":              risk_cost,
            "net_contribution":       net_contribution,
            "revenue_per_sent":       revenue_per_sent,
            "cpa":                    cpa,
            "net_per_activated":      net_per_activated,
            "roi":                    roi,
        })

    def _minmax(vals):
        mn, mx = min(vals), max(vals)
        if mx == mn:
            return [50.0] * len(vals)
        return [round((v - mn) / (mx - mn) * 100, 1) for v in vals]

    active_idx = [i for i, p in enumerate(products_out) if p["offers_generated"] > 0]
    if len(active_idx) >= 2:
        e2e  = [products_out[i]["e2e_conversion"]      for i in active_idx]
        npac = [products_out[i]["net_per_activated"]    for i in active_idx]
        comp = [products_out[i]["compliance_pass_rate"] for i in active_idx]
        compl= [products_out[i]["complaint_rate"]       for i in active_idx]
        ovr  = [products_out[i]["override_rate"]        for i in active_idx]

        n_e2e  = _minmax(e2e)
        n_npac = _minmax(npac)
        n_comp = _minmax(comp)
        n_cmpl = _minmax([100 - v for v in compl])
        n_ovr  = _minmax([100 - v for v in ovr])

        for j, i in enumerate(active_idx):
            biz  = round(0.40 * n_e2e[j] + 0.35 * n_npac[j] + 0.25 * n_comp[j], 1)
            risk = round((1 - (0.40 * n_cmpl[j] + 0.35 * n_comp[j] + 0.25 * n_ovr[j]) / 100) * 100, 1)
            products_out[i]["business_score"] = biz
            products_out[i]["risk_penalty"]   = risk
            products_out[i]["final_score"]    = round(biz - risk, 1)
    else:
        for p in products_out:
            p["business_score"] = 0.0
            p["risk_penalty"]   = 0.0
            p["final_score"]    = 0.0

    alerts = []
    for p in products_out:
        name = p["product_name"]
        if p["compliance_pass_rate"] < 95 and p["offers_generated"] > 0:
            alerts.append({"level": "HIGH",   "product": name, "msg": f"Compliance pass rate {p['compliance_pass_rate']}% < 95%"})
        if p["complaint_rate"] > 3 and p["contracts_activated"] > 0:
            alerts.append({"level": "HIGH",   "product": name, "msg": f"Complaint rate {p['complaint_rate']}% > 3%"})
        if p["net_contribution"] < 0 and p["contracts_activated"] > 0:
            alerts.append({"level": "MEDIUM", "product": name, "msg": f"Negative net contribution (€{p['net_contribution']:,.0f})"})
        if p["e2e_conversion"] < 1 and p["offers_sent"] > 5:
            alerts.append({"level": "MEDIUM", "product": name, "msg": f"Very low E2E conversion {p['e2e_conversion']}%"})

    return {"period_days": days, "products": products_out, "alerts": alerts}


# ─── also remove the duplicate at the bottom, kept below only for the economics PUT ───

@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Get a single product",
)
async def get_product(product_id: str, request: Request):
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        await _ensure_table(session)
        result = await session.execute(
            text("SELECT * FROM products WHERE product_id = :pid"),
            {"pid": product_id},
        )
        row = result.mappings().fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")
        return _row_to_product(row)


@router.post(
    "",
    response_model=ProductResponse,
    summary="Add a new product to the catalog",
    status_code=201,
)
async def create_product(body: ProductCreate, request: Request):
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        await _ensure_table(session)

        # Check for duplicate
        existing = await session.execute(
            text("SELECT product_name FROM products WHERE product_id = :pid OR product_name = :name"),
            {"pid": body.product_id, "name": body.name},
        )
        if existing.fetchone():
            raise HTTPException(status_code=409, detail="Product with this ID or name already exists")

        await session.execute(
            text("""
                INSERT INTO products (product_id, product_name, type, risk_level, short_description,
                    category, channel, priority, lifecycle_stage, when_to_recommend,
                    is_credit_product, active, updated_at)
                VALUES (:pid, :name, :type, :risk, :desc, :cat, :chan, :prio, :stage, :when,
                    :credit, :active, NOW())
            """),
            {
                "pid": body.product_id,
                "name": body.name,
                "type": body.type,
                "risk": body.risk_level,
                "desc": body.short_description,
                "cat": body.category or body.type,
                "chan": body.channel,
                "prio": body.priority,
                "stage": body.lifecycle_stage,
                "when": body.when_to_recommend,
                "credit": body.is_credit_product,
                "active": body.active,
            },
        )
        await session.commit()

        # Return the created product
        result = await session.execute(
            text("SELECT * FROM products WHERE product_id = :pid"),
            {"pid": body.product_id},
        )
        row = result.mappings().fetchone()
        try:
            await write_audit_log(
                request, action="create", resource_type="product",
                resource_id=body.product_id,
                changes={"name": body.name, "type": body.type, "risk_level": body.risk_level, "active": body.active},
            )
        except Exception:
            pass
        return _row_to_product(row)


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Update an existing product",
)
async def update_product(product_id: str, body: ProductUpdate, request: Request):
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        await _ensure_table(session)

        existing = await session.execute(
            text("SELECT product_name FROM products WHERE product_id = :pid"),
            {"pid": product_id},
        )
        if not existing.fetchone():
            raise HTTPException(status_code=404, detail="Product not found")

        # Build dynamic UPDATE
        updates = {}
        if body.name is not None:
            updates["product_name"] = body.name
        if body.type is not None:
            updates["type"] = body.type
        if body.risk_level is not None:
            updates["risk_level"] = body.risk_level
        if body.short_description is not None:
            updates["short_description"] = body.short_description
        if body.category is not None:
            updates["category"] = body.category
        if body.channel is not None:
            updates["channel"] = body.channel
        if body.priority is not None:
            updates["priority"] = body.priority
        if body.lifecycle_stage is not None:
            updates["lifecycle_stage"] = body.lifecycle_stage
        if body.when_to_recommend is not None:
            updates["when_to_recommend"] = body.when_to_recommend
        if body.is_credit_product is not None:
            updates["is_credit_product"] = body.is_credit_product
        if body.active is not None:
            updates["active"] = body.active

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
        updates["pid"] = product_id
        await session.execute(
            text(f"UPDATE products SET {set_clauses}, updated_at = NOW() WHERE product_id = :pid"),
            updates,
        )
        await session.commit()

        result = await session.execute(
            text("SELECT * FROM products WHERE product_id = :pid"),
            {"pid": product_id},
        )
        row = result.mappings().fetchone()
        try:
            await write_audit_log(
                request, action="update", resource_type="product",
                resource_id=product_id,
                changes={k: v for k, v in updates.items() if k != "pid"},
            )
        except Exception:
            pass
        return _row_to_product(row)


@router.delete(
    "/{product_id}",
    summary="Remove a product from the catalog",
)
async def delete_product(product_id: str, request: Request):
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        await _ensure_table(session)
        result = await session.execute(
            text("DELETE FROM products WHERE product_id = :pid RETURNING product_name"),
            {"pid": product_id},
        )
        row = result.fetchone()
        await session.commit()
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")
        try:
            await write_audit_log(
                request, action="delete", resource_type="product",
                resource_id=product_id,
                changes={"deleted_name": row[0]},
            )
        except Exception:
            pass
        return {"status": "deleted", "product_id": product_id, "name": row[0]}


@router.put("/{product_name}/economics", summary="Update product economics parameters")
async def update_product_economics(
    product_name: str,
    request: Request,
):
    """Update avg_ticket_value, fee_rate, distribution_cost, risk_cost_rate for a product."""
    body = await request.json()
    allowed = {"avg_ticket_value", "fee_rate", "distribution_cost_per_activation", "risk_cost_rate"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields provided")

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        # Fetch current values first, then merge
        cur = await session.execute(
            text("SELECT avg_ticket_value, fee_rate, distribution_cost_per_activation, risk_cost_rate FROM product_economics WHERE product_name = :pn"),
            {"pn": product_name},
        )
        row = cur.mappings().fetchone()
        merged = dict(row) if row else {"avg_ticket_value": 0, "fee_rate": 0, "distribution_cost_per_activation": 0, "risk_cost_rate": 0}
        merged.update(updates)
        merged["product_name"] = product_name
        result = await session.execute(
            text("""INSERT INTO product_economics
                        (product_name, avg_ticket_value, fee_rate, distribution_cost_per_activation, risk_cost_rate, updated_at)
                    VALUES (:product_name, :avg_ticket_value, :fee_rate, :distribution_cost_per_activation, :risk_cost_rate, NOW())
                    ON CONFLICT (product_name) DO UPDATE SET
                        avg_ticket_value = EXCLUDED.avg_ticket_value,
                        fee_rate = EXCLUDED.fee_rate,
                        distribution_cost_per_activation = EXCLUDED.distribution_cost_per_activation,
                        risk_cost_rate = EXCLUDED.risk_cost_rate,
                        updated_at = NOW()
                    RETURNING product_name, avg_ticket_value, fee_rate, distribution_cost_per_activation, risk_cost_rate"""),
            merged,
        )
        row = result.mappings().fetchone()
        await session.commit()
        return dict(row)
