"""seed_mood_photos.py — Dev/test helper: populate all 9 mood photo slots for a user.

PURPOSE
-------
Enables rapid end-to-end testing of the automatic mood photo selection feature
(Phase-A / Phase-B) without manually uploading 9 images through the web UI.

Each slot gets a real PNG file generated in-memory (solid-color 200×200 px).
The file is saved to app/static/uploads/mood_photos/ and a UserMoodPhoto DB row
is created/updated — exactly the same schema as a real upload.

USAGE
-----
  # Seed by user email (most common):
  python scripts/seed_mood_photos.py --email student@lfa.com

  # Seed by user ID:
  python scripts/seed_mood_photos.py --user-id 42

  # Dry-run — print what would be seeded without writing:
  python scripts/seed_mood_photos.py --email student@lfa.com --dry-run

  # Delete all mood photos for a user (clean slate):
  python scripts/seed_mood_photos.py --email student@lfa.com --delete

  # Custom DB URL (defaults to DATABASE_URL env var):
  DATABASE_URL=postgresql://... python scripts/seed_mood_photos.py --email ...

SAFETY
------
- Only modifies the user you explicitly specify.
- Never touches production data unless you explicitly point it at a production DB.
- The generated images are visually distinct (different colours per slot) so you
  can tell which mood photo is being shown in the preview.
- Existing photos for a slot are overwritten (upsert behaviour — same as the
  web upload flow).

COLOUR MAP (per slot)
----------------------
  mood_intro_neutral      → grey   #808080
  mood_happy_smile        → yellow #FFD700
  mood_celebration        → green  #00C851
  mood_sad_disappointed   → blue   #4A90E2
  mood_angry_competitive  → red    #FF4444
  mood_surprised_shocked  → orange #FF8C00
  mood_focused_ready      → teal   #00B8A9
  mood_confident          → purple #9B59B6
  mood_proud              → gold   #C8A400
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import png  # type: ignore[import]  # pip install pypng

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system")

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "uploads" / "mood_photos"

_SLOT_COLOURS: dict[str, tuple[int, int, int]] = {
    "mood_intro_neutral":     (128, 128, 128),
    "mood_happy_smile":       (255, 215,   0),
    "mood_celebration":       (  0, 200,  81),
    "mood_sad_disappointed":  ( 74, 144, 226),
    "mood_angry_competitive": (255,  68,  68),
    "mood_surprised_shocked": (255, 140,   0),
    "mood_focused_ready":     (  0, 184, 169),
    "mood_confident":         (155,  89, 182),
    "mood_proud":             (200, 164,   0),
}


def _make_png_bytes(r: int, g: int, b: int, size: int = 200) -> bytes:
    """Generate a solid-colour PNG as bytes (no Pillow dependency)."""
    row = [r, g, b] * size
    rows = [row] * size
    buf = io.BytesIO()
    w = png.Writer(width=size, height=size, greyscale=False, bitdepth=8)
    w.write(buf, rows)
    return buf.getvalue()


def seed_user(user_id: int, dry_run: bool = False) -> None:
    from app.models.user_mood_photos import MOOD_PHOTO_SLOTS, MoodPhotoStatus, UserMoodPhoto
    from app.models.user import User

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        print(f"❌  User id={user_id} not found.")
        db.close()
        return

    print(f"🎯  Seeding mood photos for: {user.email} (id={user.id})")
    print(f"    DB: {DATABASE_URL.split('@')[-1]}")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for slot in sorted(MOOD_PHOTO_SLOTS):
        colour = _SLOT_COLOURS.get(slot, (200, 200, 200))
        ts     = int(time.time())
        fname  = f"{user_id}_mood_{slot}_seed_{ts}.png"
        fpath  = UPLOAD_DIR / fname
        url    = f"/static/uploads/mood_photos/{fname}"

        if dry_run:
            print(f"  [DRY-RUN] would write {fpath} → {url}")
            continue

        # Write PNG file
        fpath.write_bytes(_make_png_bytes(*colour))

        # Upsert DB row
        existing = db.query(UserMoodPhoto).filter_by(user_id=user_id, slot=slot).first()
        if existing:
            existing.original_url      = url
            existing.processed_png_url = None
            existing.status            = MoodPhotoStatus.uploaded.value
            print(f"  ↩  updated  {slot:35s}  {url}")
        else:
            db.add(UserMoodPhoto(
                user_id=user_id, slot=slot,
                original_url=url, processed_png_url=None,
                status=MoodPhotoStatus.uploaded.value,
            ))
            print(f"  ✚  created  {slot:35s}  {url}")

    if not dry_run:
        db.commit()
        print(f"\n✅  All {len(MOOD_PHOTO_SLOTS)} slots seeded. Visit /profile/my-mood-photos to verify.")
    db.close()


def delete_user_mood_photos(user_id: int) -> None:
    from app.models.user_mood_photos import UserMoodPhoto
    from app.models.user import User

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        print(f"❌  User id={user_id} not found.")
        db.close()
        return

    rows = db.query(UserMoodPhoto).filter_by(user_id=user_id).all()
    for row in rows:
        db.delete(row)
    db.commit()
    print(f"🗑️   Deleted {len(rows)} mood photo records for {user.email} (id={user_id}).")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed mood photos for a dev/test user.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email",   type=str, help="User email")
    group.add_argument("--user-id", type=int, help="User ID", dest="user_id")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    parser.add_argument("--delete",  action="store_true", help="Delete all mood photos for this user")
    args = parser.parse_args()

    # Resolve user ID from email if needed
    if args.email:
        from sqlalchemy import create_engine as _ce
        from sqlalchemy.orm import sessionmaker as _sm
        from app.models.user import User
        _db = _sm(bind=_ce(DATABASE_URL))()
        user = _db.query(User).filter(User.email == args.email).first()
        _db.close()
        if user is None:
            print(f"❌  No user found with email {args.email!r}")
            sys.exit(1)
        user_id = user.id
    else:
        user_id = args.user_id

    if args.delete:
        delete_user_mood_photos(user_id)
    else:
        seed_user(user_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
