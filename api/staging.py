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


@app.post("/api/v1/debug/run-migration-2026-06-24", tags=["debug"])
def run_capture_cycles_migration():
    """One-shot: apply 2026_06_24_1000 capture_cycles migration via raw SQL."""
    import traceback
    from app.database import SessionLocal
    steps = []
    try:
        db = SessionLocal()

        # Check if already applied
        col_exists = db.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'capture_cycles')"
        )).scalar()
        if col_exists:
            return {"status": "already_applied", "steps": ["capture_cycles table exists"]}

        # 1. Expand session status constraint
        db.execute(text("ALTER TABLE multicamera_sessions DROP CONSTRAINT IF EXISTS ck_mcs_status"))
        db.execute(text(
            "ALTER TABLE multicamera_sessions ADD CONSTRAINT ck_mcs_status "
            "CHECK (status IN ('lobby','devices_ready','recording_pending','recording',"
            "'stopped','finalizing','completed','cancelled','active'))"
        ))
        steps.append("ck_mcs_status expanded")

        # 2. capture_cycles table
        db.execute(text("""
            CREATE TABLE capture_cycles (
                id SERIAL PRIMARY KEY,
                session_id INTEGER NOT NULL REFERENCES multicamera_sessions(id) ON DELETE CASCADE,
                cycle_index INTEGER NOT NULL CHECK (cycle_index >= 0),
                status VARCHAR(20) NOT NULL DEFAULT 'preparing'
                    CHECK (status IN ('preparing','recording_pending','recording','stopping','completed','failed','aborted')),
                result VARCHAR(20) CHECK (result IS NULL OR result IN ('success','partial','failed')),
                scheduled_start_at TIMESTAMPTZ,
                recording_started_at TIMESTAMPTZ,
                stop_requested_at TIMESTAMPTZ,
                recording_stopped_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                failure_reason TEXT,
                created_by_participant_id INTEGER NOT NULL REFERENCES session_participants(id),
                idempotency_key VARCHAR(64) NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_cc_session_cycle UNIQUE (session_id, cycle_index),
                CONSTRAINT uq_cc_session_idempotency UNIQUE (session_id, idempotency_key)
            )
        """))
        db.execute(text("CREATE INDEX ix_capture_cycles_session_id ON capture_cycles (session_id)"))
        steps.append("capture_cycles created")

        # 3. capture_cycle_devices table
        db.execute(text("""
            CREATE TABLE capture_cycle_devices (
                id SERIAL PRIMARY KEY,
                capture_cycle_id INTEGER NOT NULL REFERENCES capture_cycles(id) ON DELETE CASCADE,
                session_device_id INTEGER NOT NULL REFERENCES session_devices(id),
                required BOOLEAN NOT NULL DEFAULT true,
                recording_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (recording_status IN ('pending','confirmed_start','confirmed_stop','failed')),
                started_at TIMESTAMPTZ,
                stopped_at TIMESTAMPTZ,
                failure_reason TEXT,
                revision INTEGER NOT NULL DEFAULT 1,
                CONSTRAINT uq_ccd_cycle_device UNIQUE (capture_cycle_id, session_device_id)
            )
        """))
        db.execute(text("CREATE INDEX ix_capture_cycle_devices_capture_cycle_id ON capture_cycle_devices (capture_cycle_id)"))
        steps.append("capture_cycle_devices created")

        # 4. Add capture_cycle_id to capture_streams
        db.execute(text(
            "ALTER TABLE capture_streams ADD COLUMN capture_cycle_id INTEGER REFERENCES capture_cycles(id)"
        ))
        db.execute(text(
            "CREATE UNIQUE INDEX uix_cs_cycle_device_type ON capture_streams "
            "(capture_cycle_id, session_device_id, stream_type) WHERE capture_cycle_id IS NOT NULL"
        ))
        steps.append("capture_streams.capture_cycle_id added")

        # 5. Update alembic version
        db.execute(text("UPDATE alembic_version SET version_num = '2026_06_24_1000'"))
        steps.append("alembic_version updated")

        db.commit()
        db.close()
        return {"status": "applied", "steps": steps}
    except Exception as e:
        return {"status": "error", "steps": steps, "error": str(e), "traceback": traceback.format_exc()}


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
