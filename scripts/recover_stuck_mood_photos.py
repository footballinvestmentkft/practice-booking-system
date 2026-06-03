"""recover_stuck_mood_photos.py — Admin recovery for stuck 'processing' mood photos.

PURPOSE
-------
Finds UserMoodPhoto records stuck in status='processing' that have exceeded
PROCESSING_TIMEOUT_SECONDS (default: 300s / 5 minutes) and resets them to
a recoverable state so the user can re-trigger or upload a new image.

SAFETY RULES (non-negotiable)
------------------------------
- Only touches records where status='processing' AND updated_at > timeout.
- Never touches records in status='ready' or 'uploaded' or 'failed'.
- Never touches fresh/recent 'processing' records (within the timeout window).
- If original file exists  → reset to 'uploaded' (worker can re-process).
- If original file missing → set to 'failed'   (honest terminal state).
- Does NOT re-enqueue Celery tasks automatically (avoids uncontrolled mass processing).
- Dry-run mode by default — use --execute to actually commit changes.

USAGE
-----
  # Dry-run (default): shows what WOULD happen without changing anything
  python scripts/recover_stuck_mood_photos.py

  # Execute: actually reset stuck records
  python scripts/recover_stuck_mood_photos.py --execute

  # Limit to one user
  python scripts/recover_stuck_mood_photos.py --email rdias@manchestercity.com --execute

  # Custom timeout threshold (default: uses PROCESSING_TIMEOUT_SECONDS from settings)
  python scripts/recover_stuck_mood_photos.py --timeout-seconds 120 --execute

  # Via Makefile:
  make recover-mood          # dry-run
  make recover-mood-execute  # live run

OUTPUT
------
Each line shows: user_id | slot | original_url_exists | action_taken
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

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "uploads" / "mood_photos"


def _load_settings_timeout() -> int:
    """Load PROCESSING_TIMEOUT_SECONDS from app settings (graceful fallback)."""
    try:
        from app.config import settings
        return settings.PROCESSING_TIMEOUT_SECONDS
    except Exception:
        return 300  # 5 minute default


def recover_stuck(
    user_email: str | None = None,
    timeout_seconds: int | None = None,
    execute: bool = False,
) -> None:
    from app.models.user_mood_photos import MoodPhotoStatus, UserMoodPhoto
    from app.models.user import User

    timeout = timeout_seconds or _load_settings_timeout()
    cutoff  = datetime.now(timezone.utc) - timedelta(seconds=timeout)

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()

    print(f"{'[DRY-RUN] ' if not execute else '[EXECUTE] '}Recovering stuck mood photos")
    print(f"  DB:      {DATABASE_URL.split('@')[-1]}")
    print(f"  Timeout: {timeout}s  (cutoff: {cutoff.strftime('%Y-%m-%d %H:%M:%S UTC')})")
    if user_email:
        print(f"  Filter:  user={user_email!r}")
    print()

    query = (
        db.query(UserMoodPhoto)
        .filter(
            UserMoodPhoto.status == MoodPhotoStatus.processing.value,
            UserMoodPhoto.updated_at < cutoff,
        )
    )

    if user_email:
        user = db.query(User).filter(User.email == user_email).first()
        if user is None:
            print(f"❌  No user found with email {user_email!r}")
            db.close()
            return
        query = query.filter(UserMoodPhoto.user_id == user.id)

    stuck_records = query.all()

    if not stuck_records:
        print("✅  No stuck processing records found.")
        db.close()
        return

    print(f"Found {len(stuck_records)} stuck record(s):\n")

    reset_count  = 0
    failed_count = 0

    for rec in stuck_records:
        age_seconds = int((datetime.now(timezone.utc) - rec.updated_at).total_seconds())
        orig_path   = UPLOAD_DIR / Path(rec.original_url).name
        file_exists = orig_path.exists()

        if file_exists:
            action = "RESET → uploaded"
        else:
            action = "SET → failed (original file missing)"

        print(
            f"  user_id={rec.user_id}  slot={rec.slot!r:30s}"
            f"  age={age_seconds}s  file={'✅' if file_exists else '❌'}  action={action}"
        )

        if execute:
            if file_exists:
                rec.status            = MoodPhotoStatus.uploaded.value
                rec.processed_png_url = None
                reset_count += 1
            else:
                rec.status    = MoodPhotoStatus.failed.value
                failed_count += 1

    if execute:
        db.commit()
        print(f"\n✅  Done: {reset_count} reset to 'uploaded', {failed_count} set to 'failed'.")
        print("   Re-upload or use the ↺ Remove Background button on the mood photos page.")
    else:
        print(f"\n[DRY-RUN] Would reset {sum(1 for r in stuck_records if (UPLOAD_DIR / Path(r.original_url).name).exists())} record(s).")
        print("   Run with --execute to apply changes.")

    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover stuck 'processing' mood photo records."
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
        help="Override timeout threshold in seconds (default: PROCESSING_TIMEOUT_SECONDS from settings)",
    )
    args = parser.parse_args()
    recover_stuck(
        user_email=args.email,
        timeout_seconds=args.timeout_seconds,
        execute=args.execute,
    )


if __name__ == "__main__":
    main()
