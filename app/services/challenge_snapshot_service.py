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

    Ongoing direction-change angles are NOT snapshotted (P1 tech debt).
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

    phases: list[dict] = []
    for pi, ph in enumerate(phases_cfg):
        object_count = ph.get("object_count")
        num_rounds   = ph.get("rounds", 3)

        if object_count is None:
            raise ValueError(
                f"generate_tt_snapshot: phase[{pi}] missing 'object_count'"
            )

        rounds: list[dict] = []
        for ri in range(num_rounds):
            target_index      = random.randint(0, object_count - 1)
            initial_positions = _place_objects(object_count, arena_w, arena_h, radius)
            initial_angles    = [
                round(random.uniform(0, 2 * math.pi), 4)
                for _ in range(object_count)
            ]
            rounds.append({
                "round":             ri + 1,
                "target_index":      target_index,
                "initial_positions": initial_positions,
                "initial_angles":    initial_angles,
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
    """Structural validation for a TT snapshot dict. Returns False on any defect."""
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
    return True
