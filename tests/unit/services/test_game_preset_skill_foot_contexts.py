"""
Game Preset admin web route — skill_foot_contexts persistence tests (B2).

FC-SKILL-04  Create preset with per-skill overrides → JSONB stores skill_foot_contexts dict
FC-SKILL-05  Edit preset → all overrides cleared → skill_foot_contexts key absent from JSONB

Edge cases covered implicitly:
  - skill deselected → override not written (sk in skills guard)
  - invalid override value → not written (_VALID_FOOT_CONTEXTS guard)
  - all overrides empty → key omitted entirely (not empty dict)

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
        email=f"sfc-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="SFC Admin",
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


def _make_preset(
    test_db: Session,
    *,
    code: str,
    name: str,
    skill_foot_contexts: dict | None = None,
) -> GamePreset:
    """Create a GamePreset with optional initial skill_foot_contexts in skill_config."""
    sc: dict = {
        "skills_tested": ["crossing", "finishing", "passing"],
        "skill_weights": {"crossing": 0.4, "finishing": 0.4, "passing": 0.2},
        "skill_impact_on_matches": True,
    }
    if skill_foot_contexts:
        sc["skill_foot_contexts"] = skill_foot_contexts

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


def _sc(test_db: Session, preset_id: int) -> dict:
    """Re-read skill_config from DB (expire cache first)."""
    test_db.expire_all()
    gp = test_db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    return (gp.game_config or {}).get("skill_config", {})


# ── FC-SKILL-04 ───────────────────────────────────────────────────────────────

class TestFcSkill04CreateStoresSkillFootContexts:
    """FC-SKILL-04: create with per-skill overrides → JSONB stores skill_foot_contexts."""

    def test_fc_skill_04_create_stores_overrides(self, admin_client, test_db):
        code = f"mixed_{uuid.uuid4().hex[:6]}"
        resp = admin_client.post(
            "/admin/game-presets",
            data={
                "name": f"Mixed Preset {uuid.uuid4().hex[:4]}",
                "code": code,
                "description": "",
                "category": "FOOTBALL",
                "difficulty": "",
                "min_players": "2",
                "skill_cb_crossing": "crossing",
                "skill_w_crossing": "60",
                "skill_cb_finishing": "finishing",
                "skill_w_finishing": "40",
                "skill_fc_crossing": "right",
                "skill_fc_finishing": "left",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}: {resp.text}"

        test_db.expire_all()
        gp = test_db.query(GamePreset).filter(GamePreset.code == code).first()
        assert gp is not None, f"Preset code={code!r} not found after create"
        sfc = _sc(test_db, gp.id).get("skill_foot_contexts", {})
        assert sfc == {"crossing": "right", "finishing": "left"}, (
            f"Expected per-skill overrides, got {sfc!r}"
        )

    def test_fc_skill_04_deselected_skill_override_not_stored(self, admin_client, test_db):
        # passing is NOT in skill_cb_* but skill_fc_passing is sent — must be ignored.
        code = f"desel_{uuid.uuid4().hex[:6]}"
        resp = admin_client.post(
            "/admin/game-presets",
            data={
                "name": f"Deselect Test {uuid.uuid4().hex[:4]}",
                "code": code,
                "description": "",
                "category": "FOOTBALL",
                "difficulty": "",
                "min_players": "2",
                "skill_cb_crossing": "crossing",
                "skill_w_crossing": "100",
                "skill_fc_crossing": "right",
                "skill_fc_passing": "left",   # passing not selected → must not be stored
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        test_db.expire_all()
        gp = test_db.query(GamePreset).filter(GamePreset.code == code).first()
        sfc = _sc(test_db, gp.id).get("skill_foot_contexts", {})
        assert "passing" not in sfc, f"Deselected skill override must not be stored: {sfc!r}"
        assert sfc.get("crossing") == "right"

    def test_fc_skill_04_invalid_override_value_not_stored(self, admin_client, test_db):
        code = f"invalid_{uuid.uuid4().hex[:6]}"
        resp = admin_client.post(
            "/admin/game-presets",
            data={
                "name": f"Invalid Override {uuid.uuid4().hex[:4]}",
                "code": code,
                "description": "",
                "category": "FOOTBALL",
                "difficulty": "",
                "min_players": "2",
                "skill_cb_crossing": "crossing",
                "skill_w_crossing": "100",
                "skill_fc_crossing": "BOTH_FEET",   # invalid → must not be stored
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        test_db.expire_all()
        gp = test_db.query(GamePreset).filter(GamePreset.code == code).first()
        sc = _sc(test_db, gp.id)
        assert "skill_foot_contexts" not in sc, (
            f"Invalid override value must not produce skill_foot_contexts key: {sc!r}"
        )


# ── FC-SKILL-05 ───────────────────────────────────────────────────────────────

class TestFcSkill05EditEmptyOverridesRemovesKey:
    """FC-SKILL-05: edit with all overrides cleared → skill_foot_contexts absent from JSONB."""

    def test_fc_skill_05_clear_all_overrides_removes_key(self, admin_client, test_db):
        preset = _make_preset(
            test_db,
            code=f"sfc_clear_{uuid.uuid4().hex[:6]}",
            name="SFC Clear Test",
            skill_foot_contexts={"crossing": "right", "finishing": "left"},
        )
        # Precondition: key exists
        assert "skill_foot_contexts" in _sc(test_db, preset.id)

        resp = admin_client.post(
            f"/admin/game-presets/{preset.id}/edit",
            data={
                "name": "SFC Clear Test",
                "description": "",
                "category": "FOOTBALL",
                "difficulty": "",
                "min_players": "2",
                "skill_cb_crossing": "crossing",
                "skill_w_crossing": "60",
                "skill_cb_finishing": "finishing",
                "skill_w_finishing": "40",
                "skill_fc_crossing": "",    # cleared → empty string
                "skill_fc_finishing": "",   # cleared → empty string
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        sc = _sc(test_db, preset.id)
        assert "skill_foot_contexts" not in sc, (
            "skill_foot_contexts key must be absent when all overrides are cleared"
        )

    def test_fc_skill_05_partial_override_keeps_only_set_skills(self, admin_client, test_db):
        preset = _make_preset(
            test_db,
            code=f"sfc_partial_{uuid.uuid4().hex[:6]}",
            name="SFC Partial Test",
            skill_foot_contexts={"crossing": "right", "finishing": "left"},
        )
        resp = admin_client.post(
            f"/admin/game-presets/{preset.id}/edit",
            data={
                "name": "SFC Partial Test",
                "description": "",
                "category": "FOOTBALL",
                "difficulty": "",
                "min_players": "2",
                "skill_cb_crossing": "crossing",
                "skill_w_crossing": "60",
                "skill_cb_finishing": "finishing",
                "skill_w_finishing": "40",
                "skill_fc_crossing": "left",  # changed
                "skill_fc_finishing": "",     # cleared
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        sfc = _sc(test_db, preset.id).get("skill_foot_contexts", {})
        assert sfc == {"crossing": "left"}, (
            f"Only non-empty overrides must persist, got {sfc!r}"
        )

    def test_fc_skill_05_orphan_override_cleaned_on_skill_deselect(self, admin_client, test_db):
        # crossing had an override; now it gets deselected from skills → override must vanish.
        preset = _make_preset(
            test_db,
            code=f"sfc_orphan_{uuid.uuid4().hex[:6]}",
            name="SFC Orphan Test",
            skill_foot_contexts={"crossing": "right"},
        )
        resp = admin_client.post(
            f"/admin/game-presets/{preset.id}/edit",
            data={
                "name": "SFC Orphan Test",
                "description": "",
                "category": "FOOTBALL",
                "difficulty": "",
                "min_players": "2",
                "skill_cb_finishing": "finishing",  # crossing deselected
                "skill_w_finishing": "100",
                "skill_fc_crossing": "right",       # still sent but not in skills → ignored
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        sc = _sc(test_db, preset.id)
        assert "skill_foot_contexts" not in sc, (
            "Orphaned override for deselected skill must not persist"
        )
