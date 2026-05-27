from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError
from pathlib import Path

from .config import settings
from .api.api_v1.api import api_router
from .core.init_admin import create_initial_admin
from .core.health import HealthChecker
from .middleware.logging import LoggingMiddleware
from .middleware.security import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    RequestSizeLimitMiddleware
)
from .middleware.audit_middleware import AuditMiddleware
from .middleware.csrf_middleware import CSRFProtectionMiddleware
from .core.exceptions import (
    http_exception_handler,
    starlette_http_exception_handler,
    validation_exception_handler,
    database_exception_handler,
    pydantic_validation_exception_handler,
    general_exception_handler,
    business_logic_exception_handler,
    BusinessLogicError
)
from .background.scheduler import start_scheduler, stop_scheduler
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events
    """
    # Startup
    logger.info("🚀 Application startup initiated")
    create_initial_admin()

    # Reference-data integrity check (WARNING only, non-fatal)
    try:
        from .core.startup_checks import check_reference_data_integrity
        from .database import SessionLocal
        _startup_db = SessionLocal()
        try:
            check_reference_data_integrity(_startup_db)
        finally:
            _startup_db.close()
    except Exception:
        logger.warning("Startup reference-data check failed — continuing", exc_info=True)

    # Start background scheduler for periodic tasks
    scheduler = None
    try:
        scheduler = start_scheduler()
        logger.info("✅ Background scheduler started successfully")
    except Exception as e:
        logger.error(f"❌ Failed to start background scheduler: {e}")
        # Continue without scheduler (non-critical)

    logger.info("✅ Application startup complete")

    yield

    # Shutdown
    logger.info("🔄 Application shutdown initiated")
    if scheduler:
        try:
            stop_scheduler()  # Use stop_scheduler function
            logger.info("✅ Background scheduler stopped")
        except Exception as e:
            logger.error(f"❌ Error stopping scheduler: {e}")

    logger.info("✅ Application shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    description="LFA Education Center - Comprehensive Football Education Platform featuring LFA Player Development, Coach Training, Internship Programs, and Gamification with Parallel Specialization Tracks",
    version="2.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Setup templates and static files
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Add middleware conditionally based on environment
if settings.ENABLE_SECURITY_HEADERS:
    app.add_middleware(SecurityHeadersMiddleware)

if settings.ENABLE_REQUEST_SIZE_LIMIT:
    app.add_middleware(RequestSizeLimitMiddleware, max_size_mb=10)

if settings.ENABLE_RATE_LIMITING:
    app.add_middleware(
        RateLimitMiddleware, 
        calls=settings.RATE_LIMIT_CALLS, 
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS
    )

if settings.ENABLE_STRUCTURED_LOGGING:
    app.add_middleware(LoggingMiddleware)  # Should be after rate limiting for accurate logs

# Add audit middleware (logs all important actions)
app.add_middleware(AuditMiddleware)

# Add CSRF protection middleware (SECURITY: Double Submit Cookie pattern)
# Must be BEFORE CORS middleware to inspect requests first
app.add_middleware(CSRFProtectionMiddleware)

# Set up CORS middleware (SECURITY: Explicit allowlist to prevent CSRF)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,  # ✅ SECURITY FIX: Explicit allowlist (no wildcards)
    allow_credentials=True,  # Required for cookie-based auth
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],  # ✅ SECURITY FIX: Explicit methods
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],  # ✅ SECURITY FIX: Explicit headers
    expose_headers=["X-CSRF-Token"],  # Allow client to read CSRF token from response
)

# Add exception handlers
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(StarletteHTTPException, starlette_http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, database_exception_handler)
app.add_exception_handler(ValidationError, pydantic_validation_exception_handler)
app.add_exception_handler(BusinessLogicError, business_logic_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)

# Include web routes (HTML pages)
from .api.web_routes import router as web_router
app.include_router(web_router)


@app.get("/api")
async def api_root():
    """API root endpoint"""
    return {
        "message": "Practice Booking System API",
        "version": "1.0.0",
        "docs": f"{settings.API_V1_STR}/docs"
    }


@app.get("/health")
async def health_check():
    """Basic health check endpoint"""
    return {"status": "healthy"}


@app.get("/health/detailed")
async def detailed_health_check():
    """Comprehensive health check with system metrics"""
    return await HealthChecker.get_comprehensive_health()


@app.get("/health/ready")
async def readiness_check():
    """
    Kubernetes-style readiness probe.

    Returns HTTP 200 when the service is ready to accept traffic (database
    reachable and responding).  Returns HTTP 503 when the database is unhealthy
    so orchestrators remove the pod from the load-balancer rotation.
    """
    db_health = await HealthChecker.get_database_health()
    is_ready = db_health["status"] != "unhealthy"
    content = {
        "status": "ready" if is_ready else "not_ready",
        "database": db_health["status"],
    }
    return JSONResponse(content=content, status_code=200 if is_ready else 503)


@app.get("/health/live")
async def liveness_check():
    """Kubernetes-style liveness probe"""
    return {"status": "alive"}


@app.get("/health/worker")
async def worker_health_check():
    """
    Redis broker and Celery worker liveness probe.

    Returns HTTP 200 when Redis is reachable (workers may be degraded but
    the app is still operational).  Returns HTTP 503 only when Redis itself
    is unreachable, which prevents background job enqueueing entirely.
    """
    result = await HealthChecker.get_worker_health()
    status_code = 503 if result.get("status") == "unhealthy" else 200
    return JSONResponse(content=result, status_code=status_code)


@app.get("/metrics")
async def domain_metrics(
    format: str = Query(
        default="json",
        description="Output format: 'json' (default) or 'prometheus'.",
    ),
):
    """
    In-process domain event counters.

    Returns lifetime totals (since last process start) for key operational
    events: reward generation, booking creation, enrollment gate decisions.
    Intended for internal monitoring and alerting dashboards.

    Set ``?format=prometheus`` to receive Prometheus text exposition format
    (``text/plain; version=0.0.4``) suitable for a Prometheus scrape target.
    """
    from .core.metrics import metrics
    if format == "prometheus":
        return PlainTextResponse(
            content=metrics.format_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    return {
        "counters": metrics.get_snapshot(),
        "labeled_counters": metrics.get_labeled_snapshot(),
    }


@app.get("/metrics/alerts")
async def metrics_alerts():
    """
    Evaluate in-process counter values against configured alert thresholds.

    Returns ``{"status": "ok"|"warning", "thresholds": {...}}`` where each
    entry in ``thresholds`` includes ``value``, ``threshold`` and ``firing``.
    Only ratio-based alerts are emitted when enough traffic has been seen
    (denominator > 0); the slow-query count is always evaluated.

    Thresholds are configured via the application Settings:
    - ``ALERT_REWARD_FAILURE_RATE`` (default 0.05)
    - ``ALERT_BOOKING_WAITLIST_RATE`` (default 0.30)
    - ``ALERT_ENROLLMENT_GATE_BLOCK_RATE`` (default 0.20)
    - ``ALERT_SLOW_QUERY_TOTAL`` (default 10)
    """
    from .core.metrics import metrics
    from .config import settings
    return metrics.evaluate_alerts(settings)