"""
P1 Backfill Write-Mode — functional + boundary + guard tests

Tests first (failing until write-mode is implemented in scripts/backfill_tp_delta_p1.py).

Coverage:
  T-W-01..07  Functional write-mode (per-tournament transaction, idempotency, rollback SQL)
  T-B-01..03  Cutoff boundary (exact timestamp: 2026-05-09T10:44:59Z)
  T-G-01..08  Guard layer (CLI flags, UUID, JSON, table existence, count mismatch, cap, rebackfill)

All tests use the postgres_db SAVEPOINT fixture.  The outer transaction is rolled back
at teardown so no rows escape to the shared DB.

Scope enforced:
  - Only tournament_participations.skill_rating_delta is modified
  - XP/credit tables never touched
  - FootballSkillAssessment rows never touched
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple
from unittest.mock import patch

import pytest
from sqlalchemy import text

_ROOT = pathlib.Path(__file__).parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.backfill_tp_delta_p1 import (  # noqa: E402
    PRE_FIX_CUTOFF,
    MIN_FIELD_SIZE,
    _get_individual_field_sizes,
    _detect_affected_tps,
    # Write-mode functions (will fail until implemented)
    _AUDIT_TABLE,
    _SNAPSHOT_TABLE,
    _REQUIRED_WRITE_TABLES,
    _validate_write_cli_guards,
    _check_snapshot_tables_exist,
    _capture_xp_credit_baseline,
    _assert_xp_credit_unchanged,
    _guard_live_count,
    _guard_max_affected,
    _check_prior_backfills,
    _snapshot_tp_row,
    _audit_insert,
    _update_tp_delta,
    _write_tournament_batch,
    _generate_rollback_sql,
)
from app.models.tournament_achievement import TournamentParticipation

# ── Controlled timestamps ─────────────────────────────────────────────────────

_PRE_FIX      = PRE_FIX_CUTOFF - timedelta(days=1)         # definitely pre-fix
_AT_CUTOFF    = PRE_FIX_CUTOFF                              # exactly at cutoff (excluded)
_POST_FIX     = PRE_FIX_CUTOFF + timedelta(seconds=1)      # 1s after cutoff (excluded)

# ── Test IDs ──────────────────────────────────────────────────────────────────

_SEM_W  = 99810   # write-mode tournament (N=5)
_UID_W  = 999100  # user base (999101..999106)

_WRONG_DELTA   = {"sprint_speed": -5.1}
_CORRECT_DELTA = {"sprint_speed": 8.2}

# Valid UUID4 for tests
_RUN_ID_A = "550e8400-e29b-41d4-a716-446655440000"
_RUN_ID_B = "550e8400-e29b-41d4-a716-446655440001"


# ── DDL for write-mode tables ─────────────────────────────────────────────────

_WRITE_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS backfill_tp_delta_snapshots (
    id              SERIAL PRIMARY KEY,
    backfill_run_id UUID NOT NULL,
    tp_id           INTEGER NOT NULL REFERENCES tournament_participations(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL,
    semester_id     INTEGER NOT NULL,
    placement       INTEGER NOT NULL,
    field_size_used INTEGER NOT NULL,
    stored_delta_pre JSONB,
    correct_delta    JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS tp_delta_backfill_audit (
    id              SERIAL PRIMARY KEY,
    backfill_run_id UUID        NOT NULL,
    tp_id           INTEGER     NOT NULL REFERENCES tournament_participations(id) ON DELETE CASCADE,
    user_id         INTEGER     NOT NULL,
    tournament_id   INTEGER     NOT NULL,
    old_delta       JSONB,
    new_delta       JSONB,
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tp_backfill_audit
    ON tp_delta_backfill_audit (backfill_run_id, tp_id);
"""


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _ensure_write_tables(db) -> None:
    """Create snapshot + audit tables within the test transaction."""
    db.execute(text(_WRITE_TABLES_DDL))
    db.flush()


def _insert_write_fixture(db, *, achieved_at=None, n_players: int = 5) -> None:
    """Insert semester + users + TPs for the write-mode tournament (_SEM_W)."""
    if achieved_at is None:
        achieved_at = _PRE_FIX

    for i in range(n_players + 1):
        db.execute(text("""
            INSERT INTO users (id, name, email, password_hash, role,
                               payment_verified, credit_balance, credit_purchased,
                               xp_balance, nda_accepted, parental_consent)
            VALUES (:id, :name, :email, 'test_hash', 'STUDENT', false, 0, 0, 0, false, false)
            ON CONFLICT (id) DO NOTHING
        """), {"id": _UID_W + i, "name": f"WriteUser{i}",
               "email": f"write_test_{i}_{_UID_W}@example.com"})

    db.execute(text("""
        INSERT INTO semesters (id, code, name, start_date, end_date, status, enrollment_cost)
        VALUES (:id, :code, 'WriteMode Tournament', '2026-01-01', '2026-06-30', 'COMPLETED', 0)
        ON CONFLICT (id) DO NOTHING
    """), {"id": _SEM_W, "code": f"WRITE-{_SEM_W}"})

    for rank in range(1, n_players + 1):
        db.execute(text("""
            INSERT INTO tournament_participations
              (user_id, semester_id, placement, skill_rating_delta,
               xp_awarded, credits_awarded, achieved_at, foot_context)
            VALUES (:uid, :sem, :placement, CAST(:delta AS jsonb),
                    0, 0, :ts, 'neutral')
            ON CONFLICT ON CONSTRAINT uq_user_semester_participation DO NOTHING
        """), {
            "uid": _UID_W + rank, "sem": _SEM_W, "placement": rank,
            "delta": json.dumps(_WRONG_DELTA), "ts": achieved_at,
        })

    db.flush()


def _get_write_tps(db) -> List:
    """Return (tp, field_size, correct_delta) tuples for middle-rank pre-fix TPs."""
    fs = _get_individual_field_sizes(db)
    sem_fs = {_SEM_W: fs[_SEM_W]} if _SEM_W in fs else {}
    detected = _detect_affected_tps(db, sem_fs, tournament_id_filter=None)
    return [(tp, n, _CORRECT_DELTA) for tp, n in detected]


def _fetch_tp_delta(db, user_id: int, sem_id: int) -> Dict:
    row = db.execute(text(
        "SELECT skill_rating_delta FROM tournament_participations "
        "WHERE user_id = :uid AND semester_id = :sid"
    ), {"uid": user_id, "sid": sem_id}).fetchone()
    return row[0] if row else None


def _fetch_audit_rows(db, run_id: str, tp_id: int) -> List:
    return db.execute(text(
        "SELECT * FROM tp_delta_backfill_audit WHERE backfill_run_id = :rid AND tp_id = :tid"
    ), {"rid": run_id, "tid": tp_id}).fetchall()


def _make_args(**overrides) -> argparse.Namespace:
    """Build a minimal valid write-mode args namespace."""
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump({"summary": {"affected_tp_count": 3}}, f)
        report_path = f.name
    defaults = {
        "write": True,
        "confirm_backfill_run_id": _RUN_ID_A,
        "require_snapshot": True,
        "dry_run_report": report_path,
        "expected_affected_count": 3,
        "max_affected": None,
        "allow_rebackfill": False,
        "tournament_id": None,
        "output": None,
        "show_ddl": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# T-W: Functional write-mode tests
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestWriteModeFunctional:

    def test_tw01_rank2_tp_updated(self, postgres_db):
        """T-W-01: Pre-fix rank-2 TP gets correct delta after _write_tournament_batch."""
        _ensure_write_tables(postgres_db)
        _insert_write_fixture(postgres_db)
        postgres_db.commit()

        tps = _get_write_tps(postgres_db)
        assert len(tps) == 3, f"Expected 3 middle-rank TPs, got {len(tps)}"

        baseline = _capture_xp_credit_baseline(postgres_db)
        updated, skipped, err = _write_tournament_batch(
            postgres_db, _RUN_ID_A, _SEM_W, tps, baseline
        )

        assert err is None, f"Write-mode error: {err}"
        assert updated == 3
        assert skipped == 0

        # Verify rank-2 delta was updated
        rank2_uid = _UID_W + 2
        new_delta = _fetch_tp_delta(postgres_db, rank2_uid, _SEM_W)
        assert new_delta == _CORRECT_DELTA, (
            f"Rank-2 delta not updated: got {new_delta}, expected {_CORRECT_DELTA}"
        )

    def test_tw02_rank1_not_updated(self, postgres_db):
        """T-W-02: Rank-1 TP is never in affected list (detection filter); delta unchanged."""
        _ensure_write_tables(postgres_db)
        _insert_write_fixture(postgres_db)
        postgres_db.commit()

        tps = _get_write_tps(postgres_db)
        placements = [tp.placement for tp, *_ in tps]
        assert 1 not in placements, "Rank-1 must not be in affected list"

        baseline = _capture_xp_credit_baseline(postgres_db)
        _write_tournament_batch(postgres_db, _RUN_ID_A, _SEM_W, tps, baseline)

        rank1_delta = _fetch_tp_delta(postgres_db, _UID_W + 1, _SEM_W)
        assert rank1_delta == _WRONG_DELTA, (
            f"Rank-1 delta was modified — must not be touched"
        )

    def test_tw03_rank_n_not_updated(self, postgres_db):
        """T-W-03: Rank-N (last) TP is excluded from affected list; delta unchanged."""
        _ensure_write_tables(postgres_db)
        _insert_write_fixture(postgres_db)
        postgres_db.commit()

        tps = _get_write_tps(postgres_db)
        placements = [tp.placement for tp, *_ in tps]
        assert 5 not in placements, "Rank-N=5 must not be in affected list"

        baseline = _capture_xp_credit_baseline(postgres_db)
        _write_tournament_batch(postgres_db, _RUN_ID_A, _SEM_W, tps, baseline)

        rank_n_delta = _fetch_tp_delta(postgres_db, _UID_W + 5, _SEM_W)
        assert rank_n_delta == _WRONG_DELTA, (
            f"Rank-N delta was modified — must not be touched"
        )

    def test_tw04_post_fix_tp_not_updated(self, postgres_db):
        """T-W-04: TPs with achieved_at >= cutoff are excluded by detection; delta unchanged."""
        _ensure_write_tables(postgres_db)
        # Insert fixture with post-fix timestamp
        _insert_write_fixture(postgres_db, achieved_at=_POST_FIX)
        postgres_db.commit()

        fs = _get_individual_field_sizes(postgres_db)
        sem_fs = {_SEM_W: fs[_SEM_W]} if _SEM_W in fs else {}
        detected = _detect_affected_tps(postgres_db, sem_fs, tournament_id_filter=None)

        post_fix_found = [tp for tp, _ in detected if tp.semester_id == _SEM_W]
        assert post_fix_found == [], (
            f"Post-fix TPs detected — must be excluded: {[(tp.user_id, tp.placement) for tp in post_fix_found]}"
        )

    def test_tw05_xp_credit_unchanged(self, postgres_db):
        """T-W-05: XP and credit row counts are identical before and after write-mode."""
        _ensure_write_tables(postgres_db)
        _insert_write_fixture(postgres_db)
        postgres_db.commit()

        baseline = _capture_xp_credit_baseline(postgres_db)
        tps = _get_write_tps(postgres_db)
        _write_tournament_batch(postgres_db, _RUN_ID_A, _SEM_W, tps, baseline)

        # Assert raises nothing (counts match)
        _assert_xp_credit_unchanged(postgres_db, baseline)

    def test_tw06_rollback_sql_restores_delta(self, postgres_db):
        """T-W-06: Rollback SQL restores the original delta from old_delta in audit table."""
        _ensure_write_tables(postgres_db)
        _insert_write_fixture(postgres_db)
        postgres_db.commit()

        tps = _get_write_tps(postgres_db)
        baseline = _capture_xp_credit_baseline(postgres_db)
        _write_tournament_batch(postgres_db, _RUN_ID_A, _SEM_W, tps, baseline)

        # Verify update happened
        rank2_uid = _UID_W + 2
        assert _fetch_tp_delta(postgres_db, rank2_uid, _SEM_W) == _CORRECT_DELTA

        # Apply rollback SQL
        rollback_sql = _generate_rollback_sql(_RUN_ID_A)
        postgres_db.execute(text(rollback_sql))
        postgres_db.flush()

        # Verify delta is restored
        restored = _fetch_tp_delta(postgres_db, rank2_uid, _SEM_W)
        assert restored == _WRONG_DELTA, (
            f"Rollback SQL did not restore delta: got {restored}, expected {_WRONG_DELTA}"
        )

    def test_tw07_idempotent_second_run_same_run_id(self, postgres_db):
        """T-W-07: Second run with same run_id skips already-processed TPs; no error."""
        _ensure_write_tables(postgres_db)
        _insert_write_fixture(postgres_db)
        postgres_db.commit()

        tps = _get_write_tps(postgres_db)
        baseline = _capture_xp_credit_baseline(postgres_db)

        # First run
        updated1, skipped1, err1 = _write_tournament_batch(
            postgres_db, _RUN_ID_A, _SEM_W, tps, baseline
        )
        assert err1 is None
        assert updated1 == 3

        # Must re-fetch TPs since delta changed (for same objects)
        tps_second = _get_write_tps(postgres_db)
        # Middle-rank TPs have new delta now, so detection may return 0 (already correct)
        # But we call _write_tournament_batch directly with original tps
        updated2, skipped2, err2 = _write_tournament_batch(
            postgres_db, _RUN_ID_A, _SEM_W, tps, baseline
        )
        assert err2 is None, f"Second run with same run_id should not error: {err2}"
        assert updated2 == 0, "Second run must update 0 rows (idempotent)"
        assert skipped2 == 3, "Second run must skip all 3 already-processed TPs"

        # Audit row count unchanged
        audit_count = postgres_db.execute(text(
            "SELECT COUNT(*) FROM tp_delta_backfill_audit WHERE backfill_run_id = :rid"
        ), {"rid": _RUN_ID_A}).scalar()
        assert audit_count == 3, f"Audit row count should be 3, got {audit_count}"


# ═══════════════════════════════════════════════════════════════════════════════
# T-B: Cutoff boundary tests (2026-05-09T10:44:59Z exact)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCutoffBoundary:

    def _insert_single_tp(self, db, rank: int, achieved_at: datetime, sem_id: int) -> None:
        """Insert a single-tournament fixture with a controlled timestamp."""
        for i in range(6):
            db.execute(text("""
                INSERT INTO users (id, name, email, password_hash, role,
                                   payment_verified, credit_balance, credit_purchased,
                                   xp_balance, nda_accepted, parental_consent)
                VALUES (:id, :name, :email, 'test_hash', 'STUDENT', false, 0, 0, 0, false, false)
                ON CONFLICT (id) DO NOTHING
            """), {"id": _UID_W + i, "name": f"BoundaryUser{i}",
                   "email": f"boundary_{i}_{_UID_W}@example.com"})
        db.execute(text("""
            INSERT INTO semesters (id, code, name, start_date, end_date, status, enrollment_cost)
            VALUES (:id, :code, 'Boundary Tournament', '2026-01-01', '2026-06-30', 'COMPLETED', 0)
            ON CONFLICT (id) DO NOTHING
        """), {"id": sem_id, "code": f"BOUND-{sem_id}"})
        for r in range(1, 6):
            db.execute(text("""
                INSERT INTO tournament_participations
                  (user_id, semester_id, placement, skill_rating_delta,
                   xp_awarded, credits_awarded, achieved_at, foot_context)
                VALUES (:uid, :sem, :placement, CAST(:delta AS jsonb), 0, 0, :ts, 'neutral')
                ON CONFLICT ON CONSTRAINT uq_user_semester_participation DO NOTHING
            """), {
                "uid": _UID_W + r, "sem": sem_id, "placement": r,
                "delta": json.dumps(_WRONG_DELTA), "ts": achieved_at,
            })
        db.flush()

    def test_tb01_tp_before_cutoff_detected(self, postgres_db):
        """T-B-01: achieved_at = cutoff - 1s → TP IS detected (< strict)."""
        sem = 99815
        ts = PRE_FIX_CUTOFF - timedelta(seconds=1)
        self._insert_single_tp(postgres_db, rank=2, achieved_at=ts, sem_id=sem)

        fs = _get_individual_field_sizes(postgres_db)
        detected = _detect_affected_tps(postgres_db, {sem: fs[sem]}, None)
        sem_tps = [tp for tp, _ in detected if tp.semester_id == sem]
        assert len(sem_tps) >= 1, (
            f"TP at cutoff-1s must be detected; got {len(sem_tps)}"
        )

    def test_tb02_tp_at_cutoff_not_detected(self, postgres_db):
        """T-B-02: achieved_at = cutoff exactly → TP is NOT detected (strict <)."""
        sem = 99816
        ts = PRE_FIX_CUTOFF  # exactly at cutoff
        self._insert_single_tp(postgres_db, rank=2, achieved_at=ts, sem_id=sem)

        fs = _get_individual_field_sizes(postgres_db)
        sem_tps_in_fs = {sem: fs[sem]} if sem in fs else {}
        detected = _detect_affected_tps(postgres_db, sem_tps_in_fs, None)
        sem_tps = [tp for tp, _ in detected if tp.semester_id == sem]
        assert sem_tps == [], (
            f"TP at exact cutoff must NOT be detected (strict <): got {len(sem_tps)}"
        )

    def test_tb03_tp_after_cutoff_not_detected(self, postgres_db):
        """T-B-03: achieved_at = cutoff + 1s → TP is NOT detected."""
        sem = 99817
        ts = PRE_FIX_CUTOFF + timedelta(seconds=1)
        self._insert_single_tp(postgres_db, rank=2, achieved_at=ts, sem_id=sem)

        fs = _get_individual_field_sizes(postgres_db)
        sem_tps_in_fs = {sem: fs[sem]} if sem in fs else {}
        detected = _detect_affected_tps(postgres_db, sem_tps_in_fs, None)
        sem_tps = [tp for tp, _ in detected if tp.semester_id == sem]
        assert sem_tps == [], (
            f"Post-fix TP (cutoff+1s) must NOT be detected: got {len(sem_tps)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# T-G: Guard layer tests
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestWriteModeGuards:

    # ── G-1: missing required flags ──────────────────────────────────────────

    def test_tg01_missing_confirm_run_id_exits(self):
        """T-G-01: --write without --confirm-backfill-run-id → SystemExit."""
        args = _make_args(confirm_backfill_run_id=None)
        with pytest.raises(SystemExit):
            _validate_write_cli_guards(args)

    def test_tg01b_missing_require_snapshot_exits(self):
        """T-G-01b: --write without --require-snapshot → SystemExit."""
        args = _make_args(require_snapshot=False)
        with pytest.raises(SystemExit):
            _validate_write_cli_guards(args)

    def test_tg01c_missing_dry_run_report_exits(self):
        """T-G-01c: --write without --dry-run-report → SystemExit."""
        args = _make_args(dry_run_report=None)
        with pytest.raises(SystemExit):
            _validate_write_cli_guards(args)

    def test_tg01d_missing_expected_count_exits(self):
        """T-G-01d: --write without --expected-affected-count → SystemExit."""
        args = _make_args(expected_affected_count=None)
        with pytest.raises(SystemExit):
            _validate_write_cli_guards(args)

    # ── G-2: invalid UUID4 ───────────────────────────────────────────────────

    def test_tg02_invalid_uuid_format_exits(self):
        """T-G-02: Non-UUID4 string for --confirm-backfill-run-id → SystemExit."""
        args = _make_args(confirm_backfill_run_id="not-a-uuid")
        with pytest.raises(SystemExit):
            _validate_write_cli_guards(args)

    def test_tg02b_uuid1_not_accepted(self):
        """T-G-02b: UUID version 1 is not accepted (must be UUID4)."""
        import uuid
        uuid1 = str(uuid.uuid1())
        args = _make_args(confirm_backfill_run_id=uuid1)
        with pytest.raises(SystemExit):
            _validate_write_cli_guards(args)

    # ── G-3: missing snapshot tables ─────────────────────────────────────────

    def test_tg03_missing_snapshot_table_exits(self, postgres_db):
        """T-G-03: --require-snapshot with missing table → SystemExit (nulladik DB write)."""
        # Drop write-mode tables within the transaction so they don't exist for this test.
        # The outer postgres_db transaction rolls back at teardown, restoring the tables.
        postgres_db.execute(text("DROP TABLE IF EXISTS tp_delta_backfill_audit"))
        postgres_db.execute(text("DROP TABLE IF EXISTS backfill_tp_delta_snapshots"))
        postgres_db.flush()
        with pytest.raises(SystemExit):
            _check_snapshot_tables_exist(postgres_db)

    def test_tg03b_tables_present_does_not_exit(self, postgres_db):
        """T-G-03b: When tables exist, _check_snapshot_tables_exist does not raise."""
        _ensure_write_tables(postgres_db)
        _check_snapshot_tables_exist(postgres_db)  # must not raise

    # ── G-4: expected count mismatch ─────────────────────────────────────────

    def test_tg04_expected_count_mismatch_exits(self):
        """T-G-04: live_count ≠ expected_count → SystemExit before any write."""
        with pytest.raises(SystemExit):
            _guard_live_count(live_count=3, expected_count=99, report_count=3)

    def test_tg04b_report_count_mismatch_exits(self):
        """T-G-04b: dry-run report count ≠ live_count → SystemExit."""
        with pytest.raises(SystemExit):
            _guard_live_count(live_count=3, expected_count=3, report_count=5)

    def test_tg04c_all_counts_match_does_not_exit(self):
        """T-G-04c: live == expected == report → no SystemExit."""
        _guard_live_count(live_count=3, expected_count=3, report_count=3)  # must not raise

    # ── G-5: safety cap ──────────────────────────────────────────────────────

    def test_tg05_max_affected_exceeded_exits(self):
        """T-G-05: live_count > --max-affected → SystemExit."""
        with pytest.raises(SystemExit):
            _guard_max_affected(live_count=6, max_affected=5)

    def test_tg05b_max_affected_exact_boundary_allowed(self):
        """T-G-05b: live_count == max_affected → allowed (strictly greater triggers abort)."""
        _guard_max_affected(live_count=5, max_affected=5)  # must not raise

    def test_tg05c_max_affected_none_never_aborts(self):
        """T-G-05c: --max-affected not set (None) → no cap enforced."""
        _guard_max_affected(live_count=99999, max_affected=None)  # must not raise

    # ── G-6: rebackfill guard (prior audit row exists, different run_id) ─────

    def test_tg06_prior_backfill_guard_exits(self, postgres_db):
        """T-G-06: TP has audit row from prior run_id, no --allow-rebackfill → SystemExit."""
        _ensure_write_tables(postgres_db)
        _insert_write_fixture(postgres_db)
        postgres_db.commit()

        tps = _get_write_tps(postgres_db)
        assert tps, "No write TPs found"
        tp = tps[0][0]

        # Simulate a prior backfill with run_id_A
        postgres_db.execute(text("""
            INSERT INTO tp_delta_backfill_audit
              (backfill_run_id, tp_id, user_id, tournament_id, old_delta, new_delta)
            VALUES (:rid, :tp_id, :uid, :tid, CAST(:old AS jsonb), CAST(:new AS jsonb))
            ON CONFLICT DO NOTHING
        """), {
            "rid": _RUN_ID_A, "tp_id": tp.id, "uid": tp.user_id,
            "tid": tp.semester_id,
            "old": json.dumps(_WRONG_DELTA), "new": json.dumps(_CORRECT_DELTA),
        })
        postgres_db.flush()

        tp_ids = [tp.id for tp, *_ in tps]
        # run_id_B sees prior rows from run_id_A → abort
        with pytest.raises(SystemExit):
            _check_prior_backfills(postgres_db, tp_ids, run_id=_RUN_ID_B, allow_rebackfill=False)

    def test_tg07_allow_rebackfill_permits(self, postgres_db):
        """T-G-07: Prior audit row exists but --allow-rebackfill → no SystemExit."""
        _ensure_write_tables(postgres_db)
        _insert_write_fixture(postgres_db)
        postgres_db.commit()

        tps = _get_write_tps(postgres_db)
        assert tps
        tp = tps[0][0]

        postgres_db.execute(text("""
            INSERT INTO tp_delta_backfill_audit
              (backfill_run_id, tp_id, user_id, tournament_id, old_delta, new_delta)
            VALUES (:rid, :tp_id, :uid, :tid, CAST(:old AS jsonb), CAST(:new AS jsonb))
            ON CONFLICT DO NOTHING
        """), {
            "rid": _RUN_ID_A, "tp_id": tp.id, "uid": tp.user_id,
            "tid": tp.semester_id,
            "old": json.dumps(_WRONG_DELTA), "new": json.dumps(_CORRECT_DELTA),
        })
        postgres_db.flush()

        tp_ids = [tp.id for tp, *_ in tps]
        # --allow-rebackfill → must not raise
        _check_prior_backfills(postgres_db, tp_ids, run_id=_RUN_ID_B, allow_rebackfill=True)

    def test_tg06b_same_run_id_rerun_not_blocked(self, postgres_db):
        """T-G-06b: Same run_id as prior audit rows → NOT blocked (idempotent re-run)."""
        _ensure_write_tables(postgres_db)
        _insert_write_fixture(postgres_db)
        postgres_db.commit()

        tps = _get_write_tps(postgres_db)
        assert tps
        tp = tps[0][0]

        # Simulate run_id_A already having processed this TP
        postgres_db.execute(text("""
            INSERT INTO tp_delta_backfill_audit
              (backfill_run_id, tp_id, user_id, tournament_id, old_delta, new_delta)
            VALUES (:rid, :tp_id, :uid, :tid, CAST(:old AS jsonb), CAST(:new AS jsonb))
            ON CONFLICT DO NOTHING
        """), {
            "rid": _RUN_ID_A, "tp_id": tp.id, "uid": tp.user_id,
            "tid": tp.semester_id,
            "old": json.dumps(_WRONG_DELTA), "new": json.dumps(_CORRECT_DELTA),
        })
        postgres_db.flush()

        tp_ids = [tp.id for tp, *_ in tps]
        # Re-running with same run_id_A → must NOT block
        _check_prior_backfills(postgres_db, tp_ids, run_id=_RUN_ID_A, allow_rebackfill=False)

    # ── G-8: dry-run report parse error ──────────────────────────────────────

    def test_tg08_bad_json_dry_run_report_exits(self, tmp_path):
        """T-G-08: --dry-run-report with invalid JSON → SystemExit."""
        bad_report = tmp_path / "bad.json"
        bad_report.write_text("not-valid-json")
        args = _make_args(dry_run_report=str(bad_report))
        with pytest.raises(SystemExit):
            _validate_write_cli_guards(args)

    def test_tg08b_missing_report_file_exits(self):
        """T-G-08b: --dry-run-report pointing to non-existent file → SystemExit."""
        args = _make_args(dry_run_report="/nonexistent/path/report.json")
        with pytest.raises(SystemExit):
            _validate_write_cli_guards(args)
