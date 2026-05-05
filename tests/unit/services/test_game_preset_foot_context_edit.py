"""
Game Preset admin web route — foot_context edit/create tests.

FC-EDIT-01  Edit lat_passing_right preset → Save → foot_context remains 'right'
FC-EDIT-02  Edit lat_passing_left  preset → Save → foot_context remains 'left'
FC-EDIT-03  Edit legacy preset (no explicit foot_context) → Save → 'neutral' written
FC-EDIT-04  Create new preset with foot_context='right' → DB stores 'right'
FC-EDIT-05  POST edit with invalid foot_context value → key absent → model property 'neutral'

Route uses form_data.get() pattern (no OpenAPI drift):
  valid value   → written to skill_config
  invalid/empty → key omitted → GamePreset.foot_context property returns 'neutral' default

Auth:  get_current_user_web dependency overridden → admin injected
CSRF:  Authorization: Bearer header bypasses CSRFProtectionMiddleware
DB:    SAVEPOINT-isolated (test_db fixture from unit/conftest.py)
"""

import uuid
import pytest

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.models.game_preset import GamePreset
from app.core.security import get_password_hash


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def admin_user(test_db: Session) -> User:
    u = User(
        email=f"fcedit-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="FC Edit Admin",
        password_hash=get_password_hash("Admin123!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    test_db.add(u)
    test_db.commit()
    test_db.refresh(u)
    return u


@pytest.fixture(scope="function")
def admin_client(test_db: Session, admin_user: User) -> TestClient:
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_web] = lambda: admin_user

    with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}) as c:
        yield c

    app.dependency_overrides.clear()


def _make_preset(test_db: Session, *, code: str, name: str, foot_context: str | None) -> GamePreset:
    """Create a GamePreset with or without explicit foot_context in skill_config."""
    sc: dict = {
        "skills_tested": ["passing"],
        "skill_weights": {"passing": 1.0},
        "skill_impact_on_matches": True,
    }
    if foot_context is not None:
        sc["foot_context"] = foot_context

    gp = GamePreset(
        code=code,
        name=name,
        is_active=True,
        game_config={
            "version": "1.0",
            "format_config": {},
            "skill_config": sc,
            "simulation_config": {},
            "metadata": {"game_category": "FOOTBALL", "difficulty_level": None, "min_players": 2},
        },
    )
    test_db.add(gp)
    test_db.commit()
    test_db.refresh(gp)
    return gp


def _edit_form(*, name: str, foot_context: str, skill: str = "passing", weight: int = 100) -> dict:
    """Build the minimal POST form data for the edit route."""
    return {
        "name": name,
        "description": "",
        "category": "FOOTBALL",
        "difficulty": "",
        "min_players": "2",
        "foot_context": foot_context,
        f"skill_cb_{skill}": skill,
        f"skill_w_{skill}": str(weight),
    }


def _sc(test_db: Session, preset_id: int) -> dict:
    """Re-read skill_config from DB (expire cache first)."""
    test_db.expire_all()
    gp = test_db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    return (gp.game_config or {}).get("skill_config", {})


def _gp(test_db: Session, preset_id: int) -> GamePreset:
    test_db.expire_all()
    return test_db.query(GamePreset).filter(GamePreset.id == preset_id).first()


# ── FC-EDIT-01 ────────────────────────────────────────────────────────────────

class TestFcEdit01RightPreservedOnSave:
    """FC-EDIT-01: Edit lat_passing_right → Save → foot_context stays 'right'."""

    def test_fc_edit_01(self, admin_client, test_db):
        preset = _make_preset(
            test_db,
            code=f"lat_passing_right_{uuid.uuid4().hex[:6]}",
            name="Lat Passing Right",
            foot_context="right",
        )
        resp = admin_client.post(
            f"/admin/game-presets/{preset.id}/edit",
            data=_edit_form(name="Lat Passing Right", foot_context="right"),
            follow_redirects=False,
        )
        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}: {resp.text}"
        assert _sc(test_db, preset.id).get("foot_context") == "right"


# ── FC-EDIT-02 ────────────────────────────────────────────────────────────────

class TestFcEdit02LeftPreservedOnSave:
    """FC-EDIT-02: Edit lat_passing_left → Save → foot_context stays 'left'."""

    def test_fc_edit_02(self, admin_client, test_db):
        preset = _make_preset(
            test_db,
            code=f"lat_passing_left_{uuid.uuid4().hex[:6]}",
            name="Lat Passing Left",
            foot_context="left",
        )
        resp = admin_client.post(
            f"/admin/game-presets/{preset.id}/edit",
            data=_edit_form(name="Lat Passing Left", foot_context="left"),
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert _sc(test_db, preset.id).get("foot_context") == "left"


# ── FC-EDIT-03 ────────────────────────────────────────────────────────────────

class TestFcEdit03LegacyGetsNeutralOnSave:
    """FC-EDIT-03: Edit legacy preset (no foot_context key) → Save with neutral → 'neutral' written."""

    def test_fc_edit_03(self, admin_client, test_db):
        preset = _make_preset(
            test_db,
            code=f"legacy_preset_{uuid.uuid4().hex[:6]}",
            name="Legacy Preset",
            foot_context=None,
        )
        assert "foot_context" not in _sc(test_db, preset.id)

        resp = admin_client.post(
            f"/admin/game-presets/{preset.id}/edit",
            data=_edit_form(name="Legacy Preset", foot_context="neutral"),
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert _sc(test_db, preset.id).get("foot_context") == "neutral"


# ── FC-EDIT-04 ────────────────────────────────────────────────────────────────

class TestFcEdit04CreateWithRightContext:
    """FC-EDIT-04: Create new preset with foot_context='right' → DB stores 'right'."""

    def test_fc_edit_04(self, admin_client, test_db):
        code = f"new_right_{uuid.uuid4().hex[:6]}"
        name = f"New Right Preset {uuid.uuid4().hex[:4]}"
        resp = admin_client.post(
            "/admin/game-presets",
            data={
                "name": name,
                "code": code,
                "description": "",
                "category": "FOOTBALL",
                "difficulty": "",
                "min_players": "2",
                "foot_context": "right",
                "skill_cb_passing": "passing",
                "skill_w_passing": "100",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        gp = test_db.query(GamePreset).filter(GamePreset.code == code).first()
        assert gp is not None, f"Preset with code={code!r} not found after create"
        sc = (gp.game_config or {}).get("skill_config", {})
        assert sc.get("foot_context") == "right", (
            f"Expected foot_context='right', got {sc.get('foot_context')!r}"
        )


# ── FC-EDIT-05 ────────────────────────────────────────────────────────────────

class TestFcEdit05InvalidValueFallsBackToNeutral:
    """FC-EDIT-05: POST edit with invalid foot_context → key absent → model property returns 'neutral'.

    Route writes foot_context only for valid values (right/left/neutral).
    Invalid submission → key omitted from skill_config → GamePreset.foot_context
    property defaults to 'neutral'.  No invalid string ever reaches the DB.
    """

    def test_fc_edit_05(self, admin_client, test_db):
        preset = _make_preset(
            test_db,
            code=f"invalid_fc_{uuid.uuid4().hex[:6]}",
            name="Invalid FC Preset",
            foot_context="right",
        )
        resp = admin_client.post(
            f"/admin/game-presets/{preset.id}/edit",
            data=_edit_form(name="Invalid FC Preset", foot_context="BOTH_FEET"),
            follow_redirects=False,
        )
        assert resp.status_code == 303
        gp = _gp(test_db, preset.id)
        sc = (gp.game_config or {}).get("skill_config", {})
        assert "foot_context" not in sc or sc["foot_context"] in {"right", "left", "neutral"}, (
            "Invalid foot_context must not be stored in DB"
        )
        assert gp.foot_context == "neutral", (
            "GamePreset.foot_context property must return 'neutral' default when key is absent"
        )
