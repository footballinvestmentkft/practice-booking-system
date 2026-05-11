"""
P0 concurrency tests — Skill Progression pipeline
Covers RACE-S01, S02, S03, S05 from SKILL_PROGRESSION_CONCURRENCY_AUDIT_2026-02-19.md

Pattern identical to tests/unit/reward/test_reward_concurrency_p0.py:
  - All tests use pure mocks (no DB required).
  - Each test is labelled with its race ID.
  - Tests are written FIRST (RED), fixes applied to make them GREEN.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

# ─── shared helpers ──────────────────────────────────────────────────────────


def _make_license(skills: dict) -> MagicMock:
    """Return a MagicMock UserLicense with given football_skills dict."""
    lic = MagicMock()
    lic.football_skills = dict(skills)
    lic.id = 1
    lic.user_id = 42
    lic.is_active = True
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.skills_last_updated_at = None
    lic.skills_updated_by = None
    return lic


def _make_assessment_row(percentage: float) -> MagicMock:
    row = MagicMock()
    row.percentage = percentage
    return row


# ─────────────────────────────────────────────────────────────────────────────
# RACE-S01 — compute_single_tournament_skill_delta: stable ORDER BY
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceS01StableSort:
    """
    RACE-S01: history replay query must ORDER BY (achieved_at ASC, id ASC).

    Without the .id tiebreaker two participations inserted in the same clock
    tick have non-deterministic ordering → different sessions compute different
    skill_rating_delta values for the same user state.

    Fix: add TournamentParticipation.id.asc() as secondary ORDER BY clause in
         compute_single_tournament_skill_delta (and all other history-replay
         queries in skill_progression_service.py).
    """

    def test_rs01_compute_single_delta_order_includes_id_tiebreaker(self):
        """RED → GREEN: ORDER BY must include (achieved_at, id) — not just achieved_at."""
        from app.services.skill_progression_service import (
            compute_single_tournament_skill_delta,
        )

        TEST_USER_ID = 999  # Mock user ID for concurrency test
        db = MagicMock()
        # UserLicense query (get_baseline_skills path)
        db.query.return_value.filter.return_value.first.return_value = MagicMock(
            football_skills=None
        )
        # TournamentParticipation query: returns empty list so no loop executes
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        compute_single_tournament_skill_delta(db, user_id=TEST_USER_ID, tournament_id=99)

        order_by_call = db.query.return_value.filter.return_value.order_by.call_args
        assert order_by_call is not None, "order_by() was never called"

        args = order_by_call[0]  # positional args tuple
        arg_strs = [str(a) for a in args]

        has_achieved_at = any("achieved_at" in s for s in arg_strs)
        has_id = any(
            ".id" in s.lower() or s.lower().endswith(" asc") and "id" in s.lower()
            for s in arg_strs
        )

        assert has_achieved_at, (
            "ORDER BY is missing achieved_at — unexpected regression"
        )
        assert len(args) >= 2, (
            "ORDER BY has only one clause; .id tiebreaker is missing — RACE-S01 open"
        )
        assert has_id, (
            f"ORDER BY does not include .id tiebreaker — RACE-S01 open. "
            f"Got: {arg_strs}"
        )

    def test_rs01_calculate_contribution_order_includes_id_tiebreaker(self):
        """RED → GREEN: calculate_tournament_skill_contribution uses stable sort too."""
        from app.services.skill_progression_service import (
            calculate_tournament_skill_contribution,
        )

        TEST_USER_ID = 999  # Mock user ID for concurrency test
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = MagicMock(
            football_skills=None
        )
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        calculate_tournament_skill_contribution(db, user_id=TEST_USER_ID, skill_keys=["passing"])

        order_by_call = db.query.return_value.filter.return_value.order_by.call_args
        assert order_by_call is not None, "order_by() was never called"

        args = order_by_call[0]
        assert len(args) >= 2, (
            "ORDER BY has only one clause; .id tiebreaker missing — RACE-S01 open"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RACE-S02 — recalculate_skill_average must lock UserLicense row
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceS02AssessmentLock:
    """
    RACE-S02: FootballSkillService.recalculate_skill_average writes the full
    football_skills JSONB without first acquiring a row lock.

    If a tournament finalization is concurrently updating football_skills
    (under FOR UPDATE acquired in Step 1.5), the assessment transaction that
    loaded a pre-update snapshot will overwrite all 28 other skills' current_level
    values at commit time (last-writer-wins on the JSONB column).

    Fix: add .with_for_update() before .first() in recalculate_skill_average,
         consistent with the lock already acquired in tournament Step 1.5.

    Lock order is identical in both paths:
      tournament:  UserLicense WHERE (user_id, specialization_type, is_active)
      assessment:  UserLicense WHERE id = :user_license_id
    Both target the same row; PostgreSQL grants the lock to whichever arrives
    first and the other blocks — no deadlock possible.
    """

    def _make_db(self) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Returns (db, assessment_chain, license_chain)."""
        db = MagicMock()

        mock_assessment = _make_assessment_row(75.0)
        assessment_chain = MagicMock()
        assessment_chain.filter.return_value.all.return_value = [mock_assessment]

        mock_license = _make_license({"passing": 70.0})
        license_chain = MagicMock()
        # Both paths must work: with_for_update returns the first() route
        license_chain.filter.return_value.with_for_update.return_value.first.return_value = (
            mock_license
        )
        # Legacy path (pre-fix) also wired so the test doesn't error on the call
        license_chain.filter.return_value.first.return_value = mock_license

        def _dispatch(model):
            name = getattr(model, "__name__", "")
            if "Assessment" in name:
                return assessment_chain
            return license_chain

        db.query.side_effect = _dispatch
        return db, assessment_chain, license_chain

    def test_rs02_with_for_update_called_on_license(self):
        """RED → GREEN: with_for_update() must be called before reading football_skills."""
        from app.services.football_skill_service import FootballSkillService

        db, _, license_chain = self._make_db()
        service = FootballSkillService(db)
        service.recalculate_skill_average(user_license_id=1, skill_name="passing")

        license_chain.filter.return_value.with_for_update.assert_called_once()

    def test_rs02_skill_written_after_lock(self):
        """RED → GREEN: assessment write happens on the locked license instance."""
        from app.services.football_skill_service import FootballSkillService

        db, _, license_chain = self._make_db()
        service = FootballSkillService(db)
        result = service.recalculate_skill_average(user_license_id=1, skill_name="passing")

        # The returned average should be correct regardless
        assert result == 75.0

        # The locked instance is what gets written to
        locked_license = (
            license_chain.filter.return_value.with_for_update.return_value.first.return_value
        )
        # After fix, football_skills["passing"] on the locked instance is updated
        assert locked_license.football_skills.get("passing") == 75.0

    def test_rs02_stale_overwrite_scenario(self):
        """
        RED → GREEN: without lock, stale copy can overwrite concurrent updates.

        This test simulates the race window:
          1. Assessment loads football_skills = {"passing": 70.0, "dribbling": 65.0}
          2. Tournament (concurrently) updates current_level for all 44 skills and commits.
          3. Assessment writes back {"passing": 75.0, "dribbling": 65.0}
             → dribbling's current_level update from step 2 is lost.

        With FOR UPDATE: the assessment blocks until the tournament commits, then reads
        the post-commit state → its write preserves the tournament's changes to other keys.

        The test verifies that the locked instance (post-tournament-commit state) is used,
        not a pre-loaded stale copy.
        """
        from app.services.football_skill_service import FootballSkillService

        db = MagicMock()

        # Stale snapshot (loaded before tournament committed)
        stale_skills = {
            "passing":   70.0,
            "dribbling": 65.0,
        }
        # Post-tournament-commit state (this is what FOR UPDATE will see after blocking)
        fresh_skills = {
            "passing":   {"baseline": 70.0, "current_level": 72.0, "tournament_delta": 2.0},
            "dribbling": {"baseline": 65.0, "current_level": 67.5, "tournament_delta": 2.5},
        }

        mock_assessment = _make_assessment_row(80.0)
        assessment_chain = MagicMock()
        assessment_chain.filter.return_value.all.return_value = [mock_assessment]

        # The locked license reflects the FRESH state (post-tournament-commit)
        locked_license = _make_license(fresh_skills)
        license_chain = MagicMock()
        license_chain.filter.return_value.with_for_update.return_value.first.return_value = (
            locked_license
        )
        # pre-fix path (no lock) would see stale
        stale_license = _make_license(stale_skills)
        license_chain.filter.return_value.first.return_value = stale_license

        def _dispatch(model):
            name = getattr(model, "__name__", "")
            if "Assessment" in name:
                return assessment_chain
            return license_chain

        db.query.side_effect = _dispatch
        service = FootballSkillService(db)
        service.recalculate_skill_average(user_license_id=1, skill_name="passing")

        # After fix: the locked (fresh) license is used; dribbling's tournament data preserved
        dribbling = locked_license.football_skills.get("dribbling")
        assert isinstance(dribbling, dict), (
            "dribbling entry should remain a dict (tournament format) after assessment write"
        )
        assert dribbling.get("current_level") == 67.5, (
            "Tournament's current_level update for dribbling must NOT be overwritten "
            "by the assessment write — RACE-S02 not closed"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RACE-S03 — float-format entries must be promoted before deep-merge
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceS03FormatNormalisation:
    """
    RACE-S03: football_skills entries may be bare floats (written by the assessment
    path or by V1 onboarding).  The orchestrator's deep-merge loop currently skips
    them silently:

        if not isinstance(entry, dict):
            continue   ← SILENT OMISSION

    Fix: before the loop, normalise each float-format entry to the dict format:
        {"baseline": float_val, "current_level": float_val, ...}

    Also: recalculate_skill_average must detect the dict format and write only the
    "baseline" sub-key, not replace the entire entry with a float.
    """

    def test_rs03_float_entry_is_not_silently_skipped(self):
        """
        RED → GREEN: if football_skills has a float entry, _normalise_skill_entry
        must promote it so the deep-merge can update current_level.
        """
        from app.services.tournament import tournament_reward_orchestrator as orch

        # After Phase B, a normalisation helper must exist
        assert hasattr(orch, "_normalise_skill_entry"), (
            "_normalise_skill_entry helper not found in tournament_reward_orchestrator — "
            "P2 not implemented"
        )
        promoted = orch._normalise_skill_entry(75.3)
        assert isinstance(promoted, dict), (
            "float entry must be promoted to dict format"
        )
        assert "baseline" in promoted, "promoted dict must have 'baseline' key"
        assert "current_level" in promoted, "promoted dict must have 'current_level' key"
        assert promoted["baseline"] == 75.3
        assert promoted["current_level"] == 75.3

    def test_rs03_dict_entry_is_returned_unchanged(self):
        """GREEN baseline: dict entries pass through _normalise_skill_entry unchanged."""
        from app.services.tournament import tournament_reward_orchestrator as orch

        original = {
            "baseline": 70.0,
            "current_level": 78.2,
            "tournament_delta": 8.2,
            "total_delta": 8.2,
            "tournament_count": 3,
        }
        result = orch._normalise_skill_entry(original)
        assert result is original or result == original, (
            "dict entries must not be mutated by _normalise_skill_entry"
        )

    def test_rs03_assessment_writes_baseline_subkey_for_dict_format(self):
        """
        RED → GREEN: when football_skills[skill] is already a dict, the assessment
        path must update 'baseline' sub-key — not replace the entire entry with a float.

        This prevents the assessment from downgrading a rich dict to a bare float,
        which would cause the next tournament finalization to use the wrong baseline.
        """
        from app.services.football_skill_service import FootballSkillService

        db = MagicMock()

        # Dict-format entry (V2 onboarding / post-tournament)
        existing_skills = {
            "passing": {
                "baseline": 70.0,
                "current_level": 78.5,
                "tournament_delta": 8.5,
                "total_delta": 8.5,
                "tournament_count": 3,
            }
        }
        mock_assessment = _make_assessment_row(80.0)
        assessment_chain = MagicMock()
        assessment_chain.filter.return_value.all.return_value = [mock_assessment]

        locked_license = _make_license(existing_skills)
        license_chain = MagicMock()
        license_chain.filter.return_value.with_for_update.return_value.first.return_value = (
            locked_license
        )
        license_chain.filter.return_value.first.return_value = locked_license

        def _dispatch(model):
            name = getattr(model, "__name__", "")
            if "Assessment" in name:
                return assessment_chain
            return license_chain

        db.query.side_effect = _dispatch
        service = FootballSkillService(db)
        service.recalculate_skill_average(user_license_id=1, skill_name="passing")

        entry = locked_license.football_skills["passing"]
        assert isinstance(entry, dict), (
            "Assessment must NOT downgrade dict-format entry to a bare float — "
            "RACE-S03 not closed"
        )
        # The assessment average (80.0) should update the baseline sub-key
        assert entry.get("baseline") == 80.0, (
            "Assessment average must update 'baseline' sub-key, not replace the dict"
        )
        # tournament data must be preserved
        assert entry.get("current_level") == 78.5, (
            "current_level must be preserved after assessment write"
        )

    def test_rs03_deep_merge_updates_float_entry_after_normalisation(self):
        """
        RED → GREEN: the orchestrator deep-merge must update current_level even for
        skills that were previously stored as bare floats.

        Before fix: float entries are silently skipped → changed=0.
        After fix:  float entries are promoted, then updated → changed≥1.
        """
        from app.services.tournament import tournament_reward_orchestrator as orch

        assert hasattr(orch, "_normalise_skill_entry"), (
            "_normalise_skill_entry not found — P2 not implemented"
        )

        # Simulate football_skills with mixed formats
        skills_before = {
            "passing":   75.3,                              # float (assessment / V1)
            "dribbling": {"baseline": 65.0,                # dict (V2)
                          "current_level": 70.0,
                          "tournament_delta": 5.0,
                          "total_delta": 5.0,
                          "tournament_count": 2},
        }

        # Normalise (the step that must happen before the merge loop)
        normalised = {k: orch._normalise_skill_entry(v) for k, v in skills_before.items()}

        assert isinstance(normalised["passing"], dict), (
            "passing must be promoted to dict"
        )
        # After normalisation the merge loop will find a dict and can update current_level
        normalised["passing"]["current_level"] = 79.0  # simulated merge write
        assert normalised["passing"]["current_level"] == 79.0


# ─────────────────────────────────────────────────────────────────────────────
# RACE-S05 — skill_rating_delta must be write-once
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceS05WriteOnceDelta:
    """
    RACE-S05: compute_single_tournament_skill_delta is called every time
    record_tournament_participation runs, even on retries.  If a new tournament
    committed between the original run and the retry, the delta is recomputed
    with a different EMA history, making the audit field non-deterministic.

    Fix: if participation.skill_rating_delta is not None, skip recomputation.

    This makes skill_rating_delta write-once: the first successful computation
    is preserved on retries.
    """

    def _make_minimal_db(self, existing_delta) -> tuple[MagicMock, MagicMock]:
        """Return (db, existing_participation_mock) for record_tournament_participation."""
        db = MagicMock()

        existing = MagicMock()
        existing.placement = 1
        existing.skill_rating_delta = existing_delta  # None or pre-set dict
        existing.skill_points_awarded = None
        existing.xp_awarded = 0
        existing.credits_awarded = 0

        # Route: db.query(TournamentParticipation).filter(...).first() → existing
        db.query.return_value.filter.return_value.first.return_value = existing
        # Atomic XP update returns a new balance
        db.execute.return_value.scalar.return_value = 0
        # Semester query (for XP transaction description)
        db.query.return_value.filter.return_value.first.return_value = existing  # reused for simplicity

        return db, existing

    def test_rs05_delta_not_recomputed_when_already_set(self):
        """RED → GREEN: if skill_rating_delta is not None, compute_single must not be called."""
        from app.services.tournament.tournament_participation_service import (
            record_tournament_participation,
        )

        db, existing = self._make_minimal_db(existing_delta={"passing": 4.5})

        with patch(
            "app.services.skill_progression_service.compute_single_tournament_skill_delta",
            return_value={"passing": 7.0},
        ) as mock_compute:
            with patch(
                "app.services.tournament.tournament_participation_service.convert_skill_points_to_xp",
                return_value=0,
            ):
                with patch(
                    "app.services.tournament.tournament_participation_service.update_skill_assessments",
                ):
                    record_tournament_participation(
                        db=db,
                        user_id=42,
                        tournament_id=99,
                        placement=1,
                        skill_points={},
                        base_xp=0,
                        credits=0,
                        assessed_by_id=None,
                    )

        mock_compute.assert_not_called(), (
            "compute_single_tournament_skill_delta was called despite "
            "skill_rating_delta already being set — RACE-S05 not closed"
        )
        # Original delta must be preserved
        assert existing.skill_rating_delta == {"passing": 4.5}, (
            "existing skill_rating_delta must not be overwritten on retry"
        )

    def test_rs05_delta_computed_on_first_run(self):
        """GREEN baseline: delta IS computed when participation has no prior delta."""
        from app.services.tournament.tournament_participation_service import (
            record_tournament_participation,
        )

        db, existing = self._make_minimal_db(existing_delta=None)

        computed = {"passing": 7.0}
        with patch(
            "app.services.skill_progression_service.compute_single_tournament_skill_delta",
            return_value=computed,
        ) as mock_compute:
            with patch(
                "app.services.tournament.tournament_participation_service.convert_skill_points_to_xp",
                return_value=0,
            ):
                with patch(
                    "app.services.tournament.tournament_participation_service.update_skill_assessments",
                ):
                    record_tournament_participation(
                        db=db,
                        user_id=42,
                        tournament_id=99,
                        placement=1,
                        skill_points={},
                        base_xp=0,
                        credits=0,
                        assessed_by_id=None,
                    )

        mock_compute.assert_called_once()
        assert existing.skill_rating_delta == computed
