"""
Session Type Config — Phase 1 Stabilisation Tests

STC-01  TournamentConfiguration.session_type_config defaults to 'on_site' (Python + DB default)
STC-02  _resolve_session_type() returns 'on_site' when config is absent/null
STC-03  PATCH /api/v1/tournaments/{id} with session_type_config after sessions_generated=True → 400
STC-04  _resolve_session_type() honours virtual / hybrid config values

All tests use SAVEPOINT-isolated DB — no side effects across tests.
"""

import uuid
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import get_current_admin_user_hybrid
from app.models.semester import Semester, SemesterStatus
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_type import TournamentType
from app.services.tournament.session_generation.formats.base_format_generator import BaseFormatGenerator

_PFX = "stc"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ─────────────────────────────────────────────────────────────────────────────
# Minimal tournament factory
# ─────────────────────────────────────────────────────────────────────────────

def _tournament(db: Session, sessions_generated: bool = False, session_type_config: str = None) -> Semester:
    """Minimal tournament (Semester) with attached TournamentConfiguration."""
    sem = Semester(
        code=f"{_PFX}-{_uid()}",
        name=f"STC Test Tournament {_uid()}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.DRAFT,
        semester_category="TOURNAMENT",
    )
    db.add(sem)
    db.flush()

    cfg_kwargs = dict(
        semester_id=sem.id,
        participant_type="INDIVIDUAL",
        sessions_generated=sessions_generated,
    )
    if session_type_config is not None:
        cfg_kwargs["session_type_config"] = session_type_config

    cfg = TournamentConfiguration(**cfg_kwargs)
    db.add(cfg)
    db.flush()
    return sem


@contextmanager
def _admin_client(db: Session, admin_user):
    """TestClient sharing test SAVEPOINT session, injecting admin via dependency override."""
    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin_user
    try:
        with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}, raise_server_exceptions=True) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Minimal BaseFormatGenerator stub for unit-testing helpers
# ─────────────────────────────────────────────────────────────────────────────

class _StubGenerator(BaseFormatGenerator):
    """Concrete subclass so we can instantiate BaseFormatGenerator (it's abstract)."""
    def generate(self, *args, **kwargs):
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_STC_01_default_session_type_config(test_db: Session):
    """TournamentConfiguration without explicit session_type_config → defaults to 'on_site'."""
    tournament = _tournament(test_db)  # no session_type_config kwarg
    test_db.refresh(tournament)
    cfg = tournament.tournament_config_obj

    # Python-level default
    assert cfg.session_type_config == "on_site", (
        f"Expected 'on_site' but got {cfg.session_type_config!r}"
    )


def test_STC_02_resolve_session_type_returns_on_site_by_default(test_db: Session):
    """_resolve_session_type() returns 'on_site' when session_type_config is None or 'on_site'."""
    gen = _StubGenerator(db=test_db)

    # tournament_config_obj not loaded (None) → should return 'on_site' safely
    class _NoConfig:
        tournament_config_obj = None

    assert gen._resolve_session_type(_NoConfig()) == "on_site"

    # Explicit 'on_site'
    tournament = _tournament(test_db, session_type_config="on_site")
    test_db.refresh(tournament)
    assert gen._resolve_session_type(tournament) == "on_site"


def test_STC_03_guard_rejects_session_type_change_after_sessions_generated(
    test_db: Session, admin_user
):
    """PATCH tournament.session_type_config after sessions_generated=True → HTTP 400."""
    tournament = _tournament(test_db, sessions_generated=True)
    test_db.commit()

    with _admin_client(test_db, admin_user) as client:
        resp = client.patch(
            f"/api/v1/tournaments/{tournament.id}",
            json={"session_type_config": "virtual"},
        )

    assert resp.status_code == 400, (
        f"Expected 400 when changing session_type_config after sessions generated, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    # Custom exception handler returns {"error": {"message": ...}};
    # fall back to standard FastAPI {"detail": ...} format.
    error_msg = body.get("error", {}).get("message", "") or body.get("detail", "")
    assert "session_type_config" in error_msg or "sessions" in error_msg.lower(), (
        f"Expected error mentioning session_type_config/sessions, got: {error_msg!r}"
    )


def test_STC_04_resolve_session_type_honours_config(test_db: Session):
    """_resolve_session_type() returns the configured value for virtual and hybrid."""
    gen = _StubGenerator(db=test_db)

    for expected in ("virtual", "hybrid", "on_site"):
        tournament = _tournament(test_db, session_type_config=expected)
        test_db.refresh(tournament)
        result = gen._resolve_session_type(tournament)
        assert result == expected, (
            f"Expected _resolve_session_type to return {expected!r}, got {result!r}"
        )

    # _resolve_base_xp smoke check
    assert gen._resolve_base_xp("on_site") == 75
    assert gen._resolve_base_xp("virtual") == 50
    assert gen._resolve_base_xp("hybrid") == 100
    assert gen._resolve_base_xp("unknown") == 75  # fallback
