#!/usr/bin/env python3
"""
Dev-only: Clean up stuck / broken VT challenge records.

A challenge is "stuck" when it is in PENDING or ACCEPTED status but has
challenge_config_snapshot=NULL and no linked attempts, making it both
unplayable (snapshot guard in submit route) and permanently blocking new
challenges between the same pair on the same game.

Usage:
    python scripts/dev_cleanup_stuck_challenges.py          # dry-run (default)
    python scripts/dev_cleanup_stuck_challenges.py --apply  # execute deletes

Safety:
    - Refuses to run outside ENVIRONMENT=development / dev.
    - Every deletion is gated by explicit assertions; aborts on any failure.
    - Never touches users, friendships, licenses, skill profiles,
      virtual_training_attempts, completed challenges, or notifications.
"""
from __future__ import annotations

import os
import sys

# ── Safety: dev-only ──────────────────────────────────────────────────────────
_ENV = os.getenv("ENVIRONMENT", "development").lower()
if _ENV not in ("development", "dev"):
    print(f"[ABORT] Refusing to run in ENVIRONMENT={_ENV!r}. Dev-only script.")
    sys.exit(1)

DRY_RUN: bool = "--apply" not in sys.argv

# ── Stuck challenge specification ─────────────────────────────────────────────
# Each entry is a dict of expected field values.  ALL must match before delete.
_STUCK_CHALLENGES: list[dict] = [
    {
        "id":                    1,
        "status":                "accepted",
        "snapshot_must_be_null": True,
        "challenger_attempt_id": None,
        "challenged_attempt_id": None,
        "challenger_id":         3,
        "challenged_id":         3617,
        "game_id":               6,
        "description":           "Async MS challenge accepted pre-snapshot, never played",
    },
]


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        print(f"[ABORT] Assertion failed: {msg}")
        sys.exit(2)


def run() -> None:
    # Import app DB *after* env guard so tests can mock without a real DB
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.database import SessionLocal
    from app.models.vt_challenge import VirtualTrainingChallenge, ChallengeStatus

    db = SessionLocal()
    try:
        deleted = 0
        skipped = 0

        for spec in _STUCK_CHALLENGES:
            cid  = spec["id"]
            desc = spec["description"]

            ch = (
                db.query(VirtualTrainingChallenge)
                .filter(VirtualTrainingChallenge.id == cid)
                .first()
            )

            if ch is None:
                print(f"[SKIP] Challenge {cid} not found — already deleted or never existed.")
                skipped += 1
                continue

            print(f"\n[FOUND] Challenge id={cid}: {desc}")
            print(f"        status={ch.status.value}  game_id={ch.game_id}")
            print(f"        challenger={ch.challenger_id}  challenged={ch.challenged_id}")
            print(f"        snapshot={'PRESENT' if ch.challenge_config_snapshot is not None else 'NULL'}")
            print(f"        challenger_attempt_id={ch.challenger_attempt_id}")
            print(f"        challenged_attempt_id={ch.challenged_attempt_id}")

            # ── Safety assertions ─────────────────────────────────────────────
            _assert(
                ch.challenger_attempt_id is None,
                f"Challenge {cid} has challenger_attempt_id={ch.challenger_attempt_id} — NOT safe to delete",
            )
            _assert(
                ch.challenged_attempt_id is None,
                f"Challenge {cid} has challenged_attempt_id={ch.challenged_attempt_id} — NOT safe to delete",
            )
            _assert(
                ch.challenge_config_snapshot is None,
                f"Challenge {cid} has a snapshot — this is not a broken record, aborting",
            )
            _assert(
                ch.status in (ChallengeStatus.PENDING, ChallengeStatus.ACCEPTED),
                f"Challenge {cid} status={ch.status.value} is not PENDING/ACCEPTED — aborting",
            )
            _assert(
                ch.challenger_id == spec["challenger_id"],
                f"Challenge {cid} challenger_id={ch.challenger_id} != expected {spec['challenger_id']}",
            )
            _assert(
                ch.challenged_id == spec["challenged_id"],
                f"Challenge {cid} challenged_id={ch.challenged_id} != expected {spec['challenged_id']}",
            )
            _assert(
                ch.game_id == spec["game_id"],
                f"Challenge {cid} game_id={ch.game_id} != expected {spec['game_id']}",
            )

            if DRY_RUN:
                print(f"  [DRY-RUN] Would DELETE challenge {cid}. Run with --apply to execute.")
                skipped += 1
            else:
                db.delete(ch)
                db.commit()
                print(f"  [DELETED] Challenge {cid} removed from vt_challenges.")
                deleted += 1

        print(f"\n{'[DRY-RUN] ' if DRY_RUN else ''}Summary: {deleted} deleted, {skipped} skipped.")
        if DRY_RUN and skipped:
            print("  Run with --apply to execute deletes.")

    finally:
        db.close()


if __name__ == "__main__":
    run()
