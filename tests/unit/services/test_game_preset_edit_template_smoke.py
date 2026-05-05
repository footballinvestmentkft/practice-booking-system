"""
Game Preset edit template smoke test — B3.

FC-TMPL-01  GET edit page for preset with skill_foot_contexts override renders
            skill_fc_* select with the correct pre-filled value in the HTML.

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


@pytest.fixture(scope="function")
def admin_user(test_db: Session) -> User:
    u = User(
        email=f"tmpl-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Template Admin",
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


class TestFcTmpl01EditPageRendersSkillFcSelects:
    """FC-TMPL-01: edit page renders skill_fc_* selects with pre-filled overrides."""

    def test_fc_tmpl_01_select_rendered_and_prefilled(self, admin_client, test_db):
        gp = GamePreset(
            code=f"tmpl_test_{uuid.uuid4().hex[:6]}",
            name="Template Smoke Test",
            is_active=True,
            game_config={
                "version": "1.0",
                "format_config": {},
                "skill_config": {
                    "skills_tested": ["crossing", "finishing"],
                    "skill_weights": {"crossing": 0.6, "finishing": 0.4},
                    "skill_impact_on_matches": True,
                    "skill_foot_contexts": {"crossing": "right"},
                },
                "simulation_config": {},
                "metadata": {"game_category": "FOOTBALL", "difficulty_level": None, "min_players": 2},
            },
        )
        test_db.add(gp)
        test_db.commit()
        test_db.refresh(gp)

        resp = admin_client.get(f"/admin/game-presets/{gp.id}/edit")

        assert resp.status_code == 200
        assert 'name="skill_fc_crossing"' in resp.text, (
            "skill_fc_crossing select must be rendered for crossing skill"
        )
        assert 'name="skill_fc_finishing"' in resp.text, (
            "skill_fc_finishing select must be rendered for finishing skill"
        )
        # crossing has override="right" → option value="right" must appear selected
        html = resp.text
        crossing_block_start = html.find('name="skill_fc_crossing"')
        crossing_block_end = html.find('</select>', crossing_block_start)
        crossing_select_html = html[crossing_block_start:crossing_block_end]
        assert 'value="right"' in crossing_select_html, (
            "crossing foot context override 'right' must appear in select options"
        )

    def test_fc_tmpl_01_no_override_renders_default_option(self, admin_client, test_db):
        gp = GamePreset(
            code=f"tmpl_nofc_{uuid.uuid4().hex[:6]}",
            name="Template No Override",
            is_active=True,
            game_config={
                "version": "1.0",
                "format_config": {},
                "skill_config": {
                    "skills_tested": ["passing"],
                    "skill_weights": {"passing": 1.0},
                    "skill_impact_on_matches": True,
                },
                "simulation_config": {},
                "metadata": {"game_category": "FOOTBALL", "difficulty_level": None, "min_players": 2},
            },
        )
        test_db.add(gp)
        test_db.commit()
        test_db.refresh(gp)

        resp = admin_client.get(f"/admin/game-presets/{gp.id}/edit")

        assert resp.status_code == 200
        assert 'name="skill_fc_passing"' in resp.text, (
            "skill_fc_passing select must be rendered even without override"
        )
        # No override → default option (value="") must be present
        assert '— preset default —' in resp.text, (
            "Default option text must appear in rendered select"
        )
