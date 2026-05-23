"""Seed Virtual Training game presets.

Safe to run multiple times: new games are inserted, existing games have their
config/skill_targets/base_xp/description/name updated in-place (idempotent UPDATE).
All non-color_reaction presets remain is_active=False until an admin toggle.

show_in_hub=False in config → game excluded from the Virtual Games hub display.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models.virtual_training import VirtualTrainingGame

_GAMES = [
    # ── 1. Color Reaction (ACTIVE) ────────────────────────────────────────────
    {
        "code": "color_reaction",
        "name": "Color Reaction",
        "description": (
            "Select the colour-matching target from multiple simultaneous distractors. "
            "Three progressively faster phases — reactions, accuracy and concentration all tested."
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
            # runtime keys (used by gameplay)
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
            "miss_penalty_ms":  500,
            "wrong_penalty_ms": 300,
            # display keys (used by hub template)
            "show_in_hub":      True,
            "icon":             "⚡",
            "football_benefit": (
                "Quick visual decisions under pressure, sharp focus amid distracting "
                "stimuli, and accurate reactions in high-intensity moments."
            ),
        },
    },

    # ── 2. stroop_challenge (hidden — not in user-facing catalog) ─────────────
    {
        "code": "stroop_challenge",
        "name": "Stroop Challenge",
        "description": (
            "A cognitive inhibition task based on the Stroop effect. "
            "A colour word is displayed in an incongruent ink colour; "
            "respond to the ink colour, not the word."
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
            "trial_count":        12,
            "response_window_ms": 3000,
            "words":   ["RED", "GREEN", "BLUE", "YELLOW"],
            "colours": ["#e74c3c", "#2ecc71", "#3498db", "#f1c40f"],
            # hidden from the Virtual Games hub
            "show_in_hub": False,
        },
    },

    # ── 3. Go / No-Go Reaction ────────────────────────────────────────────────
    {
        "code": "go_no_go",
        "name": "Go / No-Go Reaction",
        "description": (
            "React quickly to Go stimuli; withhold the response entirely on No-Go stimuli. "
            "Trains impulse control alongside rapid reaction."
        ),
        "game_type": "go_no_go",
        "is_active": True,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "decisions":     0.35,
            "concentration": 0.30,
            "composure":     0.20,
            "reactions":     0.15,
        },
        "config": {
            # ── runtime gameplay keys ──────────────────────────────────────
            # 2-phase session: Phase 0 slower, Phase 1 faster ISI
            # go + no_go per phase must sum to 30 total (21 GO + 9 NO-GO)
            "phases": [
                {"go": 10, "no_go": 5, "isi_ms": 900,  "window_ms": 1000, "stimulus_ms": 800},
                {"go": 11, "no_go": 4, "isi_ms": 650,  "window_ms": 1000, "stimulus_ms": 800},
            ],
            "go_cue":    {"color": "#22c55e", "label": "GO"},
            "no_go_cue": {"color": "#ef4444", "label": "STOP"},
            # Score formula weights — kept in config for transparency / future tuning
            # score_raw = 0.40*go_hit_rate + 0.35*(1-no_go_fail_rate)
            #           + 0.15*speed_factor - 0.10*missed_go_rate
            "score_weights": {
                "go_hit_rate":      0.40,
                "no_go_success":    0.35,
                "speed_factor":     0.15,
                "missed_go_penalty": 0.10,
            },
            # Skill delta scorers reference (for result page documentation):
            # decisions    = hit_rate - 1.5 * wrong_rate (GO perf + false alarm penalty)
            # concentration = 1 - 2 * miss_rate          (missed GO = attention lapse)
            # composure    = 1 - 1.5 * wrong_rate        (false alarm = impulse failure)
            # reactions    = 0.65 * speed_score + 0.35 * hit_rate (GO hit speed)
            # ── display keys ──────────────────────────────────────────────
            "show_in_hub": True,
            "icon": "🛑",
            "football_benefit": (
                "Impulse control, avoiding premature commitments, and executing "
                "responses only at the right moment under pressure."
            ),
        },
    },

    # ── 4. Direction Swipe ────────────────────────────────────────────────────
    {
        "code": "direction_swipe",
        "name": "Direction Swipe",
        "description": (
            "An arrow or directional cue appears on screen — swipe or click in the "
            "indicated direction as quickly as possible. Speed and motor accuracy tested."
        ),
        "game_type": "direction_recognition",
        "is_active": False,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "reactions":     0.35,
            "decisions":     0.30,
            "coordination":  0.20,
            "concentration": 0.15,
        },
        "config": {
            "show_in_hub":      True,
            "icon":             "↕️",
            "football_benefit": (
                "Fast directional recognition, motor-visual switching, and "
                "support for change-of-direction decisions on the pitch."
            ),
        },
    },

    # ── 5. Number-Color Conflict ──────────────────────────────────────────────
    {
        "code": "number_color_conflict",
        "name": "Number-Color Conflict",
        "description": (
            "A number and a colour appear simultaneously — the instruction decides "
            "whether to respond to the number or the colour. Rule-switching tested each round."
        ),
        "game_type": "cognitive_inhibition",
        "is_active": False,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "decisions":     0.40,
            "concentration": 0.30,
            "composure":     0.20,
            "reactions":     0.10,
        },
        "config": {
            "show_in_hub":      True,
            "icon":             "🔢",
            "football_benefit": (
                "Filtering conflicting information, rule-based rapid decisions, "
                "and mental control under pressure."
            ),
        },
    },

    # ── 6. Memory Sequence ────────────────────────────────────────────────────
    {
        "code": "memory_sequence",
        "name": "Memory Sequence",
        "description": (
            "Colours or symbols flash in sequence — reproduce the exact order by "
            "clicking the targets in the same pattern. Sequence length increases each round."
        ),
        "game_type": "memory_span",
        "is_active": False,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "concentration":    0.35,
            "tactical_awareness": 0.25,
            "decisions":        0.25,
            "composure":        0.15,
        },
        "config": {
            "show_in_hub":      True,
            "icon":             "🧩",
            "football_benefit": (
                "Short-term memory, sustained attention, and retention of complex "
                "game-situation information across multiple phases of play."
            ),
        },
    },

    # ── 7. Target Tracking ────────────────────────────────────────────────────
    {
        "code": "target_tracking",
        "name": "Target Tracking",
        "description": (
            "Memorise one target among multiple moving objects — track it continuously "
            "while distractors overlap and change direction."
        ),
        "game_type": "tracking",
        "is_active": False,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "anticipation":       0.35,
            "concentration":      0.30,
            "tactical_awareness": 0.25,
            "reactions":          0.10,
        },
        "config": {
            "show_in_hub":      True,
            "icon":             "👁️",
            "football_benefit": (
                "Tracking off-ball movement, anticipating runs, and reading "
                "positional changes without the ball."
            ),
        },
    },

    # ── 8. Peripheral Vision ──────────────────────────────────────────────────
    {
        "code": "peripheral_vision",
        "name": "Peripheral Vision",
        "description": (
            "Maintain a fixed central fixation point while responding to targets that "
            "appear in your peripheral field. Central gaze must not waver."
        ),
        "game_type": "peripheral_detection",
        "is_active": False,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "tactical_awareness": 0.35,
            "reactions":          0.25,
            "concentration":      0.25,
            "anticipation":       0.15,
        },
        "config": {
            "show_in_hub":      True,
            "icon":             "👀",
            "football_benefit": (
                "Peripheral awareness, rapid scene perception, and processing "
                "field information without lifting the head."
            ),
        },
    },

    # ── 9. Dual Task ──────────────────────────────────────────────────────────
    {
        "code": "dual_task",
        "name": "Dual Task",
        "description": (
            "Respond to on-screen targets while simultaneously retaining a secondary "
            "mental task in working memory. Both tasks must be managed at once."
        ),
        "game_type": "dual_task",
        "is_active": False,
        "base_xp": 15,
        "max_daily_attempts": 5,
        "skill_targets": {
            "concentration":    0.35,
            "composure":        0.30,
            "decisions":        0.20,
            "tactical_awareness": 0.15,
        },
        "config": {
            "show_in_hub":      True,
            "icon":             "🔄",
            "football_benefit": (
                "Split attention, decision-making under cognitive load, and "
                "stability in complex, multi-demand game situations."
            ),
        },
    },

    # ── 10. Fake Target / Feint Reaction ──────────────────────────────────────
    {
        "code": "fake_target",
        "name": "Fake Target / Feint Reaction",
        "description": (
            "A target feints movement before its true direction is revealed — "
            "reacting too early results in a penalty. Patience and precision are key."
        ),
        "game_type": "feint_reaction",
        "is_active": False,
        "base_xp": 15,
        "max_daily_attempts": 5,
        "skill_targets": {
            "composure":    0.35,
            "anticipation": 0.30,
            "decisions":    0.25,
            "reactions":    0.10,
        },
        "config": {
            "show_in_hub":      True,
            "icon":             "🎭",
            "football_benefit": (
                "Controlling reactions to feints, avoiding premature commitment, "
                "and reading opponent intent before acting."
            ),
        },
    },

    # ── 11. Audio + Visual Reaction ───────────────────────────────────────────
    {
        "code": "audio_visual_reaction",
        "name": "Audio + Visual Reaction",
        "description": (
            "Auditory and visual stimuli appear together — react only to specific "
            "audio-visual combinations and ignore mismatched signals."
        ),
        "game_type": "multisensory",
        "is_active": False,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "concentration": 0.30,
            "decisions":     0.30,
            "reactions":     0.25,
            "composure":     0.15,
        },
        "config": {
            "show_in_hub":      True,
            "icon":             "🎵",
            "football_benefit": (
                "Multi-sensory decision-making, integrating audio and visual signals, "
                "and fast responses to rapidly changing cues."
            ),
        },
    },

    # ── 12. Pattern Break ─────────────────────────────────────────────────────
    {
        "code": "pattern_break",
        "name": "Pattern Break",
        "description": (
            "Monitor a repeating visual pattern or rhythm — detect the moment it "
            "breaks and react before the next cycle begins."
        ),
        "game_type": "pattern_recognition",
        "is_active": False,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "anticipation":  0.35,
            "concentration": 0.30,
            "decisions":     0.25,
            "reactions":     0.10,
        },
        "config": {
            "show_in_hub":      True,
            "icon":             "🔀",
            "football_benefit": (
                "Pattern recognition, detecting game-situation shifts, and rapid "
                "responses to unexpected events or momentum changes."
            ),
        },
    },
]

# Fields updated when a game already exists
_UPDATE_FIELDS = ("config", "skill_targets", "base_xp", "description", "is_active", "name")


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
