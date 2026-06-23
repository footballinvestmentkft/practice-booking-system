"""Minimal staging backend for GoPro + Session Lobby smoke tests.

Exposes:
  Auth:        POST login, POST refresh, GET /auth/me, GET /users/me
  Health:      GET /health
  Multicamera: POST sessions, GET sessions/{uuid}, POST join, PATCH status,
               POST devices, POST heartbeat

No Celery, Redis, APScheduler, WebSocket, ML, static files, or media.
No CORS middleware — the only client is the native iOS app.
"""

from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.api_v1.endpoints.auth import (
    login,
    refresh_token,
    read_users_me,
)
from app.api.api_v1.endpoints.multicamera.sessions import router as multicamera_router
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


@app.exception_handler(Exception)
async def _debug_exception_handler(request, exc):
    import traceback
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return JSONResponse(
        status_code=500,
        content={
            "error": type(exc).__name__,
            "detail": str(exc),
            "traceback": tb,
        },
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
