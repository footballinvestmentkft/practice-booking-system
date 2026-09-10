import logging
import os
import sys
import time

from sqlalchemy import create_engine, event as sa_event, text
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import settings

if settings.TESTING or "pytest" in sys.modules:
    from .core.test_database_safety import assert_safe_test_database_url

    assert_safe_test_database_url(
        settings.DATABASE_URL,
        explicitly_configured="DATABASE_URL" in os.environ,
        disposable_confirmed=os.getenv("LFA_TEST_DATABASE_DISPOSABLE", "").lower()
        in {"1", "true", "yes"},
    )

# ── Connection arguments ───────────────────────────────────────────────────────
# Passed to the underlying psycopg2 driver on every new connection.
# connect_timeout prevents the app from hanging indefinitely when PostgreSQL is
# temporarily unreachable (e.g. during a rolling restart or pod scheduling).
_connect_args: dict = {"connect_timeout": settings.DB_CONNECT_TIMEOUT}
if settings.DB_STATEMENT_TIMEOUT_MS > 0:
    # Per-statement wall-clock limit — kills runaway queries before they fill
    # the connection pool.  Set DB_STATEMENT_TIMEOUT_MS in .env for production.
    _connect_args["options"] = f"-c statement_timeout={settings.DB_STATEMENT_TIMEOUT_MS}"

# Production-ready connection pool — all values from settings so they can be
# tuned via environment variables without a code change.
# Sizing: pool_size + max_overflow = max connections per worker process.
# With 4 uvicorn workers: 4 × (20 + 30) = 200 total → keep under postgresql
# max_connections (default 100, typically raised to 200-500 in production).
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,       # persistent connections per worker
    max_overflow=settings.DB_MAX_OVERFLOW, # burst connections (released when idle)
    pool_pre_ping=True,                    # verify connections before use
    pool_recycle=settings.DB_POOL_RECYCLE, # recycle connections after N seconds
    echo_pool=False,                       # set True only for pool debugging
    connect_args=_connect_args,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# ── Slow-query monitoring ──────────────────────────────────────────────────────
# Threshold is read from settings (SLOW_QUERY_THRESHOLD_MS, default 200 ms) so
# it can be raised in .env without a code change (e.g. for reporting workloads).
_sq_logger = logging.getLogger("app.slow_query")


@sa_event.listens_for(engine, "before_cursor_execute")
def _sq_before_execute(conn, cursor, statement, params, context, executemany):
    conn.info.setdefault("_sq_start", []).append(time.perf_counter())


@sa_event.listens_for(engine, "after_cursor_execute")
def _sq_after_execute(conn, cursor, statement, params, context, executemany):
    elapsed_ms = (time.perf_counter() - conn.info["_sq_start"].pop()) * 1000
    if elapsed_ms >= settings.SLOW_QUERY_THRESHOLD_MS:
        # Deferred imports to avoid circular dependency at module load time
        from app.core.metrics import metrics
        from app.core.structured_log import log_warn
        from app.core.request_context import get_request_id
        metrics.increment("slow_queries_total")
        rid = get_request_id()
        extra = {"request_id": rid} if rid else {}
        log_warn(
            _sq_logger,
            "slow_query",
            duration_ms=round(elapsed_ms, 1),
            statement=statement[:200],
            **extra,
        )


# ── end slow-query monitoring ──────────────────────────────────────────────────


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def wait_for_db(
    max_retries: int | None = None,
    delay_seconds: float | None = None,
) -> None:
    """
    Verify database connectivity with exponential backoff.

    Intended for use during application startup so the process does not
    begin accepting traffic before the database is reachable.  Raises
    ``RuntimeError`` when all retries are exhausted.

    Parameters
    ----------
    max_retries:
        Number of connection attempts before aborting.
        Defaults to ``settings.DB_STARTUP_RETRIES`` (5).
    delay_seconds:
        Initial backoff between attempts (multiplied by attempt number).
        Defaults to ``settings.DB_STARTUP_RETRY_DELAY`` (2.0 s).
    """
    _retries = max_retries if max_retries is not None else settings.DB_STARTUP_RETRIES
    _delay = delay_seconds if delay_seconds is not None else settings.DB_STARTUP_RETRY_DELAY
    _logger = logging.getLogger("app.database")

    for attempt in range(1, _retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            _logger.info("Database connection verified (attempt %d/%d)", attempt, _retries)
            return
        except Exception as exc:
            if attempt == _retries:
                raise RuntimeError(
                    f"Database unavailable after {_retries} attempts. "
                    f"Last error: {exc}"
                ) from exc
            wait = _delay * attempt
            _logger.warning(
                "Database connection attempt %d/%d failed, retrying in %.1fs: %s",
                attempt, _retries, wait, exc,
            )
            time.sleep(wait)


def create_database():
    """Create all database tables"""
    # Import all models to ensure they are registered with Base
    Base.metadata.create_all(bind=engine)
