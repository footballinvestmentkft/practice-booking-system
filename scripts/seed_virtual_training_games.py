"""Seed Virtual Training game presets.

Safe to run multiple times: new games are inserted, existing games have their
config/skill_targets/base_xp/description/name/is_active updated in-place (idempotent UPDATE).

Active after seed: color_reaction, go_no_go, direction_swipe, number_color_conflict,
memory_sequence, target_tracking.
Inactive after seed: stroop_challenge, peripheral_vision, dual_task, fake_target,
audio_visual_reaction, pattern_break.

memory_sequence and target_tracking are challenge-compatible (CHALLENGE_COMPATIBLE_GAMES).

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

    # ── 4. Direction Swipe (ACTIVE — Phase 2.3) ──────────────────────────────
    {
        "code": "direction_swipe",
        "name": "Direction Swipe",
        "description": (
            "An arrow appears — swipe or press the matching direction key as fast as "
            "possible. Three phases test speed, accuracy and sustained directional focus."
        ),
        "game_type": "direction_reaction",
        "is_active": True,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "reactions":     0.35,
            "decisions":     0.30,
            "coordination":  0.20,
            "concentration": 0.15,
        },
        "config": {
            # gameplay parameters
            "phases": [
                {"stimuli": 10, "window_ms": 1500, "isi_ms": 900},
                {"stimuli": 12, "window_ms": 1100, "isi_ms": 700},
                {"stimuli": 14, "window_ms":  750, "isi_ms": 550},
            ],
            "directions":           ["up", "down", "left", "right"],
            "late_grace_ms":        300,
            "jitter_ms":            150,
            "swipe_min_px":         30,
            "swipe_max_duration_ms": 500,
            # display keys
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
        "is_active": True,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "decisions":     0.40,
            "concentration": 0.30,
            "composure":     0.20,
            "reactions":     0.10,
        },
        "config": {
            # ── runtime gameplay keys ──────────────────────────────────────
            # 3-phase session: 10 + 12 + 14 = 36 stimuli total
            # rule_switch: "alternating" | "random" | "random_high"
            "phases": [
                {"stimuli": 10, "window_ms": 2000, "isi_ms": 900,  "rule_switch": "alternating"},
                {"stimuli": 12, "window_ms": 1600, "isi_ms": 700,  "rule_switch": "random"},
                {"stimuli": 14, "window_ms": 1200, "isi_ms": 550,  "rule_switch": "random_high"},
            ],
            "numbers": [1, 2, 3, 4],
            "colors": {
                "RED":    "#ef4444",
                "GREEN":  "#22c55e",
                "BLUE":   "#3b82f6",
                "YELLOW": "#eab308",
            },
            "late_grace_ms": 350,
            # Score formula (documented for transparency):
            #   score_raw = 0.55 × hit_rate + 0.25 × (1 − wrong_rate) + 0.20 × speed_factor
            #   speed_factor = max(0, 1 − avg_rt / 1600)
            #   score_normalized = round(score_raw × 100)
            # Outcome mapping:
            #   correct        → correct_count
            #   wrong_value    → wrong_click_count (wrong number/color answer)
            #   wrong_dimension → wrong_click_count (answered wrong dimension)
            #   missed         → error_count
            #   late           → late_summary.late_click_count
            # ── display keys ──────────────────────────────────────────────
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
        "is_active": True,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "concentration":      0.35,
            "tactical_awareness": 0.25,
            "decisions":          0.25,
            "composure":          0.15,
        },
        "config": {
            # ── runtime gameplay keys ──────────────────────────────────────
            # 3-phase session: 3+3+3 = 9 rounds, max 45 expected positions
            # No hand/finger protocol — raw_metrics v=2 (pure memory task)
            # Score formula:
            #   hit_rate      = correct_positions / total_expected_positions (45)
            #   wrong_rate    = wrong_positions / total_expected_positions
            #   miss_rate     = timed_out_positions / total_expected_positions
            #   completion    = attempted_positions / total_expected_positions
            #   speed_factor  = max(0, 1 − avg_first_tap_ms / 13000)
            #   score_raw     = 0.55×hit_rate + 0.25×completion + 0.20×speed_factor
            "phases": [
                {
                    "phase": 0,
                    "sequence_length":  3,
                    "rounds":           3,
                    "show_ms_per_item": 800,
                    "isi_ms":           500,
                    "recall_window_ms": 8000,
                },
                {
                    "phase": 1,
                    "sequence_length":  5,
                    "rounds":           3,
                    "show_ms_per_item": 650,
                    "isi_ms":           400,
                    "recall_window_ms": 13000,
                },
                {
                    "phase": 2,
                    "sequence_length":  7,
                    "rounds":           3,
                    "show_ms_per_item": 500,
                    "isi_ms":           300,
                    "recall_window_ms": 18000,
                },
            ],
            # 3×4 grid — 12 uniquely coloured tiles
            "grid_rows":    3,
            "grid_cols":    4,
            "tile_colors": [
                "#ef4444",  # 0  red
                "#f97316",  # 1  orange
                "#eab308",  # 2  yellow
                "#22c55e",  # 3  green
                "#14b8a6",  # 4  teal
                "#3b82f6",  # 5  blue
                "#8b5cf6",  # 6  violet
                "#ec4899",  # 7  pink
                "#6b7280",  # 8  grey
                "#78716c",  # 9  stone
                "#0ea5e9",  # 10 sky
                "#84cc16",  # 11 lime
            ],
            # ── display keys ──────────────────────────────────────────────
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
        "is_active": True,
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "anticipation":       0.35,
            "concentration":      0.30,
            "tactical_awareness": 0.25,
            "reactions":          0.10,
        },
        "config": {
            # ── runtime gameplay keys ──────────────────────────────────────
            # Easy = baseline: 3-phase × 3 rounds = 9 rounds, MOT paradigm
            # No hand/finger protocol — raw_metrics v=3 (with difficulty_level)
            # Score formula (all difficulty levels):
            #   hit_rate     = correct_count / stimuli_count
            #   miss_rate    = error_count / stimuli_count (timeouts only)
            #   speed_factor = max(0, 1 − avg_reaction_ms / 3000)
            #   score_raw    = 0.60×hit_rate + 0.25×(1−miss_rate) + 0.15×speed_factor
            # Difficulty multiplier affects skill delta only (XP unchanged).
            # `phases` top-level kept as Easy fallback for backward compat.
            "phases": [
                {
                    "phase":            0,
                    "rounds":           3,
                    "object_count":     3,
                    "object_speed":     1.00,
                    "highlight_ms":     1500,
                    "tracking_ms":      4000,
                    "window_ms":        3000,
                    "distractor_flash": 0,
                },
                {
                    "phase":            1,
                    "rounds":           3,
                    "object_count":     4,
                    "object_speed":     1.15,
                    "highlight_ms":     1500,
                    "tracking_ms":      5000,
                    "window_ms":        3000,
                    "distractor_flash": 0,
                },
                {
                    "phase":            2,
                    "rounds":           3,
                    "object_count":     5,
                    "object_speed":     1.30,
                    "highlight_ms":     1500,
                    "tracking_ms":      6000,
                    "window_ms":        3000,
                    "distractor_flash": 0,
                },
            ],
            # ── multi-difficulty config ────────────────────────────────────
            "difficulties": {
                "easy": {
                    "phases": [
                        {"phase": 0, "rounds": 3, "object_count": 3, "object_speed": 1.00,
                         "highlight_ms": 1500, "tracking_ms": 4000, "window_ms": 3000,
                         "distractor_flash": 0},
                        {"phase": 1, "rounds": 3, "object_count": 4, "object_speed": 1.15,
                         "highlight_ms": 1500, "tracking_ms": 5000, "window_ms": 3000,
                         "distractor_flash": 0},
                        {"phase": 2, "rounds": 3, "object_count": 5, "object_speed": 1.30,
                         "highlight_ms": 1500, "tracking_ms": 6000, "window_ms": 3000,
                         "distractor_flash": 0},
                    ],
                    "difficulty_multiplier": 1.00,
                    "flash_config": {
                        "flash_duration_ms":      400,
                        "flash_gap_ms":           500,
                        "max_concurrent_flashes": 1,
                        "allow_repeat_flash":     False,
                        "repeat_gap_ms":          None,
                    },
                    "direction_change": {
                        "enabled":    False,
                        "interval_ms": None,
                    },
                    "validation_overrides": {
                        "min_stimuli_count":         3,
                        "min_duration_seconds":      20.0,
                        "bot_threshold_ms":          200,
                        "random_clicking_threshold": 0.70,
                    },
                },
                "medium": {
                    "phases": [
                        {"phase": 0, "rounds": 3, "object_count": 5, "object_speed": 1.60,
                         "highlight_ms": 1500, "tracking_ms": 5000, "window_ms": 2500,
                         "distractor_flash": 1},
                        {"phase": 1, "rounds": 3, "object_count": 6, "object_speed": 1.90,
                         "highlight_ms": 1500, "tracking_ms": 6000, "window_ms": 2500,
                         "distractor_flash": 2},
                        {"phase": 2, "rounds": 3, "object_count": 7, "object_speed": 2.20,
                         "highlight_ms": 1500, "tracking_ms": 7000, "window_ms": 2300,
                         "distractor_flash": 3},
                    ],
                    "difficulty_multiplier": 1.30,
                    "flash_config": {
                        "flash_duration_ms":      400,
                        "flash_gap_ms":           450,
                        "max_concurrent_flashes": 1,
                        "allow_repeat_flash":     False,
                        "repeat_gap_ms":          None,
                    },
                    "direction_change": {
                        "enabled":    False,
                        "interval_ms": None,
                    },
                    "validation_overrides": {
                        "min_stimuli_count":         3,
                        "min_duration_seconds":      25.0,
                        "bot_threshold_ms":          200,
                        "random_clicking_threshold": 0.70,
                    },
                },
                "hard": {
                    "phases": [
                        {"phase": 0, "rounds": 4, "object_count": 6, "object_speed": 2.10,
                         "highlight_ms": 1200, "tracking_ms": 5500, "window_ms": 2200,
                         "distractor_flash": 2},
                        {"phase": 1, "rounds": 4, "object_count": 7, "object_speed": 2.50,
                         "highlight_ms": 1200, "tracking_ms": 6500, "window_ms": 2000,
                         "distractor_flash": 4},
                        {"phase": 2, "rounds": 4, "object_count": 8, "object_speed": 2.90,
                         "highlight_ms": 1200, "tracking_ms": 8000, "window_ms": 1800,
                         "distractor_flash": 5},
                    ],
                    "difficulty_multiplier": 1.70,
                    "flash_config": {
                        "flash_duration_ms":      350,
                        "flash_gap_ms":           350,
                        "max_concurrent_flashes": 2,
                        "allow_repeat_flash":     True,
                        "repeat_gap_ms":          2000,
                    },
                    "direction_change": {
                        "enabled":    True,
                        "interval_ms": 1800,
                    },
                    "validation_overrides": {
                        "min_stimuli_count":         4,
                        "min_duration_seconds":      30.0,
                        "bot_threshold_ms":          200,
                        "random_clicking_threshold": 0.70,
                    },
                },
                "expert": {
                    "phases": [
                        {"phase": 0, "rounds": 3, "object_count": 7, "object_speed": 2.60,
                         "highlight_ms": 900, "tracking_ms": 5500, "window_ms": 1800,
                         "distractor_flash": 3},
                        {"phase": 1, "rounds": 3, "object_count": 8, "object_speed": 3.00,
                         "highlight_ms": 900, "tracking_ms": 7000, "window_ms": 1700,
                         "distractor_flash": 5},
                        {"phase": 2, "rounds": 3, "object_count": 9, "object_speed": 3.20,
                         "highlight_ms": 700, "tracking_ms": 8500, "window_ms": 1600,
                         "distractor_flash": 6},
                        {"phase": 3, "rounds": 3, "object_count": 9, "object_speed": 3.20,
                         "highlight_ms": 700, "tracking_ms": 10000, "window_ms": 1600,
                         "distractor_flash": 6},
                    ],
                    "difficulty_multiplier": 2.20,
                    "flash_config": {
                        "flash_duration_ms":      300,
                        "flash_gap_ms":           250,
                        "max_concurrent_flashes": 3,
                        "allow_repeat_flash":     True,
                        "repeat_gap_ms":          1500,
                    },
                    "direction_change": {
                        "enabled":    True,
                        "interval_ms": 1200,
                    },
                    "unlock_threshold": {
                        "min_hard_attempts": 3,
                        "min_hard_score":    70,
                    },
                    "validation_overrides": {
                        "min_stimuli_count":         4,
                        "min_duration_seconds":      35.0,
                        "bot_threshold_ms":          200,
                        "random_clicking_threshold": 0.70,
                    },
                },
            },
            "object_radius_px": 40,
            "arena_width":      480,
            "arena_height":     360,
            # Anti-farming overrides — fallback for Easy (used when no difficulty_level in payload)
            "validation_overrides": {
                "min_stimuli_count":           3,
                "min_duration_seconds":        20.0,
                "bot_threshold_ms":            200,
                "random_clicking_threshold":   0.70,
            },
            # ── display keys ──────────────────────────────────────────────
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
        "is_active": False,    # Activated after manual QA sign-off
        "base_xp": 12,
        "max_daily_attempts": 5,
        "skill_targets": {
            "tactical_awareness": 0.35,
            "reactions":          0.25,
            "concentration":      0.25,
            "anticipation":       0.15,
        },
        "config": {
            # Three eccentricity zones, progressively harder.
            # Distances and sizes are in CSS px (assuming 560px arena side).
            "phases": [
                {
                    "zone":              "near",
                    "stimuli":           14,
                    "eccentricity_min_px": 100,
                    "eccentricity_max_px": 160,
                    "target_size_px":    50,
                    "window_ms":         1200,
                    "isi_ms":            900,
                    "clock_positions":   8,
                },
                {
                    "zone":              "mid",
                    "stimuli":           14,
                    "eccentricity_min_px": 180,
                    "eccentricity_max_px": 260,
                    "target_size_px":    44,
                    "window_ms":         900,
                    "isi_ms":            750,
                    "clock_positions":   8,
                },
                {
                    "zone":              "far",
                    "stimuli":           14,
                    "eccentricity_min_px": 290,
                    "eccentricity_max_px": 380,
                    "target_size_px":    38,
                    "window_ms":         700,
                    "isi_ms":            600,
                    "clock_positions":   8,
                },
            ],
            # Eccentricity bonus weights: far targets score higher than near
            "eccentricity_weights": {
                "near": 1.00,
                "mid":  1.35,
                "far":  1.75,
            },
            # Arena is a square; radius in px from centre (used for JS clamp)
            "arena_radius_px":        400,
            "fixation_cross_size_px": 28,
            "target_color":           "#4f46e5",
            "fixation_color":         "#1e1b4b",
            # Free protocol — no hand/finger assignment for this game type
            "protocol_assignment": "free",
            # Anti-farming overrides (total session ~70-90s, 42 stimuli)
            "validation_overrides": {
                "min_duration_seconds":     20.0,
                "min_stimuli_count":        30,
                "bot_reaction_threshold_ms": 80,
            },
            "show_in_hub":      True,
            "icon":             "👀",
            "football_benefit": (
                "Detect off-ball runs and blind-side threats without losing track "
                "of the ball — the key visual skill of elite wide players and midfielders."
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
