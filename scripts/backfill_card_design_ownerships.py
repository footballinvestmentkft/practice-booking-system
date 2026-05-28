"""Backfill card_design_ownerships for existing users.

This script creates CardDesignOwnership rows (source="system") for users who
already have a Welcome Card or Challenge Card but have no ownership row because
they existed before the Get Card feature was deployed.

IMPORTANT:
  - Run this script ONLY after an explicit product decision.
  - It is NOT called from app startup, Alembic migrations, or CI.
  - It is idempotent: running it multiple times is safe.
  - Use --dry-run to preview what would be written without touching the DB.

Usage:
  python scripts/backfill_card_design_ownerships.py --dry-run
  python scripts/backfill_card_design_ownerships.py --card-type welcome_card
  python scripts/backfill_card_design_ownerships.py --card-type challenge_card
  python scripts/backfill_card_design_ownerships.py --card-type all
  python scripts/backfill_card_design_ownerships.py --card-type all --source system
"""
import argparse
import sys
from pathlib import Path

# Make sure the project root is on sys.path when run directly.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))


def _run(dry_run: bool, card_type: str, source: str) -> None:
    from sqlalchemy import create_engine, or_
    from sqlalchemy.orm import sessionmaker

    from app.config import settings
    from app.models.card_design_ownership import CardDesignOwnership
    from app.models.license import UserLicense
    from app.models.vt_challenge import VirtualTrainingChallenge

    engine = engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        wc_created = wc_skipped = 0
        cc_created = cc_skipped = 0

        # ── Welcome Card backfill ──────────────────────────────────────────────
        if card_type in ("welcome_card", "all"):
            licenses = (
                db.query(UserLicense)
                .filter(
                    UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                    UserLicense.onboarding_completed == True,  # noqa: E712
                )
                .all()
            )
            for lic in licenses:
                existing = (
                    db.query(CardDesignOwnership)
                    .filter_by(
                        user_id=lic.user_id,
                        card_type_id="welcome_card",
                        design_id="default",
                    )
                    .first()
                )
                if existing:
                    wc_skipped += 1
                    continue
                if not dry_run:
                    db.add(
                        CardDesignOwnership(
                            user_id=lic.user_id,
                            card_type_id="welcome_card",
                            design_id="default",
                            source=source,
                            credit_transaction_id=None,
                        )
                    )
                wc_created += 1

            if not dry_run and wc_created > 0:
                db.commit()

        # ── Challenge Card backfill ────────────────────────────────────────────
        if card_type in ("challenge_card", "all"):
            participant_user_ids: set[int] = set()
            challenges = db.query(VirtualTrainingChallenge).all()
            for ch in challenges:
                if ch.challenger_id:
                    participant_user_ids.add(ch.challenger_id)
                if ch.challenged_id:
                    participant_user_ids.add(ch.challenged_id)

            for uid in sorted(participant_user_ids):
                existing = (
                    db.query(CardDesignOwnership)
                    .filter_by(
                        user_id=uid,
                        card_type_id="challenge_card",
                        design_id="challenge",
                    )
                    .first()
                )
                if existing:
                    cc_skipped += 1
                    continue
                if not dry_run:
                    db.add(
                        CardDesignOwnership(
                            user_id=uid,
                            card_type_id="challenge_card",
                            design_id="challenge",
                            source=source,
                            credit_transaction_id=None,
                        )
                    )
                cc_created += 1

            if not dry_run and cc_created > 0:
                db.commit()

        # ── Summary ───────────────────────────────────────────────────────────
        if card_type in ("welcome_card", "all"):
            print(
                f"Welcome Card grants:  created={wc_created} / skipped(already owned)={wc_skipped}"
            )
        if card_type in ("challenge_card", "all"):
            print(
                f"Challenge Card grants: created={cc_created} / skipped(already owned)={cc_skipped}"
            )
        if dry_run:
            print("Dry-run: no changes written to the database.")

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill card_design_ownerships for existing users."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database.",
    )
    parser.add_argument(
        "--card-type",
        choices=["welcome_card", "challenge_card", "all"],
        default="all",
        help="Which card family to backfill (default: all).",
    )
    parser.add_argument(
        "--source",
        default="system",
        help="Ownership source string to write (default: 'system').",
    )
    args = parser.parse_args()
    _run(dry_run=args.dry_run, card_type=args.card_type, source=args.source)


if __name__ == "__main__":
    main()
