#!/usr/bin/env python3
"""
seed_card_export_fixtures.py
============================

Seeds two deterministic test users for validating the FIFA Square export layout.
Both users have complete skill data across all four categories so the card
renders with real content — no default/null fallbacks.

Users created (idempotent ON CONFLICT DO UPDATE):
  card.export.logo@lfa-seed.hu    — "Rafael Cardoso"   STRIKER  OVR≈78  WITH  sponsor logo
  card.export.nologо@lfa-seed.hu  — "Horváth Máté"     MF       OVR≈68  WITHOUT sponsor logo

Fixture images generated (Pillow, deterministic — same output every run):
  app/static/dev-fixtures/card_export_portrait_cardoso.png  (450×800, dark blue gradient)
  app/static/dev-fixtures/card_export_portrait_horvath.png  (450×800, dark green gradient)
  app/static/dev-fixtures/sponsor_logo_test.png             (300×120, white, LFA TEST SPONSOR)

Preview URLs after seeding (server must be running):
  http://localhost:8000/players/<id>/card?platform=instagram_square
  http://localhost:8000/players/<id>/card/export?platform=instagram_square

Usage:
  python scripts/seed_card_export_fixtures.py
  DATABASE_URL=postgresql://... python scripts/seed_card_export_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import date, datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import bcrypt
from sqlalchemy import create_engine, text
from app.config import settings

# ── Skill values ──────────────────────────────────────────────────────────────

# Rafael Cardoso — STRIKER, OVR ≈ 77.7 (ADVANCED tier)
_SKILLS_CARDOSO: dict[str, float] = {
    # Outfield (11)
    "ball_control":  83.0,
    "dribbling":     86.0,
    "finishing":     92.0,
    "shot_power":    89.0,
    "long_shots":    76.0,
    "volleys":       73.0,
    "crossing":      66.0,
    "passing":       73.0,
    "heading":       81.0,
    "tackle":        57.0,
    "marking":       51.0,
    # Set Pieces (3)
    "free_kicks":    79.0,
    "corners":       68.0,
    "penalties":     88.0,
    # Mental (8)
    "positioning_off":    88.0,
    "positioning_def":    62.0,
    "vision":             77.0,
    "aggression":         74.0,
    "reactions":          85.0,
    "composure":          82.0,
    "consistency":        76.0,
    "tactical_awareness": 79.0,
    # Physical Fitness (7)
    "acceleration":  91.0,
    "sprint_speed":  89.0,
    "agility":       83.0,
    "jumping":       77.0,
    "strength":      71.0,
    "stamina":       76.0,
    "balance":       81.0,
}

# Horváth Máté — MIDFIELDER, OVR ≈ 68.4 (COMPETENT tier)
_SKILLS_HORVATH: dict[str, float] = {
    # Outfield (11)
    "ball_control":  67.0,
    "dribbling":     71.0,
    "finishing":     59.0,
    "shot_power":    63.0,
    "long_shots":    65.0,
    "volleys":       58.0,
    "crossing":      72.0,
    "passing":       80.0,
    "heading":       62.0,
    "tackle":        68.0,
    "marking":       65.0,
    # Set Pieces (3)
    "free_kicks":    66.0,
    "corners":       74.0,
    "penalties":     70.0,
    # Mental (8)
    "positioning_off":    72.0,
    "positioning_def":    69.0,
    "vision":             76.0,
    "aggression":         64.0,
    "reactions":          71.0,
    "composure":          68.0,
    "consistency":        66.0,
    "tactical_awareness": 73.0,
    # Physical Fitness (7)
    "acceleration":  70.0,
    "sprint_speed":  68.0,
    "agility":       73.0,
    "jumping":       65.0,
    "strength":      67.0,
    "stamina":       72.0,
    "balance":       70.0,
}

# ── Fixture image generation ──────────────────────────────────────────────────

_FIXTURES_DIR = PROJECT_ROOT / "app" / "static" / "dev-fixtures"


def _generate_portrait(path: Path, initials: str, bg_top: tuple, bg_bot: tuple) -> None:
    """Generate a 450×800 portrait PNG with a vertical gradient and large initials."""
    from PIL import Image, ImageDraw, ImageFont
    import math

    W, H = 450, 800
    img = Image.new("RGBA", (W, H))
    draw = ImageDraw.Draw(img)

    # Vertical gradient
    for y in range(H):
        t = y / H
        r = int(bg_top[0] + (bg_bot[0] - bg_top[0]) * t)
        g = int(bg_top[1] + (bg_bot[1] - bg_top[1]) * t)
        b = int(bg_top[2] + (bg_bot[2] - bg_top[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # Large initials centred in the lower 60% of the image
    font_size = 140
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    # Measure and centre
    bbox = draw.textbbox((0, 0), initials, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (W - tw) // 2 - bbox[0]
    ty = int(H * 0.38) - bbox[1]

    # Shadow
    draw.text((tx + 4, ty + 4), initials, fill=(0, 0, 0, 80), font=font)
    # White text
    draw.text((tx, ty), initials, fill=(255, 255, 255, 210), font=font)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "PNG", optimize=True)


def _generate_sponsor_logo(path: Path) -> None:
    """Generate a 300×120 sponsor logo PNG: white background, coloured border, text."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 300, 120
    img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Coloured border
    ACCENT = (30, 80, 180)
    border = 4
    draw.rectangle([border, border, W - border - 1, H - border - 1],
                   outline=ACCENT, width=border)

    # Top accent bar
    draw.rectangle([border, border, W - border - 1, border + 22], fill=ACCENT)

    # "LFA" small text in bar
    try:
        font_sm = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 13)
        font_lg = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 26)
    except Exception:
        try:
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
            font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
        except Exception:
            font_sm = font_lg = ImageFont.load_default()

    draw.text((border + 8, border + 4), "LFA FOOTBALL ACADEMY", fill=(255, 255, 255, 220), font=font_sm)

    # Main "SPONSOR" text
    label = "TEST SPONSOR"
    bb = draw.textbbox((0, 0), label, font=font_lg)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text(((W - tw) // 2 - bb[0], (H + 22 - th) // 2 - bb[1] + 4),
              label, fill=ACCENT, font=font_lg)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "PNG", optimize=True)


def generate_fixtures() -> tuple[str, str, str]:
    """Generate fixture images, return their static URLs."""
    portrait_cardoso = _FIXTURES_DIR / "card_export_portrait_cardoso.png"
    portrait_horvath  = _FIXTURES_DIR / "card_export_portrait_horvath.png"
    logo_path         = _FIXTURES_DIR / "sponsor_logo_test.png"

    if not portrait_cardoso.exists():
        print("  🖼  Generating portrait: card_export_portrait_cardoso.png …")
        _generate_portrait(portrait_cardoso, "RC",
                           bg_top=(18, 35, 72),  bg_bot=(20, 58, 80))
    else:
        print("  ✓  portrait_cardoso already exists — skipped")

    if not portrait_horvath.exists():
        print("  🖼  Generating portrait: card_export_portrait_horvath.png …")
        _generate_portrait(portrait_horvath, "HM",
                           bg_top=(18, 60, 30),  bg_bot=(30, 40, 20))
    else:
        print("  ✓  portrait_horvath already exists — skipped")

    if not logo_path.exists():
        print("  🖼  Generating sponsor logo: sponsor_logo_test.png …")
        _generate_sponsor_logo(logo_path)
    else:
        print("  ✓  sponsor_logo already exists — skipped")

    url_cardoso = "/static/dev-fixtures/card_export_portrait_cardoso.png"
    url_horvath  = "/static/dev-fixtures/card_export_portrait_horvath.png"
    url_logo     = "/static/dev-fixtures/sponsor_logo_test.png"
    return url_cardoso, url_horvath, url_logo


# ── Database seed ─────────────────────────────────────────────────────────────

def _pw(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def seed_users(engine, portrait_cardoso: str, portrait_horvath: str, logo_url: str) -> None:
    from sqlalchemy.orm import sessionmaker
    from app.models.user import User
    from app.models.license import UserLicense

    USERS = [
        {
            "email":       "card.export.logo@lfa-seed.hu",
            "name":        "Rafael Cardoso",
            "nickname":    "cardoso",
            "nationality": "Brazilian",
            "dob":         date(1997, 5, 14),
            "gender":      "Male",
            "xp":          3420,
            "position":    "STRIKER",
            "height_cm":   183,
            "weight_kg":   78,
            "level":       3,
            "right_foot":  76.0,
            "left_foot":   24.0,
            "skills":      _SKILLS_CARDOSO,
            "portrait":    portrait_cardoso,
            "logo":        logo_url,
        },
        {
            "email":       "card.export.nologo@lfa-seed.hu",
            "name":        "Horváth Máté",
            "nickname":    "horvath",
            "nationality": "Hungarian",
            "dob":         date(2004, 9, 30),
            "gender":      "Male",
            "xp":          1070,
            "position":    "MIDFIELDER",
            "height_cm":   178,
            "weight_kg":   70,
            "level":       2,
            "right_foot":  None,
            "left_foot":   None,
            "skills":      _SKILLS_HORVATH,
            "portrait":    portrait_horvath,
            "logo":        None,
        },
    ]

    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        for u in USERS:
            print(f"\n  → {u['email']}")

            # Upsert user via ORM
            user = db.query(User).filter(User.email == u["email"]).first()
            if user is None:
                user = User(
                    email=u["email"],
                    password_hash=_pw("ExportTest123!"),
                    name=u["name"],
                    nickname=u["nickname"],
                    role="STUDENT",
                    is_active=True,
                    onboarding_completed=True,
                    payment_verified=True,
                    date_of_birth=datetime.combine(u["dob"], datetime.min.time()),
                    nationality=u["nationality"],
                    gender=u["gender"],
                    credit_balance=0,
                    credit_purchased=0,
                    xp_balance=u["xp"],
                    nda_accepted=True,
                    parental_consent=True,
                    specialization="LFA_FOOTBALL_PLAYER",
                )
                db.add(user)
                db.flush()
                print(f"     created user_id = {user.id}")
            else:
                user.name                 = u["name"]
                user.nickname             = u["nickname"]
                user.nationality          = u["nationality"]
                user.gender               = u["gender"]
                user.date_of_birth        = datetime.combine(u["dob"], datetime.min.time())
                user.xp_balance           = u["xp"]
                user.onboarding_completed = True
                user.is_active            = True
                print(f"     updated user_id = {user.id}")

            motivation = {
                "position":   u["position"],
                "height_cm":  u["height_cm"],
                "weight_kg":  u["weight_kg"],
            }

            # Upsert license via ORM
            lic = (
                db.query(UserLicense)
                .filter(
                    UserLicense.user_id == user.id,
                    UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                )
                .first()
            )
            if lic is None:
                lic = UserLicense(
                    user_id=user.id,
                    specialization_type="LFA_FOOTBALL_PLAYER",
                    is_active=True,
                    onboarding_completed=True,
                    payment_verified=True,
                    started_at=datetime.now(timezone.utc),
                    current_level=u["level"],
                    max_achieved_level=u["level"],
                    credit_balance=0,
                    credit_purchased=0,
                    renewal_cost=0,
                )
                db.add(lic)
                db.flush()
                print(f"     created license_id = {lic.id}")
            else:
                print(f"     updated license_id = {lic.id}")

            # Always apply full data (idempotent update)
            lic.football_skills           = u["skills"]
            lic.motivation_scores         = motivation
            lic.current_level             = u["level"]
            lic.max_achieved_level        = u["level"]
            lic.onboarding_completed      = True
            lic.is_active                 = True
            lic.right_foot_score          = u["right_foot"]
            lic.left_foot_score           = u["left_foot"]
            lic.card_variant              = "fifa"
            lic.player_card_photo_url     = u["portrait"]
            lic.card_photo_portrait_url   = u["portrait"]
            lic.card_photo_landscape_url  = u["portrait"]
            lic.sponsor_logo_url          = u["logo"]

            avg = sum(u["skills"].values()) / len(u["skills"])
            logo_marker = "✅ logo" if u["logo"] else "⬜ no logo"
            print(f"     OVR ≈ {avg:.1f} | level={u['level']} | {logo_marker}")
            print(f"     Preview: http://localhost:8000/players/{user.id}/card?platform=instagram_square")
            print(f"     Export:  http://localhost:8000/players/{user.id}/card/export?platform=instagram_square")

        db.commit()

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("═" * 62)
    print("  FIFA Square Export Fixtures — Card Layout Validation Seed")
    print("═" * 62)

    print("\n▸ Step 1: Generate fixture images")
    portrait_cardoso, portrait_horvath, logo_url = generate_fixtures()

    print("\n▸ Step 2: Seed users")
    engine = create_engine(settings.DATABASE_URL)
    seed_users(engine, portrait_cardoso, portrait_horvath, logo_url)

    print()
    print("═" * 62)
    print("  ✅  Done — 2 users seeded, fixtures ready")
    print()
    print("  Credentials: ExportTest123!")
    print("  card.export.logo@lfa-seed.hu   → WITH sponsor logo")
    print("  card.export.nologo@lfa-seed.hu → NO sponsor logo")
    print("═" * 62)
    print()


if __name__ == "__main__":
    main()
