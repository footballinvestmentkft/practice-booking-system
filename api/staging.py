"""Minimal staging backend for GoPro + Session Lobby smoke tests.

Exposes:
  Auth:        POST login, POST refresh, GET /auth/me, GET /users/me
  Health:      GET /health
  System:      GET /system/time (ClockSync)
  Multicamera: sessions, devices, heartbeat, cycles, activate, finalize

No Celery, Redis, APScheduler, WebSocket, ML, static files, or media.
No CORS middleware — the only client is the native iOS app.
Auto-migrate: runs alembic upgrade head on cold start.
"""

import os
import sys
from pathlib import Path

from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

# ── Auto-migrate on cold start ──────────────────────────────────────────────
# Vercel serverless: each cold start must bring the DB schema up to date.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

def _run_migrations():
    try:
        from alembic.config import Config
        from alembic import command
        cfg = Config(os.path.join(_project_root, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(_project_root, "alembic"))
        command.upgrade(cfg, "head")
    except Exception as e:
        print(f"[staging] auto-migrate warning: {e}")

_run_migrations()

from app.api.api_v1.endpoints.auth import (
    login,
    refresh_token,
    read_users_me,
)
from app.api.api_v1.endpoints.multicamera.sessions import router as multicamera_router
from app.api.api_v1.endpoints.multicamera.cycles import router as cycles_router
from app.api.api_v1.endpoints.system_time import router as system_time_router
from app.schemas.auth import Token
from app.schemas.user import User as UserSchema

app = FastAPI(
    title="LFA Staging API",
    description="Minimal staging backend — auth + multicamera session lobby.",
    version="0.2.0-staging",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
auth_router.add_api_route("/login", login, methods=["POST"], response_model=Token)
auth_router.add_api_route("/refresh", refresh_token, methods=["POST"], response_model=Token)
auth_router.add_api_route("/me", read_users_me, methods=["GET"], response_model=UserSchema)
app.include_router(auth_router)

users_router = APIRouter(prefix="/api/v1/users", tags=["users"])
users_router.add_api_route("/me", read_users_me, methods=["GET"], response_model=UserSchema)
app.include_router(users_router)

app.include_router(multicamera_router, prefix="/api/v1/multicamera", tags=["multicamera"])
app.include_router(cycles_router, prefix="/api/v1/multicamera", tags=["multicamera", "capture-cycles"])
app.include_router(system_time_router, prefix="/api/v1/system", tags=["system"])


@app.get("/api/v1/health", tags=["health"])
def health():
    from app.database import SessionLocal
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return {"status": "ok", "database": "connected", "variant": "staging"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


@app.get("/api/v1/debug/db-schema", tags=["debug"])
def debug_db_schema():
    """Check staging DB schema for cycle tables + columns. No auth."""
    import traceback
    from app.database import SessionLocal
    result = {"migration_status": "unknown", "tables": {}, "alembic_head": None, "errors": []}
    try:
        db = SessionLocal()
        for tbl in ("capture_cycles", "capture_cycle_devices"):
            row = db.execute(text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :t)"
            ), {"t": tbl}).scalar()
            result["tables"][tbl] = "exists" if row else "MISSING"

        col = db.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'capture_streams' AND column_name = 'capture_cycle_id')"
        )).scalar()
        result["tables"]["capture_streams.capture_cycle_id"] = "exists" if col else "MISSING"

        try:
            head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            result["alembic_head"] = head
        except Exception:
            result["alembic_head"] = "no alembic_version table"

        db.close()
        result["migration_status"] = "ok"
    except Exception as e:
        result["errors"].append(traceback.format_exc())
    return result


@app.get("/api/v1/debug/test-session-create", tags=["debug"])
def debug_test_session_create():
    """Simulate session create flow and return any traceback. No auth."""
    import traceback
    from app.database import SessionLocal
    result = {"step": "init", "error": None, "traceback": None}
    try:
        db = SessionLocal()
        result["step"] = "db_connected"

        from app.services.multicamera.session_service import SessionService
        ss = SessionService(db)
        result["step"] = "service_created"

        # Find any user to act as creator
        from app.models.user import User
        user = db.query(User).filter(User.is_active.is_(True)).first()
        if not user:
            result["error"] = "No active user in staging DB"
            return result
        result["step"] = f"found_user_{user.id}"

        session = ss.create_session(user.id, max_participants=2, max_devices=4)
        result["step"] = "session_created"
        result["session_id"] = session.id
        result["session_uuid"] = str(session.session_uuid)

        ss.join_session(session.session_uuid, user.id, "instructor")
        result["step"] = "joined"

        full = ss.get_session(session.session_uuid)
        result["step"] = "get_session_ok"
        result["status"] = full.status
        result["participants"] = len(full.participants)
        result["devices_count"] = len(full.devices)

        # Cleanup — cancel the test session
        session.status = "cancelled"
        db.commit()
        result["step"] = "cleanup_done"
        db.close()
    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
    return result
