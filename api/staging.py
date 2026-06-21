"""Minimal staging backend for GoPro connection smoke tests.

Exposes exactly 5 endpoints:
  POST /api/v1/auth/login
  POST /api/v1/auth/refresh
  GET  /api/v1/auth/me
  GET  /api/v1/users/me
  GET  /api/v1/health

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
from app.api.api_v1.endpoints.users.profile import (
    get_current_user_profile,
)
from app.schemas.auth import Token
from app.schemas.user import User as UserSchema

app = FastAPI(
    title="LFA Staging API",
    description="Minimal staging backend — login only, no workers or media.",
    version="0.1.0-staging",
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
users_router.add_api_route("/me", get_current_user_profile, methods=["GET"], response_model=UserSchema)
app.include_router(users_router)


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
