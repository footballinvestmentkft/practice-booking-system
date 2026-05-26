"""Challenge config snapshot service — P0 Fairness.

Generates server-side, pre-determined game content for challenge mode so that
both challenger and challenged play an identical starting state.

Memory Sequence:
  All phase × round sequences are pre-drawn on the server.  The frontend uses
  these exact tile indices instead of calling Math.random().

Target Tracking:
  Per-round initial_positions, target_index, and initial_angles are pre-drawn.
  Ongoing direction-change angles during the tracking phase remain random
  (client-side Math.random()) — this is an accepted P1 tech debt; only the
  round start state matters for fairness.

Supported game codes: "memory_sequence", "target_tracking"
All others raise ValueError (no silent fallback).
"""
from __future__ import annotations

import math
import random
from typing import Any

# ── Public dispatcher ──────────────────────────────────────────────────────────

_VALID_MODES: frozenset[str] = frozenset({"async", "live"})

VALID_CHALLENGE_MODES = _VALID_MODES


def validate_challenge_mode(mode: str) -> str:
    """Return mode unchanged if valid, raise ValueError otherwise."""
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Invalid challenge_mode {mode!r}. Must be one of: {sorted(_VALID_MODES)}"
        )
    return mode


def generate_snapshot(
    game_code: str,
    game_config: dict,
    difficulty_level: str | None = None,
) -> dict:
    """Generate a fairness snapshot for the given game.

    Args:
        game_code:        VirtualTrainingGame.code ("memory_sequence" | "target_tracking")
        game_config:      VirtualTrainingGame.config JSONB dict
        difficulty_level: Required for "target_tracking"; ignored for "memory_sequence"

    Returns:
        Snapshot dict suitable for storage in challenge_config_snapshot JSONB column.

    Raises:
        ValueError: unknown game_code or malformed game_config
    """
    if game_code == "memory_sequence":
        return generate_ms_snapshot(game_config, difficulty_level)
    if game_code == "target_tracking":
        if not difficulty_level:
            difficulty_level = "easy"
        return generate_tt_snapshot(game_config, difficulty_level)
    raise ValueError(
        f"Unknown game_code {game_code!r}. "
        "Only 'memory_sequence' and 'target_tracking' support snapshots."
    )


# ── Memory Sequence ────────────────────────────────────────────────────────────

def generate_ms_snapshot(
    game_config: dict,
    difficulty_level: str | None = None,  # unused for MS — kept for uniform signature
) -> dict:
    """Generate a Memory Sequence snapshot.

    Reads game_config["phases"] (list of phase dicts with sequence_length + rounds).
    grid_tiles = game_config.get("grid_rows", 3) * game_config.get("grid_cols", 4)
    Defaults to 12 tiles (3×4 grid) if grid_rows/grid_cols absent.
    """
    phases_cfg: list[dict] = game_config.get("phases", [])
    if not phases_cfg:
        raise ValueError("generate_ms_snapshot: game_config missing 'phases'")

    grid_rows  = game_config.get("grid_rows", 3)
    grid_cols  = game_config.get("grid_cols", 4)
    grid_tiles = grid_rows * grid_cols

    phases: list[dict] = []
    for pi, ph in enumerate(phases_cfg):
        seq_len    = ph.get("sequence_length")
        num_rounds = ph.get("rounds", 3)

        if seq_len is None:
            raise ValueError(
                f"generate_ms_snapshot: phase[{pi}] missing 'sequence_length'"
            )
        if seq_len > grid_tiles:
            raise ValueError(
                f"generate_ms_snapshot: phase[{pi}] sequence_length={seq_len} "
                f"exceeds grid_tiles={grid_tiles}"
            )

        rounds: list[dict] = []
        for ri in range(num_rounds):
            sequence = random.sample(range(grid_tiles), seq_len)
            rounds.append({"round": ri + 1, "sequence": sequence})

        phases.append({
            "phase":           pi + 1,
            "sequence_length": seq_len,
            "rounds":          rounds,
        })

    return {
        "game_code":  "memory_sequence",
        "grid_tiles": grid_tiles,
        "phases":     phases,
    }


# ── Target Tracking ────────────────────────────────────────────────────────────

# Canonical server-side arena dimensions (matches template fallback values).
_TT_ARENA_W = 480
_TT_ARENA_H = 360
_TT_RADIUS  = 40   # object_radius used in placement clearance calculations
_TT_CLEARANCE_MULT = 2.8   # must match spawnObjects() in template
_TT_MAX_PLACEMENT_TRIES = 80

# Flash schedule constants — must mirror the JS constants in virtual_training_target_tracking.html
_FLASH_DURATION_MS  = 400
_FLASH_MIN_GAP_MS   = 500
_FLASH_EARLIEST_PCT = 0.15
_FLASH_LATEST_PCT   = 0.85


def _place_objects(count: int, arena_w: int, arena_h: int, radius: int) -> list[dict]:
    """Non-overlapping random placement — mirrors spawnObjects() in the TT template."""
    objects: list[dict] = []
    clearance = radius * _TT_CLEARANCE_MULT

    for _ in range(count):
        x = y = 0.0
        ok    = False
        tries = 0
        while not ok and tries < _TT_MAX_PLACEMENT_TRIES:
            x  = radius + random.random() * (arena_w - 2 * radius)
            y  = radius + random.random() * (arena_h - 2 * radius)
            ok = all(
                math.sqrt((x - o["x"]) ** 2 + (y - o["y"]) ** 2) >= clearance
                for o in objects
            )
            tries += 1
        objects.append({"x": round(x, 2), "y": round(y, 2)})

    return objects


def _generate_flash_schedule(
    flash_count: int,
    tracking_ms: int,
    object_count: int,
    flash_config: dict | None,
    target_index: int,
) -> list[dict]:
    """Python port of generateFlashSchedule() from virtual_training_target_tracking.html.

    Generates a deterministic list of flash events for distractor objects (never the
    target) so both challenge players see the identical flash sequence.

    Args match the JS call signature:
        flash_count:  number of flash events to place (round.distractorFlash)
        tracking_ms:  round tracking window duration in ms (round.trackingMs)
        object_count: total objects in this round (round.objectCount)
        flash_config: difficulty-level flash config dict or None
        target_index: index of the target object — excluded from distractors
    """
    flash_duration = (flash_config or {}).get("flash_duration_ms", _FLASH_DURATION_MS)
    flash_gap      = (flash_config or {}).get("flash_gap_ms",      _FLASH_MIN_GAP_MS)
    max_concurrent = (flash_config or {}).get("max_concurrent_flashes", 1)
    allow_repeat   = bool((flash_config or {}).get("allow_repeat_flash", False))
    repeat_gap     = (flash_config or {}).get("repeat_gap_ms", 2000)

    if flash_count <= 0 or object_count <= 1:
        return []

    non_targets = [i for i in range(object_count) if i != target_index]
    if not non_targets:
        return []

    earliest = int(tracking_ms * _FLASH_EARLIEST_PCT)
    latest   = int(tracking_ms * _FLASH_LATEST_PCT) - flash_duration
    if latest <= earliest:
        return []

    schedule:    list[dict]      = []
    used_once:   set[int]        = set()
    last_end_ms: dict[int, int]  = {}
    seen_once:   set[int]        = set()
    group_id  = 0
    t_cursor  = earliest
    placed    = 0
    safe_break = 0

    while placed < flash_count and t_cursor <= latest:
        safe_break += 1
        if safe_break > 500:
            break

        pool: list[int] = []
        for idx in non_targets:
            if not allow_repeat and idx in used_once:
                continue
            if allow_repeat and idx in last_end_ms and t_cursor < last_end_ms[idx] + repeat_gap:
                continue
            pool.append(idx)

        if not pool:
            if not allow_repeat:
                break
            t_cursor += max(flash_gap, 200)
            continue

        random.shuffle(pool)

        batch_size = min(max_concurrent, len(pool), flash_count - placed)
        batch      = pool[:batch_size]
        gid: int | None = None
        if len(batch) > 1:
            group_id += 1
            gid = group_id

        for dist_idx in batch:
            is_repeat = dist_idx in seen_once
            schedule.append({
                "distractor_index":    dist_idx,
                "t_offset_ms":         t_cursor,
                "duration_ms":         flash_duration,
                "color":               "#f59e0b",
                "concurrent_group_id": gid,
                "repeat":              is_repeat,
                "is_target":           False,
            })
            used_once.add(dist_idx)
            seen_once.add(dist_idx)
            last_end_ms[dist_idx] = t_cursor + flash_duration
            placed += 1

        t_cursor += flash_duration + flash_gap

    return schedule


def generate_tt_snapshot(
    game_config: dict,
    difficulty_level: str,
) -> dict:
    """Generate a Target Tracking snapshot for the given difficulty.

    Reads game_config["difficulties"][difficulty_level]["phases"].
    Falls back to game_config["phases"] (easy backward-compat) if the
    difficulty key is absent.

    Per-round snapshot contains:
      target_index:      int   — which object is the target (0-based)
      initial_positions: list  — [{x, y}] one per object
      initial_angles:    list  — [float radians] one per object
      direction_changes: list  — list of per-change angle lists [[float, ...], ...]
                                 length = ceil(tracking_ms / interval_ms) + 1
                                 empty list when direction change is disabled
      flash_schedule:    list  — pre-generated distractor flash events (see
                                 _generate_flash_schedule for schema); ensures
                                 both challenge players see the identical sequence

    P2 tech debt: arena/radius device-scaling differences (different screens produce
    different pixel clearances).  Requires fixed logical coordinate system; deferred.
    """
    difficulties: dict = game_config.get("difficulties", {})
    diff_cfg: dict     = difficulties.get(difficulty_level, {})
    phases_cfg: list   = diff_cfg.get("phases") or game_config.get("phases", [])

    if not phases_cfg:
        raise ValueError(
            f"generate_tt_snapshot: no phases found for difficulty={difficulty_level!r}"
        )

    arena_w = game_config.get("arena_width",  _TT_ARENA_W)
    arena_h = game_config.get("arena_height", _TT_ARENA_H)
    radius  = game_config.get("object_radius", _TT_RADIUS)

    # Direction change and flash config live at the difficulty level (not per-phase).
    dir_change_cfg: dict      = diff_cfg.get("direction_change") or {}
    dir_enabled: bool         = bool(dir_change_cfg.get("enabled", False))
    dir_interval_ms: int      = int(dir_change_cfg.get("interval_ms", 2000))
    flash_config: dict | None = diff_cfg.get("flash_config") or None

    phases: list[dict] = []
    for pi, ph in enumerate(phases_cfg):
        object_count = ph.get("object_count")
        num_rounds   = ph.get("rounds", 3)

        if object_count is None:
            raise ValueError(
                f"generate_tt_snapshot: phase[{pi}] missing 'object_count'"
            )

        tracking_ms:     int = int(ph.get("tracking_ms", 5000))
        distractor_flash: int = int(ph.get("distractor_flash", 0))

        # Pre-generate direction-change angle sequences for this phase.
        # We generate ceil(tracking_ms / interval_ms) + 1 entries so that even with
        # rAF jitter the frontend never runs out of pre-drawn angles.
        if dir_enabled and dir_interval_ms > 0:
            n_changes = math.ceil(tracking_ms / dir_interval_ms) + 1
        else:
            n_changes = 0

        rounds: list[dict] = []
        for ri in range(num_rounds):
            target_index      = random.randint(0, object_count - 1)
            initial_positions = _place_objects(object_count, arena_w, arena_h, radius)
            initial_angles    = [
                round(random.uniform(0, 2 * math.pi), 4)
                for _ in range(object_count)
            ]

            direction_changes: list[list[float]] = [
                [round(random.uniform(0, 2 * math.pi), 4) for _ in range(object_count)]
                for _ in range(n_changes)
            ]

            flash_schedule = _generate_flash_schedule(
                distractor_flash, tracking_ms, object_count, flash_config, target_index,
            )

            rounds.append({
                "round":             ri + 1,
                "target_index":      target_index,
                "initial_positions": initial_positions,
                "initial_angles":    initial_angles,
                "direction_changes": direction_changes,
                "flash_schedule":    flash_schedule,
            })

        phases.append({
            "phase":        pi + 1,
            "object_count": object_count,
            "rounds":       rounds,
        })

    return {
        "game_code":  "target_tracking",
        "difficulty": difficulty_level,
        "arena":      {"width": arena_w, "height": arena_h},
        "phases":     phases,
    }


# ── Validators ─────────────────────────────────────────────────────────────────

def validate_ms_snapshot(snapshot: Any) -> bool:
    """Structural validation for an MS snapshot dict. Returns False on any defect."""
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("game_code") != "memory_sequence":
        return False
    grid_tiles = snapshot.get("grid_tiles")
    if not isinstance(grid_tiles, int) or grid_tiles <= 0:
        return False
    phases = snapshot.get("phases")
    if not isinstance(phases, list) or not phases:
        return False
    for ph in phases:
        if not isinstance(ph, dict):
            return False
        seq_len = ph.get("sequence_length")
        rounds  = ph.get("rounds")
        if not isinstance(seq_len, int) or not isinstance(rounds, list) or not rounds:
            return False
        for r in rounds:
            if not isinstance(r, dict):
                return False
            seq = r.get("sequence")
            if not isinstance(seq, list) or len(seq) != seq_len:
                return False
            if any(not isinstance(i, int) or i < 0 or i >= grid_tiles for i in seq):
                return False
    return True


def validate_tt_snapshot(snapshot: Any) -> bool:
    """Structural validation for a TT snapshot dict. Returns False on any defect.

    direction_changes and flash_schedule are optional for backward-compatibility
    with snapshots generated before the TT-FAIR fix.  When present they are
    validated fully; when absent the snapshot is still accepted.
    """
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("game_code") != "target_tracking":
        return False
    arena = snapshot.get("arena")
    if not isinstance(arena, dict):
        return False
    arena_w = arena.get("width", 0)
    arena_h = arena.get("height", 0)
    phases  = snapshot.get("phases")
    if not isinstance(phases, list) or not phases:
        return False
    for ph in phases:
        if not isinstance(ph, dict):
            return False
        obj_count = ph.get("object_count")
        rounds    = ph.get("rounds")
        if not isinstance(obj_count, int) or not isinstance(rounds, list) or not rounds:
            return False
        for r in rounds:
            if not isinstance(r, dict):
                return False
            ti    = r.get("target_index")
            pos   = r.get("initial_positions")
            angs  = r.get("initial_angles")
            if not isinstance(ti, int) or ti < 0 or ti >= obj_count:
                return False
            if not isinstance(pos, list) or len(pos) != obj_count:
                return False
            if not isinstance(angs, list) or len(angs) != obj_count:
                return False
            for p in pos:
                if not isinstance(p, dict):
                    return False
                x, y = p.get("x", -1), p.get("y", -1)
                if x < 0 or x > arena_w or y < 0 or y > arena_h:
                    return False

            # ── Optional: direction_changes (TT-FAIR) ──────────────────────────
            dir_changes = r.get("direction_changes")
            if dir_changes is not None:
                if not isinstance(dir_changes, list):
                    return False
                for entry in dir_changes:
                    if not isinstance(entry, list) or len(entry) != obj_count:
                        return False
                    if not all(isinstance(a, (int, float)) and 0 <= a <= 2 * math.pi for a in entry):
                        return False

            # ── Optional: flash_schedule (TT-FAIR) ────────────────────────────
            flash_sched = r.get("flash_schedule")
            if flash_sched is not None:
                if not isinstance(flash_sched, list):
                    return False
                for ev in flash_sched:
                    if not isinstance(ev, dict):
                        return False
                    d_idx = ev.get("distractor_index")
                    t_off = ev.get("t_offset_ms")
                    dur   = ev.get("duration_ms")
                    if not isinstance(d_idx, int) or d_idx < 0 or d_idx >= obj_count:
                        return False
                    if d_idx == ti:          # target must never be a distractor
                        return False
                    if not isinstance(t_off, (int, float)) or t_off < 0:
                        return False
                    if not isinstance(dur, (int, float)) or dur <= 0:
                        return False

    return True
