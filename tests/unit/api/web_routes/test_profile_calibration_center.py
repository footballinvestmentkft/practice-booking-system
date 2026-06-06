"""
Calibration Center — P0 route sanity tests.

CC-01  GET /profile/calibration → 200 for authenticated user.
CC-02  GET /profile/calibration → 401/303 for unauthenticated user (auth guard).
CC-03  GET /profile/calibration response contains #ccChecklist DOM section.
CC-04  GET /profile/calibration response contains CalibCenter JS identifier.
CC-05  GET /profile/calibration response contains all 7 checklist DOM IDs.
CC-06  GET /profile/calibration response loads /static/js/calib-center.js.
CC-07  GET /profile page contains Camera & Tracking card with /profile/calibration link.
CC-08  GET /virtual-training/camera-test → 200 (regression — route still works).
CC-09  GET /profile/calibration response contains Stop / Skip-style affordance.
CC-10  GET /profile/calibration response contains face section placeholder.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.user import UserRole

_PROFILE_MODULE = "app.api.web_routes.profile"


# ── Shared helpers ────────────────────────────────────────────────────────────

def _mock_user() -> MagicMock:
    u = MagicMock()
    u.id = 42
    u.email = "tester@example.com"
    u.role = UserRole.STUDENT
    u.is_active = True
    u.onboarding_completed = True
    u.specialization = None
    u.date_of_birth = None
    u.credit_balance = 0
    u.credit_purchased = 0
    return u


def _mock_db() -> MagicMock:
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = None
    q.all.return_value = []
    db.query.return_value = q
    return db


def _make_profile_client(user: MagicMock | None = None) -> TestClient:
    from app.api.web_routes import profile as profile_module
    from app.dependencies import get_current_user_web
    from app.database import get_db

    _user = user or _mock_user()

    app = FastAPI()
    app.include_router(profile_module.router)
    app.dependency_overrides[get_current_user_web] = lambda: _user
    app.dependency_overrides[get_db] = lambda: _mock_db()
    return TestClient(app, raise_server_exceptions=False)


def _make_no_auth_client() -> TestClient:
    from app.api.web_routes import profile as profile_module
    app = FastAPI()
    app.include_router(profile_module.router)
    return TestClient(app, raise_server_exceptions=False)


def _make_vt_client() -> TestClient:
    """Minimal VT client for camera-test regression."""
    from app.api.web_routes import virtual_training as vt_module
    app = FastAPI()
    app.include_router(vt_module.router)
    return TestClient(app, raise_server_exceptions=False)


CC_URL  = "/profile/calibration"
PROF_URL = "/profile"
CAM_URL  = "/virtual-training/camera-test"


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCalibCenterRoute:

    def _get_html(self) -> str:
        client = _make_profile_client()
        r = client.get(CC_URL)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        return r.text

    def test_cc_01_authenticated_returns_200(self):
        """CC-01: GET /profile/calibration → 200 for authenticated student."""
        client = _make_profile_client()
        r = client.get(CC_URL)
        assert r.status_code == 200

    def test_cc_02_unauthenticated_returns_auth_guard(self):
        """CC-02: Unauthenticated request → 401 or 303 redirect (auth guard intact)."""
        client = _make_no_auth_client()
        r = client.get(CC_URL, follow_redirects=False)
        assert r.status_code in (401, 303), \
            f"Expected auth guard (401 or 303), got {r.status_code}"

    def test_cc_03_checklist_section_present(self):
        """CC-03: Response contains #ccChecklist DOM section."""
        html = self._get_html()
        assert 'id="ccChecklist"' in html

    def test_cc_04_calibcenter_js_identifier_present(self):
        """CC-04: Response contains CalibCenter JS identifier."""
        html = self._get_html()
        assert "CalibCenter" in html

    def test_cc_05_all_7_checklist_ids_present(self):
        """CC-05: All 7 checklist row DOM IDs are present."""
        html = self._get_html()
        for dom_id in ("ccSecure", "ccCamera", "ccStream",
                       "ccModel", "ccFrames", "ccCanvas", "ccHand"):
            assert f'id="{dom_id}"' in html, f"Missing DOM id: {dom_id}"

    def test_cc_06_calib_center_js_loaded(self):
        """CC-06: Template loads /static/js/calib-center.js."""
        html = self._get_html()
        assert "/static/js/calib-center.js" in html

    def test_cc_07_profile_page_has_calibration_card(self):
        """CC-07: GET /profile contains the Camera & Tracking card link."""
        client = _make_profile_client()
        with patch(f"{_PROFILE_MODULE}.templates") as mock_tpl:
            # Use the real template response (TestClient renders via Jinja2)
            pass
        # Re-use real client which hits the actual template
        r = client.get(PROF_URL)
        assert r.status_code == 200
        assert "/profile/calibration" in r.text

    def test_cc_08_camera_test_route_regression(self):
        """CC-08: /virtual-training/camera-test still returns 200 (regression guard)."""
        client = _make_vt_client()
        r = client.get(CAM_URL)
        assert r.status_code == 200
        assert "getUserMedia" in r.text

    def test_cc_09_stop_affordance_present(self):
        """CC-09: Template contains Stop button affordance."""
        html = self._get_html()
        assert "ccBtnStop" in html or "CalibCenter.stop" in html

    def test_cc_10_face_section_placeholder_present(self):
        """CC-10: Face tracking placeholder section is present (P1+ notice)."""
        html = self._get_html()
        assert "Face" in html
        assert "COMING NEXT" in html or "coming" in html.lower()
