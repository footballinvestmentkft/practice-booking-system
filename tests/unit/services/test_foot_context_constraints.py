"""
Constraint and model tests for foot-laterality feature.

Tests: FC-01 through FC-07
Covers:
  - TournamentParticipation.foot_context DB constraint
  - GamePreset.foot_context property (pure Python)
  - UserLicense right/left foot score CHECK constraints
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.game_preset import GamePreset


# ---------------------------------------------------------------------------
# FC-04: GamePreset.foot_context property — pure Python, no DB needed
# ---------------------------------------------------------------------------

class TestGamePresetFootContextProperty:
    """
    GamePreset.foot_context is a pure-Python @property — test it without a DB.

    We can't use GamePreset.__new__ directly because the SQLAlchemy mapper
    is not initialised on a bare instance, so attribute access raises
    AttributeError.  Instead we call the property's fget via a minimal stub
    class that owns game_config as a plain Python attribute.
    """

    class _Stub:
        """Minimal stand-in that owns game_config as a plain attribute."""
        _VALID_FOOT_CONTEXTS = frozenset({"right", "left", "neutral"})
        foot_context = GamePreset.foot_context  # copy the property descriptor

        def __init__(self, game_config):
            self.game_config = game_config

    def _preset(self, skill_config: dict) -> "_Stub":
        return self._Stub(game_config={"skill_config": skill_config})

    def test_fc04_valid_right_returned(self):
        """FC-04a: foot_context='right' stored correctly → property returns 'right'."""
        assert self._preset({"foot_context": "right"}).foot_context == "right"

    def test_fc04_valid_left_returned(self):
        """FC-04b: foot_context='left' stored correctly → property returns 'left'."""
        assert self._preset({"foot_context": "left"}).foot_context == "left"

    def test_fc04_valid_neutral_returned(self):
        """FC-04c: foot_context='neutral' stored correctly → property returns 'neutral'."""
        assert self._preset({"foot_context": "neutral"}).foot_context == "neutral"

    def test_fc04_invalid_value_falls_back_to_neutral(self):
        """FC-04d: invalid stored value → property silently returns 'neutral'."""
        assert self._preset({"foot_context": "BOTH"}).foot_context == "neutral"

    def test_fc04_missing_key_falls_back_to_neutral(self):
        """FC-04e: foot_context key absent → property returns 'neutral'."""
        assert self._preset({}).foot_context == "neutral"

    def test_fc04_none_game_config_falls_back_to_neutral(self):
        """FC-04f: game_config is None → property returns 'neutral' without crash."""
        p = self._Stub(game_config=None)
        assert p.foot_context == "neutral"


# ---------------------------------------------------------------------------
# FC-01..03: TournamentParticipation.foot_context DB constraint
# ---------------------------------------------------------------------------

class TestTournamentParticipationFootContextConstraint:
    """
    These tests insert raw SQL into tournament_participations to verify
    the CHECK constraint at the DB level.  The test_db fixture wraps
    everything in a SAVEPOINT that is rolled back after each test.
    """

    def _insert_participation(self, db, foot_context_value: str, user_id: int, semester_id: int):
        from sqlalchemy import text
        db.execute(text(
            "INSERT INTO tournament_participations "
            "(user_id, semester_id, placement, xp_awarded, credits_awarded, foot_context) "
            "VALUES (:uid, :sid, 1, 0, 0, :fc)"
        ), {"uid": user_id, "sid": semester_id, "fc": foot_context_value})
        db.flush()

    def _make_minimal_user_and_semester(self, db):
        """Create the minimum records needed for a participation FK."""
        from sqlalchemy import text
        import uuid
        email = f"fc-test-{uuid.uuid4().hex[:8]}@test.com"
        db.execute(text(
            "INSERT INTO users "
            "(email, name, password_hash, role, is_active, "
            "payment_verified, credit_balance, credit_purchased, "
            "xp_balance, nda_accepted, parental_consent) "
            "VALUES (:e, 'FC Test', 'x', 'STUDENT', true, "
            "false, 0, 0, 0, false, false)"
        ), {"e": email})
        user_id = db.execute(text(
            "SELECT id FROM users WHERE email = :e"
        ), {"e": email}).scalar()

        code = f"FC-SEM-{uuid.uuid4().hex[:8]}"
        db.execute(text(
            "INSERT INTO semesters (code, name, semester_category, status, "
            "specialization_type, start_date, end_date, enrollment_cost) "
            "VALUES (:c, 'FC Sem', 'MINI_SEASON', 'ONGOING', "
            "'LFA_FOOTBALL_PLAYER', '2026-01-01', '2026-12-31', 0)"
        ), {"c": code})
        semester_id = db.execute(text(
            "SELECT id FROM semesters WHERE code = :c"
        ), {"c": code}).scalar()

        db.flush()
        return user_id, semester_id

    def test_fc01_default_neutral_when_omitted(self, test_db):
        """FC-01: INSERT without foot_context → DB DEFAULT 'neutral' applied."""
        from sqlalchemy import text
        uid, sid = self._make_minimal_user_and_semester(test_db)
        test_db.execute(text(
            "INSERT INTO tournament_participations "
            "(user_id, semester_id, placement, xp_awarded, credits_awarded) "
            "VALUES (:uid, :sid, 1, 0, 0)"
        ), {"uid": uid, "sid": sid})
        test_db.flush()
        row = test_db.execute(text(
            "SELECT foot_context FROM tournament_participations "
            "WHERE user_id = :uid AND semester_id = :sid"
        ), {"uid": uid, "sid": sid}).fetchone()
        assert row[0] == "neutral"

    def test_fc02_invalid_foot_context_rejected(self, test_db):
        """FC-02: INSERT foot_context='invalid' → IntegrityError from CHECK constraint."""
        uid, sid = self._make_minimal_user_and_semester(test_db)
        with pytest.raises(IntegrityError):
            self._insert_participation(test_db, "invalid", uid, sid)

    def test_fc03_valid_values_accepted(self, test_db):
        """FC-03: 'right', 'left', 'neutral' all accepted without error."""
        import uuid
        from sqlalchemy import text
        for ctx in ("right", "left", "neutral"):
            uid, sid = self._make_minimal_user_and_semester(test_db)
            self._insert_participation(test_db, ctx, uid, sid)
            row = test_db.execute(text(
                "SELECT foot_context FROM tournament_participations "
                "WHERE user_id = :uid AND semester_id = :sid"
            ), {"uid": uid, "sid": sid}).fetchone()
            assert row[0] == ctx


# ---------------------------------------------------------------------------
# FC-05..07: UserLicense right/left foot score CHECK constraints
# ---------------------------------------------------------------------------

class TestFootScoreCheckConstraints:

    def _make_minimal_user(self, db):
        from sqlalchemy import text
        import uuid
        email = f"fs-test-{uuid.uuid4().hex[:8]}@test.com"
        db.execute(text(
            "INSERT INTO users "
            "(email, name, password_hash, role, is_active, "
            "payment_verified, credit_balance, credit_purchased, "
            "xp_balance, nda_accepted, parental_consent) "
            "VALUES (:e, 'FS Test', 'x', 'STUDENT', true, "
            "false, 0, 0, 0, false, false)"
        ), {"e": email})
        uid = db.execute(text(
            "SELECT id FROM users WHERE email = :e"
        ), {"e": email}).scalar()
        db.flush()
        return uid

    def _insert_license(self, db, uid, right_score, left_score):
        from sqlalchemy import text
        db.execute(text(
            "INSERT INTO user_licenses "
            "(user_id, specialization_type, is_active, onboarding_completed, "
            "payment_verified, credit_balance, credit_purchased, "
            "current_level, max_achieved_level, renewal_cost, "
            "started_at, right_foot_score, left_foot_score) "
            "VALUES (:uid, 'LFA_FOOTBALL_PLAYER', true, false, false, 0, 0, "
            "1, 1, 0, now(), :r, :l)"
        ), {"uid": uid, "r": right_score, "l": left_score})
        db.flush()

    def test_fc05_null_foot_scores_allowed(self, test_db):
        """FC-05: NULL right/left foot scores pass constraint (not assessed yet)."""
        uid = self._make_minimal_user(test_db)
        self._insert_license(test_db, uid, None, None)  # must not raise

    def test_fc05b_valid_range_accepted(self, test_db):
        """FC-05b: scores within 0–100 accepted."""
        uid = self._make_minimal_user(test_db)
        self._insert_license(test_db, uid, 68.0, 32.0)  # must not raise

    def test_fc05c_boundary_0_100_accepted(self, test_db):
        """FC-05c: boundary values 0 and 100 accepted."""
        uid = self._make_minimal_user(test_db)
        self._insert_license(test_db, uid, 0.0, 100.0)

    def test_fc06_right_foot_over_100_rejected(self, test_db):
        """FC-06: right_foot_score=101.0 → IntegrityError (CHECK constraint)."""
        uid = self._make_minimal_user(test_db)
        with pytest.raises(IntegrityError):
            self._insert_license(test_db, uid, 101.0, 50.0)

    def test_fc07_left_foot_negative_rejected(self, test_db):
        """FC-07: left_foot_score=-1.0 → IntegrityError (CHECK constraint)."""
        uid = self._make_minimal_user(test_db)
        with pytest.raises(IntegrityError):
            self._insert_license(test_db, uid, 50.0, -1.0)
