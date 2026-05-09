#!/usr/bin/env python3
"""
P1 Backfill Dry-Run — BUG-P0-CARD-01 historical TP delta recompute

Detects TournamentParticipation rows whose skill_rating_delta was stored with
the wrong total_players value due to the sequential-distribution race condition
(fixed in bef0a7f, 2026-05-09). Recomputes the correct delta using
compute_single_tournament_skill_delta(field_size=N) and reports expected changes.

DRY-RUN ONLY — no DB writes of any kind. Pass --write (not yet implemented)
for write-mode in a future approved implementation step.

Usage:
    PYTHONPATH=. python scripts/backfill_tp_delta_p1.py
    PYTHONPATH=. python scripts/backfill_tp_delta_p1.py --tournament-id 4600
    PYTHONPATH=. python scripts/backfill_tp_delta_p1.py --output report.json
    PYTHONPATH=. python scripts/backfill_tp_delta_p1.py --show-ddl
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

# Timestamp of bef0a7f push to origin/main.
# TPs with achieved_at >= this cutoff were distributed post-fix: DO NOT TOUCH.
PRE_FIX_CUTOFF = datetime(2026, 5, 9, 12, 32, 59, tzinfo=timezone.utc)

# Bug only affects rank-2..rank-(N-1), which only exists when N >= 3.
MIN_FIELD_SIZE = 3

# Threshold below which a delta difference is considered negligible (float precision).
DELTA_EPSILON = 0.05


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
    FSA row and compute the expected corrected percentage.

    Note: this is a first-order approximation. The true correction requires
    rebuilding the full FSA accumulation chain. For write-mode, a full replay
    per player is needed. For dry-run reporting, this approximation shows the
    direction and magnitude of the error.

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

        # Net correction: difference between what was stored and what should have been
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

SNAPSHOT_DDL = """
-- P1 Backfill snapshot tables — execute MANUALLY before write-mode run.
-- These tables are the sole rollback mechanism. Notes-string matching is
-- explicitly NOT used as a rollback strategy.

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
    pre_status          VARCHAR(20) NOT NULL,   -- status BEFORE backfill (ASSESSED/VALIDATED)
    pre_percentage      FLOAT NOT NULL,         -- percentage BEFORE backfill
    new_fsa_id          INTEGER,                -- id of the NEW ASSESSED row inserted by backfill
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bfs_run_id ON backfill_fsa_snapshots (backfill_run_id);
COMMENT ON TABLE backfill_fsa_snapshots IS
    'Pre-backfill snapshot of FSA rows changed by write-mode. '
    'Rollback: DELETE new_fsa_id row; UPDATE old fsa_id SET status=pre_status, percentage=pre_percentage.';
"""

ROLLBACK_TEMPLATE = """
-- Rollback script template — replace :run_id with the actual backfill_run_id UUID.

BEGIN;

-- Step 1: Restore TP.skill_rating_delta from snapshot
UPDATE tournament_participations tp
SET    skill_rating_delta = snap.stored_delta_pre
FROM   backfill_tp_delta_snapshots snap
WHERE  snap.tp_id          = tp.id
  AND  snap.backfill_run_id = :run_id;

-- Step 2: Delete new FSA rows inserted by backfill
DELETE FROM football_skill_assessments
WHERE id IN (
    SELECT new_fsa_id FROM backfill_fsa_snapshots
    WHERE backfill_run_id = :run_id AND new_fsa_id IS NOT NULL
);

-- Step 3: Restore archived FSA rows to their pre-backfill status
UPDATE football_skill_assessments fsa
SET    status     = snap.pre_status,
       percentage = snap.pre_percentage,
       previous_status = NULL
FROM   backfill_fsa_snapshots snap
WHERE  snap.fsa_id          = fsa.id
  AND  snap.backfill_run_id = :run_id;

-- Step 4: Clean up snapshot tables
DELETE FROM backfill_fsa_snapshots       WHERE backfill_run_id = :run_id;
DELETE FROM backfill_tp_delta_snapshots  WHERE backfill_run_id = :run_id;

COMMIT;
"""


# ── Report ────────────────────────────────────────────────────────────────────

AffectedRow = Tuple[TournamentParticipation, int, Dict[str, float], List[Dict]]


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

    print(f"\n[Expected FSA impact — write-mode only]")
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
    print(f"            records; used as sole rollback handle (NOT notes-string).")
    print(f"  Scope   : One UUID per script invocation; generated before any DB read.")

    print(f"\n[Snapshot tables — DDL for write-mode]")
    print(f"  Tables  : backfill_tp_delta_snapshots, backfill_fsa_snapshots")
    print(f"  Status  : Not created by dry-run. Pass --show-ddl to print CREATE SQL.")

    if show_ddl:
        print(f"\n{'─' * 72}")
        print("[Snapshot DDL]")
        print(SNAPSHOT_DDL)
        print("[Rollback template]")
        print(ROLLBACK_TEMPLATE)
        print("─" * 72)

    print(f"\n[Post-fix cutoff enforcement]")
    print(f"  PRE_FIX_CUTOFF = {PRE_FIX_CUTOFF.isoformat()}")
    print(f"  All detection queries filter achieved_at < cutoff.")
    print(f"  Write-mode MUST re-enforce cutoff per-row before each UPDATE.")

    print(f"\n{sep}")
    print(f"DRY-RUN COMPLETE — 0 DB writes performed")
    print(f"backfill_run_id : {backfill_run_id}")
    print(sep)

    if output_json:
        data = {
            "backfill_run_id": backfill_run_id,
            "cutoff": PRE_FIX_CUTOFF.isoformat(),
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
            "P1 backfill dry-run — detect and report BUG-P0-CARD-01 affected TPs. "
            "DRY-RUN ONLY: no DB writes."
        )
    )
    parser.add_argument(
        "--tournament-id", type=int, default=None, metavar="TID",
        help="Restrict analysis to a single tournament (semester) ID.",
    )
    parser.add_argument(
        "--output", type=str, default=None, metavar="FILE",
        help="Write full JSON report to FILE.",
    )
    parser.add_argument(
        "--show-ddl", action="store_true",
        help="Print snapshot table DDL and rollback template to stdout.",
    )
    parser.add_argument(
        "--write", action="store_true",
        help="[NOT YET IMPLEMENTED] Write-mode requires separate approval.",
    )
    args = parser.parse_args()

    if args.write:
        print(
            "ERROR: --write is not yet implemented. "
            "Write-mode requires explicit approval and a separate implementation step.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Generate run ID before any DB access — it is the identity of this run.
    backfill_run_id = str(uuid.uuid4())

    db = SessionLocal()
    try:
        print(f"[P1 dry-run] cutoff={PRE_FIX_CUTOFF.isoformat()}")
        print(f"[P1 dry-run] backfill_run_id={backfill_run_id}")
        print(f"[P1 dry-run] Loading field sizes...")

        field_sizes = _get_individual_field_sizes(db)

        if args.tournament_id is not None:
            if args.tournament_id not in field_sizes:
                print(
                    f"Tournament {args.tournament_id} not found or has "
                    f"fewer than {MIN_FIELD_SIZE} participants."
                )
                sys.exit(0)
            field_sizes = {args.tournament_id: field_sizes[args.tournament_id]}

        print(f"[P1 dry-run] {len(field_sizes)} INDIVIDUAL tournaments with N>={MIN_FIELD_SIZE}")

        null_delta_count = _count_null_delta_tps(db)
        candidates = _detect_affected_tps(db, field_sizes, args.tournament_id)

        print(f"[P1 dry-run] {len(candidates)} candidate TPs. Recomputing correct deltas...")

        affected: List[AffectedRow] = []
        for i, (tp, n) in enumerate(candidates, 1):
            if i % 20 == 0 or i == len(candidates):
                print(f"[P1 dry-run] {i}/{len(candidates)}...", end="\r", flush=True)
            correct_delta = _recompute_correct_delta(db, tp, n)
            fsa_impact = _project_fsa_impact(db, tp, correct_delta)
            affected.append((tp, n, correct_delta, fsa_impact))

        if candidates:
            print()  # newline after progress line

        _print_report(
            backfill_run_id, affected, field_sizes,
            null_delta_count, args.output, args.show_ddl,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
