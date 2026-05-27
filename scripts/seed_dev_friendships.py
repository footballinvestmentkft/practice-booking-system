"""Seed accepted friendships between bootstrap LFA Adult player pairs.

Idempotent: skips existing ACCEPTED pairs, upgrades PENDING → ACCEPTED,
replaces DECLINED with ACCEPTED. Safe to run multiple times.

All pairs use bootstrap-guaranteed users (seeded by bootstrap_clean.py LFA Adult team),
so this script is safe to run immediately after any fresh bootstrap.

Usage:
    PYTHONPATH=. python scripts/seed_dev_friendships.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/lfa_intern_system",
)

from app.database import SessionLocal                       # noqa: E402
from app.models.friendship import Friendship, FriendshipStatus  # noqa: E402
from app.models.user import User                            # noqa: E402

# Bootstrap-guaranteed pairs — all seeded by bootstrap_clean.py LFA Adult team.
# Passwords: Bootstrap#123
_DEV_PAIRS: list[tuple[str, str]] = [
    ("lfa-adult-robert.adams@lfa.com",     "lfa-adult-michael.baker@lfa.com"),
    ("lfa-adult-christopher.cole@lfa.com", "lfa-adult-andrew.davis@lfa.com"),
    ("lfa-adult-jonathan.evans@lfa.com",   "lfa-adult-matthew.fisher@lfa.com"),
    ("lfa-adult-benjamin.gray@lfa.com",    "lfa-adult-nicholas.hall@lfa.com"),
]


def _find_user(db, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def _find_existing(db, user_a_id: int, user_b_id: int) -> Friendship | None:
    return (
        db.query(Friendship)
        .filter(
            (
                (Friendship.requester_id == user_a_id) & (Friendship.addressee_id == user_b_id)
            ) | (
                (Friendship.requester_id == user_b_id) & (Friendship.addressee_id == user_a_id)
            )
        )
        .first()
    )


def seed_dev_friendships() -> dict:
    """Create/upgrade friendship pairs. Returns summary dict."""
    db = SessionLocal()
    created = upgraded = skipped = 0
    try:
        for email_a, email_b in _DEV_PAIRS:
            user_a = _find_user(db, email_a)
            user_b = _find_user(db, email_b)

            if user_a is None:
                print(f"  ⚠️  Skip — user not found: {email_a}")
                skipped += 1
                continue
            if user_b is None:
                print(f"  ⚠️  Skip — user not found: {email_b}")
                skipped += 1
                continue

            existing = _find_existing(db, user_a.id, user_b.id)

            if existing is None:
                db.add(Friendship(
                    requester_id=user_a.id,
                    addressee_id=user_b.id,
                    status=FriendshipStatus.ACCEPTED,
                ))
                db.flush()
                created += 1
                print(f"  +  ACCEPTED  {email_a}  ↔  {email_b}")

            elif existing.status == FriendshipStatus.ACCEPTED:
                skipped += 1
                print(f"  ⏭️  Already ACCEPTED  {email_a}  ↔  {email_b}")

            elif existing.status in (FriendshipStatus.PENDING, FriendshipStatus.DECLINED):
                old_status = existing.status.value
                existing.status = FriendshipStatus.ACCEPTED
                db.flush()
                upgraded += 1
                print(f"  ↑  {old_status} → ACCEPTED  {email_a}  ↔  {email_b}")

            else:
                # BLOCKED — leave untouched
                skipped += 1
                print(f"  ⏭️  Skip (BLOCKED)  {email_a}  ↔  {email_b}")

        db.commit()
        print(
            f"\nDone. {created} created, {upgraded} upgraded, {skipped} skipped."
        )
        return {"created": created, "upgraded": upgraded, "skipped": skipped}

    except Exception as exc:
        db.rollback()
        print(f"Error: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_dev_friendships()
