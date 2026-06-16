"""recover_stuck_juggling.py — Admin recovery for stuck 'processing' juggling videos.

PURPOSE
-------
Finds JugglingVideo records stuck in status='processing' that have exceeded
JUGGLING_PROCESSING_TIMEOUT_SECONDS (default: 600s / 10 minutes) and resets
them to a recoverable state.

The timeout is intentionally generous: transcode_video_task has a hard
time_limit of 240 s and analyze_video_task 90 s, so any record that has been
'processing' for more than 10 minutes is definitively stuck (worker crashed or
was never started).

SAFETY RULES (non-negotiable)
------------------------------
- Only touches records where status='processing' AND updated_at older than timeout.
- Never touches records in any terminal status (analyzed/rejected/failed/gdpr_deleted).
- Never touches fresh/recent 'processing' records (within the timeout window).
- If storage_path file exists → reset to 'uploaded' (worker can re-process).
- If storage_path file missing → set to 'failed'  (honest terminal state).
- Does NOT re-enqueue Celery tasks automatically (avoids uncontrolled mass processing).
- Dry-run mode by default — use --execute to actually commit changes.

USAGE
-----
  # Dry-run (default): shows what WOULD happen without changing anything
  python scripts/recover_stuck_juggling.py

  # Execute: actually reset stuck records
  python scripts/recover_stuck_juggling.py --execute

  # Limit to one user
  python scripts/recover_stuck_juggling.py --email player@lfa.example.com --execute

  # Custom timeout threshold (default: JUGGLING_PROCESSING_TIMEOUT_SECONDS from settings)
  python scripts/recover_stuck_juggling.py --timeout-seconds 300 --execute

  # Via Makefile:
  make recover-juggling          # dry-run
  make recover-juggling-execute  # live run

OUTPUT
------
Each line shows: video_id | user_id | age | file | action_taken
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/lfa_intern_system",
)

# Default: 10 minutes — covers transcode (240 s hard limit) + analyze (90 s) + margin
_DEFAULT_TIMEOUT_SECONDS = 600


def _load_settings_timeout() -> int:
    try:
        from app.config import settings
        return getattr(settings, "JUGGLING_PROCESSING_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
    except Exception:
        return _DEFAULT_TIMEOUT_SECONDS


def recover_stuck(
    user_email: str | None = None,
    timeout_seconds: int | None = None,
    execute: bool = False,
) -> None:
    from app.models.juggling import JugglingVideo, JugglingVideoStatus
    from app.models.user import User

    timeout = timeout_seconds or _load_settings_timeout()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout)

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()

    label = "[EXECUTE]" if execute else "[DRY-RUN]"
    print(f"{label} Recovering stuck juggling videos")
    print(f"  DB:      {DATABASE_URL.split('@')[-1]}")
    print(f"  Timeout: {timeout}s  (cutoff: {cutoff.strftime('%Y-%m-%d %H:%M:%S UTC')})")
    if user_email:
        print(f"  Filter:  user={user_email!r}")
    print()

    query = db.query(JugglingVideo).filter(
        JugglingVideo.status == JugglingVideoStatus.processing.value,
        JugglingVideo.updated_at < cutoff,
    )

    if user_email:
        user = db.query(User).filter(User.email == user_email).first()
        if user is None:
            print(f"❌  No user found with email {user_email!r}")
            db.close()
            return
        query = query.filter(JugglingVideo.user_id == user.id)

    stuck = query.all()

    if not stuck:
        print("✅  No stuck processing records found.")
        db.close()
        return

    print(f"Found {len(stuck)} stuck record(s):\n")

    reset_count = 0
    failed_count = 0

    for rec in stuck:
        age_seconds = int((datetime.now(timezone.utc) - rec.updated_at).total_seconds())
        file_exists = bool(rec.storage_path and Path(rec.storage_path).exists())

        if file_exists:
            action = "RESET → uploaded  (file present; re-enqueue with complete endpoint)"
        else:
            action = "SET   → failed    (storage file missing)"

        print(
            f"  video_id={str(rec.id)[:8]}…  user_id={str(rec.user_id)[:8]}…"
            f"  age={age_seconds}s  file={'✅' if file_exists else '❌'}  action={action}"
        )

        if execute:
            now = datetime.now(timezone.utc)
            if file_exists:
                rec.status = JugglingVideoStatus.uploaded.value
                rec.updated_at = now
                reset_count += 1
            else:
                rec.status = JugglingVideoStatus.failed.value
                rec.rejection_reason = "recovered_missing_file"
                rec.updated_at = now
                failed_count += 1

    if execute:
        db.commit()
        print(
            f"\n✅  Done: {reset_count} reset to 'uploaded', {failed_count} set to 'failed'."
        )
        print(
            "   To re-process an 'uploaded' video, call POST /complete on it,\n"
            "   which re-enqueues transcode_video_task to the juggling_videos queue.\n"
            "   Ensure 'make worker-juggling' is running to consume the task."
        )
    else:
        would_reset = sum(
            1 for r in stuck if r.storage_path and Path(r.storage_path).exists()
        )
        would_fail = len(stuck) - would_reset
        print(
            f"\n[DRY-RUN] Would reset {would_reset} to 'uploaded', "
            f"set {would_fail} to 'failed'."
        )
        print("   Run with --execute to apply changes.")

    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover stuck 'processing' juggling video records."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes (default is dry-run)",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Limit recovery to one user by email",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        dest="timeout_seconds",
        help=(
            "Override timeout threshold in seconds "
            f"(default: {_DEFAULT_TIMEOUT_SECONDS}s / 10 minutes)"
        ),
    )
    args = parser.parse_args()
    recover_stuck(
        user_email=args.email,
        timeout_seconds=args.timeout_seconds,
        execute=args.execute,
    )


if __name__ == "__main__":
    main()
