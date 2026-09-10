"""
Unit tests for app/api/web_routes/specialization.py

Covers:
  specialization_unlock — insufficient credits, invalid spec, age not met, success
  student_motivation_questionnaire_page — invalid spec redirects, valid renders template
  student_motivation_questionnaire_submit — invalid spec, score out of range,
                                            license not found (creates), license found (updates)
  specialization_switch — invalid spec, no license, success

Note: lfa_player_onboarding_page and lfa_player_onboarding_cancel are now exclusively
  in onboarding.py (canonical) — duplicate definitions removed from specialization.py.
  Coverage for those routes is in test_onboarding_web.py.
"""
import asyncio
from datetime import date, datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app.api.web_routes.specialization import (
    specialization_switch,
    specialization_unlock,
    student_motivation_questionnaire_page,
    student_motivation_questionnaire_submit,
)
from app.models.user import UserRole
from app.models.specialization import SpecializationType
from app.services.credit_service import InsufficientCreditsError
from app.services.player_identity_service import (
    PlayerEntitlementExistsError,
    PlayerIdentityPolicyError,
)

_BASE = "app.api.web_routes.specialization"


@pytest.fixture(autouse=True)
def _credit_service_double(monkeypatch):
    class FakeCreditService:
        def __init__(self, db):
            self.db = db

        def deduct(self, *, user, amount, **kwargs):
            user.credit_balance -= amount
            return MagicMock()

    monkeypatch.setattr(f"{_BASE}.CreditService", FakeCreditService)
    monkeypatch.setattr(
        f"{_BASE}.is_user_eligible_for_program",
        lambda db, user, program: (True, None),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    r = MagicMock()
    r.form = AsyncMock(return_value={})
    return r


def _req_with_form(data: dict):
    """Request mock with specific form data."""
    r = MagicMock()
    form_mock = MagicMock()
    form_mock.get = lambda key, default=None: data.get(key, default)
    r.form = AsyncMock(return_value=form_mock)
    return r


def _user(uid=99, credit_balance=200, age=20, role=UserRole.STUDENT):
    u = MagicMock()
    u.id = uid
    u.role = role
    u.email = "test@test.com"
    u.credit_balance = credit_balance
    u.age = age
    u.onboarding_completed = False
    return u


def _mock_db(first_return=None, user_return=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = first_return
    db.query.return_value.filter.return_value.filter.return_value.filter.return_value.first.return_value = first_return
    db.query.return_value.with_for_update.return_value.filter.return_value.first.return_value = user_return
    return db


def _player_result(user, *, cost=100):
    user.credit_balance -= cost
    license_row = MagicMock(
        id=1,
        canonical_program_id="LFA_FOOTBALL_PLAYER",
        expires_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    assignment = MagicMock(
        season_start=date(2026, 7, 1),
        season_end=date(2027, 6, 30),
        season_base_category="AMATEUR",
        effective_category="AMATEUR",
        base_participation_retained=False,
    )
    return MagicMock(license=license_row, assignment=assignment, cost=cost)


# ──────────────────────────────────────────────────────────────────────────────
# specialization_unlock (POST /specialization/unlock)
# ──────────────────────────────────────────────────────────────────────────────

class TestSpecializationUnlock:

    def test_insufficient_credits_raises_400(self):
        user = _user(credit_balance=50)
        with patch(
            f"{_BASE}.unlock_football_player",
            side_effect=InsufficientCreditsError(required=100, available=50),
        ), pytest.raises(HTTPException) as exc_info:
            _run(specialization_unlock(specialization="LFA_FOOTBALL_PLAYER", duration_months=1, db=_mock_db(user_return=user), current_user=user))
        assert exc_info.value.status_code == 400
        assert "credits" in exc_info.value.detail.lower()

    def test_invalid_specialization_raises_400(self):
        user = _user(credit_balance=500)
        import pytest
        with pytest.raises(HTTPException) as exc_info:
            _run(specialization_unlock(specialization="INVALID_SPEC", duration_months=1, db=_mock_db(), current_user=user))
        assert exc_info.value.status_code == 400

    def test_age_requirement_not_met_raises_403(self):
        user = _user(credit_balance=500, age=3)
        with patch(
            f"{_BASE}.unlock_football_player",
            side_effect=PlayerIdentityPolicyError("MINIMUM_ACCOUNT_AGE"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                _run(specialization_unlock(specialization="LFA_FOOTBALL_PLAYER", duration_months=1, db=_mock_db(), current_user=user))
        assert exc_info.value.status_code == 403

    def test_success_creates_license_and_deducts_credits(self):
        user = _user(credit_balance=200)
        db = MagicMock()
        with patch(
            f"{_BASE}.unlock_football_player",
            side_effect=lambda *args, **kwargs: _player_result(user),
        ) as unlock:
            result = _run(specialization_unlock(specialization="LFA_FOOTBALL_PLAYER", duration_months=1, db=db, current_user=user))
        assert result["success"] is True
        assert user.credit_balance == 100  # 200 - 100
        unlock.assert_called_once_with(db, user=user, duration_months=1)
        db.commit.assert_called_once()

    def test_existing_license_raises_409(self):
        """Race condition: license already exists AFTER SELECT FOR UPDATE → 409 Conflict."""
        user = _user(credit_balance=200)
        db = MagicMock()
        with patch(
            f"{_BASE}.unlock_football_player",
            side_effect=PlayerEntitlementExistsError(),
        ), pytest.raises(HTTPException) as exc_info:
            _run(specialization_unlock(specialization="LFA_FOOTBALL_PLAYER", duration_months=1, db=db, current_user=user))

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "FOOTBALL_ENTITLEMENT_ALREADY_EXISTS"

    def test_integrity_error_during_commit_raises_409(self):
        """IntegrityError on db.commit (ultra-rare race) → 409 Conflict."""
        from sqlalchemy.exc import IntegrityError
        user = _user(credit_balance=200)
        db = MagicMock()
        with patch(
            f"{_BASE}.unlock_football_player",
            side_effect=IntegrityError("duplicate key", {}, Exception()),
        ), \
             pytest.raises(HTTPException) as exc_info:
            _run(specialization_unlock(specialization="LFA_FOOTBALL_PLAYER", duration_months=1, db=db, current_user=user))

        assert exc_info.value.status_code == 409
        assert "concurrent" in exc_info.value.detail.lower() or "already exists" in exc_info.value.detail.lower()

    def test_credit_boundary_99_is_rejected(self):
        """credit_balance=99 < 100 → 400 Bad Request."""
        user = _user(credit_balance=99)
        with patch(
            f"{_BASE}.unlock_football_player",
            side_effect=InsufficientCreditsError(required=100, available=99),
        ), pytest.raises(HTTPException) as exc_info:
            _run(specialization_unlock(specialization="LFA_FOOTBALL_PLAYER", duration_months=1, db=_mock_db(user_return=user), current_user=user))
        assert exc_info.value.status_code == 400
        assert "credits" in exc_info.value.detail.lower()

    def test_credit_boundary_100_is_accepted(self):
        """credit_balance=100 == 100 → exactly enough → success."""
        user = _user(credit_balance=100)
        db = MagicMock()
        with patch(
            f"{_BASE}.unlock_football_player",
            side_effect=lambda *args, **kwargs: _player_result(user),
        ):
            result = _run(specialization_unlock(specialization="LFA_FOOTBALL_PLAYER", duration_months=1, db=db, current_user=user))

        assert result["success"] is True
        assert user.credit_balance == 0  # 100 - 100


# ──────────────────────────────────────────────────────────────────────────────
# student_motivation_questionnaire_page (GET /specialization/motivation)
# ──────────────────────────────────────────────────────────────────────────────

class TestMotivationQuestionnairePage:

    def test_invalid_spec_redirects_to_select(self):
        user = _user()
        result = _run(student_motivation_questionnaire_page(
            request=_req(), spec="INVALID_SPEC", db=_mock_db(), user=user
        ))
        assert isinstance(result, RedirectResponse)
        assert "/specialization/select" in result.headers["location"]

    def test_valid_spec_renders_template(self):
        user = _user()
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(student_motivation_questionnaire_page(
                request=_req(), spec="LFA_FOOTBALL_PLAYER", db=_mock_db(), user=user
            ))
        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "student_motivation_questionnaire.html"

    def test_context_includes_specialization_display(self):
        user = _user()
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(student_motivation_questionnaire_page(
                request=_req(), spec="GANCUJU_PLAYER", db=_mock_db(), user=user
            ))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "specialization_display" in ctx
        assert "GānCuju" in ctx["specialization_display"]


# ──────────────────────────────────────────────────────────────────────────────
# student_motivation_questionnaire_submit (POST /specialization/motivation-submit)
# ──────────────────────────────────────────────────────────────────────────────

class TestMotivationQuestionnaireSubmit:

    def _valid_form(self, spec="LFA_FOOTBALL_PLAYER"):
        return _req_with_form({
            "specialization": spec,
            "goal_clarity": "4",
            "commitment_level": "4",
            "engagement": "3",
            "progress_mindset": "5",
            "initiative": "4",
            "notes": "Good",
        })

    def test_invalid_spec_redirects(self):
        req = _req_with_form({"specialization": "BAD_SPEC", "goal_clarity": "3",
                               "commitment_level": "3", "engagement": "3",
                               "progress_mindset": "3", "initiative": "3"})
        user = _user()
        result = _run(student_motivation_questionnaire_submit(
            request=req, db=_mock_db(), user=user
        ))
        assert isinstance(result, RedirectResponse)

    def test_score_out_of_range_renders_error(self):
        req = _req_with_form({
            "specialization": "LFA_FOOTBALL_PLAYER",
            "goal_clarity": "0",  # < 1 — invalid
            "commitment_level": "3",
            "engagement": "3",
            "progress_mindset": "3",
            "initiative": "3",
        })
        user = _user()
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(student_motivation_questionnaire_submit(request=req, db=_mock_db(), user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx

    def test_valid_submit_with_existing_license_commits_and_redirects(self):
        license_mock = MagicMock()
        db = _mock_db(first_return=license_mock)
        user = _user()
        result = _run(student_motivation_questionnaire_submit(
            request=self._valid_form(), db=db, user=user
        ))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]
        db.commit.assert_called_once()
        assert license_mock.onboarding_completed is True

    def test_valid_player_submit_without_entitlement_fails_closed(self):
        db = _mock_db(first_return=None)  # No existing license
        user = _user()
        with patch(f"{_BASE}.templates") as mock_templates:
            mock_templates.TemplateResponse.return_value = MagicMock()
            result = _run(student_motivation_questionnaire_submit(
                request=self._valid_form(), db=db, user=user
            ))
        assert not isinstance(result, RedirectResponse)
        context = mock_templates.TemplateResponse.call_args.args[1]
        assert context["error"] == "ACTIVE_FOOTBALL_ENTITLEMENT_REQUIRED"
        db.add.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# specialization_switch (POST /specialization/switch)
# ──────────────────────────────────────────────────────────────────────────────

class TestSpecializationSwitch:

    def test_invalid_spec_redirects(self):
        user = _user()
        result = _run(specialization_switch(
            request=_req(), specialization="INVALID_SPEC", db=_mock_db(), user=user
        ))
        assert isinstance(result, RedirectResponse)

    def test_no_license_redirects(self):
        user = _user()
        result = _run(specialization_switch(
            request=_req(), specialization="LFA_FOOTBALL_PLAYER",
            db=_mock_db(first_return=None), user=user
        ))
        assert isinstance(result, RedirectResponse)

    def test_has_license_commits_and_redirects_to_dashboard(self):
        license_mock = MagicMock()
        user = _user()
        db = _mock_db(first_return=license_mock)
        with patch(f"{_BASE}.activate_football_player") as activate:
            result = _run(specialization_switch(
                request=_req(), specialization="LFA_FOOTBALL_PLAYER", db=db, user=user
            ))
        assert isinstance(result, RedirectResponse)
        activate.assert_called_once_with(db, user=user)
        db.commit.assert_called_once()

    def test_return_url_is_honoured(self):
        license_mock = MagicMock()
        user = _user()
        db = _mock_db(first_return=license_mock)
        with patch(f"{_BASE}.activate_football_player"):
            result = _run(specialization_switch(
                request=_req(), specialization="LFA_FOOTBALL_PLAYER",
                return_url="/profile", db=db, user=user
            ))
        assert isinstance(result, RedirectResponse)
        assert "/profile" in result.headers["location"]
