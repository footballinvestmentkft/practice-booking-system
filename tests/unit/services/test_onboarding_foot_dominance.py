"""
Onboarding foot_dominance field validation tests.

Tests: OB-01 through OB-06
Covers both POST handlers:
  - /specialization/lfa-player/onboarding-web  (cookie auth, browser)
  - /specialization/lfa-player/onboarding-submit (JWT auth, API)

Uses the same MagicMock-DB pattern as test_onboarding_web.py.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.api.web_routes.onboarding import (
    lfa_player_onboarding_web_submit,
    lfa_player_onboarding_submit,
)
from app.models.user import UserRole

_BASE = "app.api.web_routes.onboarding"
# complete_lfa_player_onboarding and flag_modified are imported inside the
# handler function body, so they must be patched at their source modules.
_PATCH_COMPLETE = "app.services.onboarding_service.complete_lfa_player_onboarding"
_PATCH_FLAG     = "sqlalchemy.orm.attributes.flag_modified"


# ---------------------------------------------------------------------------
# Helpers (mirror test_onboarding_web.py)
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _user(uid=42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.email = "foot@test.com"
    return u


def _mock_db(license_mock=None):
    """Return a minimal DB mock that returns `license_mock` for .first()."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (
        license_mock if license_mock is not None else MagicMock()
    )
    return db


def _valid_skills():
    from app.services.skill_progression._config import get_all_skill_keys
    return {k: 50 for k in get_all_skill_keys()}


# ---------------------------------------------------------------------------
# Web handler (/onboarding-web)
# ---------------------------------------------------------------------------

class TestOnboardingWebFootDominance:

    def _req(self, body: dict):
        r = MagicMock()
        r.json = AsyncMock(return_value=body)
        return r

    def _valid_body(self, foot_dominance=50):
        body = {
            "position":      "MIDFIELDER",
            "goals":         "improve_skills",
            "motivation":    "",
            "skills":        _valid_skills(),
        }
        if foot_dominance is not None:
            body["foot_dominance"] = foot_dominance
        return body

    def test_ob01_foot_dominance_70_saves_right70_left30(self):
        """OB-01: foot_dominance=70 → right_foot_score=70, left_foot_score=30."""
        license_mock = MagicMock()
        db = _mock_db(license_mock)
        body = self._valid_body(foot_dominance=70)

        with patch(_PATCH_COMPLETE), \
             patch(_PATCH_FLAG):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._req(body), db=db, user=_user()
            ))

        assert result.get("success") is True
        assert license_mock.right_foot_score == 70.0
        assert license_mock.left_foot_score  == 30.0

    def test_ob02_foot_dominance_boundary_0(self):
        """OB-02: foot_dominance=0 → right=0.0, left=100.0."""
        license_mock = MagicMock()
        db = _mock_db(license_mock)
        body = self._valid_body(foot_dominance=0)

        with patch(_PATCH_COMPLETE), \
             patch(_PATCH_FLAG):
            _run(lfa_player_onboarding_web_submit(
                request=self._req(body), db=db, user=_user()
            ))

        assert license_mock.right_foot_score == 0.0
        assert license_mock.left_foot_score  == 100.0

    def test_ob03_foot_dominance_boundary_100(self):
        """OB-03: foot_dominance=100 → right=100.0, left=0.0."""
        license_mock = MagicMock()
        db = _mock_db(license_mock)
        body = self._valid_body(foot_dominance=100)

        with patch(_PATCH_COMPLETE), \
             patch(_PATCH_FLAG):
            _run(lfa_player_onboarding_web_submit(
                request=self._req(body), db=db, user=_user()
            ))

        assert license_mock.right_foot_score == 100.0
        assert license_mock.left_foot_score  == 0.0

    def test_ob04_missing_foot_dominance_defaults_to_50(self):
        """OB-04: foot_dominance absent → defaults to 50 → right=50.0, left=50.0."""
        license_mock = MagicMock()
        db = _mock_db(license_mock)
        body = self._valid_body(foot_dominance=None)  # key omitted
        body.pop("foot_dominance", None)

        with patch(_PATCH_COMPLETE), \
             patch(_PATCH_FLAG):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._req(body), db=db, user=_user()
            ))

        assert result.get("success") is True
        assert license_mock.right_foot_score == 50.0
        assert license_mock.left_foot_score  == 50.0

    def test_ob05_foot_dominance_out_of_range_returns_400(self):
        """OB-05: foot_dominance=101 → 400 JSON error response."""
        from starlette.responses import JSONResponse
        db = _mock_db()
        body = self._valid_body(foot_dominance=101)

        result = _run(lfa_player_onboarding_web_submit(
            request=self._req(body), db=db, user=_user()
        ))

        assert isinstance(result, JSONResponse)
        assert result.status_code == 400

    def test_ob06_foot_dominance_negative_returns_400(self):
        """OB-06: foot_dominance=-1 → 400 JSON error response."""
        from starlette.responses import JSONResponse
        db = _mock_db()
        body = self._valid_body(foot_dominance=-1)

        result = _run(lfa_player_onboarding_web_submit(
            request=self._req(body), db=db, user=_user()
        ))

        assert isinstance(result, JSONResponse)
        assert result.status_code == 400


# ---------------------------------------------------------------------------
# JWT handler (/onboarding-submit) — same validation, raises ValueError
# ---------------------------------------------------------------------------

class TestOnboardingSubmitFootDominance:

    def _req(self, body: dict):
        r = MagicMock()
        r.json = AsyncMock(return_value=body)
        return r

    def _valid_body(self, foot_dominance=50):
        body = {
            "position":   "MIDFIELDER",
            "goals":      "improve_skills",
            "motivation": "",
            "skills":     _valid_skills(),
        }
        if foot_dominance is not None:
            body["foot_dominance"] = foot_dominance
        return body

    def test_jwt_foot_dominance_70_saves_correctly(self):
        """JWT handler: foot_dominance=70 → right=70.0, left=30.0."""
        from fastapi import HTTPException
        license_mock = MagicMock()
        db = _mock_db(license_mock)
        body = self._valid_body(foot_dominance=70)

        with patch("app.skills_config.get_all_skill_keys",
                   return_value=list(body["skills"].keys())), \
             patch(_PATCH_COMPLETE), \
             patch(_PATCH_FLAG):
            result = _run(lfa_player_onboarding_submit(
                request=self._req(body), db=db, user=_user()
            ))

        assert result.get("success") is True
        assert license_mock.right_foot_score == 70.0
        assert license_mock.left_foot_score  == 30.0

    def test_jwt_foot_dominance_out_of_range_raises_500(self):
        """JWT handler: foot_dominance=105 → wrapped in 500 HTTPException."""
        from fastapi import HTTPException
        db = _mock_db()
        body = self._valid_body(foot_dominance=105)

        with patch("app.skills_config.get_all_skill_keys",
                   return_value=list(body["skills"].keys())):
            with pytest.raises(HTTPException) as exc:
                _run(lfa_player_onboarding_submit(
                    request=self._req(body), db=db, user=_user()
                ))
        assert exc.value.status_code == 500
