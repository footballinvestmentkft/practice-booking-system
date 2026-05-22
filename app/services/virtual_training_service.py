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

from sqlalchemy.orm import Session

from app.models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame

_XP_MULTIPLIER_TABLE: dict[int, float] = {1: 1.0, 2: 0.6, 3: 0.3}
_BOT_REACTION_THRESHOLD_MS   = 80.0   # Phase 2.1: tighter bot floor (was 100)
_MIN_DURATION_SECONDS        = 25.0   # Phase 2.1: 36 stimuli × avg ~0.7 s (was 5)
_MIN_STIMULI_COUNT           = 28     # Phase 2.1: 36 total, allow minor losses (was 5)
_RANDOM_CLICKING_THRESHOLD   = 0.55   # G4: wrong_click_count / stimuli_count > this → invalid


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

    # ── XP calculation ────────────────────────────────────────────────────────

    @staticmethod
    def calculate_xp_multiplier(attempt_index: int) -> float:
        """
        Diminishing returns multiplier by daily attempt index.

        Index 1 → 1.0 (full XP)
        Index 2 → 0.6
        Index 3 → 0.3
        Index 4+ → 0.0 (no XP, but attempt still recorded)
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
        multiplier = VirtualTrainingService.calculate_xp_multiplier(attempt_index)
        xp_awarded = VirtualTrainingService.calculate_xp_awarded(game, multiplier) if is_valid else 0

        skill_deltas = (
            compute_vt_skill_deltas(data=data, game=game, multiplier=multiplier)
            if is_valid and xp_awarded > 0
            else {}
        )

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
