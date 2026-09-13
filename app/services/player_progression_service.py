"""PC5 canonical write authority for LFA Football Player progression.

The caller owns commit/rollback. Every XP, skill and canonical achievement write
is part of the same transaction as its immutable source outcome.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.gamification import UserAchievement, UserStats
from app.models.player_progression import PlayerProgressionOutcome, PlayerSkillChange, PlayerSkillState
from app.models.user import User
from app.services.player_identity_service import PlayerIdentityError, get_authorized_football_player_context
from app.services.xp_write_service import XPWriteService
from app.skills_config import get_all_skill_keys

PLAYER_PROGRAM_ID = "LFA_FOOTBALL_PLAYER"
_SOURCE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,49}$")


class PlayerProgressionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _legacy_skill_value(license_row, skill_key: str) -> float:
    value = (license_row.football_skills or {}).get(skill_key, 0.0)
    if isinstance(value, dict):
        value = value.get("current_level", value.get("baseline", 0.0))
    try:
        return max(0.0, min(100.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


class PlayerProgressionService:
    def __init__(self, db: Session):
        self.db = db

    def _authorized_context(self, player: User):
        try:
            return get_authorized_football_player_context(self.db, user=player)
        except PlayerIdentityError as exc:
            raise PlayerProgressionError(exc.code) from exc

    def apply_outcome(
        self,
        *,
        player: User,
        source_type: str,
        source_id: str,
        idempotency_key: str,
        actor: User,
        source_player_user_id: int | None = None,
        xp_delta: int = 0,
        skill_updates: dict[str, float] | None = None,
        achievements: list[dict[str, Any]] | None = None,
        provenance: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> PlayerProgressionOutcome:
        if source_player_user_id is not None and source_player_user_id != player.id:
            raise PlayerProgressionError("SOURCE_PLAYER_MISMATCH")
        if not source_id or not idempotency_key or not _SOURCE_PATTERN.fullmatch(source_type or ""):
            raise PlayerProgressionError("INVALID_OUTCOME_IDENTITY")
        if not isinstance(xp_delta, int) or xp_delta < 0:
            raise PlayerProgressionError("INVALID_XP_DELTA")

        skills = {key: round(float(value), 4) for key, value in (skill_updates or {}).items()}
        valid_skills = set(get_all_skill_keys())
        if any(key not in valid_skills or value < 0 or value > 100 for key, value in skills.items()):
            raise PlayerProgressionError("INVALID_SKILL_UPDATE")
        grants = sorted((achievements or []), key=lambda row: row.get("key", ""))
        if any(not row.get("key") or not row.get("badge_type") or not row.get("title") for row in grants):
            raise PlayerProgressionError("INVALID_ACHIEVEMENT_GRANT")

        payload = {
            "player_user_id": player.id,
            "source_type": source_type,
            "source_id": str(source_id),
            "xp_delta": xp_delta,
            "skill_updates": skills,
            "achievements": grants,
            "provenance": provenance or {},
            "context": context or {},
        }
        payload_hash = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

        locked_player = self.db.query(User).filter(User.id == player.id).with_for_update().one_or_none()
        if locked_player is None:
            raise PlayerProgressionError("PLAYER_NOT_FOUND")
        player_context = self._authorized_context(locked_player)

        existing = self.db.query(PlayerProgressionOutcome).filter_by(
            player_user_id=locked_player.id, source_type=source_type, source_id=str(source_id)
        ).one_or_none()
        if existing is None:
            existing = self.db.query(PlayerProgressionOutcome).filter_by(idempotency_key=idempotency_key).one_or_none()
        if existing is not None:
            if existing.payload_hash != payload_hash or existing.player_user_id != locked_player.id:
                raise PlayerProgressionError("OUTCOME_REPLAY_MISMATCH")
            return existing

        outcome = PlayerProgressionOutcome(
            player_user_id=locked_player.id,
            specialization_id=PLAYER_PROGRAM_ID,
            source_type=source_type,
            source_id=str(source_id),
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            xp_delta=xp_delta,
            provenance=provenance or {},
            context=context or {},
            actor_user_id=actor.id,
        )
        self.db.add(outcome)
        self.db.flush()

        if xp_delta:
            self._award_xp(locked_player, outcome)
        if skills:
            self._apply_skills(locked_player, player_context.license, actor, outcome, skills)
        for grant in grants:
            self._award_achievement(locked_player, outcome, grant)
        self.db.flush()
        return outcome

    def _award_xp(self, player: User, outcome: PlayerProgressionOutcome) -> None:
        key = (
            f"assessment:{outcome.source_id}:xp"
            if outcome.source_type == "EDUCATION_ASSESSMENT"
            else f"progression:{outcome.id}:xp"
        )
        XPWriteService(self.db).award(
            user_id=player.id,
            amount=outcome.xp_delta,
            transaction_type="PLAYER_PROGRESSION_OUTCOME",
            description=f"Canonical Player progression outcome {outcome.source_type}:{outcome.source_id}",
            idempotency_key=key,
        )

    def _apply_skills(self, player: User, license_row, actor: User, outcome: PlayerProgressionOutcome, skills: dict[str, float]) -> None:
        if license_row.canonical_program_id != PLAYER_PROGRAM_ID:
            raise PlayerProgressionError("PLAYER_SPECIALIZATION_REQUIRED")
        if license_row.football_skills is None:
            license_row.football_skills = {}
        for skill_key, value in sorted(skills.items()):
            state = self.db.query(PlayerSkillState).filter_by(
                player_user_id=player.id, skill_key=skill_key
            ).with_for_update().one_or_none()
            previous = state.current_value if state is not None else _legacy_skill_value(license_row, skill_key)
            if state is None:
                state = PlayerSkillState(
                    player_user_id=player.id, skill_key=skill_key,
                    current_value=value, last_outcome_id=outcome.id,
                )
                self.db.add(state)
            else:
                state.current_value = value
                state.last_outcome_id = outcome.id
                state.updated_at = datetime.now(timezone.utc)
            self.db.add(PlayerSkillChange(
                outcome_id=outcome.id, player_user_id=player.id, skill_key=skill_key,
                previous_value=previous, new_value=value, delta=round(value - previous, 4),
            ))
            legacy = license_row.football_skills.get(skill_key)
            if isinstance(legacy, dict):
                legacy = dict(legacy)
                legacy["current_level"] = value
                license_row.football_skills[skill_key] = legacy
            else:
                license_row.football_skills[skill_key] = value
        license_row.skills_last_updated_at = datetime.now(timezone.utc)
        license_row.skills_updated_by = actor.id
        flag_modified(license_row, "football_skills")

    def _award_achievement(self, player: User, outcome: PlayerProgressionOutcome, grant: dict[str, Any]) -> None:
        existing = self.db.query(UserAchievement).filter_by(
            user_id=player.id, canonical_key=grant["key"]
        ).one_or_none()
        if existing is not None:
            return
        self.db.add(UserAchievement(
            user_id=player.id,
            badge_type=grant["badge_type"],
            title=grant["title"],
            description=grant.get("description"),
            icon=grant.get("icon"),
            specialization_id=PLAYER_PROGRAM_ID,
            canonical_key=grant["key"],
            progression_outcome_id=outcome.id,
        ))

    def apply_assessment_outcome(self, *, player: User, attempt, actor: User) -> PlayerProgressionOutcome:
        if attempt.user_id != player.id:
            raise PlayerProgressionError("SOURCE_PLAYER_MISMATCH")
        if attempt.status != "COMPLETED":
            raise PlayerProgressionError("ASSESSMENT_OUTCOME_NOT_COMPLETE")
        release = attempt.release
        if (
            release is None
            or release.track.specialization_id != PLAYER_PROGRAM_ID
            or attempt.lesson.module.track_release_id != attempt.track_release_id
            or attempt.assessment.track_release_id != attempt.track_release_id
        ):
            raise PlayerProgressionError("ASSESSMENT_PROVENANCE_MISMATCH")
        xp_delta = int((attempt.content_provenance or {}).get("xp_reward", 0)) if attempt.passed else 0
        return self.apply_outcome(
            player=player,
            source_player_user_id=attempt.user_id,
            source_type="EDUCATION_ASSESSMENT",
            source_id=str(attempt.id),
            idempotency_key=f"assessment:{attempt.id}:progression",
            actor=actor,
            xp_delta=xp_delta,
            achievements=[{
                "key": "FIRST_PLAYER_ASSESSMENT_COMPLETED",
                "badge_type": "first_quiz_completed",
                "title": "First Player Assessment Completed",
                "description": "Completed the first canonical Player assessment.",
                "icon": "quiz",
            }],
            provenance={
                "assessment_attempt_id": str(attempt.id),
                "track_release_id": str(attempt.track_release_id),
                "lesson_id": str(attempt.lesson_id),
                "lesson_assessment_id": str(attempt.lesson_assessment_id),
                "quiz_id": attempt.quiz_id,
                "quiz_revision": attempt.quiz_revision,
                "locale": attempt.locale,
                "issued_question_snapshot_hash": (attempt.content_provenance or {}).get("issued_question_snapshot_hash"),
                "score_percent": attempt.score_percent,
                "passed": attempt.passed,
                "scoring_version": (attempt.content_provenance or {}).get("scoring_version"),
            },
            context={"delivery_mode": attempt.delivery_mode},
        )

    def get_state(self, *, player: User) -> dict[str, Any]:
        self._authorized_context(player)
        stats = self.db.query(UserStats).filter_by(user_id=player.id).one_or_none()
        skills = self.db.query(PlayerSkillState).filter_by(player_user_id=player.id).order_by(PlayerSkillState.skill_key).all()
        achievements = self.db.query(UserAchievement).filter_by(user_id=player.id).filter(
            UserAchievement.canonical_key.isnot(None)
        ).order_by(UserAchievement.earned_at, UserAchievement.id).all()
        return {
            "player_user_id": player.id,
            "specialization_id": PLAYER_PROGRAM_ID,
            "xp": int(stats.total_xp if stats is not None else player.xp_balance or 0),
            "level": int(stats.level if stats is not None else 1),
            "skills": [{"key": row.skill_key, "value": row.current_value, "last_outcome_id": str(row.last_outcome_id)} for row in skills],
            "achievements": [{"key": row.canonical_key, "badge_type": row.badge_type, "title": row.title, "earned_at": row.earned_at} for row in achievements],
        }

    def get_history(self, *, player: User, limit: int = 100) -> list[dict[str, Any]]:
        self._authorized_context(player)
        rows = self.db.query(PlayerProgressionOutcome).filter_by(player_user_id=player.id).order_by(
            PlayerProgressionOutcome.applied_at.desc(), PlayerProgressionOutcome.id.desc()
        ).limit(max(1, min(limit, 200))).all()
        return [{
            "id": str(row.id), "source_type": row.source_type, "source_id": row.source_id,
            "xp_delta": row.xp_delta, "provenance": row.provenance,
            "context": row.context, "actor_user_id": row.actor_user_id,
            "applied_at": row.applied_at,
        } for row in rows]
