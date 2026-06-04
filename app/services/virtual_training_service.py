"""
Virtual Training Service — Phase 2 (Color Reaction MVP)

Anti-farming rules enforced here (not in the route):
  - too_short:       duration_seconds < 5.0 — session ended too fast to be real
  - too_few_stimuli: stimuli_count < 5 — not enough data for a valid score
  - bot_suspected:   avg_reaction_ms < 100 — physically impossible reaction time

Skill delta computation reuses compute_skill_deltas() from segment_reward_service
so the formula is identical to the tournament/session training pipeline.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame

_XP_MULTIPLIER_TABLE: dict[int, float] = {1: 1.00, 2: 0.75, 3: 0.50, 4: 0.30, 5: 0.15}
_BOT_REACTION_THRESHOLD_MS   = 80.0   # Phase 2.1: tighter bot floor (was 100)
_MIN_DURATION_SECONDS        = 25.0   # Phase 2.1: 36 stimuli × avg ~0.7 s (was 5)
_MIN_STIMULI_COUNT           = 28     # Phase 2.1: 36 total, allow minor losses (was 5)
_RANDOM_CLICKING_THRESHOLD   = 0.55   # G4: wrong_click_count / stimuli_count > this → invalid

# ── Protocol assignment pool (Phase 2.4 — 4 combos) ──────────────────────────
# Phase 2.5 TODO: expand to 10-combo pool (left/right × thumb/index/middle/ring/pinky).
# Requires: UX study for middle/ring/pinky, multiplier calibration from collected
# attempt data, and balanced scheduler validation across all 10 slots before activation.
_PROTOCOL_POOL: list[dict] = [
    {"hand": "right", "finger": "index", "label": "Right Index",
     "protocol_difficulty_multiplier": 1.00},
    {"hand": "right", "finger": "thumb", "label": "Right Thumb",
     "protocol_difficulty_multiplier": 1.05},
    {"hand": "left",  "finger": "index", "label": "Left Index",
     "protocol_difficulty_multiplier": 1.10},
    {"hand": "left",  "finger": "thumb", "label": "Left Thumb",
     "protocol_difficulty_multiplier": 1.15},
]

_FREE_PROTOCOL: dict = {
    "hand": "free", "finger": "free", "label": "Free / No Protocol",
    "protocol_difficulty_multiplier": 1.00,
    "assignment_source": "system", "self_declared": True, "not_verified": True,
}


class VirtualTrainingService:

    # ── Read helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def get_games(db: Session) -> list[VirtualTrainingGame]:
        """Return all active game presets ordered by id."""
        return (
            db.query(VirtualTrainingGame)
            .filter(VirtualTrainingGame.is_active == True)  # noqa: E712
            .order_by(VirtualTrainingGame.id)
            .all()
        )

    @staticmethod
    def get_game(db: Session, code: str) -> Optional[VirtualTrainingGame]:
        """Fetch a game preset by its unique code (regardless of is_active)."""
        return (
            db.query(VirtualTrainingGame)
            .filter(VirtualTrainingGame.code == code)
            .first()
        )

    @staticmethod
    def get_hub_games(db: Session) -> list[VirtualTrainingGame]:
        """Return all games visible in the Virtual Games hub (active + planned).

        Excludes any game where config.show_in_hub == false.
        Ordered by id (insertion / catalog order).
        """
        all_games = (
            db.query(VirtualTrainingGame)
            .order_by(VirtualTrainingGame.id)
            .all()
        )
        return [g for g in all_games if (g.config or {}).get("show_in_hub", True) is not False]

    # ── Validation ────────────────────────────────────────────────────────────

    @staticmethod
    def validate_attempt(
        data: dict, overrides: dict | None = None
    ) -> tuple[bool, Optional[str]]:
        """
        Run anti-abuse checks on raw attempt data.

        Returns (is_valid, invalid_reason).
        Checks (in order — first failing check wins):
          1. duration_seconds < min_dur     → too_short
          2. stimuli_count < min_stim       → too_few_stimuli
          3. wrong_click_count > rand_thresh × stimuli_count → random_clicking
          4. avg_reaction_ms < bot_thresh   → bot_suspected

        overrides: per-game threshold overrides from game.config["validation_overrides"].
        Backward-compatible: None → module-level defaults (existing game behaviour unchanged).
        """
        _ov = overrides or {}
        min_dur     = float(_ov.get("min_duration_seconds",      _MIN_DURATION_SECONDS))
        min_stim    = int(_ov.get("min_stimuli_count",           _MIN_STIMULI_COUNT))
        bot_thresh  = float(_ov.get("bot_threshold_ms",          _BOT_REACTION_THRESHOLD_MS))
        rand_thresh = float(_ov.get("random_clicking_threshold", _RANDOM_CLICKING_THRESHOLD))

        duration = data.get("duration_seconds")
        if duration is not None and float(duration) < min_dur:
            return False, "too_short"

        stimuli = data.get("stimuli_count")
        if stimuli is not None and int(stimuli) < min_stim:
            return False, "too_few_stimuli"

        wrong_clicks = data.get("wrong_click_count")
        if wrong_clicks is not None and stimuli is not None and int(stimuli) > 0:
            if int(wrong_clicks) > rand_thresh * int(stimuli):
                return False, "random_clicking"

        avg_ms = data.get("avg_reaction_ms")
        if avg_ms is not None and float(avg_ms) < bot_thresh:
            return False, "bot_suspected"

        return True, None

    # ── Daily indexing ────────────────────────────────────────────────────────

    @staticmethod
    def calculate_daily_attempt_index(
        db: Session, user_id: int, game_id: int,
        training_local_date: "date | None" = None,
    ) -> int:
        """Return the 1-based attempt index for the given training day.

        Uses training_local_date (Phase 1: browser timezone) as the day
        boundary instead of UTC started_at. Falls back to UTC today if
        training_local_date is not provided.
        """
        from app.services.training_day import current_training_date_utc
        _date = training_local_date if training_local_date is not None else current_training_date_utc()
        count = (
            db.query(VirtualTrainingAttempt)
            .filter(
                VirtualTrainingAttempt.user_id             == user_id,
                VirtualTrainingAttempt.game_id              == game_id,
                VirtualTrainingAttempt.training_local_date  == _date,
                VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
            )
            .count()
        )
        return count + 1

    # ── Protocol assignment ───────────────────────────────────────────────────

    @staticmethod
    def _select_protocol_from_history(history: list[str]) -> dict:
        """
        Pure scheduler logic — no DB.

        history: combo keys most-recent first, e.g. ["right_index", "left_thumb", ...]
        Each key is "{hand}_{finger}" for a valid v3 attempt.

        Rules:
        1. Seeded first-rotation: first 4 valid v3 attempts → pool[0..3] in order
        2. Consecutive guard: last 2 same combo → exclude from candidates
        3. Frequency-based: least-used wins; tiebreak by pool index (deterministic)
        """
        from collections import Counter

        def _key(slot: dict) -> str:
            return f"{slot['hand']}_{slot['finger']}"

        if len(history) < 4:
            return dict(_PROTOCOL_POOL[len(history)])

        excluded: str | None = None
        if len(history) >= 2 and history[0] == history[1]:
            excluded = history[0]

        candidates = [s for s in _PROTOCOL_POOL if _key(s) != excluded]
        counts = Counter(history)
        return dict(min(candidates, key=lambda s: counts.get(_key(s), 0)))

    @staticmethod
    def assign_protocol(db: Session, user_id: int, game_id: int) -> dict:
        """
        Balanced scheduler — returns the next protocol dict for this user+game.

        Priority order:
        1. Feature flag game.config["protocol_assignment"] == "free" → Free
        2. _select_protocol_from_history() with last-30 valid v3 attempt history
        3. Exception fallback → Free (gameplay must never crash)

        History scope: last 30 valid attempts with raw_metrics.v >= 3 and
        hand_profile present, per-user per-game. Invalid attempts excluded.
        Returns a dict with all required keys including assignment_source="system".
        """
        import json as _json

        def _make_result(slot: dict) -> dict:
            r = dict(slot)
            r["assignment_source"] = "system"
            r["self_declared"]     = True
            r["not_verified"]      = True
            return r

        try:
            game = (
                db.query(VirtualTrainingGame)
                .filter(VirtualTrainingGame.id == game_id)
                .first()
            )
            if game is None:
                return dict(_FREE_PROTOCOL)

            cfg = game.config or {}
            if isinstance(cfg, dict) and cfg.get("protocol_assignment") == "free":
                return dict(_FREE_PROTOCOL)

            rows = db.execute(
                text(
                    """
                    SELECT raw_metrics->'hand_profile' AS hp
                    FROM virtual_training_attempts
                    WHERE user_id    = :uid
                      AND game_id    = :gid
                      AND is_valid   = true
                      AND raw_metrics IS NOT NULL
                      AND (raw_metrics->>'v')::int >= 3
                      AND raw_metrics ? 'hand_profile'
                    ORDER BY completed_at DESC
                    LIMIT 30
                    """
                ),
                {"uid": user_id, "gid": game_id},
            ).fetchall()

            history: list[str] = []
            for row in rows:
                hp = row.hp
                if isinstance(hp, str):
                    try:
                        hp = _json.loads(hp)
                    except Exception:
                        continue
                if isinstance(hp, dict):
                    hand   = hp.get("hand", "")
                    finger = hp.get("finger", "")
                    if hand and finger and hand != "free":
                        history.append(f"{hand}_{finger}")

            slot = VirtualTrainingService._select_protocol_from_history(history)
            return _make_result(slot)

        except Exception:
            return dict(_FREE_PROTOCOL)

    # ── Difficulty config helpers (Target Tracking) ───────────────────────────

    @staticmethod
    def get_difficulty_config(game: VirtualTrainingGame, level: str) -> dict:
        """Return the difficulty block for *level*, falling back to 'easy'.

        Returns {} when the game has no 'difficulties' config key.
        """
        cfg = game.config if isinstance(game.config, dict) else {}
        difficulties = cfg.get("difficulties")
        if not isinstance(difficulties, dict):
            return {}
        return difficulties.get(level) or difficulties.get("easy") or {}

    @staticmethod
    def is_expert_unlocked(db: Session, user_id: int, game_id: int) -> bool:
        """Return True when the user has ≥3 valid Hard attempts with score_normalized ≥ 70."""
        try:
            row = db.execute(
                text(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM virtual_training_attempts
                    WHERE user_id          = :uid
                      AND game_id          = :gid
                      AND is_valid         = true
                      AND score_normalized >= 70
                      AND raw_metrics IS NOT NULL
                      AND raw_metrics->>'difficulty_level' = 'hard'
                    """
                ),
                {"uid": user_id, "gid": game_id},
            ).first()
            return (row.cnt if row else 0) >= 3
        except Exception:
            return False

    @staticmethod
    def extract_difficulty_multiplier(data: dict) -> float:
        """Extract difficulty_multiplier from raw_metrics (v=3, TT difficulty games).

        Returns 1.00 when absent, v < 3, or invalid.  Clamp: [1.00, 2.50].
        """
        raw = data.get("raw_metrics")
        if not isinstance(raw, dict) or int(raw.get("v", 1)) < 3:
            return 1.00
        try:
            dm = float(raw.get("difficulty_multiplier", 1.00))
            return max(1.00, min(2.50, dm))
        except (TypeError, ValueError):
            return 1.00

    # ── Protocol difficulty ───────────────────────────────────────────────────

    @staticmethod
    def extract_protocol_difficulty(data: dict) -> float:
        """
        Extract and clamp the self-declared protocol difficulty multiplier.

        Returns 1.00 when raw_metrics is absent, v<3, or hand_profile missing.
        Server-side clamp: floor=1.00, hard cap=1.25 (ignores client values).
        Affects skill deltas only — XP and score_normalized are unaffected.
        """
        raw = data.get("raw_metrics")
        if not isinstance(raw, dict) or int(raw.get("v", 1)) < 3:
            return 1.00
        hp = raw.get("hand_profile") or {}
        try:
            pdm = float(hp.get("protocol_difficulty_multiplier", 1.00))
        except (TypeError, ValueError):
            return 1.00
        return max(1.00, min(1.25, pdm))

    # ── XP calculation ────────────────────────────────────────────────────────

    @staticmethod
    def calculate_xp_multiplier(attempt_index: int) -> float:
        """
        Diminishing returns multiplier by daily attempt index (per game).

        Index 1 → 1.00 (full XP)
        Index 2 → 0.75
        Index 3 → 0.50
        Index 4 → 0.30
        Index 5 → 0.15
        Index 6+ → 0.00 (no XP, no skill delta, no negative penalty)
        """
        return _XP_MULTIPLIER_TABLE.get(attempt_index, 0.0)

    @staticmethod
    def calculate_xp_awarded(game: VirtualTrainingGame, multiplier: float) -> int:
        """Compute floor(base_xp * multiplier). Returns 0 when multiplier is 0."""
        return int(game.base_xp * multiplier)

    # ── Write path ────────────────────────────────────────────────────────────

    @staticmethod
    def record_attempt(
        db: Session,
        user_id: int,
        game: VirtualTrainingGame,
        data: dict,
        idempotency_key: str,
        is_challenge: bool = False,
        # Phase 1: location + timezone from browser (all optional)
        location_lat: "float | None" = None,
        location_lng: "float | None" = None,
        location_accuracy_m: "int | None" = None,
        location_captured_at: "datetime | None" = None,
        browser_timezone: "str | None" = None,
    ) -> VirtualTrainingAttempt:
        """
        Persist one validated VirtualTrainingAttempt and award XP.

        Idempotent: if a row with the same idempotency_key already exists,
        the existing row is returned without re-awarding XP.
        Does NOT commit — caller owns the transaction boundary.

        is_challenge=True applies Virtual Challenge accounting policy:
          - skill delta uses full game_mult (no daily-index penalty);
          - skill delta is computed even when xp_awarded=0 (attempt_index>=6);
          - XP diminishing returns are unchanged (reward scope excluded).
          - Caller must inject raw_metrics["attempt_source"]="challenge" before calling.

        data keys (all optional except started_at):
          started_at, duration_seconds, stimuli_count, correct_count,
          error_count, wrong_click_count, avg_reaction_ms, min_reaction_ms,
          score_raw, score_normalized
        """
        from sqlalchemy.exc import IntegrityError
        from app.services.gamification import xp_service
        from app.services.virtual_training_metrics import compute_vt_skill_deltas
        from app.services.training_day import (
            resolve_training_timezone,
            resolve_location_source,
            compute_training_local_date,
        )

        now = datetime.now(timezone.utc)

        # ── Phase 1: resolve training day from browser timezone ───────────────
        training_tz, tz_src = resolve_training_timezone(browser_timezone)
        training_date = compute_training_local_date(now, training_tz)
        loc_src = resolve_location_source(location_lat, location_lng, location_captured_at, now)

        # For games with per-difficulty validation (e.g. TT), use difficulty-specific
        # overrides when difficulty_level is present in raw_metrics.
        _cfg = game.config if isinstance(game.config, dict) else {}
        _difficulties = _cfg.get("difficulties")
        if isinstance(_difficulties, dict):
            _raw = data.get("raw_metrics")
            _level = (
                _raw.get("difficulty_level", "easy")
                if isinstance(_raw, dict) else "easy"
            )
            _diff_block = _difficulties.get(_level) or _difficulties.get("easy") or {}
            validation_overrides = _diff_block.get("validation_overrides") or _cfg.get("validation_overrides")
        else:
            validation_overrides = _cfg.get("validation_overrides")
        is_valid, invalid_reason = VirtualTrainingService.validate_attempt(
            data, overrides=validation_overrides
        )

        attempt_index = VirtualTrainingService.calculate_daily_attempt_index(
            db, user_id, game.id, training_local_date=training_date,
        )
        # xp_multiplier: diminishing returns by attempt index (affects XP + delta ceiling)
        xp_multiplier = VirtualTrainingService.calculate_xp_multiplier(attempt_index)
        xp_awarded = VirtualTrainingService.calculate_xp_awarded(game, xp_multiplier) if is_valid else 0

        # game_mult: difficulty multiplier affecting skill delta only (XP unchanged).
        # Games with a 'difficulties' config (e.g. TT) use the level-based multiplier
        # from raw_metrics.difficulty_multiplier.  All others use the hand/finger
        # protocol multiplier from raw_metrics.hand_profile.protocol_difficulty_multiplier.
        _game_cfg = game.config if isinstance(game.config, dict) else {}
        if _game_cfg.get("difficulties"):
            game_mult = VirtualTrainingService.extract_difficulty_multiplier(data)
        else:
            game_mult = VirtualTrainingService.extract_protocol_difficulty(data)
        # Challenge attempts: skill delta always at full game_mult (no daily-index
        # penalty). Solo attempts: existing xp_multiplier × game_mult unchanged.
        if is_challenge:
            skill_delta_multiplier = game_mult
        else:
            skill_delta_multiplier = xp_multiplier * game_mult

        compute_delta = is_valid and (xp_awarded > 0 or is_challenge)
        if compute_delta:
            neg_rows = db.execute(
                text(
                    """
                    SELECT kv.key, SUM(kv.value::float) AS neg_today
                    FROM virtual_training_attempts vta,
                         jsonb_each_text(vta.skill_deltas) AS kv(key, value)
                    WHERE vta.user_id            = :uid
                      AND vta.is_valid           = true
                      AND vta.training_local_date = :training_date
                      AND kv.value::float < 0
                    GROUP BY kv.key
                    """
                ),
                {"uid": user_id, "training_date": training_date},
            ).fetchall()
            existing_neg_today: dict[str, float] = {row.key: row.neg_today for row in neg_rows}
            skill_deltas = compute_vt_skill_deltas(
                data=data, game=game, multiplier=skill_delta_multiplier,
                existing_neg_today=existing_neg_today,
            )
        else:
            skill_deltas = {}

        started_at = data.get("started_at")
        if isinstance(started_at, str):
            try:
                started_at = datetime.fromisoformat(started_at)
            except ValueError:
                started_at = now
        if started_at is None:
            started_at = now

        sp = db.begin_nested()
        try:
            attempt = VirtualTrainingAttempt(
                user_id=user_id,
                game_id=game.id,
                started_at=started_at,
                completed_at=now,
                is_valid=is_valid,
                invalid_reason=invalid_reason,
                score_raw=data.get("score_raw"),
                score_normalized=data.get("score_normalized"),
                avg_reaction_ms=data.get("avg_reaction_ms"),
                min_reaction_ms=data.get("min_reaction_ms"),
                duration_seconds=data.get("duration_seconds"),
                stimuli_count=data.get("stimuli_count"),
                correct_count=data.get("correct_count"),
                error_count=data.get("error_count"),
                wrong_click_count=data.get("wrong_click_count"),
                raw_metrics=data.get("raw_metrics"),
                xp_awarded=xp_awarded,
                skill_deltas=skill_deltas,
                attempt_index_today=attempt_index,
                idempotency_key=idempotency_key,
                # Phase 1: location + training day
                location_lat=location_lat,
                location_lng=location_lng,
                location_accuracy_m=location_accuracy_m,
                location_captured_at=location_captured_at,
                location_source=loc_src,
                browser_timezone=browser_timezone,
                training_timezone=training_tz,
                training_timezone_source=tz_src,
                training_local_date=training_date,
            )
            db.add(attempt)
            sp.commit()
        except IntegrityError:
            sp.rollback()
            attempt = (
                db.query(VirtualTrainingAttempt)
                .filter(VirtualTrainingAttempt.idempotency_key == idempotency_key)
                .first()
            )
            return attempt

        if is_valid and xp_awarded > 0:
            xp_service.award_xp(
                db=db,
                user_id=user_id,
                xp_amount=xp_awarded,
                reason=f"Virtual Training: {game.name}",
                idempotency_key=f"{idempotency_key}_xp",
                transaction_type="VIRTUAL_TRAINING_XP",
            )

        return attempt
