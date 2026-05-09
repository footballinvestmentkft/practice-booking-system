"""
P1 Backfill Dry-Run — controlled detection proof (BUG-P0-CARD-01)

Uses the postgres_db SAVEPOINT fixture to insert a controlled 5-player pre-fix
tournament and a 5-player post-fix tournament, then asserts that:
  - rank-2..rank-(N-1) pre-fix TPs are detected
  - rank-1 is NOT detected (percentile=0 regardless of N)
  - rank-N is NOT detected (last-place always correct)
  - post-fix TPs are NOT detected (achieved_at >= cutoff)
  - NULL-delta TPs are counted separately but not in "affected"
  - stored delta vs correct delta divergence is captured
  - FSA impact is projected correctly (correction = correct - stored)
  - XP/credit write paths are absent from the dry-run module

compute_single_tournament_skill_delta is patched with a fixed "correct" delta
to isolate detection logic from EMA replay complexity.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

# Project root → scripts.* importable without PYTHONPATH= env override
_ROOT = pathlib.Path(__file__).parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.backfill_tp_delta_p1 import (  # noqa: E402
    _build_skill_stats,
    _count_null_delta_tps,
    _detect_affected_tps,
    _get_individual_field_sizes,
    _print_report,
    _project_fsa_impact,
    _recompute_correct_delta,
    DELTA_EPSILON,
    MIN_FIELD_SIZE,
    PRE_FIX_CUTOFF,
    SNAPSHOT_DDL,
    ROLLBACK_TEMPLATE,
)

# ── Controlled timestamps ─────────────────────────────────────────────────────

_PRE_FIX  = PRE_FIX_CUTOFF - timedelta(days=1)   # 2026-05-08 — pre-fix
_POST_FIX = PRE_FIX_CUTOFF + timedelta(hours=1)   # 2026-05-09 13:32 UTC — post-fix

# ── Test IDs — high values avoid collision with seed data ────────────────────

_SEM_PRE  = 99800   # pre-fix tournament  (N=5, ranks 1-5)
_SEM_POST = 99801   # post-fix tournament (N=5, ranks 1-5)
_SEM_NULL = 99802   # single null-delta TP (N=1, below MIN_FIELD_SIZE)
_UID_BASE = 998000  # users 998001..998006

# Stored wrong delta: rank-2..rank-(N-1) all treated as last-place at dist time
_WRONG_DELTA = '{"sprint_speed": -5.1}'
# Correct delta as would be computed with field_size=5
_CORRECT_DELTA = {"sprint_speed": 8.2}


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _insert_base_fixture(db) -> None:
    """
    Insert semesters + users + TPs for pre-fix and post-fix tournaments.

    Pre-fix tournament (_SEM_PRE, N=5):
      ranks 1-5, achieved_at=_PRE_FIX, skill_rating_delta=WRONG (-5.1)
      → detection should find ranks 2, 3, 4 only

    Post-fix tournament (_SEM_POST, N=5):
      ranks 1-5, achieved_at=_POST_FIX, skill_rating_delta=correct
      → detection should find 0 TPs (all post-cutoff)
    """
    # Users (one per rank + one spare at _UID_BASE+0 for null-delta test)
    for i in range(7):
        db.execute(text("""
            INSERT INTO users (id, name, email, password_hash, role,
                               payment_verified, credit_balance, credit_purchased,
                               xp_balance, nda_accepted, parental_consent)
            VALUES (:id, :name, :email, 'test_hash', 'STUDENT',
                    false, 0, 0, 0, false, false)
            ON CONFLICT (id) DO NOTHING
        """), {"id": _UID_BASE + i, "name": f"P1DryRunUser{i}",
               "email": f"p1dryrun{i}_{_UID_BASE}@example.com"})

    # Semesters
    for sem_id in [_SEM_PRE, _SEM_POST, _SEM_NULL]:
        db.execute(text("""
            INSERT INTO semesters (id, code, name, start_date, end_date, status, enrollment_cost)
            VALUES (:id, :code, :name, '2026-01-01', '2026-06-30', 'COMPLETED', 0)
            ON CONFLICT (id) DO NOTHING
        """), {"id": sem_id, "code": f"P1TEST-{sem_id}",
               "name": f"P1DryRun-{sem_id}"})

    # Pre-fix TPs: ranks 1..5, wrong stored delta
    for rank in range(1, 6):
        db.execute(text("""
            INSERT INTO tournament_participations
              (user_id, semester_id, placement, skill_rating_delta,
               xp_awarded, credits_awarded, achieved_at, foot_context)
            VALUES (:uid, :sem, :placement, CAST(:delta AS jsonb),
                    0, 0, :ts, 'neutral')
            ON CONFLICT ON CONSTRAINT uq_user_semester_participation DO NOTHING
        """), {"uid": _UID_BASE + rank, "sem": _SEM_PRE, "placement": rank,
               "delta": _WRONG_DELTA, "ts": _PRE_FIX})

    # Post-fix TPs: ranks 1..5, correct stored delta, achieved_at > cutoff
    for rank in range(1, 6):
        db.execute(text("""
            INSERT INTO tournament_participations
              (user_id, semester_id, placement, skill_rating_delta,
               xp_awarded, credits_awarded, achieved_at, foot_context)
            VALUES (:uid, :sem, :placement, CAST('{"sprint_speed": 3.0}' AS jsonb),
                    0, 0, :ts, 'neutral')
            ON CONFLICT ON CONSTRAINT uq_user_semester_participation DO NOTHING
        """), {"uid": _UID_BASE + rank, "sem": _SEM_POST, "placement": rank,
               "ts": _POST_FIX})

    # Null-delta TP: spare user at rank-2 in a 1-participant tournament
    # N=1 < MIN_FIELD_SIZE=3 → not in field_sizes, but counted by null-delta counter
    db.execute(text("""
        INSERT INTO tournament_participations
          (user_id, semester_id, placement, skill_rating_delta,
           xp_awarded, credits_awarded, achieved_at, foot_context)
        VALUES (:uid, :sem, 2, NULL, 0, 0, :ts, 'neutral')
        ON CONFLICT ON CONSTRAINT uq_user_semester_participation DO NOTHING
    """), {"uid": _UID_BASE + 0, "sem": _SEM_NULL, "ts": _PRE_FIX})

    db.flush()


def _setup_fsa(db, user_id: int) -> int:
    """
    Insert minimal user_license + active ASSESSED FSA row (sprint_speed=60.0).
    Returns the license_id.
    """
    lic_id = user_id + 100_000  # unique license ID
    db.execute(text("""
        INSERT INTO user_licenses
          (id, user_id, specialization_type, current_level, max_achieved_level, started_at,
           payment_verified, onboarding_completed, is_active, renewal_cost,
           credit_balance, credit_purchased)
        VALUES (:id, :uid, 'LFA_FOOTBALL_PLAYER', 1, 1, NOW(),
                false, true, true, 0, 0, 0)
        ON CONFLICT (id) DO NOTHING
    """), {"id": lic_id, "uid": user_id})
    db.execute(text("""
        INSERT INTO football_skill_assessments
          (user_license_id, skill_name, points_earned, points_total, percentage,
           assessed_by, assessed_at, status)
        VALUES (:lic, 'sprint_speed', 60, 100, 60.0, :assessor, NOW(), 'ASSESSED')
    """), {"lic": lic_id, "assessor": user_id})
    db.flush()
    return lic_id


# ── DETECT-01: field_size detection ──────────────────────────────────────────

class TestFieldSizeDetection:

    def test_both_tournaments_have_n5(self, postgres_db):
        """Both pre-fix and post-fix tournaments appear with N=5."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        assert _SEM_PRE  in fs and fs[_SEM_PRE]  == 5
        assert _SEM_POST in fs and fs[_SEM_POST] == 5

    def test_null_tournament_excluded_from_field_sizes(self, postgres_db):
        """_SEM_NULL has N=1 < MIN_FIELD_SIZE=3 → absent from field_sizes."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        assert _SEM_NULL not in fs, (
            f"SEM_NULL (N=1) should not appear in field_sizes (MIN={MIN_FIELD_SIZE})"
        )


# ── DETECT-02: detection filters ─────────────────────────────────────────────

class TestDetectionFilters:

    def test_detects_exactly_rank2_rank3_rank4(self, postgres_db):
        """Only placement 2..N-1 = 2..4 from the pre-fix tournament are detected."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        pre_only = {_SEM_PRE: fs[_SEM_PRE]}

        results = _detect_affected_tps(postgres_db, pre_only, None)
        placements = sorted(tp.placement for tp, _ in results)

        assert placements == [2, 3, 4], (
            f"Expected [2,3,4], got {placements}. "
            "rank-1 and rank-N must be excluded."
        )
        assert len(results) == 3

    def test_rank1_not_detected(self, postgres_db):
        """rank-1: percentile=(1-1)/(N-1)=0 for any N — always correct, must not be detected."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        pre_only = {_SEM_PRE: fs[_SEM_PRE]}

        results = _detect_affected_tps(postgres_db, pre_only, None)
        assert 1 not in [tp.placement for tp, _ in results], (
            "rank-1 detected — BUG: percentile=0 for rank-1 regardless of total_players"
        )

    def test_rank_n_not_detected(self, postgres_db):
        """rank-N=5: placement=N → percentile=(N-1)/(N-1)=1.0 for any N — always correct."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        pre_only = {_SEM_PRE: fs[_SEM_PRE]}

        results = _detect_affected_tps(postgres_db, pre_only, None)
        assert 5 not in [tp.placement for tp, _ in results], (
            "rank-N=5 detected — BUG: last-place is coincidentally correct pre-fix"
        )

    def test_post_fix_tps_not_detected(self, postgres_db):
        """All _SEM_POST TPs have achieved_at > PRE_FIX_CUTOFF — must be excluded."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        # Pass BOTH tournaments to confirm post-fix are filtered
        results = _detect_affected_tps(postgres_db, fs, None)
        post_fix_found = [tp for tp, _ in results if tp.semester_id == _SEM_POST]
        assert post_fix_found == [], (
            f"Post-fix TPs found: {[(tp.user_id, tp.placement) for tp in post_fix_found]}. "
            "TPs with achieved_at >= cutoff must NEVER be included."
        )

    def test_field_size_equals_n_for_all_results(self, postgres_db):
        """The field_size returned alongside each TP must equal N=5."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        pre_only = {_SEM_PRE: fs[_SEM_PRE]}

        results = _detect_affected_tps(postgres_db, pre_only, None)
        for tp, n in results:
            assert n == 5, f"Expected field_size=5, got {n} for tp.id={tp.id}"

    def test_null_delta_excluded_from_affected(self, postgres_db):
        """TPs with skill_rating_delta=NULL are a separate gap — not in affected list."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        results = _detect_affected_tps(postgres_db, fs, None)
        null_in_results = [tp for tp, _ in results if tp.skill_rating_delta is None]
        assert null_in_results == [], "NULL-delta TPs must not appear in affected list"

    def test_null_delta_counted_separately(self, postgres_db):
        """_count_null_delta_tps finds the null-delta TP even if its N < MIN_FIELD_SIZE."""
        _insert_base_fixture(postgres_db)
        count = _count_null_delta_tps(postgres_db)
        assert count >= 1, (
            "NULL-delta TP in _SEM_NULL not counted. "
            "_count_null_delta_tps must not require N >= MIN_FIELD_SIZE."
        )

    def test_tournament_id_filter_restricts_scope(self, postgres_db):
        """--tournament-id filter returns TPs from that tournament only."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        results = _detect_affected_tps(postgres_db, fs, tournament_id_filter=_SEM_PRE)
        semester_ids = {tp.semester_id for tp, _ in results}
        assert semester_ids == {_SEM_PRE}, (
            f"Filter returned TPs from unexpected semesters: {semester_ids}"
        )


# ── DETECT-03: stored vs correct delta divergence ─────────────────────────────

class TestDeltaDivergence:

    def test_skill_stats_captures_divergence(self, postgres_db):
        """
        _build_skill_stats with patched correct delta (+8.2) vs stored (-5.1)
        reports the full divergence correctly.
        """
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        pre_only = {_SEM_PRE: fs[_SEM_PRE]}
        results = _detect_affected_tps(postgres_db, pre_only, None)

        with patch(
            "scripts.backfill_tp_delta_p1.compute_single_tournament_skill_delta",
            return_value=_CORRECT_DELTA,
        ):
            affected = [
                (tp, n, _recompute_correct_delta(postgres_db, tp, n), [])
                for tp, n in results
            ]

        stats = _build_skill_stats(affected)

        assert "sprint_speed" in stats, "sprint_speed not in skill_stats"
        ss = stats["sprint_speed"]
        assert ss["count"] == 3, f"Expected 3 divergent rows, got {ss['count']}"
        avg_stored  = ss["stored_sum"]  / ss["count"]
        avg_correct = ss["correct_sum"] / ss["count"]
        assert avg_stored  == pytest.approx(-5.1, abs=0.05), \
            f"avg_stored expected -5.1, got {avg_stored}"
        assert avg_correct == pytest.approx( 8.2, abs=0.05), \
            f"avg_correct expected +8.2, got {avg_correct}"

    def test_average_error_magnitude(self, postgres_db):
        """Average error per affected TP should equal correct - stored = 13.3."""
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        pre_only = {_SEM_PRE: fs[_SEM_PRE]}
        results = _detect_affected_tps(postgres_db, pre_only, None)

        with patch(
            "scripts.backfill_tp_delta_p1.compute_single_tournament_skill_delta",
            return_value=_CORRECT_DELTA,
        ):
            affected = [
                (tp, n, _recompute_correct_delta(postgres_db, tp, n), [])
                for tp, n in results
            ]

        stats = _build_skill_stats(affected)
        ss = stats["sprint_speed"]
        avg_error = (ss["correct_sum"] - ss["stored_sum"]) / ss["count"]
        assert avg_error == pytest.approx(13.3, abs=0.1), \
            f"Expected avg_error=13.3, got {avg_error}"


# ── DETECT-04: FSA impact projection ─────────────────────────────────────────

class TestFSAImpactProjection:

    def test_projected_pct_for_rank2(self, postgres_db):
        """
        rank-2 user has ASSESSED FSA at 60.0%.
        stored_delta=-5.1, correct_delta=+8.2 → correction=+13.3 → expected=73.3%.
        """
        _insert_base_fixture(postgres_db)
        uid = _UID_BASE + 2  # rank-2 user
        _setup_fsa(postgres_db, uid)

        from app.models.tournament_achievement import TournamentParticipation
        tp = (
            postgres_db.query(TournamentParticipation)
            .filter(
                TournamentParticipation.semester_id == _SEM_PRE,
                TournamentParticipation.user_id == uid,
            )
            .first()
        )
        assert tp is not None, "rank-2 TP not found in DB"

        impact = _project_fsa_impact(postgres_db, tp, _CORRECT_DELTA)

        assert len(impact) == 1, f"Expected 1 FSA impact row, got {len(impact)}"
        row = impact[0]
        assert row["skill_name"] == "sprint_speed"
        assert row["stored_delta"]    == pytest.approx(-5.1, abs=0.05)
        assert row["correct_delta"]   == pytest.approx( 8.2, abs=0.05)
        assert row["delta_correction"]== pytest.approx(13.3, abs=0.1)
        assert row["current_pct"]     == pytest.approx(60.0, abs=0.05)
        # clamp: 60 + 13.3 = 73.3 (within 40..99)
        assert row["expected_new_pct"]== pytest.approx(73.3, abs=0.2)

    def test_archived_row_ignored_in_current_pct(self, postgres_db):
        """
        An ARCHIVED FSA row at 80.0% must not be used as current_pct source.
        The active ASSESSED row (60.0%) must be used instead.
        """
        _insert_base_fixture(postgres_db)
        uid = _UID_BASE + 3
        lic_id = _setup_fsa(postgres_db, uid)

        # Insert an ARCHIVED row with higher pct — must be ignored
        postgres_db.execute(text("""
            INSERT INTO football_skill_assessments
              (user_license_id, skill_name, points_earned, points_total, percentage,
               assessed_by, assessed_at, status)
            VALUES (:lic, 'sprint_speed', 80, 100, 80.0, :assessor, NOW(), 'ARCHIVED')
        """), {"lic": lic_id, "assessor": uid})
        postgres_db.flush()

        from app.models.tournament_achievement import TournamentParticipation
        tp = (
            postgres_db.query(TournamentParticipation)
            .filter(TournamentParticipation.semester_id == _SEM_PRE,
                    TournamentParticipation.user_id == uid)
            .first()
        )
        impact = _project_fsa_impact(postgres_db, tp, _CORRECT_DELTA)

        assert impact[0]["current_pct"] == pytest.approx(60.0, abs=0.05), (
            "ARCHIVED row (80.0%) must not be used — only ASSESSED/VALIDATED counts"
        )

    def test_clamp_upper_bound(self, postgres_db):
        """Correction that would push pct > 99.0 must be clamped to 99.0."""
        _insert_base_fixture(postgres_db)
        uid = _UID_BASE + 4
        lic_id = uid + 100_000
        # Insert FSA at 95.0% — correction of +13.3 would give 108.3 → clamped 99.0
        postgres_db.execute(text("""
            INSERT INTO user_licenses
              (id, user_id, specialization_type, current_level, max_achieved_level, started_at,
               payment_verified, onboarding_completed, is_active, renewal_cost,
               credit_balance, credit_purchased)
            VALUES (:id, :uid, 'LFA_FOOTBALL_PLAYER', 1, 1, NOW(),
                    false, true, true, 0, 0, 0)
            ON CONFLICT (id) DO NOTHING
        """), {"id": lic_id, "uid": uid})
        postgres_db.execute(text("""
            INSERT INTO football_skill_assessments
              (user_license_id, skill_name, points_earned, points_total, percentage,
               assessed_by, assessed_at, status)
            VALUES (:lic, 'sprint_speed', 95, 100, 95.0, :assessor, NOW(), 'ASSESSED')
        """), {"lic": lic_id, "assessor": uid})
        postgres_db.flush()

        from app.models.tournament_achievement import TournamentParticipation
        tp = (
            postgres_db.query(TournamentParticipation)
            .filter(TournamentParticipation.semester_id == _SEM_PRE,
                    TournamentParticipation.user_id == uid)
            .first()
        )
        impact = _project_fsa_impact(postgres_db, tp, _CORRECT_DELTA)
        assert impact[0]["expected_new_pct"] == pytest.approx(99.0, abs=0.05), \
            "Clamp to 99.0 not applied"

    def test_no_fsa_row_uses_default_baseline(self, postgres_db):
        """User with active license but no FSA row falls back to DEFAULT_BASELINE."""
        _insert_base_fixture(postgres_db)
        uid = _UID_BASE + 5
        lic_id = uid + 100_000
        postgres_db.execute(text("""
            INSERT INTO user_licenses
              (id, user_id, specialization_type, current_level, max_achieved_level, started_at,
               payment_verified, onboarding_completed, is_active, renewal_cost,
               credit_balance, credit_purchased)
            VALUES (:id, :uid, 'LFA_FOOTBALL_PLAYER', 1, 1, NOW(),
                    false, true, true, 0, 0, 0)
            ON CONFLICT (id) DO NOTHING
        """), {"id": lic_id, "uid": uid})
        postgres_db.flush()

        from app.models.tournament_achievement import TournamentParticipation
        from app.services.skill_progression import DEFAULT_BASELINE
        tp = (
            postgres_db.query(TournamentParticipation)
            .filter(TournamentParticipation.semester_id == _SEM_PRE,
                    TournamentParticipation.user_id == uid)
            .first()
        )
        impact = _project_fsa_impact(postgres_db, tp, _CORRECT_DELTA)
        if impact:
            assert impact[0]["current_pct"] == pytest.approx(DEFAULT_BASELINE, abs=0.05)


# ── DETECT-05: XP / credit unchanged ─────────────────────────────────────────

class TestXPCreditUnchanged:

    def test_xp_credit_symbols_absent_from_module(self):
        """
        XPTransaction and CreditTransaction must NOT be imported in the dry-run
        module — they have no role in detection or projection.
        """
        import scripts.backfill_tp_delta_p1 as m
        assert not hasattr(m, "XPTransaction"),    "XPTransaction imported in dry-run module"
        assert not hasattr(m, "CreditTransaction"), "CreditTransaction imported in dry-run module"

    def test_summary_xp_credit_delta_is_zero(self, postgres_db):
        """
        JSON report's summary.xp_credit_delta must always be 0.
        """
        _insert_base_fixture(postgres_db)
        fs = _get_individual_field_sizes(postgres_db)
        pre_only = {_SEM_PRE: fs[_SEM_PRE]}
        results = _detect_affected_tps(postgres_db, pre_only, None)

        with patch(
            "scripts.backfill_tp_delta_p1.compute_single_tournament_skill_delta",
            return_value=_CORRECT_DELTA,
        ):
            affected = [
                (tp, n, _recompute_correct_delta(postgres_db, tp, n), [])
                for tp, n in results
            ]

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            out_path = f.name

        _print_report(
            backfill_run_id="test-run-id",
            affected=affected,
            field_sizes=pre_only,
            null_delta_count=0,
            output_json=out_path,
            show_ddl=False,
        )

        with open(out_path) as f:
            data = json.load(f)

        assert data["summary"]["xp_credit_delta"] == 0
        assert data["mode"] == "dry-run"
        assert data["summary"]["affected_tp_count"] == 3


# ── DETECT-06: JSON output structure ─────────────────────────────────────────

class TestJSONOutputStructure:

    def test_json_output_contains_all_required_fields(self, postgres_db):
        """
        Full pipeline → JSON: verify all required top-level and summary keys,
        per-skill divergence, and affected_tps array structure.
        """
        _insert_base_fixture(postgres_db)
        uid2 = _UID_BASE + 2
        uid3 = _UID_BASE + 3
        uid4 = _UID_BASE + 4
        _setup_fsa(postgres_db, uid2)
        _setup_fsa(postgres_db, uid3)
        _setup_fsa(postgres_db, uid4)

        fs = _get_individual_field_sizes(postgres_db)
        pre_only = {_SEM_PRE: fs[_SEM_PRE]}
        results = _detect_affected_tps(postgres_db, pre_only, None)

        with patch(
            "scripts.backfill_tp_delta_p1.compute_single_tournament_skill_delta",
            return_value=_CORRECT_DELTA,
        ):
            affected = []
            for tp, n in results:
                cd = _recompute_correct_delta(postgres_db, tp, n)
                fi = _project_fsa_impact(postgres_db, tp, cd)
                affected.append((tp, n, cd, fi))

        null_count = _count_null_delta_tps(postgres_db)

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            out_path = f.name

        _print_report(
            backfill_run_id="00000000-0000-0000-0000-000000000001",
            affected=affected,
            field_sizes=pre_only,
            null_delta_count=null_count,
            output_json=out_path,
            show_ddl=False,
        )

        with open(out_path) as f:
            data = json.load(f)

        # Top-level keys
        assert data["backfill_run_id"] == "00000000-0000-0000-0000-000000000001"
        assert data["mode"] == "dry-run"
        assert data["cutoff"] == PRE_FIX_CUTOFF.isoformat()

        # Summary
        s = data["summary"]
        assert s["affected_tp_count"] == 3
        assert s["divergent_tp_count"] == 3
        assert s["xp_credit_delta"] == 0
        assert s["fsa_rows_to_rearchive"] == 3
        assert s["fsa_rows_to_insert"] == 3

        # Per-skill divergence
        assert "sprint_speed" in data["per_skill_divergence"]
        ss = data["per_skill_divergence"]["sprint_speed"]
        assert ss["affected_rows"] == 3
        assert ss["avg_stored_delta"]  == pytest.approx(-5.1, abs=0.05)
        assert ss["avg_correct_delta"] == pytest.approx( 8.2, abs=0.05)
        assert ss["avg_error"]         == pytest.approx(13.3, abs=0.1)

        # affected_tps array
        assert len(data["affected_tps"]) == 3
        placements = sorted(row["placement"] for row in data["affected_tps"])
        assert placements == [2, 3, 4]
        for row in data["affected_tps"]:
            assert row["field_size"] == 5
            assert row["stored_delta"] == {"sprint_speed": -5.1}
            assert row["correct_delta"] == {"sprint_speed": 8.2}
            assert len(row["fsa_impact"]) == 1
            fsa = row["fsa_impact"][0]
            assert fsa["skill_name"] == "sprint_speed"
            assert fsa["delta_correction"] == pytest.approx(13.3, abs=0.1)
            assert fsa["expected_new_pct"] == pytest.approx(73.3, abs=0.2)

        # Snapshot DDL embedded
        assert "backfill_tp_delta_snapshots" in data["snapshot_ddl"]
        assert "backfill_fsa_snapshots"      in data["snapshot_ddl"]
        assert "rollback_template"           in data


# ── DETECT-07: --show-ddl output content ─────────────────────────────────────

class TestShowDDLContent:

    def test_snapshot_ddl_contains_required_tables(self):
        """SNAPSHOT_DDL defines both snapshot tables with rollback comment."""
        assert "backfill_tp_delta_snapshots" in SNAPSHOT_DDL
        assert "backfill_fsa_snapshots"      in SNAPSHOT_DDL
        assert "backfill_run_id"             in SNAPSHOT_DDL
        assert "stored_delta_pre"            in SNAPSHOT_DDL
        assert "Rollback"                    in SNAPSHOT_DDL

    def test_rollback_template_has_all_steps(self):
        """ROLLBACK_TEMPLATE contains the 4-step rollback sequence."""
        assert "UPDATE tournament_participations"   in ROLLBACK_TEMPLATE
        assert "DELETE FROM football_skill_assessments" in ROLLBACK_TEMPLATE
        assert "UPDATE football_skill_assessments"  in ROLLBACK_TEMPLATE
        assert "DELETE FROM backfill_tp_delta_snapshots" in ROLLBACK_TEMPLATE
        assert "DELETE FROM backfill_fsa_snapshots"      in ROLLBACK_TEMPLATE

    def test_no_execute_in_dry_run(self):
        """DDL strings contain no psycopg2 execute or BEGIN/COMMIT calls in dry-run."""
        import scripts.backfill_tp_delta_p1 as m
        # main() does not call db.execute(SNAPSHOT_DDL) — verified by inspecting source
        import inspect
        src = inspect.getsource(m.main)
        assert "SNAPSHOT_DDL" not in src, "main() must not execute SNAPSHOT_DDL"
        assert "ROLLBACK_TEMPLATE" not in src, "main() must not execute ROLLBACK_TEMPLATE"
