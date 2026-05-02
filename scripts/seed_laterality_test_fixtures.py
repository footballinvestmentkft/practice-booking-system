"""
Seed laterality test fixtures.

Creates 6 GamePresets (shooting × right/left/neutral  +  crossing × right/left/neutral)
and 4 control users with LFA licenses for laterality-aware skill routing tests.

Idempotent: code-based for presets, email-based for users.

Exposes seed_laterality_fixtures(db) so pytest session fixtures can call it
directly without a manual pre-run.  __main__ calls the same function.

Run standalone:
  DATABASE_URL="..." python scripts/seed_laterality_test_fixtures.py
"""
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.game_preset import GamePreset
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.services.skill_progression._config import get_all_skill_keys
from app.core.security import get_password_hash

_SKILL_KEYS = get_all_skill_keys()

# ── Preset definitions ────────────────────────────────────────────────────────
# Skill weights identical within each group; only foot_context differs.

_LAT_PRESETS = [
    {
        "code": "lat_shooting_right",
        "name": "Laterality Test: Shooting (right foot)",
        "foot_context": "right",
        "skills": ["finishing", "shot_power"],
    },
    {
        "code": "lat_shooting_left",
        "name": "Laterality Test: Shooting (left foot)",
        "foot_context": "left",
        "skills": ["finishing", "shot_power"],
    },
    {
        "code": "lat_shooting_neutral",
        "name": "Laterality Test: Shooting (neutral)",
        "foot_context": "neutral",
        "skills": ["finishing", "shot_power"],
    },
    {
        "code": "lat_crossing_right",
        "name": "Laterality Test: Crossing (right foot)",
        "foot_context": "right",
        "skills": ["crossing"],
    },
    {
        "code": "lat_crossing_left",
        "name": "Laterality Test: Crossing (left foot)",
        "foot_context": "left",
        "skills": ["crossing"],
    },
    {
        "code": "lat_crossing_neutral",
        "name": "Laterality Test: Crossing (neutral)",
        "foot_context": "neutral",
        "skills": ["crossing"],
    },
]

# ── Control users ─────────────────────────────────────────────────────────────

_CONTROL_USERS = [
    {
        "email": "lat.right@lfa-test.com",
        "name": "Lat Right Dominant",
        "right_foot_score": 80.0,
        "left_foot_score": 20.0,
    },
    {
        "email": "lat.left@lfa-test.com",
        "name": "Lat Left Dominant",
        "right_foot_score": 20.0,
        "left_foot_score": 80.0,
    },
    {
        "email": "lat.balanced@lfa-test.com",
        "name": "Lat Balanced",
        "right_foot_score": 50.0,
        "left_foot_score": 50.0,
    },
    {
        "email": "lat.unmeasured@lfa-test.com",
        "name": "Lat Unmeasured",
        "right_foot_score": None,
        "left_foot_score": None,
    },
]


def _build_football_skills() -> dict:
    """
    Flat float format — the orchestrator normalises scalars to V2 dict on first
    write-back, so this is the lightest valid seed format.

    finishing=65.0, crossing=62.0, all other 27 skills=62.0.
    """
    base = {k: 62.0 for k in _SKILL_KEYS}
    base["finishing"] = 65.0
    return base


def _build_preset_game_config(foot_context: str, skills: list) -> dict:
    return {
        "version": "1.0",
        "metadata": {
            "game_category": "FOOTBALL",
            "difficulty_level": "intermediate",
            "min_players": 2,
            "recommended_player_count": {"min": 2, "max": 16},
        },
        "skill_config": {
            "foot_context": foot_context,
            "skills_tested": skills,
            "skill_weights": {s: 1.5 for s in skills},
        },
        "format_config": {},
        "simulation_config": {},
    }


# ── Public API ────────────────────────────────────────────────────────────────

def seed_laterality_fixtures(db: Session) -> dict:
    """
    Idempotent seed: inserts missing records, skips existing ones.

    Returns
    -------
    dict with two keys:
      "presets" : {code: preset_id}
      "users"   : {email: user_id}
    """
    preset_ids: dict = {}

    for defn in _LAT_PRESETS:
        existing = db.query(GamePreset).filter(GamePreset.code == defn["code"]).first()
        if existing:
            preset_ids[defn["code"]] = existing.id
            continue

        preset = GamePreset(
            code=defn["code"],
            name=defn["name"],
            description=f"Laterality test preset — foot_context={defn['foot_context']}",
            game_config=_build_preset_game_config(defn["foot_context"], defn["skills"]),
            is_active=True,
            is_recommended=False,
            is_locked=False,
        )
        db.add(preset)
        db.flush()
        preset_ids[defn["code"]] = preset.id

    user_ids: dict = {}
    football_skills = _build_football_skills()

    for u in _CONTROL_USERS:
        user = db.query(User).filter(User.email == u["email"]).first()
        if not user:
            user = User(
                email=u["email"],
                name=u["name"],
                password_hash=get_password_hash("LatTest123!"),
                role=UserRole.STUDENT,
                is_active=True,
            )
            db.add(user)
            db.flush()
        user_ids[u["email"]] = user.id

        lic = db.query(UserLicense).filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        ).first()

        if not lic:
            lic = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc),
                is_active=True,
                right_foot_score=u["right_foot_score"],
                left_foot_score=u["left_foot_score"],
                football_skills=football_skills,
            )
            db.add(lic)
        else:
            # Idempotent reset — ensure test-clean state
            lic.is_active = True
            lic.right_foot_score = u["right_foot_score"]
            lic.left_foot_score = u["left_foot_score"]
            lic.football_skills = football_skills

        db.flush()

    db.commit()
    return {"presets": preset_ids, "users": user_ids}


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    db: Session = SessionLocal()
    try:
        print("🦵 Seeding laterality test fixtures (idempotent)…")
        print("=" * 70)
        result = seed_laterality_fixtures(db)

        print("\nPresets:")
        for code, pid in result["presets"].items():
            print(f"  [{pid:>4}] {code}")

        print("\nControl users:")
        for email, uid in result["users"].items():
            print(f"  [{uid:>4}] {email}")

        print("\n✅ Done.")
    except Exception:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()
