"""
Sprint K — GanCuju Player Service unit tests

Target: ≥ 85% stmt, high branch coverage on:
  - get_progression_status   (achievement levels 4/5/7/8, max belt)
  - promote_to_next_belt     (max belt, invalid score, happy path, license update)
  - can_book_session         (license denied, no enrollment, payment, wrong type)
  - get_current_belt         (with / without prior promotions)
  - get_next_belt            (has next, at max, invalid belt name)
  - get_belt_history         (promoter found/not found, from_belt=None, empty)
  - validate_age_eligibility (no DOB, too young, eligible)
  - get_enrollment_requirements (all combinations)
"""
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch, call

from app.services.specs.semester_based.gancuju_player_service import GanCujuPlayerService
from app.models.belt_promotion import BeltPromotion
from app.models.license import UserLicense
from app.models.semester_enrollment import SemesterEnrollment
from app.models.user import User


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════


def _svc():
    return GanCujuPlayerService()


def _dob(years_ago: int) -> date:
    return date.today() - timedelta(days=years_ago * 365 + 2)


def _promo(to_belt: str, from_belt="BAMBOO_DISCIPLE", promoted_by=1, promo_id=1):
    p = MagicMock()
    p.id = promo_id
    p.to_belt = to_belt
    p.from_belt = from_belt
    p.promoted_by = promoted_by
    p.notes = None
    p.exam_score = 90
    p.exam_notes = "Good performance"
    at = MagicMock()
    at.isoformat.return_value = "2026-01-15T10:00:00"
    p.promoted_at = at
    return p


def _license(lid=1, current_level=1, max_level=1):
    lic = MagicMock()
    lic.id = lid
    lic.current_level = current_level
    lic.max_achieved_level = max_level
    return lic


def _user(uid=1, dob=None):
    u = MagicMock()
    u.id = uid
    u.date_of_birth = dob
    u.name = "GanCuju Student"
    return u


def _split_db(bp_first=None, bp_all=None, lic_first=None, enroll_first=None, user_first=None):
    """
    DB mock that dispatches query() calls by model.

    bp_all controls BeltPromotion.filter().order_by().all()
    bp_first controls BeltPromotion.filter().order_by().first()
    lic_first controls UserLicense.filter().first()
    enroll_first controls SemesterEnrollment.filter().first()
    user_first controls User.filter().first()
    """
    db = MagicMock()

    bp_q = MagicMock()
    bp_q.filter.return_value = bp_q
    bp_q.order_by.return_value = bp_q
    bp_q.first.return_value = bp_first
    bp_q.all.return_value = bp_all if bp_all is not None else ([] if bp_first is None else [bp_first])

    lic_q = MagicMock()
    lic_q.filter.return_value = lic_q
    lic_q.first.return_value = lic_first

    enroll_q = MagicMock()
    enroll_q.filter.return_value = enroll_q
    enroll_q.first.return_value = enroll_first

    user_q = MagicMock()
    user_q.filter.return_value = user_q
    user_q.first.return_value = user_first

    def _q(model):
        if model is BeltPromotion:
            return bp_q
        if model is UserLicense:
            return lic_q
        if model is SemesterEnrollment:
            return enroll_q
        if model is User:
            return user_q
        return MagicMock()

    db.query.side_effect = _q
    return db


# ══════════════════════════════════════════════════════════════════
# get_current_belt
# ══════════════════════════════════════════════════════════════════


class TestGetCurrentBelt:

    def test_returns_latest_to_belt_from_promotion(self):
        """Lines 313-314: latest promotion exists → return its to_belt."""
        svc = _svc()
        promo = _promo("CELESTIAL_RIVER")
        db = _split_db(bp_first=promo)

        result = svc.get_current_belt(user_license_id=42, db=db)

        assert result == "CELESTIAL_RIVER"

    def test_no_promotions_returns_bamboo_disciple(self):
        """Lines 315-316: no prior promotions → default BAMBOO_DISCIPLE (L1)."""
        svc = _svc()
        db = _split_db(bp_first=None)

        result = svc.get_current_belt(user_license_id=42, db=db)

        assert result == "BAMBOO_DISCIPLE"

    def test_most_recent_promotion_wins(self):
        """Latest promo (desc order) is returned — order_by called."""
        svc = _svc()
        promo = _promo("STRONG_ROOT")
        db = _split_db(bp_first=promo)

        result = svc.get_current_belt(1, db)

        # Verify order_by was invoked (desc ordering)
        db.query(BeltPromotion).filter.return_value.order_by.assert_called_once()
        assert result == "STRONG_ROOT"


# ══════════════════════════════════════════════════════════════════
# get_next_belt
# ══════════════════════════════════════════════════════════════════


class TestGetNextBelt:

    def test_bamboo_disciple_next_is_dawn_dew(self):
        """L1 → next = L2."""
        svc = _svc()
        assert svc.get_next_belt("BAMBOO_DISCIPLE") == "DAWN_DEW"

    def test_midnight_guardian_next_is_dragon_wisdom(self):
        """L7 → next = L8."""
        svc = _svc()
        assert svc.get_next_belt("MIDNIGHT_GUARDIAN") == "DRAGON_WISDOM"

    def test_dragon_wisdom_returns_none(self):
        """Lines 322-324: max belt (L8) → None (already at top)."""
        svc = _svc()
        result = svc.get_next_belt("DRAGON_WISDOM")
        assert result is None

    def test_invalid_belt_raises_value_error(self):
        """Lines 325-326: unknown belt name → ValueError."""
        svc = _svc()
        with pytest.raises(ValueError, match="Invalid belt level"):
            svc.get_next_belt("PURPLE_BELT")


# ══════════════════════════════════════════════════════════════════
# get_belt_info
# ══════════════════════════════════════════════════════════════════


class TestGetBeltInfo:

    def test_known_belt_returns_correct_info(self):
        svc = _svc()
        info = svc.get_belt_info("CELESTIAL_RIVER")
        assert info["level"] == 4
        assert info["color"] == "Blue"
        assert info["stage"] == "Flow"

    def test_unknown_belt_returns_defaults(self):
        """Line 330 fallback: unknown belt → level=0."""
        svc = _svc()
        info = svc.get_belt_info("UNKNOWN_BELT")
        assert info["level"] == 0
        assert info["name"] == "Unknown"


# ══════════════════════════════════════════════════════════════════
# get_progression_status
# ══════════════════════════════════════════════════════════════════


class TestGetProgressionStatus:

    def _result(self, belt: str, history=None):
        svc = _svc()
        lic = _license()
        with patch.object(svc, "get_current_belt", return_value=belt):
            with patch.object(svc, "get_belt_history", return_value=history or []):
                return svc.get_progression_status(lic, MagicMock())

    def test_level_1_no_achievements(self):
        """L1: no achievements, next_belt=DAWN_DEW, progress=12.5%."""
        result = self._result("BAMBOO_DISCIPLE")
        assert result["current_level"] == 1
        assert result["next_belt"] == "DAWN_DEW"
        assert result["next_belt_info"]["level"] == 2
        assert result["achievements"] == []
        assert result["progress_percentage"] == pytest.approx(12.5)

    def test_level_4_competition_ready(self):
        """Lines 283-284: L4 → 'Competition Ready' achievement."""
        result = self._result("CELESTIAL_RIVER")
        names = [a["name"] for a in result["achievements"]]
        assert "Competition Ready" in names
        assert "Teaching Certified" not in names
        assert result["current_level"] == 4

    def test_level_5_teaching_certified(self):
        """Lines 285-286: L5 → 'Competition Ready' + 'Teaching Certified'."""
        result = self._result("STRONG_ROOT")
        names = [a["name"] for a in result["achievements"]]
        assert "Competition Ready" in names
        assert "Teaching Certified" in names
        assert "Expert Practitioner" not in names

    def test_level_7_expert_practitioner(self):
        """Lines 287-288: L7 → 3 achievements including Expert Practitioner."""
        result = self._result("MIDNIGHT_GUARDIAN")
        names = [a["name"] for a in result["achievements"]]
        assert "Expert Practitioner" in names
        assert "Grand Master" not in names
        assert result["current_level"] == 7

    def test_level_8_grand_master_all_achievements(self):
        """Lines 289-290: L8 → all 4 achievements, next_belt=None, progress=100%."""
        result = self._result("DRAGON_WISDOM")
        names = [a["name"] for a in result["achievements"]]
        assert "Grand Master" in names
        assert "Expert Practitioner" in names
        assert "Competition Ready" in names
        assert result["next_belt"] is None
        assert result["next_belt_info"] is None
        assert result["progress_percentage"] == pytest.approx(100.0)

    def test_belt_history_passed_through(self):
        """Belt history from get_belt_history is returned in result."""
        history = [{"from_belt": "BAMBOO_DISCIPLE", "to_belt": "DAWN_DEW"}]
        result = self._result("DAWN_DEW", history=history)
        assert result["belt_history"] == history


# ══════════════════════════════════════════════════════════════════
# promote_to_next_belt
# ══════════════════════════════════════════════════════════════════


class TestPromoteToNextBelt:

    def test_happy_path_bamboo_to_dawn(self):
        """Normal promotion: BAMBOO_DISCIPLE → DAWN_DEW, license updated."""
        svc = _svc()
        lic = _license(lid=1, current_level=1, max_level=1)
        db = _split_db(bp_first=None, lic_first=lic)  # no promo → BAMBOO_DISCIPLE

        result = svc.promote_to_next_belt(
            user_license_id=42, promoted_by=99, db=db
        )

        assert result.from_belt == "BAMBOO_DISCIPLE"
        assert result.to_belt == "DAWN_DEW"
        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()

    def test_license_level_updated_on_promotion(self):
        """Lines 384-386: license.current_level and max_achieved_level updated."""
        svc = _svc()
        lic = _license(lid=1, current_level=1, max_level=1)
        db = _split_db(bp_first=None, lic_first=lic)

        svc.promote_to_next_belt(user_license_id=42, promoted_by=99, db=db)

        # DAWN_DEW is index 1 → level 2
        assert lic.current_level == 2
        assert lic.max_achieved_level == 2

    def test_already_at_max_belt_raises(self):
        """Lines 363-364: DRAGON_WISDOM → ValueError 'Already at highest belt'."""
        svc = _svc()
        db = _split_db(bp_first=_promo("DRAGON_WISDOM", from_belt="MIDNIGHT_GUARDIAN"))

        with pytest.raises(ValueError, match="Already at highest belt"):
            svc.promote_to_next_belt(user_license_id=42, promoted_by=99, db=db)

    def test_exam_score_above_100_raises(self):
        """Lines 366-367: exam_score=101 → ValueError."""
        svc = _svc()
        db = _split_db(bp_first=None)  # current = BAMBOO_DISCIPLE

        with pytest.raises(ValueError, match="Exam score must be between 0-100"):
            svc.promote_to_next_belt(1, promoted_by=1, db=db, exam_score=101)

    def test_exam_score_below_0_raises(self):
        """Lines 366-367: exam_score=-1 → ValueError."""
        svc = _svc()
        db = _split_db(bp_first=None)

        with pytest.raises(ValueError, match="Exam score must be between 0-100"):
            svc.promote_to_next_belt(1, promoted_by=1, db=db, exam_score=-1)

    def test_exam_score_boundary_0_accepted(self):
        """exam_score=0 is valid (boundary check)."""
        svc = _svc()
        lic = _license()
        db = _split_db(bp_first=None, lic_first=lic)

        result = svc.promote_to_next_belt(1, promoted_by=1, db=db, exam_score=0)

        assert result.exam_score == 0

    def test_exam_score_boundary_100_accepted(self):
        """exam_score=100 is valid (boundary check)."""
        svc = _svc()
        lic = _license()
        db = _split_db(bp_first=None, lic_first=lic)

        result = svc.promote_to_next_belt(1, promoted_by=1, db=db, exam_score=100)

        assert result.exam_score == 100

    def test_notes_stored_on_promotion(self):
        """Optional notes and exam_notes are passed to BeltPromotion constructor."""
        svc = _svc()
        lic = _license()
        db = _split_db(bp_first=None, lic_first=lic)

        result = svc.promote_to_next_belt(
            1, promoted_by=1, db=db,
            notes="Great technique",
            exam_notes="Passed all drills"
        )

        assert result.notes == "Great technique"
        assert result.exam_notes == "Passed all drills"

    def test_max_achieved_level_not_lowered(self):
        """max_achieved_level only increases (max() call on line 386)."""
        svc = _svc()
        # Current level 1, but max_achieved = 5 (was demoted)
        lic = _license(lid=1, current_level=1, max_level=5)
        db = _split_db(bp_first=None, lic_first=lic)

        svc.promote_to_next_belt(1, promoted_by=1, db=db)

        # new_level = 2, max should remain 5 (max(5, 2) = 5)
        assert lic.max_achieved_level == 5


# ══════════════════════════════════════════════════════════════════
# can_book_session
# ══════════════════════════════════════════════════════════════════


class TestCanBookSession:

    def test_no_active_license_denied(self):
        """Lines 138-140: no license → False + license error message."""
        svc = _svc()
        db = _split_db(lic_first=None)
        user = _user(uid=1)
        session = MagicMock()

        ok, reason = svc.can_book_session(user, session, db)

        assert ok is False
        assert "license" in reason.lower() or "No active" in reason

    def test_no_active_enrollment_denied(self):
        """Lines 148-149: license ok, but no enrollment → False."""
        svc = _svc()
        lic = _license()
        db = _split_db(lic_first=lic, enroll_first=None)
        user = _user()

        ok, reason = svc.can_book_session(user, MagicMock(), db)

        assert ok is False
        assert "enrollment" in reason.lower()

    def test_payment_not_verified_denied(self):
        """Lines 151-152: enrollment exists but payment_verified=False → False."""
        svc = _svc()
        lic = _license()
        enroll = MagicMock()
        enroll.payment_verified = False
        db = _split_db(lic_first=lic, enroll_first=enroll)

        ok, reason = svc.can_book_session(_user(), MagicMock(), db)

        assert ok is False
        assert "payment" in reason.lower()

    def test_wrong_session_type_denied(self):
        """Lines 155-156: specialization_type not starting with 'GANCUJU' → False."""
        svc = _svc()
        lic = _license()
        enroll = MagicMock()
        enroll.payment_verified = True
        db = _split_db(lic_first=lic, enroll_first=enroll)

        session = MagicMock()
        session.specialization_type = "LFA_PLAYER"

        ok, reason = svc.can_book_session(_user(), session, db)

        assert ok is False
        assert "not for GanCuju" in reason

    def test_session_type_none_denied(self):
        """specialization_type=None → first branch of condition fails."""
        svc = _svc()
        lic = _license()
        enroll = MagicMock()
        enroll.payment_verified = True
        db = _split_db(lic_first=lic, enroll_first=enroll)

        session = MagicMock()
        session.specialization_type = None

        ok, reason = svc.can_book_session(_user(), session, db)

        assert ok is False

    def test_all_conditions_met_allowed(self):
        """Lines 158: happy path → True."""
        svc = _svc()
        lic = _license()
        enroll = MagicMock()
        enroll.payment_verified = True
        db = _split_db(lic_first=lic, enroll_first=enroll)

        session = MagicMock()
        session.specialization_type = "GANCUJU_PLAYER"

        ok, reason = svc.can_book_session(_user(), session, db)

        assert ok is True
        assert "Eligible" in reason

    def test_gancuju_prefix_accepted(self):
        """specialization_type='GANCUJU_COMPETITION' starts with 'GANCUJU' → ok."""
        svc = _svc()
        lic = _license()
        enroll = MagicMock()
        enroll.payment_verified = True
        db = _split_db(lic_first=lic, enroll_first=enroll)

        session = MagicMock()
        session.specialization_type = "GANCUJU_COMPETITION"

        ok, _ = svc.can_book_session(_user(), session, db)

        assert ok is True


# ══════════════════════════════════════════════════════════════════
# validate_age_eligibility
# ══════════════════════════════════════════════════════════════════


class TestValidateAgeEligibility:

    def test_no_dob_fails(self):
        """Lines 105-107: no date_of_birth → validate_date_of_birth fails."""
        svc = _svc()
        user = _user(dob=None)

        ok, reason = svc.validate_age_eligibility(user)

        assert ok is False
        assert "required" in reason.lower() or "Date of birth" in reason

    def test_too_young_fails(self):
        """Lines 111-112: age < 5 → False with min-age message."""
        svc = _svc()
        user = _user(dob=_dob(years_ago=3))  # 3 years old

        ok, reason = svc.validate_age_eligibility(user)

        assert ok is False
        assert reason == "MINIMUM_ACCOUNT_AGE"

    def test_exactly_minimum_age_passes(self):
        """age = 5 (MINIMUM_AGE) → eligible."""
        svc = _svc()
        user = _user(dob=_dob(years_ago=5))

        ok, reason = svc.validate_age_eligibility(user, db=MagicMock())

        assert ok is True
        assert "Eligible" in reason

    def test_well_above_minimum_age_passes(self):
        """age = 25 → eligible."""
        svc = _svc()
        user = _user(dob=_dob(years_ago=25))

        ok, reason = svc.validate_age_eligibility(user)

        assert ok is True

    def test_future_dob_fails(self):
        """validate_date_of_birth: DOB in the future → False."""
        svc = _svc()
        user = _user(dob=date.today() + timedelta(days=30))

        ok, reason = svc.validate_age_eligibility(user)

        assert ok is False


# ══════════════════════════════════════════════════════════════════
# get_belt_history
# ══════════════════════════════════════════════════════════════════


class TestGetBeltHistory:

    def _make_history_db(self, promos, promoter):
        """Build DB: BeltPromotion.all() → promos; User.first() → promoter per promo."""
        db = MagicMock()

        bp_q = MagicMock()
        bp_q.filter.return_value = bp_q
        bp_q.order_by.return_value = bp_q
        bp_q.all.return_value = promos

        # User query returns promoter for each call
        user_q = MagicMock()
        user_q.filter.return_value = user_q
        user_q.first.return_value = promoter

        def _q(model):
            if model is BeltPromotion:
                return bp_q
            if model is User:
                return user_q
            return MagicMock()

        db.query.side_effect = _q
        return db

    def test_empty_history(self):
        """No promotions → empty list returned."""
        svc = _svc()
        db = self._make_history_db([], promoter=None)

        result = svc.get_belt_history(1, db)

        assert result == []

    def test_history_with_promoter_found(self):
        """Lines 401-415: promoter found → promoter_name = promoter.name."""
        svc = _svc()
        promo = _promo("DAWN_DEW", from_belt="BAMBOO_DISCIPLE", promoted_by=99)
        promoter = MagicMock()
        promoter.name = "Master Li"
        db = self._make_history_db([promo], promoter=promoter)

        result = svc.get_belt_history(1, db)

        assert len(result) == 1
        assert result[0]["promoter_name"] == "Master Li"
        assert result[0]["from_belt"] == "BAMBOO_DISCIPLE"
        assert result[0]["to_belt"] == "DAWN_DEW"
        assert result[0]["exam_score"] == promo.exam_score

    def test_history_promoter_not_found_unknown(self):
        """Line 410: promoter query returns None → 'Unknown'."""
        svc = _svc()
        promo = _promo("FLEXIBLE_REED", promoted_by=999)
        db = self._make_history_db([promo], promoter=None)

        result = svc.get_belt_history(1, db)

        assert result[0]["promoter_name"] == "Unknown"

    def test_history_from_belt_none_gives_none_info(self):
        """Lines 406-407: from_belt=None → from_belt_info=None."""
        svc = _svc()
        promo = _promo("BAMBOO_DISCIPLE", from_belt=None)
        promoter = MagicMock()
        promoter.name = "Instructor"
        db = self._make_history_db([promo], promoter=promoter)

        result = svc.get_belt_history(1, db)

        assert result[0]["from_belt"] is None
        assert result[0]["from_belt_info"] is None

    def test_history_promoted_at_isoformat(self):
        """promoted_at.isoformat() called to format timestamp."""
        svc = _svc()
        promo = _promo("DAWN_DEW")
        promoter = MagicMock()
        promoter.name = "Teacher"
        db = self._make_history_db([promo], promoter=promoter)

        result = svc.get_belt_history(1, db)

        assert result[0]["promoted_at"] == "2026-01-15T10:00:00"

    def test_history_multiple_promotions(self):
        """Multiple promos in history → all returned, newest first (ordering)."""
        svc = _svc()
        p1 = _promo("DAWN_DEW", from_belt="BAMBOO_DISCIPLE", promo_id=1)
        p2 = _promo("FLEXIBLE_REED", from_belt="DAWN_DEW", promo_id=2)
        promoter = MagicMock()
        promoter.name = "Sifu"
        db = self._make_history_db([p2, p1], promoter=promoter)

        result = svc.get_belt_history(1, db)

        assert len(result) == 2
        assert result[0]["to_belt"] == "FLEXIBLE_REED"


# ══════════════════════════════════════════════════════════════════
# get_enrollment_requirements
# ══════════════════════════════════════════════════════════════════


class TestGetEnrollmentRequirements:

    def _enroll_req_db(self, lic, enroll):
        """DB for get_enrollment_requirements: UserLicense (x2), BeltPromotion, SemesterEnrollment."""
        db = MagicMock()

        lic_q = MagicMock()
        lic_q.filter.return_value = lic_q
        lic_q.first.return_value = lic

        bp_q = MagicMock()
        bp_q.filter.return_value = bp_q
        bp_q.order_by.return_value = bp_q
        bp_q.first.return_value = None  # no prior promotions → BAMBOO_DISCIPLE

        enroll_q = MagicMock()
        enroll_q.filter.return_value = enroll_q
        enroll_q.first.return_value = enroll

        def _q(model):
            if model is UserLicense:
                return lic_q
            if model is BeltPromotion:
                return bp_q
            if model is SemesterEnrollment:
                return enroll_q
            return MagicMock()

        db.query.side_effect = _q
        return db

    def test_all_requirements_met(self):
        """Happy path: license + enrollment + payment → can_participate=True."""
        svc = _svc()
        user = _user(dob=_dob(years_ago=20))
        lic = _license(lid=1, current_level=3)
        enroll = MagicMock()
        enroll.payment_verified = True
        db = self._enroll_req_db(lic, enroll)

        result = svc.get_enrollment_requirements(user, db)

        assert result["can_participate"] is True
        assert result["missing_requirements"] == []
        status = result["current_status"]
        assert status["has_license"] is True
        assert status["has_semester_enrollment"] is True
        assert status["payment_verified"] is True
        assert status["current_belt"] == "BAMBOO_DISCIPLE"
        assert status["current_level"] == 3

    def test_no_license_missing_requirement(self):
        """Lines 216-217: no license → missing_requirements includes license error."""
        svc = _svc()
        user = _user(dob=_dob(years_ago=20))
        enroll = MagicMock()
        enroll.payment_verified = True
        db = self._enroll_req_db(lic=None, enroll=enroll)

        result = svc.get_enrollment_requirements(user, db)

        assert result["can_participate"] is False
        assert any("license" in m.lower() for m in result["missing_requirements"])
        assert result["current_status"]["has_license"] is False

    def test_no_enrollment_missing_requirement(self):
        """Lines 231-232: no enrollment → 'Semester enrollment required'."""
        svc = _svc()
        user = _user(dob=_dob(years_ago=15))
        lic = _license()
        db = self._enroll_req_db(lic=lic, enroll=None)

        result = svc.get_enrollment_requirements(user, db)

        assert result["can_participate"] is False
        assert any("enrollment" in m.lower() for m in result["missing_requirements"])
        assert result["current_status"]["has_semester_enrollment"] is False

    def test_payment_not_verified_missing(self):
        """Lines 229-230: enrollment exists but payment_verified=False → payment missing."""
        svc = _svc()
        user = _user(dob=_dob(years_ago=15))
        lic = _license()
        enroll = MagicMock()
        enroll.payment_verified = False
        db = self._enroll_req_db(lic=lic, enroll=enroll)

        result = svc.get_enrollment_requirements(user, db)

        assert result["can_participate"] is False
        assert any("payment" in m.lower() for m in result["missing_requirements"])
        assert result["current_status"]["payment_verified"] is False

    def test_age_ineligible_appended_to_missing(self):
        """Lines 199-200: too young → age requirement in missing_requirements."""
        svc = _svc()
        user = _user(dob=_dob(years_ago=2))  # 2 years old
        lic = _license()
        enroll = MagicMock()
        enroll.payment_verified = True
        db = self._enroll_req_db(lic=lic, enroll=enroll)

        result = svc.get_enrollment_requirements(user, db)

        assert result["can_participate"] is False
        assert any("age" in m.lower() for m in result["missing_requirements"])

    def test_no_dob_age_requirement_fails(self):
        """Missing DOB → age check fails, added to missing_requirements."""
        svc = _svc()
        user = _user(dob=None)
        lic = _license()
        enroll = MagicMock()
        enroll.payment_verified = True
        db = self._enroll_req_db(lic=lic, enroll=enroll)

        result = svc.get_enrollment_requirements(user, db)

        assert result["can_participate"] is False
        assert any("age" in m.lower() for m in result["missing_requirements"])


# ══════════════════════════════════════════════════════════════════
# Service metadata (quick sanity)
# ══════════════════════════════════════════════════════════════════


class TestServiceMetadata:

    def test_is_semester_based(self):
        assert GanCujuPlayerService().is_semester_based() is True

    def test_get_specialization_name(self):
        assert GanCujuPlayerService().get_specialization_name() == "GanCuju Player"

    def test_belt_levels_count(self):
        assert len(GanCujuPlayerService.BELT_LEVELS) == 8

    def test_dragon_wisdom_is_max(self):
        assert GanCujuPlayerService.BELT_LEVELS[-1] == "DRAGON_WISDOM"
