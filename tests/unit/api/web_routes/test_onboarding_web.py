"""
Unit tests for app/api/web_routes/onboarding.py

Covers unique routes (not duplicated in specialization.py):
  specialization_select_page — renders select template with user licenses
  specialization_select_submit — invalid spec renders error, insufficient credits → /dashboard,
                                  existing license → no credit deduction, lfa-player → lfa onboarding URL,
                                  other spec → motivation URL
  lfa_player_onboarding_page (onboarding.py version) — no license → /dashboard,
                                                        completed → /dashboard, valid → template
  lfa_player_onboarding_cancel (onboarding.py version) — no license → /dashboard, cancel → refund
  lfa_player_onboarding_submit — invalid position → 500, no license → 500, valid → success dict
  lfa_player_onboarding_web_submit (Phase B) — response fields: success/user_id/welcome_card URLs;
                                                validation: missing position/height/skills → 4xx
  OnboardingTemplatePhaseB — static template assertions: step-7, TOTAL_STEPS=7, no auto-redirect,
                              goTo(7) on success, btn-go-dashboard, btn-go-profile present
  onboarding_start — renders onboarding_new.html, age=None skips spec lookup
  onboarding_set_birthdate — invalid format → 400, too young → 400, valid → /onboarding/start

Note: onboarding.py missing imports (SpecializationType, CreditTransaction, TransactionType,
      get_available_specializations) were fixed in Sprint 54 P0. create=True removed.
"""
import asyncio
import pathlib
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app.api.web_routes.onboarding import (
    lfa_player_onboarding_cancel,
    lfa_player_onboarding_page,
    lfa_player_onboarding_submit,
    lfa_player_onboarding_web_submit,
    onboarding_set_birthdate,
    onboarding_start,
    specialization_select_page,
    specialization_select_submit,
)
from app.models.user import UserRole
from app.models.specialization import SpecializationType

_BASE = "app.api.web_routes.onboarding"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    return MagicMock()


def _user(uid=99, credit_balance=200, age=20):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.email = "student@test.com"
    u.credit_balance = credit_balance
    u.age = age
    u.onboarding_completed = False
    return u


def _mock_db(first_return=None, all_return=None, user_return=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = first_return
    db.query.return_value.filter.return_value.all.return_value = all_return or []
    db.query.return_value.filter.return_value.filter.return_value.filter.return_value.first.return_value = first_return
    # SELECT FOR UPDATE chain (user re-query in specialization_select_submit)
    _ur = user_return if user_return is not None else first_return
    db.query.return_value.with_for_update.return_value.filter.return_value.first.return_value = _ur
    return db


# ──────────────────────────────────────────────────────────────────────────────
# specialization_select_page (GET /specialization/select)
# ──────────────────────────────────────────────────────────────────────────────

class TestSpecializationSelectPage:

    def test_renders_select_template(self):
        user = _user()
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(specialization_select_page(request=_req(), db=_mock_db(), user=user))
        tmpl, _ = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "specialization_select.html"

    def test_context_includes_user_license_types(self):
        license_mock = MagicMock()
        license_mock.specialization_type = "LFA_FOOTBALL_PLAYER"
        user = _user()
        db = _mock_db(all_return=[license_mock])
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(specialization_select_page(request=_req(), db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "LFA_FOOTBALL_PLAYER" in ctx["user_specialization_types"]

    def test_context_includes_active_specializations_dict(self):
        user = _user()
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(specialization_select_page(request=_req(), db=_mock_db(), user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "active_specializations" in ctx
        assert "LFA_FOOTBALL_PLAYER" in ctx["active_specializations"]


# ──────────────────────────────────────────────────────────────────────────────
# specialization_select_submit (POST /specialization/select)
# ──────────────────────────────────────────────────────────────────────────────

class TestSpecializationSelectSubmit:

    def _run_submit(self, user, spec, db=None):
        if db is None:
            db = _mock_db()
        with patch(f"{_BASE}.SpecializationType", SpecializationType), \
             patch(f"{_BASE}.CreditTransaction", MagicMock()), \
             patch(f"{_BASE}.TransactionType", MagicMock()), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(specialization_select_submit(
                request=_req(), specialization=spec, db=db, user=user
            ))
        return result, mock_tmpl

    def test_invalid_spec_renders_error_template(self):
        user = _user()
        result, mock_tmpl = self._run_submit(user, "TOTALLY_INVALID")
        tmpl, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "specialization_select.html"
        assert "error" in ctx

    def test_insufficient_credits_redirects_to_dashboard(self):
        user = _user(credit_balance=50)
        # user_return=user: SELECT FOR UPDATE re-query returns the real user mock (50 credits < 100)
        db = _mock_db(first_return=None, user_return=user)
        result, _ = self._run_submit(user, "LFA_FOOTBALL_PLAYER", db=db)
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_existing_license_skips_credit_deduction(self):
        license_mock = MagicMock()
        user = _user(credit_balance=200)
        # user_return=user: SELECT FOR UPDATE returns user; first_return=license_mock: license check returns existing
        db = _mock_db(first_return=license_mock, user_return=user)
        result, _ = self._run_submit(user, "LFA_FOOTBALL_PLAYER", db=db)
        assert isinstance(result, RedirectResponse)
        assert user.credit_balance == 200  # Credits NOT deducted (license already exists)
        # Guard: existing license path still redirects to the correct onboarding URL
        assert "lfa-player/onboarding" in result.headers["location"]

    def test_lfa_player_redirects_to_lfa_onboarding(self):
        user = _user(credit_balance=200)
        # user_return=user: SELECT FOR UPDATE re-query returns the same user (with 200 credits)
        # first_return=None: license check returns None → new unlock path
        db = _mock_db(first_return=None, user_return=user)
        result, _ = self._run_submit(user, "LFA_FOOTBALL_PLAYER", db=db)
        assert isinstance(result, RedirectResponse)
        assert "lfa-player/onboarding" in result.headers["location"]

    def test_non_lfa_spec_redirects_to_motivation(self):
        user = _user(credit_balance=200)
        db = _mock_db(first_return=None, user_return=user)
        result, _ = self._run_submit(user, "GANCUJU_PLAYER", db=db)
        assert isinstance(result, RedirectResponse)
        assert "motivation" in result.headers["location"]

    def test_credit_boundary_99_is_rejected(self):
        """credit_balance=99 < 100 → insufficient credits → /dashboard redirect."""
        user = _user(credit_balance=99)
        db = _mock_db(first_return=None, user_return=user)
        result, _ = self._run_submit(user, "LFA_FOOTBALL_PLAYER", db=db)
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_credit_boundary_100_is_accepted(self):
        """credit_balance=100 == 100 → exactly enough → proceed to lfa onboarding redirect."""
        user = _user(credit_balance=100)
        db = _mock_db(first_return=None, user_return=user)
        result, _ = self._run_submit(user, "LFA_FOOTBALL_PLAYER", db=db)
        assert isinstance(result, RedirectResponse)
        assert "lfa-player/onboarding" in result.headers["location"]

    def test_integrity_error_during_license_creation_redirects_to_dashboard(self):
        """IntegrityError during DB commit (race condition duplicate) → 303 /dashboard."""
        from sqlalchemy.exc import IntegrityError
        user = _user(credit_balance=200)
        db = _mock_db(first_return=None, user_return=user)
        db.flush.side_effect = IntegrityError("duplicate", {}, Exception())

        result, _ = self._run_submit(user, "LFA_FOOTBALL_PLAYER", db=db)

        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303
        assert result.headers["location"] == "/dashboard"


# ──────────────────────────────────────────────────────────────────────────────
# lfa_player_onboarding_page (onboarding.py version — same logic as specialization.py)
# ──────────────────────────────────────────────────────────────────────────────

class TestOnboardingLfaPlayerPage:

    def test_no_license_redirects_to_dashboard(self):
        user = _user()
        result = _run(lfa_player_onboarding_page(
            request=_req(), db=_mock_db(first_return=None), user=user
        ))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_completed_onboarding_redirects_to_dashboard(self):
        license_mock = MagicMock()
        license_mock.onboarding_completed = True
        user = _user()
        result = _run(lfa_player_onboarding_page(
            request=_req(), db=_mock_db(first_return=license_mock), user=user
        ))
        assert isinstance(result, RedirectResponse)

    def test_incomplete_license_renders_template(self):
        license_mock = MagicMock()
        license_mock.onboarding_completed = False
        user = _user()
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(lfa_player_onboarding_page(
                request=_req(), db=_mock_db(first_return=license_mock), user=user
            ))
        tmpl, _ = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "lfa_player_onboarding.html"


# ──────────────────────────────────────────────────────────────────────────────
# lfa_player_onboarding_cancel (onboarding.py version)
# ──────────────────────────────────────────────────────────────────────────────

class TestOnboardingLfaPlayerCancel:

    def test_no_license_redirects_to_dashboard(self):
        user = _user()
        result = _run(lfa_player_onboarding_cancel(
            request=_req(), db=_mock_db(first_return=None), user=user
        ))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_incomplete_license_refunds_and_redirects(self):
        license_mock = MagicMock()
        license_mock.id = 1
        user = _user(credit_balance=0)
        db = _mock_db(first_return=license_mock)
        with patch(f"{_BASE}.CreditTransaction", MagicMock()), \
             patch(f"{_BASE}.TransactionType", MagicMock()):
            result = _run(lfa_player_onboarding_cancel(
                request=_req(), db=db, user=user
            ))
        assert isinstance(result, RedirectResponse)
        assert user.credit_balance == 100
        db.delete.assert_called_once_with(license_mock)


# ──────────────────────────────────────────────────────────────────────────────
# lfa_player_onboarding_submit (POST /specialization/lfa-player/onboarding-submit)
# ──────────────────────────────────────────────────────────────────────────────

class TestLfaPlayerOnboardingSubmit:

    def _make_req(self, body: dict):
        r = MagicMock()
        r.json = AsyncMock(return_value=body)
        return r

    def _valid_body(self):
        return {
            "position": "STRIKER",
            "goals": "Win",
            "motivation": "Love football",
            "skills": {f"skill_{i}": 50 for i in range(36)},
        }

    def test_invalid_position_raises_500(self):
        body = self._valid_body()
        body["position"] = "INVALID_POS"
        user = _user()
        db = _mock_db(first_return=MagicMock())
        with patch("app.skills_config.get_all_skill_keys", return_value=list(body["skills"].keys())):
            with pytest.raises(HTTPException) as exc_info:
                _run(lfa_player_onboarding_submit(
                    request=self._make_req(body), db=db, user=user
                ))
        assert exc_info.value.status_code == 500

    def test_no_license_raises_500(self):
        body = self._valid_body()
        user = _user()
        db = _mock_db(first_return=None)
        with patch("app.skills_config.get_all_skill_keys", return_value=list(body["skills"].keys())):
            with pytest.raises(HTTPException) as exc_info:
                _run(lfa_player_onboarding_submit(
                    request=self._make_req(body), db=db, user=user
                ))
        assert exc_info.value.status_code == 500

    def test_valid_submit_returns_success(self):
        body = self._valid_body()
        license_mock = MagicMock()
        user = _user()
        db = _mock_db(first_return=license_mock)
        with patch("app.skills_config.get_all_skill_keys", return_value=list(body["skills"].keys())):
            result = _run(lfa_player_onboarding_submit(
                request=self._make_req(body), db=db, user=user
            ))
        assert result["success"] is True
        assert license_mock.onboarding_completed is True
        db.commit.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# onboarding_start (GET /onboarding/start)
# ──────────────────────────────────────────────────────────────────────────────

class TestOnboardingStart:

    def test_renders_onboarding_template(self):
        user = _user(age=None)  # No age → skip spec lookup
        with patch(f"{_BASE}.get_available_specializations", MagicMock(return_value=[])), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_start(request=_req(), db=_mock_db(), user=user))
        tmpl, _ = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "student/onboarding_new.html"

    def test_user_with_age_calls_get_available_specializations(self):
        user = _user(age=18)
        mock_specs = [{"name": "LFA_FOOTBALL_PLAYER"}]
        mock_get = MagicMock(return_value=mock_specs)
        with patch(f"{_BASE}.get_available_specializations", mock_get), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_start(request=_req(), db=_mock_db(), user=user))
        mock_get.assert_called_once_with(18)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["available_specs"] == mock_specs


# ──────────────────────────────────────────────────────────────────────────────
# onboarding_set_birthdate (POST /onboarding/set-birthdate)
# ──────────────────────────────────────────────────────────────────────────────

class TestOnboardingSetBirthdate:

    def test_invalid_date_format_raises_400(self):
        user = _user()
        with pytest.raises(HTTPException) as exc_info:
            _run(onboarding_set_birthdate(
                request=_req(), date_of_birth="not-a-date", db=_mock_db(), user=user
            ))
        assert exc_info.value.status_code == 400

    def test_too_young_raises_400(self):
        user = _user()
        dob = f"{date.today().year - 2}-01-01"
        with pytest.raises(HTTPException) as exc_info:
            _run(onboarding_set_birthdate(
                request=_req(), date_of_birth=dob, db=_mock_db(), user=user
            ))
        assert exc_info.value.status_code == 400
        assert "5" in exc_info.value.detail

    def test_valid_dob_saves_and_redirects(self):
        user = _user()
        db = _mock_db()
        result = _run(onboarding_set_birthdate(
            request=_req(), date_of_birth="2000-06-15", db=db, user=user
        ))
        assert isinstance(result, RedirectResponse)
        assert "/onboarding/start" in result.headers["location"]
        db.commit.assert_called_once()

    def test_valid_dob_sets_user_date_of_birth(self):
        user = _user()
        db = _mock_db()
        _run(onboarding_set_birthdate(
            request=_req(), date_of_birth="1998-03-20", db=db, user=user
        ))
        assert user.date_of_birth == date(1998, 3, 20)


# ──────────────────────────────────────────────────────────────────────────────
# lfa_player_onboarding_web_submit (POST /specialization/lfa-player/onboarding-web)
# Phase B — cookie-auth web endpoint; returns welcome card URLs in response
# ──────────────────────────────────────────────────────────────────────────────

class TestLfaPlayerOnboardingWebSubmit:

    # Patch get_all_skill_keys to a 2-key stub so tests don't depend on taxonomy size
    _MOCK_KEYS = ["skill_a", "skill_b"]

    def _make_req(self, body: dict):
        r = MagicMock()
        r.json = AsyncMock(return_value=body)
        return r

    def _valid_body(self):
        return {
            "position":       "STRIKER",
            # omit "positions" → handler defaults to [position], which is fine
            "goals":          "Win",
            "motivation":     "Love football",
            "skills":         {"skill_a": 70, "skill_b": 65},
            "height_cm":      175,
            "weight_kg":      72,
            "preferred_foot": "right",
            "foot_dominance": 60,
        }

    def _run_valid(self, uid=77):
        body = self._valid_body()
        license_mock = MagicMock()
        user = _user(uid=uid)
        db = _mock_db(first_return=license_mock)
        with patch(f"{_BASE}.get_all_skill_keys", return_value=self._MOCK_KEYS), \
             patch("app.services.onboarding_service.complete_lfa_player_onboarding"), \
             patch("sqlalchemy.orm.attributes.flag_modified"):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._make_req(body), db=db, user=user
            ))
        return result, user, license_mock

    # ── response shape ────────────────────────────────────────────────────────

    def test_success_is_true(self):
        result, _, _ = self._run_valid()
        assert result["success"] is True

    def test_response_contains_user_id(self):
        result, user, _ = self._run_valid(uid=77)
        assert result["user_id"] == 77

    def test_response_contains_welcome_card_url(self):
        result, _, _ = self._run_valid()
        assert result["welcome_card_url"] == "/profile/onboarding-card"

    def test_response_contains_welcome_card_export_url(self):
        result, _, _ = self._run_valid()
        assert result["welcome_card_export_url"] == "/profile/onboarding-card/export"

    def test_response_contains_redirect_to_dashboard(self):
        result, _, _ = self._run_valid()
        assert result["redirect"] == "/dashboard/lfa-football-player"

    # ── validation rejections ─────────────────────────────────────────────────

    def test_empty_position_returns_400(self):
        body = self._valid_body()
        body["position"] = ""
        user = _user()
        db = _mock_db(first_return=MagicMock())
        with patch(f"{_BASE}.get_all_skill_keys", return_value=self._MOCK_KEYS):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._make_req(body), db=db, user=user
            ))
        assert result.status_code == 400

    def test_missing_height_cm_returns_422(self):
        body = self._valid_body()
        del body["height_cm"]
        user = _user()
        db = _mock_db(first_return=MagicMock())
        with patch(f"{_BASE}.get_all_skill_keys", return_value=self._MOCK_KEYS):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._make_req(body), db=db, user=user
            ))
        assert result.status_code == 422

    def test_missing_weight_kg_returns_422(self):
        body = self._valid_body()
        del body["weight_kg"]
        user = _user()
        db = _mock_db(first_return=MagicMock())
        with patch(f"{_BASE}.get_all_skill_keys", return_value=self._MOCK_KEYS):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._make_req(body), db=db, user=user
            ))
        assert result.status_code == 422

    def test_missing_preferred_foot_returns_422(self):
        body = self._valid_body()
        del body["preferred_foot"]
        user = _user()
        db = _mock_db(first_return=MagicMock())
        with patch(f"{_BASE}.get_all_skill_keys", return_value=self._MOCK_KEYS):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._make_req(body), db=db, user=user
            ))
        assert result.status_code == 422

    def test_missing_skills_returns_400(self):
        body = self._valid_body()
        body["skills"] = {}
        user = _user()
        db = _mock_db(first_return=MagicMock())
        with patch(f"{_BASE}.get_all_skill_keys", return_value=self._MOCK_KEYS):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._make_req(body), db=db, user=user
            ))
        assert result.status_code == 400

    def test_no_license_returns_400(self):
        body = self._valid_body()
        user = _user()
        db = _mock_db(first_return=None)
        with patch(f"{_BASE}.get_all_skill_keys", return_value=self._MOCK_KEYS):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._make_req(body), db=db, user=user
            ))
        assert result.status_code == 400

    def test_invalid_preferred_foot_returns_422(self):
        body = self._valid_body()
        body["preferred_foot"] = "both_feet"  # not in left|right|both
        user = _user()
        db = _mock_db(first_return=MagicMock())
        with patch(f"{_BASE}.get_all_skill_keys", return_value=self._MOCK_KEYS):
            result = _run(lfa_player_onboarding_web_submit(
                request=self._make_req(body), db=db, user=user
            ))
        assert result.status_code == 422


# ──────────────────────────────────────────────────────────────────────────────
# Phase B template static assertions
# ──────────────────────────────────────────────────────────────────────────────

_TEMPLATE_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "lfa_player_onboarding.html"
)


@pytest.fixture(scope="module")
def template_src():
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


class TestOnboardingTemplatePhaseB:
    """
    Static analysis of lfa_player_onboarding.html confirming Phase B invariants.
    These checks do not render Jinja2 — they assert raw HTML/JS source markers.
    """

    def test_step7_div_present(self, template_src):
        assert 'id="step-7"' in template_src

    def test_total_steps_is_7(self, template_src):
        assert "TOTAL_STEPS" in template_src
        # Match either `= 7;` or spaces around assignment
        import re
        assert re.search(r"TOTAL_STEPS\s*=\s*7\b", template_src)

    def test_step_labels_contains_welcome(self, template_src):
        assert "'Welcome'" in template_src or '"Welcome"' in template_src

    def test_no_auto_redirect_on_success(self, template_src):
        """Phase B replaced 800ms auto-redirect with goTo(7) — old pattern must be gone."""
        assert "Redirecting to dashboard" not in template_src
        assert "window.location.href = data.redirect" not in template_src

    def test_goto_7_called_on_success(self, template_src):
        assert "goTo(7)" in template_src

    def test_btn_go_dashboard_present(self, template_src):
        assert 'id="btn-go-dashboard"' in template_src

    def test_btn_go_profile_present(self, template_src):
        assert 'id="btn-go-profile"' in template_src

    def test_progress_dots_range_covers_7_steps(self, template_src):
        # Progress dots loop: range(1, 8) generates 7 dots
        assert "range(1, 8)" in template_src
