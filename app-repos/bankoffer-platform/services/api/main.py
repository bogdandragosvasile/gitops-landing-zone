"""FastAPI application entry point for the Bank Offering AI API service."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

import asyncio

from services.api.metrics import CUSTOMERS_TOTAL, PRODUCTS_ACTIVE, KILL_SWITCH_ACTIVE

from services.api.audit import AuditMiddleware
from services.api.routers import api_tokens, compliance, connectors, consent_registry, customer_auth, intelligence, offers, products, profiles, staff_auth, workflow

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = [
    "https://bankoffer.lupulup.com",
    "https://my-bankoffer.lupulup.com",
    "https://bankoffer-k8s.lupulup.com",
    "https://my-bankoffer-k8s.lupulup.com",
    "https://bankofferingai.example.com",
    "http://localhost:3000",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown lifecycle events."""
    # Startup: initialize DB connection pool and Redis
    logger.info("Starting up API service...")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import redis.asyncio as aioredis
    import os

    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/bankofferingai",
    )
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    engine = create_async_engine(database_url, pool_size=20, max_overflow=10)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)

    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    app.state.redis = redis_client

    logger.info("Database and Redis connections established.")

    # Start consent registry background sync
    sync_task = asyncio.create_task(
        consent_registry.start_background_sync(session_factory)
    )
    app.state.consent_sync_task = sync_task

    # Start regulatory source change detection
    source_check_task = asyncio.create_task(
        consent_registry.start_background_source_checks(session_factory)
    )
    app.state.source_check_task = source_check_task

    # Start metrics gauge updater
    async def _update_gauges():
        while True:
            try:
                async with session_factory() as session:
                    from sqlalchemy import text
                    r = await session.execute(text("SELECT count(*) FROM customers"))
                    CUSTOMERS_TOTAL.set(r.scalar() or 0)
                    r = await session.execute(text("SELECT count(*) FROM products WHERE active = TRUE"))
                    PRODUCTS_ACTIVE.set(r.scalar() or 0)
                    r = await session.execute(text("SELECT active FROM model_kill_switch ORDER BY id DESC LIMIT 1"))
                    ks = r.scalar()
                    KILL_SWITCH_ACTIVE.set(1 if ks else 0)
            except Exception:
                pass
            await asyncio.sleep(30)

    gauge_task = asyncio.create_task(_update_gauges())
    app.state.gauge_task = gauge_task

    yield

    # Cancel background tasks
    sync_task.cancel()
    source_check_task.cancel()
    gauge_task.cancel()

    # Shutdown: close connections
    logger.info("Shutting down API service...")
    await redis_client.aclose()
    await engine.dispose()
    logger.info("Connections closed.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Bank Offering AI",
        description="Personalized bank product offering engine powered by AI",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Audit middleware — logs every POST/PUT/DELETE to audit_log
    app.add_middleware(AuditMiddleware)

    # Prometheus metrics
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics")

    # Routers
    app.include_router(offers.router, prefix="/offers", tags=["offers"])
    app.include_router(profiles.router, prefix="/profiles", tags=["profiles"])
    app.include_router(compliance.router, prefix="/compliance", tags=["compliance"])
    app.include_router(customer_auth.router, prefix="/customer-auth", tags=["customer-auth"])
    app.include_router(staff_auth.router, prefix="/staff-auth", tags=["staff-auth"])
    app.include_router(api_tokens.router, prefix="/api-tokens", tags=["api-tokens"])
    app.include_router(products.router, prefix="/products-catalog", tags=["products"])
    app.include_router(consent_registry.router, prefix="/consent-registry", tags=["consent-registry"])
    app.include_router(workflow.router, prefix="/workflow", tags=["workflow"])
    app.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
    app.include_router(intelligence.router, prefix="/intelligence", tags=["intelligence"])

    if os.getenv("KAFKA_BOOTSTRAP_SERVERS"):
        from services.api.routers import webhooks
        app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])

    @app.get("/health", tags=["health"])
    async def health_check():
        """Liveness probe endpoint."""
        return {
            "status": "healthy",
            "service": "bank-offering-api",
            "demo_mode": os.getenv("DEMO_MODE", "false").lower() == "true",
            "keycloak_configured": bool(os.getenv("KEYCLOAK_URL")),
        }

    # Serve frontend portals
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        @app.get("/", include_in_schema=False)
        async def root():
            """Employee portal (bank staff dashboard)."""
            return FileResponse(static_dir / "index.html")

        @app.get("/portal", include_in_schema=False)
        async def customer_portal():
            """Customer portal (client-facing)."""
            return FileResponse(static_dir / "portal.html")

        @app.get("/admin", include_in_schema=False)
        async def admin_portal():
            """Admin portal (administrators only)."""
            return FileResponse(static_dir / "admin.html")

        @app.get("/presentation", include_in_schema=False)
        async def presentation():
            """Interactive HTML presentation of the platform."""
            pres_file = Path(__file__).resolve().parent.parent.parent / "presentation.html"
            if pres_file.is_file():
                return FileResponse(pres_file)
            return {"error": "presentation.html not found"}

        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.api.main:app", host="0.0.0.0", port=8000, reload=True)
