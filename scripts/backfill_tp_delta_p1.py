#!/usr/bin/env python3
"""
P1 Backfill — BUG-P0-CARD-01 historical TP delta recompute

Detects TournamentParticipation rows whose skill_rating_delta was stored with
the wrong total_players value due to the sequential-distribution race condition
(fixed in bef0a7f, 2026-05-09). Recomputes the correct delta using
compute_single_tournament_skill_delta(field_size=N) and writes it back.

Scope (write-mode): ONLY TournamentParticipation.skill_rating_delta is modified.
FSA, football_skills, lateral_components, current_level: NOT part of this script.
XP/credit transactions and user balance fields: NEVER touched.

Usage — dry-run (no DB writes):
    PYTHONPATH=. python scripts/backfill_tp_delta_p1.py
    PYTHONPATH=. python scripts/backfill_tp_delta_p1.py --tournament-id 4600
    PYTHONPATH=. python scripts/backfill_tp_delta_p1.py --output report.json
    PYTHONPATH=. python scripts/backfill_tp_delta_p1.py --show-ddl

Usage — write-mode (all five flags required):
    PYTHONPATH=. python scripts/backfill_tp_delta_p1.py \\
        --write \\
        --confirm-backfill-run-id <uuid4> \\
        --require-snapshot \\
        --dry-run-report report.json \\
        --expected-affected-count <N> \\
        [--max-affected <N>] \\
        [--allow-rebackfill] \\
        [--output write_result.json]

Production write-mode requires separate manual approval and pre-created snapshot tables.
Run --show-ddl to get the DDL for all required tables.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.tournament_achievement import TournamentParticipation
from app.models.football_skill_assessment import FootballSkillAssessment
from app.models.license import UserLicense
from app.services.skill_progression import DEFAULT_BASELINE
from app.services.skill_progression._ema_engine import compute_single_tournament_skill_delta

# ── Constants ──────────────────────────────────────────────────────────────────

# Exact UTC timestamp of bef0a7f (2026-05-09T12:44:59+02:00 → UTC 10:44:59).
# TPs with achieved_at >= this cutoff were distributed post-fix: DO NOT TOUCH.
_P0_FIX_COMMIT_SHA = "bef0a7f5a2970467ee62513e4e2d419ec67770a2"
PRE_FIX_CUTOFF = datetime(2026, 5, 9, 10, 44, 59, tzinfo=timezone.utc)

# Bug only affects rank-2..rank-(N-1), which only exists when N >= 3.
MIN_FIELD_SIZE = 3

# Threshold below which a delta difference is considered negligible (float precision).
DELTA_EPSILON = 0.05

# Write-mode table names
_SNAPSHOT_TABLE = "backfill_tp_delta_snapshots"
_AUDIT_TABLE    = "tp_delta_backfill_audit"
_REQUIRED_WRITE_TABLES = [_SNAPSHOT_TABLE, _AUDIT_TABLE]


# ── Detection ─────────────────────────────────────────────────────────────────

def _get_individual_field_sizes(db: Session) -> Dict[int, int]:
    """
    Return {semester_id: participant_count} for all INDIVIDUAL tournaments
    that have at least MIN_FIELD_SIZE participants.

    Joins tournament_configurations to exclude TEAM tournaments
    (participant_type='TEAM'). Tournaments with no config row default to
    INDIVIDUAL (same logic as Semester.participant_type property).
    """
    rows = db.execute(text("""
        SELECT tp.semester_id, COUNT(*) AS n
        FROM tournament_participations tp
        LEFT JOIN tournament_configurations tc ON tc.semester_id = tp.semester_id
        WHERE (tc.participant_type IS NULL OR tc.participant_type != 'TEAM')
        GROUP BY tp.semester_id
        HAVING COUNT(*) >= :min_n
    """), {"min_n": MIN_FIELD_SIZE}).fetchall()
    return {row.semester_id: row.n for row in rows}


def _count_null_delta_tps(db: Session) -> int:
    """
    Count INDIVIDUAL pre-fix TPs with placement set but skill_rating_delta=NULL.
    These represent a separate gap (EMA never ran) — informational only, not
    touched by this backfill.
    """
    row = db.execute(text("""
        SELECT COUNT(*) AS cnt
        FROM tournament_participations tp
        LEFT JOIN tournament_configurations tc ON tc.semester_id = tp.semester_id
        WHERE (tc.participant_type IS NULL OR tc.participant_type != 'TEAM')
          AND tp.achieved_at < :cutoff
          AND tp.skill_rating_delta IS NULL
          AND tp.placement IS NOT NULL
    """), {"cutoff": PRE_FIX_CUTOFF}).fetchone()
    return row.cnt if row else 0


def _detect_affected_tps(
    db: Session,
    field_sizes: Dict[int, int],
    tournament_id_filter: Optional[int],
) -> List[Tuple[TournamentParticipation, int]]:
    """
    Return (tp, field_size) pairs for TPs that are candidates for correction:

    - Tournament is INDIVIDUAL with N >= MIN_FIELD_SIZE
    - placement BETWEEN 2 AND N-1 (rank-1 and rank-N are mathematically
      correct regardless of total_players; only the middle ranks are wrong)
    - achieved_at < PRE_FIX_CUTOFF (post-fix TPs are correct, must not be touched)
    - skill_rating_delta IS NOT NULL (NULL rows = separate gap, separate handling)
    """
    results: List[Tuple[TournamentParticipation, int]] = []

    for tid, n in field_sizes.items():
        if tournament_id_filter is not None and tid != tournament_id_filter:
            continue

        tps = (
            db.query(TournamentParticipation)
            .filter(
                TournamentParticipation.semester_id == tid,
                TournamentParticipation.placement >= 2,
                TournamentParticipation.placement <= n - 1,
                TournamentParticipation.achieved_at < PRE_FIX_CUTOFF,
                TournamentParticipation.skill_rating_delta.isnot(None),
            )
            .all()
        )
        for tp in tps:
            results.append((tp, n))

    return results


# ── Delta recompute ────────────────────────────────────────────────────────────

def _recompute_correct_delta(
    db: Session, tp: TournamentParticipation, field_size: int
) -> Dict[str, float]:
    """
    Replay the full EMA history with the correct field_size for this tournament.
    Pure read — compute_single_tournament_skill_delta does not write to DB.
    """
    return compute_single_tournament_skill_delta(
        db, tp.user_id, tp.semester_id, field_size=field_size
    ) or {}


# ── FSA impact projection ──────────────────────────────────────────────────────

def _project_fsa_impact(
    db: Session,
    tp: TournamentParticipation,
    correct_delta: Dict[str, float],
) -> List[Dict]:
    """
    For each skill where stored_delta != correct_delta, find the current active
    FootballSkillAssessment row and compute the expected corrected percentage.

    Note: this is a first-order approximation. The true correction requires
    rebuilding the full FSA accumulation chain (P3 scope). For dry-run reporting,
    this approximation shows the direction and magnitude of the error.

    Returns list of dicts with keys:
        skill_name, user_license_id, current_pct,
        stored_delta, correct_delta, delta_correction, expected_new_pct
    """
    stored_delta: Dict = tp.skill_rating_delta or {}
    all_skills = set(stored_delta.keys()) | set(correct_delta.keys())
    if not all_skills:
        return []

    license_row = (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id == tp.user_id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            UserLicense.is_active == True,
        )
        .order_by(UserLicense.id.desc())
        .first()
    )
    if not license_row:
        return []

    impact = []
    for skill in sorted(all_skills):
        s_delta = stored_delta.get(skill, 0.0)
        c_delta = correct_delta.get(skill, 0.0)
        if abs(s_delta - c_delta) < DELTA_EPSILON:
            continue  # negligible — not worth reporting

        fsa = (
            db.query(FootballSkillAssessment)
            .filter(
                FootballSkillAssessment.user_license_id == license_row.id,
                FootballSkillAssessment.skill_name == skill,
                FootballSkillAssessment.status.in_(["ASSESSED", "VALIDATED"]),
            )
            .order_by(FootballSkillAssessment.id.desc())
            .first()
        )
        current_pct = fsa.percentage if fsa else DEFAULT_BASELINE

        correction = c_delta - s_delta
        expected_pct = round(max(40.0, min(99.0, current_pct + correction)), 1)

        impact.append({
            "skill_name": skill,
            "user_license_id": license_row.id,
            "fsa_id": fsa.id if fsa else None,
            "current_pct": current_pct,
            "stored_delta": round(s_delta, 1),
            "correct_delta": round(c_delta, 1),
            "delta_correction": round(correction, 1),
            "expected_new_pct": expected_pct,
        })

    return impact


# ── Snapshot DDL ───────────────────────────────────────────────────────────────
# Printed when --show-ddl is passed. Never executed by this script.
# All tables are manual ops DDL — NOT application migrations (no alembic entry).
# Production: create manually, obtain explicit approval, then run write-mode.

SNAPSHOT_DDL = """
-- P1 Backfill snapshot + audit tables — execute MANUALLY before write-mode run.
-- NOT application migrations (no alembic). Require separate DDL approval.
-- These tables are the sole rollback mechanism.

CREATE TABLE IF NOT EXISTS backfill_tp_delta_snapshots (
    id                  SERIAL PRIMARY KEY,
    backfill_run_id     UUID NOT NULL,
    tp_id               INTEGER NOT NULL REFERENCES tournament_participations(id) ON DELETE CASCADE,
    user_id             INTEGER NOT NULL,
    semester_id         INTEGER NOT NULL,
    placement           INTEGER NOT NULL,
    field_size_used     INTEGER NOT NULL,
    stored_delta_pre    JSONB,          -- skill_rating_delta BEFORE backfill (rollback source)
    correct_delta       JSONB,          -- recomputed delta that will be written
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bts_run_id ON backfill_tp_delta_snapshots (backfill_run_id);
CREATE INDEX IF NOT EXISTS idx_bts_tp_id  ON backfill_tp_delta_snapshots (tp_id);
COMMENT ON TABLE backfill_tp_delta_snapshots IS
    'Pre-backfill snapshot of TP.skill_rating_delta. '
    'Rollback: UPDATE tournament_participations tp '
    'SET skill_rating_delta = snap.stored_delta_pre '
    'FROM backfill_tp_delta_snapshots snap '
    'WHERE snap.tp_id = tp.id AND snap.backfill_run_id = $1';

CREATE TABLE IF NOT EXISTS backfill_fsa_snapshots (
    id                  SERIAL PRIMARY KEY,
    backfill_run_id     UUID NOT NULL,
    fsa_id              INTEGER NOT NULL REFERENCES football_skill_assessments(id) ON DELETE CASCADE,
    user_license_id     INTEGER NOT NULL,
    skill_name          VARCHAR(50) NOT NULL,
    pre_status          VARCHAR(20) NOT NULL,
    pre_percentage      FLOAT NOT NULL,
    new_fsa_id          INTEGER,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bfs_run_id ON backfill_fsa_snapshots (backfill_run_id);
COMMENT ON TABLE backfill_fsa_snapshots IS
    'Pre-backfill snapshot of FootballSkillAssessment rows. '
    'P3 scope: populated only when FSA rebuild is approved.';

-- Audit table: records every TP.skill_rating_delta change with old/new values.
-- Primary rollback anchor and idempotency key.
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
CREATE INDEX IF NOT EXISTS idx_tp_backfill_audit_run
    ON tp_delta_backfill_audit (backfill_run_id);
COMMENT ON TABLE tp_delta_backfill_audit IS
    'Audit log of every TP.skill_rating_delta updated by write-mode. '
    'Rollback SQL: UPDATE tournament_participations tp '
    'SET skill_rating_delta = a.old_delta '
    'FROM tp_delta_backfill_audit a WHERE a.tp_id = tp.id AND a.backfill_run_id = $1';
"""

ROLLBACK_TEMPLATE = """
-- Rollback script template — replace :run_id with the actual backfill_run_id UUID.

BEGIN;

-- Step 1: Restore TP.skill_rating_delta from audit table
UPDATE tournament_participations tp
SET    skill_rating_delta = audit.old_delta
FROM   tp_delta_backfill_audit audit
WHERE  audit.tp_id          = tp.id
  AND  audit.backfill_run_id = :run_id;

-- Step 2: Clean up audit rows for this run
DELETE FROM tp_delta_backfill_audit WHERE backfill_run_id = :run_id;

-- Step 3: Clean up snapshot rows for this run
DELETE FROM backfill_tp_delta_snapshots WHERE backfill_run_id = :run_id;

COMMIT;
"""


# ── Write-mode: CLI guard layer ────────────────────────────────────────────────

def _validate_write_cli_guards(args) -> None:
    """
    G-1..G-3: Pre-DB guards. Fail-fast before any DB connection.

    G-1: All five required write-mode flags must be present.
    G-2: --confirm-backfill-run-id must be a valid UUID4.
    G-3: --dry-run-report must exist and parse as JSON.
    """
    # G-1: flag completeness
    required = [
        ("confirm_backfill_run_id", "--confirm-backfill-run-id"),
        ("require_snapshot",         "--require-snapshot"),
        ("dry_run_report",           "--dry-run-report"),
        ("expected_affected_count",  "--expected-affected-count"),
    ]
    missing = [flag for attr, flag in required if not getattr(args, attr, None)]
    if missing:
        print(
            f"ERROR: --write requires all of: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # G-2: UUID4 format
    try:
        parsed = uuid.UUID(args.confirm_backfill_run_id, version=4)
        # uuid.UUID accepts any version if version param matches; also verify canonical form
        if str(parsed) != args.confirm_backfill_run_id.lower():
            raise ValueError("Non-canonical form")
    except (ValueError, AttributeError):
        print(
            f"ERROR: --confirm-backfill-run-id '{args.confirm_backfill_run_id}' "
            f"is not a valid UUID4.",
            file=sys.stderr,
        )
        sys.exit(1)

    # G-3: dry-run report parseable
    try:
        with open(args.dry_run_report) as f:
            json.load(f)
    except OSError as e:
        print(f"ERROR: Cannot read --dry-run-report: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: --dry-run-report is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def _check_snapshot_tables_exist(db: Session) -> None:
    """
    G-4: Verify all required write-mode tables exist in the public schema.
    Aborts with SystemExit if any table is missing.
    """
    for table in _REQUIRED_WRITE_TABLES:
        exists = db.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ), {"t": table}).scalar()
        if not exists:
            print(
                f"ERROR: Required table '{table}' does not exist. "
                f"Run --show-ddl, create all tables manually, then retry.",
                file=sys.stderr,
            )
            sys.exit(1)


def _capture_xp_credit_baseline(db: Session) -> Dict:
    """Capture XP and credit transaction row counts as integrity baseline."""
    xp_count = db.execute(text("SELECT COUNT(*) FROM xp_transactions")).scalar()
    cr_count = db.execute(text("SELECT COUNT(*) FROM credit_transactions")).scalar()
    return {"xp_count": int(xp_count), "credit_count": int(cr_count)}


def _assert_xp_credit_unchanged(db: Session, baseline: Dict) -> None:
    """
    Assert XP and credit row counts are identical to baseline.
    Raises RuntimeError (triggers ROLLBACK in _write_tournament_batch) on mismatch.
    """
    post = _capture_xp_credit_baseline(db)
    if post != baseline:
        raise RuntimeError(
            f"XP/credit integrity violation — counts changed during write-mode!\n"
            f"  baseline: {baseline}\n"
            f"  post:     {post}"
        )


def _guard_live_count(live_count: int, expected_count: int, report_count: int) -> None:
    """
    G-6: Live affected count must match both --expected-affected-count and dry-run report.
    Extracted as standalone function for testability.
    """
    if live_count != expected_count:
        print(
            f"ERROR: Live affected count {live_count} ≠ --expected-affected-count {expected_count}. "
            f"DB state may have changed since dry-run. Rerun dry-run and verify.",
            file=sys.stderr,
        )
        sys.exit(1)
    if report_count != live_count:
        print(
            f"ERROR: Dry-run report shows {report_count} affected TPs but live count is {live_count}. "
            f"Report may be stale — rerun dry-run.",
            file=sys.stderr,
        )
        sys.exit(1)


def _guard_max_affected(live_count: int, max_affected: Optional[int]) -> None:
    """
    G-7: If --max-affected is set, abort if live count exceeds it.
    Extracted as standalone function for testability.
    """
    if max_affected is not None and live_count > max_affected:
        print(
            f"ERROR: Live affected count {live_count} exceeds --max-affected {max_affected}. "
            f"Lower the scope or raise --max-affected.",
            file=sys.stderr,
        )
        sys.exit(1)


def _check_prior_backfills(
    db: Session,
    tp_ids: List[int],
    run_id: str,
    allow_rebackfill: bool,
) -> None:
    """
    G-8: Check for TP audit rows from a DIFFERENT run_id.

    Same run_id (idempotent re-run) → NOT blocked.
    Different run_id + no --allow-rebackfill → SystemExit with conflicting TP list.
    Different run_id + --allow-rebackfill → allowed.
    """
    if not tp_ids:
        return
    rows = db.execute(text("""
        SELECT DISTINCT tp_id, backfill_run_id
        FROM   tp_delta_backfill_audit
        WHERE  tp_id = ANY(:ids)
          AND  backfill_run_id != :run_id
        LIMIT  20
    """), {"ids": tp_ids, "run_id": run_id}).fetchall()

    if rows and not allow_rebackfill:
        conflicts = [f"tp_id={r.tp_id} (prior_run={r.backfill_run_id})" for r in rows]
        print(
            f"ERROR: {len(rows)} TP(s) already backfilled in a prior run. "
            f"Use --allow-rebackfill to override.\n"
            f"Conflicting: {conflicts}",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Write-mode: per-TP operations ─────────────────────────────────────────────

def _snapshot_tp_row(
    db: Session,
    run_id: str,
    tp: TournamentParticipation,
    n: int,
    correct_delta: Dict,
) -> None:
    """Insert pre-write snapshot of this TP into backfill_tp_delta_snapshots."""
    db.execute(text("""
        INSERT INTO backfill_tp_delta_snapshots
          (backfill_run_id, tp_id, user_id, semester_id, placement,
           field_size_used, stored_delta_pre, correct_delta)
        VALUES
          (:run_id, :tp_id, :user_id, :sem_id, :placement,
           :n, CAST(:stored AS jsonb), CAST(:correct AS jsonb))
    """), {
        "run_id":  run_id,
        "tp_id":   tp.id,
        "user_id": tp.user_id,
        "sem_id":  tp.semester_id,
        "placement": tp.placement,
        "n":       n,
        "stored":  json.dumps(tp.skill_rating_delta or {}),
        "correct": json.dumps(correct_delta),
    })


def _audit_insert(
    db: Session,
    run_id: str,
    tp: TournamentParticipation,
    correct_delta: Dict,
) -> None:
    """
    Insert audit row into tp_delta_backfill_audit.
    ON CONFLICT DO NOTHING ensures idempotency: same (run_id, tp_id) is a no-op.
    """
    db.execute(text("""
        INSERT INTO tp_delta_backfill_audit
          (backfill_run_id, tp_id, user_id, tournament_id, old_delta, new_delta)
        VALUES
          (:run_id, :tp_id, :user_id, :tournament_id,
           CAST(:old AS jsonb), CAST(:new AS jsonb))
        ON CONFLICT (backfill_run_id, tp_id) DO NOTHING
    """), {
        "run_id":       run_id,
        "tp_id":        tp.id,
        "user_id":      tp.user_id,
        "tournament_id": tp.semester_id,
        "old":          json.dumps(tp.skill_rating_delta or {}),
        "new":          json.dumps(correct_delta),
    })


def _update_tp_delta(
    db: Session,
    tp: TournamentParticipation,
    correct_delta: Dict,
) -> int:
    """
    UPDATE tournament_participations with optimistic lock and cutoff re-enforcement.

    The WHERE clause:
    - achieved_at < cutoff: final safety net — even if detection had a bug, the
      UPDATE itself will refuse to touch post-fix TPs.
    - IS NOT DISTINCT FROM stored value: optimistic lock — if another process
      modified the delta since we read it, the UPDATE returns rowcount=0.

    Returns number of rows updated (0 or 1).
    """
    result = db.execute(text("""
        UPDATE tournament_participations
        SET    skill_rating_delta = CAST(:new AS jsonb)
        WHERE  id            = :tp_id
          AND  achieved_at   < :cutoff
          AND  skill_rating_delta IS NOT DISTINCT FROM CAST(:old AS jsonb)
    """), {
        "tp_id":  tp.id,
        "new":    json.dumps(correct_delta),
        "old":    json.dumps(tp.skill_rating_delta or {}),
        "cutoff": PRE_FIX_CUTOFF,
    })
    return result.rowcount


def _write_tournament_batch(
    db: Session,
    run_id: str,
    tournament_id: int,
    tps: List[Tuple[TournamentParticipation, int, Dict]],
    baseline: Dict,
) -> Tuple[int, int, Optional[str]]:
    """
    Execute write-mode for all TPs in one tournament within a single transaction.

    Returns (updated_count, skipped_idempotent_count, error_message_or_None).
    On any exception: db.rollback() is called and (0, 0, error) is returned.
    """
    try:
        updated = 0
        skipped = 0

        for tp, n, correct_delta in tps:
            # Idempotency: skip TPs already processed in THIS run
            already_done = db.execute(text("""
                SELECT 1 FROM tp_delta_backfill_audit
                WHERE backfill_run_id = :rid AND tp_id = :tid
                LIMIT 1
            """), {"rid": run_id, "tid": tp.id}).scalar()

            if already_done:
                skipped += 1
                continue

            _snapshot_tp_row(db, run_id, tp, n, correct_delta)
            _audit_insert(db, run_id, tp, correct_delta)
            rows = _update_tp_delta(db, tp, correct_delta)

            if rows == 0:
                raise RuntimeError(
                    f"Optimistic lock failed for tp_id={tp.id} "
                    f"(tournament={tournament_id}): delta changed since read "
                    f"or post-fix cutoff enforced"
                )
            updated += rows

        # XP/credit assertion before commit (safety net)
        _assert_xp_credit_unchanged(db, baseline)

        db.commit()
        return updated, skipped, None

    except Exception as exc:
        db.rollback()
        return 0, 0, str(exc)


def _generate_rollback_sql(run_id: str) -> str:
    """Generate deterministic rollback SQL for a completed write-mode run."""
    return (
        f"UPDATE tournament_participations tp\n"
        f"SET    skill_rating_delta = audit.old_delta\n"
        f"FROM   tp_delta_backfill_audit audit\n"
        f"WHERE  audit.tp_id          = tp.id\n"
        f"  AND  audit.backfill_run_id = '{run_id}';"
    )


# ── Write-mode: orchestrator ───────────────────────────────────────────────────

def _run_write_mode(
    args,
    db: Session,
    affected: List,  # List[AffectedRow] = [(tp, n, correct_delta, fsa_impact), ...]
    run_id: str,
) -> None:
    """
    Main write-mode execution after detection is complete.

    Guards (G-4..G-8) are applied in order. Then per-tournament transactions.
    """
    # G-4: snapshot + audit tables exist
    _check_snapshot_tables_exist(db)

    # Baseline before any write
    baseline = _capture_xp_credit_baseline(db)

    # G-6: live count vs expected and report
    live_count = len(affected)
    with open(args.dry_run_report) as f:
        report = json.load(f)
    report_count = report.get("summary", {}).get("affected_tp_count", -1)
    _guard_live_count(live_count, args.expected_affected_count, report_count)

    # G-7: safety cap
    _guard_max_affected(live_count, getattr(args, "max_affected", None))

    # G-8: rebackfill guard
    tp_ids = [tp.id for tp, *_ in affected]
    _check_prior_backfills(db, tp_ids, run_id, getattr(args, "allow_rebackfill", False))

    # Group by tournament
    by_tournament: Dict[int, List] = defaultdict(list)
    for tp, n, correct_delta, _fsa in affected:
        by_tournament[tp.semester_id].append((tp, n, correct_delta))

    # Per-tournament transactions
    total_updated = 0
    total_skipped = 0
    failed_tournaments = []

    for t_id, tps in by_tournament.items():
        updated, skipped, err = _write_tournament_batch(db, run_id, t_id, tps, baseline)
        if err:
            failed_tournaments.append({"tournament_id": t_id, "error": err})
            print(f"  [FAIL] tournament {t_id}: {err}", file=sys.stderr)
        else:
            total_updated += updated
            total_skipped += skipped
            print(f"  [OK]   tournament {t_id}: {updated} updated, {skipped} skipped")

    # Global XP/credit final assertion
    _assert_xp_credit_unchanged(db, baseline)

    # Capture post-state for report
    post_baseline = _capture_xp_credit_baseline(db)

    # Rollback SQL
    rollback_sql = _generate_rollback_sql(run_id)

    # Output
    sep = "=" * 72
    print(sep)
    print("P1 BACKFILL WRITE-MODE — BUG-P0-CARD-01 TP delta correction")
    print(f"backfill_run_id    : {run_id}")
    print(f"p0_fix_commit      : {_P0_FIX_COMMIT_SHA}")
    print(f"cutoff (UTC)       : {PRE_FIX_CUTOFF.isoformat()}")
    print(f"scope              : TournamentParticipation.skill_rating_delta ONLY")
    print(sep)
    print(f"  live_affected    : {live_count}")
    print(f"  updated          : {total_updated}")
    print(f"  skipped          : {total_skipped}")
    print(f"  failed_tours     : {len(failed_tournaments)}")
    print(f"\n[XP/credit proof]")
    print(f"  xp_count   before={baseline['xp_count']}  after={post_baseline['xp_count']} — {'OK' if baseline['xp_count'] == post_baseline['xp_count'] else 'VIOLATION'}")
    print(f"  credit_count before={baseline['credit_count']}  after={post_baseline['credit_count']} — {'OK' if baseline['credit_count'] == post_baseline['credit_count'] else 'VIOLATION'}")
    print(f"\n[Rollback SQL]")
    print(f"  {rollback_sql}")
    print(sep)

    if getattr(args, "output", None):
        data = {
            "backfill_run_id":        run_id,
            "mode":                   "write",
            "p0_fix_cutoff_utc":      PRE_FIX_CUTOFF.isoformat(),
            "p0_fix_commit":          _P0_FIX_COMMIT_SHA,
            "scope":                  ["TournamentParticipation.skill_rating_delta"],
            "affected_count_expected": args.expected_affected_count,
            "affected_count_live":    live_count,
            "updated_count":          total_updated,
            "skipped_idempotent":     total_skipped,
            "failed_tournaments":     failed_tournaments,
            "xp_baseline":            baseline,
            "xp_post":                post_baseline,
            "rollback_sql":           rollback_sql,
            "audit_table":            _AUDIT_TABLE,
            "snapshot_table":         _SNAPSHOT_TABLE,
        }
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"\nJSON result written to: {args.output}")

    if failed_tournaments:
        print(f"\n{len(failed_tournaments)} tournament(s) FAILED. See errors above.", file=sys.stderr)
        sys.exit(1)


# ── Report ────────────────────────────────────────────────────────────────────

AffectedRow = Tuple[TournamentParticipation, int, Dict, List]


def _build_skill_stats(affected: List[AffectedRow]) -> Dict[str, Dict]:
    stats: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "stored_sum": 0.0, "correct_sum": 0.0})
    for tp, _n, correct_delta, _fsa in affected:
        stored: Dict = tp.skill_rating_delta or {}
        all_skills = set(stored.keys()) | set(correct_delta.keys())
        for skill in all_skills:
            s = stored.get(skill, 0.0)
            c = correct_delta.get(skill, 0.0)
            if abs(s - c) >= DELTA_EPSILON:
                stats[skill]["count"] += 1
                stats[skill]["stored_sum"] += s
                stats[skill]["correct_sum"] += c
    return dict(stats)


def _print_report(
    backfill_run_id: str,
    affected: List[AffectedRow],
    field_sizes: Dict[int, int],
    null_delta_count: int,
    output_json: Optional[str],
    show_ddl: bool,
) -> None:
    sep = "=" * 72
    divergent = [(tp, n, cd, fi) for tp, n, cd, fi in affected if fi]
    skill_stats = _build_skill_stats(affected)
    total_fsa_rows = sum(len(fi) for _, _, _, fi in affected)

    print(sep)
    print("P1 BACKFILL DRY-RUN — BUG-P0-CARD-01 TP delta recompute")
    print(f"backfill_run_id : {backfill_run_id}")
    print(f"cutoff          : {PRE_FIX_CUTOFF.isoformat()}")
    print(f"p0_fix_commit   : {_P0_FIX_COMMIT_SHA}")
    print(f"mode            : DRY-RUN — 0 DB writes")
    print(sep)

    print(f"\n[Detection]")
    print(f"  INDIVIDUAL tournaments with N>={MIN_FIELD_SIZE}              : {len(field_sizes)}")
    print(f"  Affected TPs (placement 2..N-1, pre-fix, delta NOT NULL): {len(affected)}")
    print(f"  TPs with skill_rating_delta=NULL (separate gap, skip)   : {null_delta_count}")
    print(f"  Tournaments with >=1 affected TP                        : {len({tp.semester_id for tp, *_ in affected})}")
    print(f"  TPs with divergent stored vs correct delta              : {len(divergent)}")

    if affected:
        print(f"\n[Affected TPs — stored vs correct delta]")
        hdr = f"  {'TID':>6} {'UID':>6} {'Rank':>4} {'N':>3}  {'Stored delta':<32} {'Correct delta':<32}"
        print(hdr)
        print(f"  {'-'*6} {'-'*6} {'-'*4} {'-'*3}  {'-'*32} {'-'*32}")
        for tp, n, cd, _fi in affected[:50]:
            stored_str = json.dumps(tp.skill_rating_delta or {}, sort_keys=True)[:30]
            correct_str = json.dumps(cd, sort_keys=True)[:30]
            print(f"  {tp.semester_id:>6} {tp.user_id:>6} {tp.placement:>4} {n:>3}  {stored_str:<32} {correct_str:<32}")
        if len(affected) > 50:
            print(f"  ... ({len(affected) - 50} more — use --output to see all)")

    if skill_stats:
        print(f"\n[Per-skill divergence summary]")
        print(f"  {'Skill':<24} {'Rows':>5} {'Avg stored':>12} {'Avg correct':>12} {'Avg error':>10}")
        print(f"  {'-'*24} {'-'*5} {'-'*12} {'-'*12} {'-'*10}")
        for skill, st in sorted(skill_stats.items()):
            c = st["count"]
            avg_s = st["stored_sum"] / c
            avg_c = st["correct_sum"] / c
            print(f"  {skill:<24} {c:>5} {avg_s:>+12.2f} {avg_c:>+12.2f} {avg_c - avg_s:>+10.2f}")

    print(f"\n[Expected FSA impact — write-mode only, P3 scope]")
    print(f"  ARCHIVED rows: untouched (terminal state — never modified)")
    print(f"  Current ASSESSED/VALIDATED rows that would be re-archived: {total_fsa_rows}")
    print(f"  New ASSESSED rows that would be inserted:                  {total_fsa_rows}")
    if divergent:
        print(f"\n  Sample corrections (first 10):")
        shown = 0
        for tp, _n, _cd, fsa_impact in divergent:
            for row in fsa_impact:
                print(
                    f"    uid={tp.user_id:<6} skill={row['skill_name']:<22} "
                    f"current={row['current_pct']:>5.1f}%  "
                    f"stored_d={row['stored_delta']:>+6.1f}  "
                    f"correct_d={row['correct_delta']:>+6.1f}  "
                    f"expected={row['expected_new_pct']:>5.1f}%"
                )
                shown += 1
                if shown >= 10:
                    break
            if shown >= 10:
                break

    print(f"\n[XP / Credit proof]")
    print(f"  XP and credit awards are placement-based (PLACEMENT_SKILL_POINTS lookup).")
    print(f"  They do NOT read skill_rating_delta and are NOT affected by backfill.")
    print(f"  XP/credit transactions changed by write-mode backfill: 0")

    print(f"\n[backfill_run_id design]")
    print(f"  run_id  : {backfill_run_id}")
    print(f"  Purpose : Primary key of snapshot tables; embedded in all write-mode")
    print(f"            records; used as sole rollback handle.")
    print(f"  Scope   : One UUID per script invocation; generated before any DB read.")

    print(f"\n[Snapshot + audit tables — DDL for write-mode]")
    print(f"  Tables  : {_SNAPSHOT_TABLE}, backfill_fsa_snapshots, {_AUDIT_TABLE}")
    print(f"  Status  : NOT application migrations. Pass --show-ddl to print DDL.")

    if show_ddl:
        print(f"\n{'─' * 72}")
        print("[Snapshot + Audit DDL — execute MANUALLY, separate DDL approval required]")
        print(SNAPSHOT_DDL)
        print("[Rollback template]")
        print(ROLLBACK_TEMPLATE)
        print("─" * 72)

    print(f"\n[Post-fix cutoff enforcement]")
    print(f"  PRE_FIX_CUTOFF = {PRE_FIX_CUTOFF.isoformat()} (bef0a7f UTC)")
    print(f"  All detection queries filter achieved_at < cutoff.")
    print(f"  Write-mode UPDATE also re-enforces cutoff (optimistic lock).")

    print(f"\n{sep}")
    print(f"DRY-RUN COMPLETE — 0 DB writes performed")
    print(f"backfill_run_id : {backfill_run_id}")
    print(sep)

    if output_json:
        data = {
            "backfill_run_id": backfill_run_id,
            "cutoff": PRE_FIX_CUTOFF.isoformat(),
            "p0_fix_commit": _P0_FIX_COMMIT_SHA,
            "mode": "dry-run",
            "summary": {
                "individual_tournaments_n_gte_3": len(field_sizes),
                "affected_tp_count": len(affected),
                "null_delta_tp_count": null_delta_count,
                "tournaments_affected": len({tp.semester_id for tp, *_ in affected}),
                "divergent_tp_count": len(divergent),
                "fsa_rows_to_rearchive": total_fsa_rows,
                "fsa_rows_to_insert": total_fsa_rows,
                "xp_credit_delta": 0,
            },
            "per_skill_divergence": {
                skill: {
                    "affected_rows": st["count"],
                    "avg_stored_delta": round(st["stored_sum"] / st["count"], 2),
                    "avg_correct_delta": round(st["correct_sum"] / st["count"], 2),
                    "avg_error": round((st["correct_sum"] - st["stored_sum"]) / st["count"], 2),
                }
                for skill, st in skill_stats.items()
            },
            "affected_tps": [
                {
                    "tp_id": tp.id,
                    "tournament_id": tp.semester_id,
                    "user_id": tp.user_id,
                    "placement": tp.placement,
                    "achieved_at": tp.achieved_at.isoformat() if tp.achieved_at else None,
                    "field_size": n,
                    "stored_delta": tp.skill_rating_delta,
                    "correct_delta": cd,
                    "fsa_impact": fi,
                }
                for tp, n, cd, fi in affected
            ],
            "snapshot_ddl": SNAPSHOT_DDL,
            "rollback_template": ROLLBACK_TEMPLATE,
        }
        with open(output_json, "w") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"\nJSON report written to: {output_json}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "P1 backfill — detect and optionally correct BUG-P0-CARD-01 affected TPs. "
            "Default: DRY-RUN (no DB writes). Use --write with all required guards for write-mode."
        )
    )
    parser.add_argument(
        "--tournament-id", type=int, default=None, metavar="TID",
        help="Restrict analysis to a single tournament (semester) ID.",
    )
    parser.add_argument(
        "--output", type=str, default=None, metavar="FILE",
        help="Write full JSON report/result to FILE.",
    )
    parser.add_argument(
        "--show-ddl", action="store_true",
        help="Print snapshot + audit table DDL and rollback template to stdout.",
    )
    # Write-mode flags
    parser.add_argument(
        "--write", action="store_true",
        help="Enable write-mode. Requires all four additional guard flags.",
    )
    parser.add_argument(
        "--confirm-backfill-run-id", type=str, default=None, metavar="UUID4",
        help="[write-mode] UUID4 that will be used as backfill_run_id in audit + snapshot tables.",
    )
    parser.add_argument(
        "--require-snapshot", action="store_true",
        help="[write-mode] Abort if snapshot/audit tables do not exist.",
    )
    parser.add_argument(
        "--dry-run-report", type=str, default=None, metavar="FILE",
        help="[write-mode] Path to dry-run JSON report. affected_count is cross-checked.",
    )
    parser.add_argument(
        "--expected-affected-count", type=int, default=None, metavar="N",
        help="[write-mode] Expected live affected count. Abort if live count differs.",
    )
    parser.add_argument(
        "--max-affected", type=int, default=None, metavar="N",
        help="[write-mode] Safety cap: abort if live count exceeds N.",
    )
    parser.add_argument(
        "--allow-rebackfill", action="store_true",
        help="[write-mode] Allow updating TPs that already have a prior-run audit record.",
    )
    args = parser.parse_args()

    # Pre-DB guards (fail-fast before SessionLocal)
    if args.write:
        _validate_write_cli_guards(args)

    # Use confirmed run_id for write-mode; generate fresh for dry-run
    backfill_run_id = (
        args.confirm_backfill_run_id if args.write else str(uuid.uuid4())
    )

    db = SessionLocal()
    try:
        print(f"[P1] mode={'write' if args.write else 'dry-run'}")
        print(f"[P1] cutoff={PRE_FIX_CUTOFF.isoformat()} (bef0a7f UTC)")
        print(f"[P1] backfill_run_id={backfill_run_id}")
        print(f"[P1] Loading field sizes...")

        field_sizes = _get_individual_field_sizes(db)

        if args.tournament_id is not None:
            if args.tournament_id not in field_sizes:
                print(
                    f"Tournament {args.tournament_id} not found or has "
                    f"fewer than {MIN_FIELD_SIZE} participants."
                )
                sys.exit(0)
            field_sizes = {args.tournament_id: field_sizes[args.tournament_id]}

        print(f"[P1] {len(field_sizes)} INDIVIDUAL tournaments with N>={MIN_FIELD_SIZE}")

        null_delta_count = _count_null_delta_tps(db)
        candidates = _detect_affected_tps(db, field_sizes, args.tournament_id)

        print(f"[P1] {len(candidates)} candidate TPs. Recomputing correct deltas...")

        affected: List[AffectedRow] = []
        for i, (tp, n) in enumerate(candidates, 1):
            if i % 20 == 0 or i == len(candidates):
                print(f"[P1] {i}/{len(candidates)}...", end="\r", flush=True)
            correct_delta = _recompute_correct_delta(db, tp, n)
            fsa_impact = _project_fsa_impact(db, tp, correct_delta)
            affected.append((tp, n, correct_delta, fsa_impact))

        if candidates:
            print()

        if args.write:
            _run_write_mode(args, db, affected, backfill_run_id)
        else:
            _print_report(
                backfill_run_id, affected, field_sizes,
                null_delta_count, args.output, args.show_ddl,
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
