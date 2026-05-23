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

_MIN_SAMPLES: int = 3   # minimum eligible attempts per (hand, finger) cell to show data

# Canonical finger display order for the stats page
_FINGER_ORDER: list[tuple[str, str, str]] = [
    ("right", "index", "Right Index"),
    ("right", "thumb", "Right Thumb"),
    ("left",  "index", "Left Index"),
    ("left",  "thumb", "Left Thumb"),
]


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
    def validate_attempt(data: dict) -> tuple[bool, Optional[str]]:
        """
        Run anti-abuse checks on raw attempt data.

        Returns (is_valid, invalid_reason).
        Checks (in order — first failing check wins):
          1. duration_seconds < 25.0   → too_short
          2. stimuli_count < 28        → too_few_stimuli
          3. wrong_click_count > 0.55 × stimuli_count → random_clicking  (G4)
          4. avg_reaction_ms < 80      → bot_suspected
        """
        duration = data.get("duration_seconds")
        if duration is not None and float(duration) < _MIN_DURATION_SECONDS:
            return False, "too_short"

        stimuli = data.get("stimuli_count")
        if stimuli is not None and int(stimuli) < _MIN_STIMULI_COUNT:
            return False, "too_few_stimuli"

        wrong_clicks = data.get("wrong_click_count")
        if wrong_clicks is not None and stimuli is not None and int(stimuli) > 0:
            if int(wrong_clicks) > _RANDOM_CLICKING_THRESHOLD * int(stimuli):
                return False, "random_clicking"

        avg_ms = data.get("avg_reaction_ms")
        if avg_ms is not None and float(avg_ms) < _BOT_REACTION_THRESHOLD_MS:
            return False, "bot_suspected"

        return True, None

    # ── Daily indexing ────────────────────────────────────────────────────────

    @staticmethod
    def calculate_daily_attempt_index(
        db: Session, user_id: int, game_id: int
    ) -> int:
        """
        Return the 1-based attempt index for today.

        Counts only valid attempts for the (user, game) pair that started
        on the current UTC calendar day. Returns 1 when no prior attempts.
        """
        today_start = datetime.combine(date.today(), datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        count = (
            db.query(VirtualTrainingAttempt)
            .filter(
                VirtualTrainingAttempt.user_id == user_id,
                VirtualTrainingAttempt.game_id == game_id,
                VirtualTrainingAttempt.started_at >= today_start,
                VirtualTrainingAttempt.is_valid == True,  # noqa: E712
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
    ) -> VirtualTrainingAttempt:
        """
        Persist one validated VirtualTrainingAttempt and award XP.

        Idempotent: if a row with the same idempotency_key already exists,
        the existing row is returned without re-awarding XP.
        Does NOT commit — caller owns the transaction boundary.

        data keys (all optional except started_at):
          started_at, duration_seconds, stimuli_count, correct_count,
          error_count, wrong_click_count, avg_reaction_ms, min_reaction_ms,
          score_raw, score_normalized
        """
        from sqlalchemy.exc import IntegrityError
        from app.services.gamification import xp_service
        from app.services.virtual_training_metrics import compute_vt_skill_deltas

        now = datetime.now(timezone.utc)

        is_valid, invalid_reason = VirtualTrainingService.validate_attempt(data)

        attempt_index = VirtualTrainingService.calculate_daily_attempt_index(
            db, user_id, game.id
        )
        # xp_multiplier: diminishing returns by attempt index (affects XP + delta ceiling)
        xp_multiplier = VirtualTrainingService.calculate_xp_multiplier(attempt_index)
        xp_awarded = VirtualTrainingService.calculate_xp_awarded(game, xp_multiplier) if is_valid else 0

        # protocol_mult: self-declared hand/finger difficulty (affects delta only)
        protocol_mult     = VirtualTrainingService.extract_protocol_difficulty(data)
        effective_multiplier = xp_multiplier * protocol_mult

        if is_valid and xp_awarded > 0:
            today_start = datetime.combine(date.today(), datetime.min.time()).replace(
                tzinfo=timezone.utc
            )
            neg_rows = db.execute(
                text(
                    """
                    SELECT kv.key, SUM(kv.value::float) AS neg_today
                    FROM virtual_training_attempts vta,
                         jsonb_each_text(vta.skill_deltas) AS kv(key, value)
                    WHERE vta.user_id    = :uid
                      AND vta.is_valid   = true
                      AND vta.started_at >= :today_start
                      AND kv.value::float < 0
                    GROUP BY kv.key
                    """
                ),
                {"uid": user_id, "today_start": today_start},
            ).fetchall()
            existing_neg_today: dict[str, float] = {row.key: row.neg_today for row in neg_rows}
            skill_deltas = compute_vt_skill_deltas(
                data=data, game=game, multiplier=effective_multiplier,
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

    # ── Hand/Finger Performance Stats (Phase 2.5) ─────────────────────────────

    @staticmethod
    def get_hand_finger_stats(
        db: Session,
        user_id: int,
        game_id: int | None = None,
    ) -> dict:
        """
        Aggregate hand/finger performance from system-assigned v3 attempts.

        Eligible filter: is_valid=TRUE, raw_metrics.v>=3, hand_profile present,
        assignment_source="system".  v1/v2 and free-protocol attempts excluded.

        game_id=None → all VT games combined.

        Returns:
          {
            "min_samples":  3,
            "finger_rows":  [...],   # 4 entries: ri/rt/li/lt in canonical order
            "by_hand":      {"right": {...}, "left": {...}},
            "skill_totals": {"right_index": {"reactions": 0.34, ...}, ...},
          }
        """
        from collections import defaultdict

        _base = """
            a.user_id = :uid
            AND a.is_valid = TRUE
            AND a.raw_metrics IS NOT NULL
            AND (a.raw_metrics->>'v')::int >= 3
            AND a.raw_metrics ? 'hand_profile'
            AND a.raw_metrics->'hand_profile'->>'assignment_source' = 'system'
        """
        params: dict = {"uid": user_id}
        if game_id is not None:
            _base += " AND a.game_id = :gid"
            params["gid"] = game_id

        # ── 1. Per-(hand, finger) aggregate metrics ───────────────────────────
        finger_sql = text(f"""
            SELECT
                raw_metrics->'hand_profile'->>'hand'   AS hand,
                raw_metrics->'hand_profile'->>'finger' AS finger,
                raw_metrics->'hand_profile'->>'label'  AS label,
                COUNT(*)                               AS attempt_count,
                ROUND(AVG(a.score_normalized)::numeric, 1)  AS avg_score,
                ROUND(AVG(a.avg_reaction_ms)::numeric, 0)   AS avg_rt_ms,
                ROUND(MIN(a.avg_reaction_ms)::numeric, 0)   AS best_rt_ms,
                ROUND(100.0 * AVG(
                    a.correct_count::float / NULLIF(a.stimuli_count, 0)
                )::numeric, 1) AS accuracy_pct,
                ROUND(100.0 * AVG(
                    a.error_count::float / NULLIF(a.stimuli_count, 0)
                )::numeric, 1) AS miss_pct,
                ROUND(100.0 * AVG(
                    a.wrong_click_count::float / NULLIF(a.stimuli_count, 0)
                )::numeric, 1) AS wrong_pct,
                ROUND(100.0 * AVG(
                    COALESCE(
                        (a.raw_metrics->'late_summary'->>'late_click_count')::float, 0
                    ) / NULLIF(a.stimuli_count, 0)
                )::numeric, 1) AS late_pct
            FROM virtual_training_attempts a
            WHERE {_base}
            GROUP BY hand, finger, label
            ORDER BY hand, finger
        """)

        seen: dict[str, dict] = {}
        for r in db.execute(finger_sql, params).fetchall():
            cnt = int(r.attempt_count)
            seen[f"{r.hand}_{r.finger}"] = {
                "hand":          r.hand,
                "finger":        r.finger,
                "label":         r.label or f"{r.hand.capitalize()} {r.finger.capitalize()}",
                "attempt_count": cnt,
                "state":         "ready" if cnt >= _MIN_SAMPLES else "low_sample",
                "avg_score":     float(r.avg_score)    if r.avg_score    is not None else None,
                "avg_rt_ms":     int(r.avg_rt_ms)      if r.avg_rt_ms    is not None else None,
                "best_rt_ms":    int(r.best_rt_ms)     if r.best_rt_ms   is not None else None,
                "accuracy_pct":  float(r.accuracy_pct) if r.accuracy_pct is not None else None,
                "miss_pct":      float(r.miss_pct)     if r.miss_pct     is not None else None,
                "wrong_pct":     float(r.wrong_pct)    if r.wrong_pct    is not None else None,
                "late_pct":      float(r.late_pct)     if r.late_pct     is not None else None,
            }

        finger_rows = []
        for hand, finger, default_label in _FINGER_ORDER:
            key = f"{hand}_{finger}"
            if key in seen:
                finger_rows.append(seen[key])
            else:
                finger_rows.append({
                    "hand": hand, "finger": finger, "label": default_label,
                    "attempt_count": 0, "state": "no_data",
                })

        # ── 2. Per-hand aggregate metrics ─────────────────────────────────────
        hand_sql = text(f"""
            SELECT
                raw_metrics->'hand_profile'->>'hand' AS hand,
                COUNT(*)                             AS attempt_count,
                ROUND(AVG(a.score_normalized)::numeric, 1)  AS avg_score,
                ROUND(AVG(a.avg_reaction_ms)::numeric, 0)   AS avg_rt_ms,
                ROUND(100.0 * AVG(
                    a.correct_count::float / NULLIF(a.stimuli_count, 0)
                )::numeric, 1) AS accuracy_pct
            FROM virtual_training_attempts a
            WHERE {_base}
            GROUP BY hand
            ORDER BY hand
        """)

        by_hand: dict[str, dict] = {
            "right": {"attempt_count": 0, "state": "no_data"},
            "left":  {"attempt_count": 0, "state": "no_data"},
        }
        for r in db.execute(hand_sql, params).fetchall():
            if r.hand in by_hand:
                cnt = int(r.attempt_count)
                by_hand[r.hand] = {
                    "attempt_count": cnt,
                    "state":         "ready" if cnt >= _MIN_SAMPLES else "low_sample",
                    "avg_score":     float(r.avg_score)    if r.avg_score    is not None else None,
                    "avg_rt_ms":     int(r.avg_rt_ms)      if r.avg_rt_ms    is not None else None,
                    "accuracy_pct":  float(r.accuracy_pct) if r.accuracy_pct is not None else None,
                }

        # ── 3. Skill delta totals per (hand_finger) ───────────────────────────
        delta_sql = text(f"""
            SELECT
                raw_metrics->'hand_profile'->>'hand'   AS hand,
                raw_metrics->'hand_profile'->>'finger' AS finger,
                a.skill_deltas
            FROM virtual_training_attempts a
            WHERE {_base}
            ORDER BY a.completed_at
        """)

        skill_acc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in db.execute(delta_sql, params).fetchall():
            deltas = r.skill_deltas
            if not isinstance(deltas, dict) or not deltas:
                continue
            combo = f"{r.hand}_{r.finger}"
            for skill, delta in deltas.items():
                try:
                    skill_acc[combo][skill] = round(
                        skill_acc[combo][skill] + float(delta), 4
                    )
                except (TypeError, ValueError):
                    pass

        skill_totals = {k: dict(v) for k, v in skill_acc.items()}

        return {
            "min_samples":  _MIN_SAMPLES,
            "finger_rows":  finger_rows,
            "by_hand":      by_hand,
            "skill_totals": skill_totals,
        }
