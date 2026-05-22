"""Seed Virtual Training game presets.

Safe to run multiple times: new games are inserted, existing games have their
config/skill_targets/base_xp/description updated in-place (idempotent UPDATE).
All non-color_reaction presets remain is_active=False until an admin toggle.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models.virtual_training import VirtualTrainingGame

_GAMES = [
    {
        "code": "color_reaction",
        "name": "Color Reaction",
        "description": (
            "A target-selection reaction trainer. Multiple coloured circles "
            "appear simultaneously — click the one matching the given instruction. "
            "Trains reaction speed, decision-making, and focus under distraction."
        ),
        "game_type": "reaction_time",
        "is_active": True,
        "base_xp": 20,
        "max_daily_attempts": 5,
        "skill_targets": {
            "reactions":     0.35,
            "decisions":     0.30,
            "concentration": 0.20,
            "anticipation":  0.15,
        },
        "config": {
            "phases": [
                {"stimuli": 12, "targets": 3, "delay_ms": 2000, "window_ms": 4000, "diameter_px": 70},
                {"stimuli": 12, "targets": 4, "delay_ms": 1200, "window_ms": 3000, "diameter_px": 64},
                {"stimuli": 12, "targets": 5, "delay_ms":  700, "window_ms": 2200, "diameter_px": 58},
            ],
            "colours": {
                "RED":    "#e74c3c",
                "GREEN":  "#2ecc71",
                "BLUE":   "#3498db",
                "YELLOW": "#f39c12",
                "PURPLE": "#9b59b6",
                "ORANGE": "#e67e22",
            },
            "miss_penalty_ms":  300,
            "wrong_penalty_ms": 200,
        },
    },
    {
        "code": "stroop_challenge",
        "name": "Stroop Challenge",
        "description": (
            "A cognitive inhibition task based on the Stroop effect. "
            "A colour word is displayed in an incongruent ink colour; "
            "respond to the ink colour, not the word. "
            "Trains decision-making under interference and concentration."
        ),
        "game_type": "cognitive_inhibition",
        "is_active": False,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "decisions":     0.50,
            "concentration": 0.30,
            "composure":     0.20,
        },
        "config": {
            "trial_count": 12,
            "response_window_ms": 3000,
            "words":   ["RED", "GREEN", "BLUE", "YELLOW"],
            "colours": ["#e74c3c", "#2ecc71", "#3498db", "#f1c40f"],
        },
    },
    {
        "code": "go_no_go",
        "name": "Go / No-Go",
        "description": (
            "An impulse control task. Respond quickly to a target stimulus "
            "(Go) but withhold the response to a distractor (No-Go). "
            "Trains composure, concentration and quick decision-making."
        ),
        "game_type": "go_no_go",
        "is_active": False,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "composure":     0.40,
            "concentration": 0.35,
            "decisions":     0.25,
        },
        "config": {
            "trial_count":        20,
            "go_ratio":           0.75,
            "stimulus_duration_ms": 800,
            "inter_trial_ms":     1000,
        },
    },
]

# Fields updated when a game already exists (config migrations)
_UPDATE_FIELDS = ("config", "skill_targets", "base_xp", "description", "is_active")


def seed_virtual_training_games() -> None:
    db = SessionLocal()
    try:
        inserted = 0
        updated  = 0
        for data in _GAMES:
            existing = (
                db.query(VirtualTrainingGame)
                .filter(VirtualTrainingGame.code == data["code"])
                .first()
            )
            if existing:
                for field in _UPDATE_FIELDS:
                    if field in data:
                        setattr(existing, field, data[field])
                updated += 1
                print(f"  upd   {data['code']} (id={existing.id})")
            else:
                game = VirtualTrainingGame(**data)
                db.add(game)
                inserted += 1
                print(f"  +     {data['code']} (is_active={data['is_active']})")

        db.commit()
        print(
            f"\nDone. {inserted} game(s) inserted, {updated} updated, "
            f"{len(_GAMES) - inserted - updated} skipped."
        )
    except Exception as exc:
        db.rollback()
        print(f"Error: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_virtual_training_games()
