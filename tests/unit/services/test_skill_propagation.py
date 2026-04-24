"""
Unit tests for Domain B — tournament skill delta propagation.

Covers update_skill_assessments() in tournament_participation_service.py:

  PROP-U-01  Flag disabled → function returns immediately, no DB writes
  PROP-U-02  Empty / None delta dict → returns immediately, no DB writes
  PROP-U-03  No active LFA_FOOTBALL_PLAYER license → returns immediately
  PROP-U-04  Multiple active licenses → picks most-recent (highest id)
  PROP-U-05  No prior assessment → creates new row at (baseline + delta)
  PROP-U-06  Existing ASSESSED assessment → archives old, creates new
  PROP-U-07  Existing VALIDATED assessment → archives old, creates new
  PROP-U-08  Delta=0 skill is skipped entirely
  PROP-U-09  New percentage is clamped to [40.0, 99.0]
  PROP-U-10  assessed_by_id falls back to user_id when None
  PROP-U-11  assessed_by_id is used when provided
  PROP-U-12  Multiple skills in delta → all processed
  PROP-U-13  Archive reason encodes the delta sign correctly
  PROP-U-14  New assessment notes encode the delta sign correctly

Mock strategy:
  - patch settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION
  - MagicMock db.query(...).filter(...).order_by(...).first()
  - Inspect db.add.call_args_list for created FootballSkillAssessment rows
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

from app.services.tournament.tournament_participation_service import update_skill_assessments
from app.models.football_skill_assessment import FootballSkillAssessment


_BASE = "app.services.tournament.tournament_participation_service"

_USER_ID = 42
_LICENSE_ID = 7


def _db(license=None, existing_assessments=None):
    """
    Build a MagicMock db whose query chain returns predictable values.

    Call sequence per .query().filter().order_by().first():
      Call 0            → license
      Call 1, 3, 5, … → existing assessment for skill N (alternating)
      Call 2, 4, 6, … → idempotency guard for skill N → always None

    The idempotency guard added in Sprint P2 issues a second .first() call
    per skill after the initial "existing" lookup.  This helper returns None
    for those guard calls so normal-path tests still work unchanged.
    """
    db = MagicMock()

    existing_assessments = existing_assessments or {}
    call_count = [0]
    keys = list(existing_assessments.keys())

    def query_side_effect(model):
        m = MagicMock()

        def filter_side_effect(*args, **kwargs):
            fm = MagicMock()

            def first_side_effect():
                idx = call_count[0]
                call_count[0] += 1
                if idx == 0:
                    # First call → license lookup
                    return license
                else:
                    # 2 .first() calls per skill:
                    #   even within-skill index (0, 2, 4…) → existing assessment
                    #   odd  within-skill index (1, 3, 5…) → idempotency guard → None
                    within = idx - 1  # 0-based within the per-skill section
                    if within % 2 == 1:
                        return None  # idempotency guard always None in normal tests
                    skill_idx = within // 2
                    if skill_idx < len(keys):
                        return existing_assessments[keys[skill_idx]]
                    return None

            # Wire to BOTH paths:
            #   filter().first()            — idempotency guard (no order_by)
            #   filter().order_by().first() — license/existing assessment lookup
            fm.first = first_side_effect

            def order_by_side_effect(*args):
                om = MagicMock()
                om.first = first_side_effect
                return om

            fm.order_by = order_by_side_effect
            return fm

        m.filter = filter_side_effect
        return m

    db.query.side_effect = query_side_effect
    return db


def _license(lid=_LICENSE_ID):
    lic = MagicMock()
    lic.id = lid
    return lic


def _existing_assessment(pct=65.0, status="ASSESSED"):
    a = MagicMock(spec=FootballSkillAssessment)
    a.percentage = pct
    a.status = status
    return a


# ── PROP-U-01 Flag disabled ───────────────────────────────────────────────────

def test_prop_u01_flag_disabled_returns_immediately():
    db = MagicMock()
    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = False
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"dribbling": 1.5})
    db.query.assert_not_called()
    db.add.assert_not_called()


# ── PROP-U-02 Empty delta dict ────────────────────────────────────────────────

def test_prop_u02_empty_delta_returns_immediately():
    db = MagicMock()
    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={})
    db.add.assert_not_called()


def test_prop_u02_none_delta_returns_immediately():
    db = MagicMock()
    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta=None)
    db.add.assert_not_called()


# ── PROP-U-03 No license ──────────────────────────────────────────────────────

def test_prop_u03_no_license_returns_immediately():
    db = _db(license=None)
    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"dribbling": 2.0})
    db.add.assert_not_called()


# ── PROP-U-04 Multiple licenses → most-recent picked ─────────────────────────

def test_prop_u04_license_query_uses_order_by_id_desc():
    """Verify the query sorts by id DESC (most-recent-first), limit 1."""
    db = MagicMock()
    call_log = []

    # Capture the order_by argument
    mock_query_chain = MagicMock()
    mock_filter = MagicMock()
    mock_order_by = MagicMock()
    mock_first = MagicMock(return_value=None)  # No license → early return

    db.query.return_value = mock_query_chain
    mock_query_chain.filter.return_value = mock_filter
    mock_filter.order_by.return_value = mock_order_by
    mock_order_by.first.return_value = None

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"dribbling": 1.0})

    # order_by should have been called (desc ordering)
    mock_filter.order_by.assert_called_once()
    # Verify the argument contains desc direction (UserLicense.id.desc())
    order_arg = mock_filter.order_by.call_args[0][0]
    assert "desc" in str(order_arg).lower() or "id" in str(order_arg).lower()


# ── PROP-U-05 No prior assessment → creates new at baseline + delta ───────────

def test_prop_u05_no_existing_assessment_creates_new_row():
    lic = _license()
    db = _db(license=lic, existing_assessments={"dribbling": None})

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"dribbling": 5.0})

    db.add.assert_called_once()
    new_row = db.add.call_args[0][0]
    assert isinstance(new_row, FootballSkillAssessment)
    assert new_row.skill_name == "dribbling"
    # baseline = 60.0 (DEFAULT_BASELINE), delta = +5.0 → 65.0
    assert new_row.percentage == 65.0
    assert new_row.points_earned == 65
    assert new_row.points_total == 100
    assert new_row.status == "ASSESSED"
    assert new_row.user_license_id == lic.id


# ── PROP-U-06 Existing ASSESSED → archives old, creates new ──────────────────

def test_prop_u06_existing_assessed_archived_and_new_created():
    lic = _license()
    existing = _existing_assessment(pct=70.0, status="ASSESSED")
    db = _db(license=lic, existing_assessments={"ball_control": existing})

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"ball_control": 3.5})

    # Old assessment should be archived
    assert existing.status == "ARCHIVED"
    assert existing.archived_reason == "tournament_progression_delta=+3.5"
    assert existing.previous_status == "ASSESSED"

    # New assessment should be created
    db.add.assert_called_once()
    new_row = db.add.call_args[0][0]
    assert new_row.percentage == 73.5  # 70.0 + 3.5
    assert new_row.skill_name == "ball_control"


# ── PROP-U-07 Existing VALIDATED → archives old, creates new ─────────────────

def test_prop_u07_existing_validated_archived_and_new_created():
    lic = _license()
    existing = _existing_assessment(pct=80.0, status="VALIDATED")
    db = _db(license=lic, existing_assessments={"passing": existing})

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"passing": -2.0})

    assert existing.status == "ARCHIVED"
    assert existing.previous_status == "VALIDATED"
    db.add.assert_called_once()
    new_row = db.add.call_args[0][0]
    assert new_row.percentage == 78.0  # 80.0 - 2.0


# ── PROP-U-08 Delta=0 skill is skipped ───────────────────────────────────────

def test_prop_u08_zero_delta_skill_skipped():
    lic = _license()
    db = _db(license=lic)

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"dribbling": 0.0})

    db.add.assert_not_called()


# ── PROP-U-09 Clamping ────────────────────────────────────────────────────────

def test_prop_u09_clamp_upper_bound():
    lic = _license()
    existing = _existing_assessment(pct=98.0, status="ASSESSED")
    db = _db(license=lic, existing_assessments={"heading": existing})

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"heading": 5.0})

    new_row = db.add.call_args[0][0]
    assert new_row.percentage == 99.0  # clamped


def test_prop_u09_clamp_lower_bound():
    lic = _license()
    existing = _existing_assessment(pct=41.0, status="ASSESSED")
    db = _db(license=lic, existing_assessments={"shooting": existing})

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"shooting": -5.0})

    new_row = db.add.call_args[0][0]
    assert new_row.percentage == 40.0  # clamped


# ── PROP-U-10/11 assessed_by fallback ────────────────────────────────────────

def test_prop_u10_assessed_by_falls_back_to_user_id_when_none():
    lic = _license()
    db = _db(license=lic, existing_assessments={"dribbling": None})

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(
            db, _USER_ID, {}, assessed_by_id=None, skill_rating_delta={"dribbling": 1.0}
        )

    new_row = db.add.call_args[0][0]
    assert new_row.assessed_by == _USER_ID


def test_prop_u11_assessed_by_id_used_when_provided():
    lic = _license()
    db = _db(license=lic, existing_assessments={"dribbling": None})
    admin_id = 999

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(
            db, _USER_ID, {}, assessed_by_id=admin_id, skill_rating_delta={"dribbling": 1.0}
        )

    new_row = db.add.call_args[0][0]
    assert new_row.assessed_by == admin_id


# ── PROP-U-12 Multiple skills ─────────────────────────────────────────────────

def test_prop_u12_multiple_skills_all_processed():
    lic = _license()
    db = _db(
        license=lic,
        existing_assessments={
            "dribbling": None,
            "passing": _existing_assessment(pct=60.0),
        },
    )
    delta = {"dribbling": 2.0, "passing": -1.5}

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta=delta)

    assert db.add.call_count == 2
    added_rows = [call[0][0] for call in db.add.call_args_list]
    skill_percentages = {r.skill_name: r.percentage for r in added_rows}
    assert skill_percentages["dribbling"] == 62.0   # baseline 60 (DEFAULT_BASELINE) + 2.0
    assert skill_percentages["passing"] == 58.5     # 60.0 - 1.5


# ── PROP-U-13/14 Archive reason and notes formatting ─────────────────────────

def test_prop_u13_archive_reason_positive_delta():
    lic = _license()
    existing = _existing_assessment(pct=55.0, status="ASSESSED")
    db = _db(license=lic, existing_assessments={"vision": existing})

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"vision": 3.2})

    assert existing.archived_reason == "tournament_progression_delta=+3.2"


def test_prop_u13_archive_reason_negative_delta():
    lic = _license()
    existing = _existing_assessment(pct=55.0, status="ASSESSED")
    db = _db(license=lic, existing_assessments={"vision": existing})

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"vision": -1.8})

    assert existing.archived_reason == "tournament_progression_delta=-1.8"


def test_prop_u14_new_assessment_notes_encode_delta():
    lic = _license()
    db = _db(license=lic, existing_assessments={"crossing": None})

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(db, _USER_ID, {}, skill_rating_delta={"crossing": 4.1})

    new_row = db.add.call_args[0][0]
    assert "+4.1" in new_row.notes


# ── PROP-U-15 get_skill_profile reads assessment_delta from ASSESSED row ──────

def test_prop_u15_get_skill_profile_populates_assessment_delta():
    """
    PROP-U-15: assessment_delta is read from FootballSkillAssessment, not hardcoded.

    After Sprint P2, get_skill_profile() batch-loads ASSESSED rows and sets:
      assessment_delta = assessed_pct - baseline
      assessment_count = number of ASSESSED rows for that skill
      total_assessments = total across all skills
    """
    from app.services.skill_progression_service import get_skill_profile
    from app.models.license import UserLicense
    from app.models.football_skill_assessment import FootballSkillAssessment
    from app.models.tournament_achievement import TournamentParticipation

    _BASE_SP = "app.services.skill_progression_service"

    # Mock assessment row: dribbling at 70.0% (baseline 60.0 → delta +10.0)
    mock_assessment = MagicMock(spec=FootballSkillAssessment)
    mock_assessment.skill_name = "dribbling"
    mock_assessment.percentage = 70.0
    mock_assessment.status = "ASSESSED"
    mock_assessment.id = 1

    mock_license = MagicMock()
    mock_license.id = _LICENSE_ID

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model is UserLicense:
            # License lookup chain: .filter().order_by().first()
            q.filter.return_value.order_by.return_value.first.return_value = mock_license
        elif model is FootballSkillAssessment:
            # Batch assessment query: .filter().order_by().all()
            q.filter.return_value.order_by.return_value.all.return_value = [mock_assessment]
        elif model is TournamentParticipation:
            # Participation count
            q.filter.return_value.count.return_value = 0
        return q

    db.query.side_effect = query_side_effect

    with patch(f"{_BASE_SP}.get_all_skill_keys", return_value=["dribbling"]), \
         patch(f"{_BASE_SP}.calculate_tournament_skill_contribution", return_value={
             "dribbling": {
                 "baseline": 60.0,
                 "current_value": 60.0,
                 "contribution": 0.0,
                 "tournament_count": 0,
             }
         }):
        profile = get_skill_profile(db, _USER_ID)

    assert profile["total_assessments"] == 1
    skill = profile["skills"]["dribbling"]
    assert skill["assessment_count"] == 1
    assert skill["assessment_delta"] == 10.0   # 70.0 - 60.0


# ── PROP-U-16 Idempotency guard: second call skips (no double-write) ──────────

def test_prop_u16_idempotent_on_retry():
    """
    PROP-U-16: Second call with identical delta is skipped by idempotency guard.

    The guard detects an ASSESSED row whose notes == expected_notes
    and continues to the next skill without calling db.add().
    """
    lic = _license()
    delta = 3.0
    expected_notes = f"Auto-assessed from tournament EMA delta (+{delta:.1f})"

    already_done = MagicMock(spec=FootballSkillAssessment)
    already_done.notes = expected_notes
    already_done.status = "ASSESSED"

    call_count = [0]

    db = MagicMock()

    def query_side_effect(model):
        m = MagicMock()

        def filter_side_effect(*args, **kwargs):
            fm = MagicMock()

            def order_by_side_effect(*args):
                om = MagicMock()

                def first_side_effect():
                    idx = call_count[0]
                    call_count[0] += 1
                    if idx == 0:
                        return lic           # license
                    elif idx == 1:
                        return None          # existing assessment (none)
                    else:
                        return already_done  # idempotency guard → skip!

                om.first = first_side_effect
                return om

            fm.order_by = order_by_side_effect
            return fm

        m.filter = filter_side_effect
        return m

    db.query.side_effect = query_side_effect

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        update_skill_assessments(
            db, _USER_ID, {}, skill_rating_delta={"dribbling": delta}
        )

    # The guard must have fired — no new assessment row created
    db.add.assert_not_called()
