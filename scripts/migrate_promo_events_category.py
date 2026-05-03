"""
Data migration: reclassify Promotion Event semesters.

Updates all Semester records where:
  - code LIKE 'PROMO-%'  (the promo event code prefix set by clubs admin)
  - semester_category = 'TOURNAMENT'  (old classification)

to:
  - semester_category = 'PROMOTION_EVENT'  (new dedicated category)

No other records are touched. TournamentConfiguration, SemesterEnrollment,
reward/ranking pipeline, and tournament_status are all unchanged.

Modes
-----
  --dry-run   (default) — list affected records, make no changes
  --apply     — execute UPDATE and commit
  --rollback  — revert: set PROMOTION_EVENT → TOURNAMENT for emergency rollback

Usage
-----
  DATABASE_URL="..." python scripts/migrate_promo_events_category.py           # dry-run
  DATABASE_URL="..." python scripts/migrate_promo_events_category.py --apply
  DATABASE_URL="..." python scripts/migrate_promo_events_category.py --rollback

Deployment order
----------------
  1. alembic upgrade head        (adds PROMOTION_EVENT enum value)
  2. deploy application code     (code recognises PROMOTION_EVENT)
  3. python ... --dry-run        (verify which records will move)
  4. python ... --apply          (execute the migration)
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.semester import Semester, SemesterCategory


# ── Criteria ──────────────────────────────────────────────────────────────────

def _promo_candidates(db: Session):
    """Records matching the migration criteria (not yet reclassified)."""
    return (
        db.query(Semester)
        .filter(
            Semester.code.like("PROMO-%"),
            Semester.semester_category == SemesterCategory.TOURNAMENT,
        )
        .order_by(Semester.start_date.desc(), Semester.id.asc())
        .all()
    )


def _already_reclassified(db: Session):
    """Records already in PROMOTION_EVENT category."""
    return (
        db.query(Semester)
        .filter(Semester.semester_category == SemesterCategory.PROMOTION_EVENT)
        .order_by(Semester.start_date.desc(), Semester.id.asc())
        .all()
    )


def _tournament_non_promo(db: Session):
    """TOURNAMENT records whose code does NOT start with PROMO- (shown for audit, never touched)."""
    return (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.TOURNAMENT,
            ~Semester.code.like("PROMO-%"),
        )
        .count()
    )


# ── Dry-run ───────────────────────────────────────────────────────────────────

def _dry_run(db: Session) -> None:
    candidates = _promo_candidates(db)
    already    = _already_reclassified(db)
    non_promo  = _tournament_non_promo(db)

    print("=" * 70)
    print("DRY RUN — Promotion Event category migration")
    print("Criteria: code LIKE 'PROMO-%' AND semester_category = 'TOURNAMENT'")
    print("=" * 70)

    if candidates:
        print(f"\n[{len(candidates)}] records will be reclassified → PROMOTION_EVENT:\n")
        for s in candidates:
            print(f"  [{s.id:>5}]  {s.code:<45}  status={s.tournament_status or s.status.value!r:<20}  \"{s.name}\"")
    else:
        print("\n  No records match the criteria — nothing to migrate.")

    if already:
        print(f"\n[{len(already)}] records already have semester_category = PROMOTION_EVENT (skipped):\n")
        for s in already:
            print(f"  [{s.id:>5}]  {s.code}")

    print(f"\n[audit]  TOURNAMENT records with non-PROMO- codes (will NOT be touched): {non_promo}")
    print()
    if candidates:
        print("To apply:    python scripts/migrate_promo_events_category.py --apply")
    print("To rollback: python scripts/migrate_promo_events_category.py --rollback")


# ── Apply ─────────────────────────────────────────────────────────────────────

def _apply(db: Session) -> None:
    candidates = _promo_candidates(db)

    if not candidates:
        print("No records match the migration criteria. Nothing to do.")
        return

    print(f"Reclassifying {len(candidates)} record(s) → PROMOTION_EVENT …")
    for s in candidates:
        print(f"  [{s.id:>5}]  {s.code}  \"{s.name}\"")
        s.semester_category = SemesterCategory.PROMOTION_EVENT

    db.commit()

    # Verify
    remaining = _promo_candidates(db)
    reclassified = _already_reclassified(db)
    print(f"\n✅  Done.")
    print(f"    Reclassified: {len(candidates)}")
    print(f"    PROMOTION_EVENT total now: {len(reclassified)}")
    print(f"    PROMO- records still as TOURNAMENT: {len(remaining)}  (should be 0)")


# ── Rollback ──────────────────────────────────────────────────────────────────

def _rollback(db: Session) -> None:
    targets = _already_reclassified(db)

    if not targets:
        print("No PROMOTION_EVENT records found. Nothing to roll back.")
        return

    print(f"ROLLBACK: reverting {len(targets)} record(s) PROMOTION_EVENT → TOURNAMENT …")
    for s in targets:
        print(f"  [{s.id:>5}]  {s.code}  \"{s.name}\"")
        s.semester_category = SemesterCategory.TOURNAMENT

    db.commit()
    print(f"\n✅  Rollback complete. {len(targets)} records restored to TOURNAMENT.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclassify PROMO-* semesters to PROMOTION_EVENT category."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run",  dest="mode", action="store_const", const="dry",
                       help="List affected records without making changes (default)")
    group.add_argument("--apply",    dest="mode", action="store_const", const="apply",
                       help="Execute the UPDATE and commit")
    group.add_argument("--rollback", dest="mode", action="store_const", const="rollback",
                       help="Revert PROMOTION_EVENT records back to TOURNAMENT")
    parser.set_defaults(mode="dry")
    args = parser.parse_args()

    db: Session = SessionLocal()
    try:
        if args.mode == "dry":
            _dry_run(db)
        elif args.mode == "apply":
            _apply(db)
        elif args.mode == "rollback":
            _rollback(db)
    except Exception:
        db.rollback()
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
