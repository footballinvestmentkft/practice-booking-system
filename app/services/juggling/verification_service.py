"""PC6A canonical, human-reviewed juggling verification authority.

The caller owns commit/rollback. One locked video annotation revision produces
one immutable verification and one canonical PC5 outcome.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.config import settings
from app.models.credit_transaction import TransactionType
from app.models.juggling import (
    JugglingCaptureAttempt,
    JugglingCaptureAttemptStatus,
    JugglingCaptureMode,
    JugglingConsent,
    JugglingContactEvent,
    JugglingVerification,
    JugglingVideo,
)
from app.models.user import User, UserRole
from app.services.credit_service import CreditService
from app.services.juggling.sequence_policy import ReviewedContact, SequencePolicyError, validate_human_sequence
from app.services.juggling.taxonomy_service import derive_side, is_stable, validate_contact_type
from app.services.player_progression_service import PlayerProgressionError, PlayerProgressionService
from app.services.public_identity_service import (
    PublicIdentityPolicyError,
    require_public_display_identity,
)


VERIFICATION_VERSION = "PC6A_V1"


class JugglingVerificationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JugglingVerificationPolicy:
    xp_reward: int = 0
    credit_reward: int = 0

    def __post_init__(self) -> None:
        if self.xp_reward < 0 or self.credit_reward < 0:
            raise ValueError("JUGGLING_REWARD_MUST_BE_NONNEGATIVE")


@dataclass(frozen=True)
class ContactReviewDecision:
    event_id: UUID
    decision: Literal["CONFIRM", "REJECT", "CORRECT"]
    include_in_sequence: bool = False
    corrected_contact_type: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class HumanVerificationCommand:
    player_user_id: int
    expected_annotation_revision_hash: str
    event_decisions: tuple[ContactReviewDecision, ...]
    status: Literal["VALID", "INVALID"]
    sequence_start_ms: int | None
    sequence_end_ms: int | None
    termination_reason: Literal["BALL_DROPPED", "VIDEO_ENDED", "PLAYER_STOPPED", "INVALID_SEQUENCE"]
    confidence: float


@dataclass(frozen=True)
class VerificationCandidate:
    video_id: UUID
    player_user_id: int
    video_checksum_sha256: str
    video_version: str
    annotation_revision_hash: str
    contact_events: tuple[dict[str, Any], ...]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _event_snapshot(event: JugglingContactEvent) -> dict[str, Any]:
    return {
        "event_id": str(event.id),
        "device_event_id": str(event.device_event_id),
        "created_by_user_id": event.created_by_user_id,
        "timestamp_ms": int(event.timestamp_ms),
        "contact_type": event.contact_type,
        "side": event.side,
        "annotation_confidence": event.annotation_confidence,
        "annotation_source": event.annotation_source,
        "source_kind": "MODEL" if event.annotation_source == "model_prediction" else "HUMAN",
        "annotation_review_status": event.annotation_review_status,
        "taxonomy_review_status": event.taxonomy_review_status,
        "taxonomy_version": event.taxonomy_version,
        "excluded_from_count": bool(event.excluded_from_count),
        "model_confidence": event.model_confidence,
        "version": int(event.version),
        "reviewed_by_user_id": event.reviewed_by_user_id,
        "reviewed_at": event.reviewed_at.isoformat() if event.reviewed_at else None,
        "review_note": event.review_note,
        "deleted_at": event.deleted_at.isoformat() if event.deleted_at else None,
    }


def _snapshot(events: list[JugglingContactEvent]) -> list[dict[str, Any]]:
    return [_event_snapshot(row) for row in sorted(events, key=lambda row: (row.timestamp_ms, str(row.id)))]


def _command_hash(command: HumanVerificationCommand) -> str:
    value = asdict(command)
    value["event_decisions"] = sorted(value["event_decisions"], key=lambda row: str(row["event_id"]))
    return _hash(value)


def _body_part(contact_type: str) -> str:
    for prefix in ("left_", "right_"):
        if contact_type.startswith(prefix):
            return contact_type[len(prefix):]
    return contact_type


class JugglingVerificationService:
    def __init__(self, db: Session, *, policy: JugglingVerificationPolicy | None = None):
        self.db = db
        self.policy = policy or JugglingVerificationPolicy(
            xp_reward=settings.JUGGLING_VERIFIED_RESULT_XP,
            credit_reward=settings.JUGGLING_VERIFIED_RESULT_CREDIT,
        )

    def _video(self, video_id: UUID, *, lock: bool = False) -> JugglingVideo:
        query = self.db.query(JugglingVideo).filter(JugglingVideo.id == video_id)
        if lock:
            query = query.with_for_update()
        row = query.one_or_none()
        if row is None:
            raise JugglingVerificationError("VIDEO_NOT_FOUND")
        return row

    def _events(self, video_id: UUID, *, lock: bool = False) -> list[JugglingContactEvent]:
        query = self.db.query(JugglingContactEvent).filter(JugglingContactEvent.video_id == video_id)
        if lock:
            query = query.with_for_update()
        return query.order_by(JugglingContactEvent.timestamp_ms, JugglingContactEvent.id).all()

    def _require_canonical_capture(self, video: JugglingVideo) -> JugglingCaptureAttempt:
        if (
            video.capture_attempt_id is None
            or video.capture_mode != JugglingCaptureMode.solo_verified.value
            or video.authoritative_camera_position != "front"
            or video.source_type != "in_app_capture"
            or video.upload_source != "camera"
        ):
            raise JugglingVerificationError("NON_CANONICAL_CAPTURE_PROVENANCE")
        attempt = self.db.get(JugglingCaptureAttempt, video.capture_attempt_id)
        if (
            attempt is None
            or attempt.user_id != video.user_id
            or attempt.status != JugglingCaptureAttemptStatus.bound.value
            or attempt.capture_mode != video.capture_mode
            or attempt.camera_position != video.authoritative_camera_position
        ):
            raise JugglingVerificationError("NON_CANONICAL_CAPTURE_PROVENANCE")
        return attempt

    @staticmethod
    def _video_identity(video: JugglingVideo) -> tuple[str, str]:
        if video.checksum_processed:
            return video.checksum_processed, f"processed:sha256:{video.checksum_processed}"
        if video.checksum_sha256:
            return video.checksum_sha256, f"original:sha256:{video.checksum_sha256}"
        raise JugglingVerificationError("VIDEO_CHECKSUM_REQUIRED")

    def get_candidate(
        self, *, video_id: UUID, reviewer: User | None = None,
    ) -> VerificationCandidate:
        video = self._video(video_id)
        self._require_canonical_capture(video)
        if reviewer is not None:
            if reviewer.role != UserRole.ADMIN:
                raise JugglingVerificationError("ADMIN_REVIEWER_REQUIRED")
            consent = self.db.query(JugglingConsent).filter_by(user_id=video.user_id).one_or_none()
            if consent is None or not consent.service_consent or not consent.admin_review_consent:
                raise JugglingVerificationError("ADMIN_REVIEW_CONSENT_REQUIRED")
        checksum, version = self._video_identity(video)
        snapshot = _snapshot(self._events(video.id))
        return VerificationCandidate(
            video_id=video.id,
            player_user_id=video.user_id,
            video_checksum_sha256=checksum,
            video_version=version,
            annotation_revision_hash=_hash(snapshot),
            contact_events=tuple(row for row in snapshot if row["deleted_at"] is None),
        )

    def verify_human(
        self,
        *,
        video_id: UUID,
        reviewer: User,
        command: HumanVerificationCommand,
    ) -> JugglingVerification:
        if reviewer.role != UserRole.ADMIN:
            raise JugglingVerificationError("ADMIN_REVIEWER_REQUIRED")
        if not 0.0 <= command.confidence <= 1.0:
            raise JugglingVerificationError("INVALID_CONFIDENCE")
        if len(command.expected_annotation_revision_hash) != 64:
            raise JugglingVerificationError("INVALID_ANNOTATION_REVISION")

        video = self._video(video_id, lock=True)
        if video.user_id != command.player_user_id:
            raise JugglingVerificationError("PLAYER_VIDEO_MISMATCH")
        if video.training_video_type != "juggling":
            raise JugglingVerificationError("JUGGLING_VIDEO_REQUIRED")
        capture_attempt = self._require_canonical_capture(video)

        request_hash = _command_hash(command)
        existing = self.db.query(JugglingVerification).filter_by(
            video_id=video.id,
            source_annotation_revision_hash=command.expected_annotation_revision_hash,
        ).one_or_none()
        if existing is not None:
            if existing.request_hash != request_hash or existing.player_user_id != command.player_user_id:
                raise JugglingVerificationError("VERIFICATION_REPLAY_MISMATCH")
            return existing

        if video.status != "analyzed" or video.annotation_status != "human_review_pending":
            raise JugglingVerificationError("VIDEO_NOT_READY_FOR_REVIEW")
        consent = self.db.query(JugglingConsent).filter_by(user_id=video.user_id).one_or_none()
        if consent is None or not consent.service_consent or not consent.admin_review_consent:
            raise JugglingVerificationError("ADMIN_REVIEW_CONSENT_REQUIRED")

        player = self.db.get(User, command.player_user_id)
        if player is None:
            raise JugglingVerificationError("PLAYER_NOT_FOUND")
        try:
            player_nickname = require_public_display_identity(self.db, player)
        except PublicIdentityPolicyError as exc:
            raise JugglingVerificationError(exc.code) from exc
        all_events = self._events(video.id, lock=True)
        source_snapshot = _snapshot(all_events)
        if _hash(source_snapshot) != command.expected_annotation_revision_hash:
            raise JugglingVerificationError("ANNOTATION_REVISION_MISMATCH")

        active = {row.id: row for row in all_events if row.deleted_at is None}
        decision_ids = [row.event_id for row in command.event_decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise JugglingVerificationError("DUPLICATE_CONTACT_REVIEW")
        if set(decision_ids) != set(active):
            raise JugglingVerificationError("CONTACT_REVIEW_MUST_COVER_REVISION")

        now = datetime.now(timezone.utc)
        included: set[UUID] = set()
        for decision in command.event_decisions:
            event = active[decision.event_id]
            if decision.decision == "REJECT":
                if decision.include_in_sequence:
                    raise JugglingVerificationError("REJECTED_CONTACT_CANNOT_BE_INCLUDED")
                event.annotation_review_status = "rejected"
                event.excluded_from_count = True
            elif decision.decision == "CONFIRM":
                if not is_stable(event.contact_type):
                    raise JugglingVerificationError("AMBIGUOUS_CONTACT_REQUIRES_CORRECTION_OR_REJECTION")
                event.annotation_review_status = "confirmed"
                event.taxonomy_review_status = "not_applicable"
                event.excluded_from_count = not decision.include_in_sequence
            else:
                if not decision.corrected_contact_type:
                    raise JugglingVerificationError("CORRECTED_CONTACT_TYPE_REQUIRED")
                try:
                    validate_contact_type(decision.corrected_contact_type)
                except ValueError as exc:
                    raise JugglingVerificationError("INVALID_CORRECTED_CONTACT_TYPE") from exc
                if not is_stable(decision.corrected_contact_type):
                    raise JugglingVerificationError("CORRECTION_MUST_USE_STABLE_CONTACT_TYPE")
                event.contact_type = decision.corrected_contact_type
                event.side = derive_side(decision.corrected_contact_type)
                event.annotation_source = "user_corrected"
                event.annotation_review_status = "corrected"
                event.taxonomy_review_status = "not_applicable"
                event.excluded_from_count = not decision.include_in_sequence
            if decision.include_in_sequence:
                included.add(event.id)
            event.user_confirmed = decision.decision in {"CONFIRM", "CORRECT"}
            event.reviewed_by_user_id = reviewer.id
            event.reviewed_at = now
            event.review_note = decision.note
            event.version += 1
            event.updated_at = now

        reviewed_contacts = [
            ReviewedContact(
                event_id=row.id,
                timestamp_ms=int(row.timestamp_ms),
                contact_type=row.contact_type,
                side=row.side,
                review_status=row.annotation_review_status,
                taxonomy_review_status=row.taxonomy_review_status,
                included=row.id in included,
            )
            for row in active.values()
        ]
        try:
            sequence = validate_human_sequence(
                contacts=reviewed_contacts,
                status=command.status,
                sequence_start_ms=command.sequence_start_ms,
                sequence_end_ms=command.sequence_end_ms,
                termination_reason=command.termination_reason,
            )
        except SequencePolicyError as exc:
            raise JugglingVerificationError(exc.code) from exc

        final_snapshot = _snapshot(all_events)
        final_revision_hash = _hash(final_snapshot)
        side_breakdown: dict[str, int] = {}
        body_breakdown: dict[str, int] = {}
        for row in reviewed_contacts:
            if not row.included:
                continue
            if row.side:
                side_breakdown[row.side] = side_breakdown.get(row.side, 0) + 1
            body = _body_part(row.contact_type)
            body_breakdown[body] = body_breakdown.get(body, 0) + 1

        checksum, video_version = self._video_identity(video)
        verification_id = uuid4()
        provenance = {
            "verification_id": str(verification_id),
            "verification_method": "HUMAN",
            "verification_version": VERIFICATION_VERSION,
            "video_id": str(video.id),
            "video_checksum_sha256": checksum,
            "video_version": video_version,
            "source_annotation_revision_hash": command.expected_annotation_revision_hash,
            "contact_event_revision_hash": final_revision_hash,
            "reviewer_user_id": reviewer.id,
            "player_user_id": player.id,
            "player_nickname_snapshot": player_nickname,
            "capture_attempt_id": str(capture_attempt.id),
            "capture_mode": capture_attempt.capture_mode,
            "camera_position": capture_attempt.camera_position,
            "status": command.status,
            "score": sequence.score,
            "touch_count": sequence.touch_count,
            "sequence_start_ms": sequence.sequence_start_ms,
            "sequence_end_ms": sequence.sequence_end_ms,
            "termination_reason": sequence.termination_reason,
            "confidence": command.confidence,
            "source_contact_event_snapshot": source_snapshot,
        }
        provenance_hash = _hash(provenance)
        xp_reward = self.policy.xp_reward if sequence.is_valid else 0
        credit_reward = self.policy.credit_reward if sequence.is_valid else 0
        try:
            outcome = PlayerProgressionService(self.db).apply_outcome(
                player=player,
                source_player_user_id=player.id,
                source_type="JUGGLING_VERIFICATION",
                source_id=str(verification_id),
                idempotency_key=f"juggling-verification:{verification_id}:progression",
                actor=reviewer,
                xp_delta=xp_reward,
                skill_updates={},
                achievements=[],
                provenance={**provenance, "provenance_hash": provenance_hash},
                context={"score": sequence.score, "sequence_valid": sequence.is_valid},
            )
        except PlayerProgressionError as exc:
            raise JugglingVerificationError(exc.code) from exc

        credit_transaction = None
        if credit_reward:
            credit_transaction = CreditService(self.db).credit(
                user=player,
                amount=credit_reward,
                transaction_type=TransactionType.JUGGLING_VERIFIED_RESULT_REWARD.value,
                description=f"Verified juggling result {verification_id}",
                idempotency_key=f"juggling_verification_{verification_id}_credit",
            )

        record = JugglingVerification(
            id=verification_id,
            player_user_id=player.id,
            player_nickname_snapshot=player_nickname,
            video_id=video.id,
            reviewer_user_id=reviewer.id,
            verification_method="HUMAN",
            verification_version=VERIFICATION_VERSION,
            status=command.status,
            video_checksum_sha256=checksum,
            video_version=video_version,
            source_annotation_revision_hash=command.expected_annotation_revision_hash,
            contact_event_revision_hash=final_revision_hash,
            request_hash=request_hash,
            contact_event_snapshot=final_snapshot,
            touch_count=sequence.touch_count,
            score=sequence.score,
            side_breakdown=side_breakdown,
            body_part_breakdown=body_breakdown,
            sequence_start_ms=sequence.sequence_start_ms,
            sequence_end_ms=sequence.sequence_end_ms,
            termination_reason=sequence.termination_reason,
            confidence=command.confidence,
            provenance=provenance,
            provenance_hash=provenance_hash,
            progression_outcome_id=outcome.id,
            credit_transaction_id=credit_transaction.id if credit_transaction else None,
            verified_at=now,
        )
        self.db.add(record)
        video.annotation_status = "reviewed" if sequence.is_valid else "rejected"
        video.total_juggling_count = sequence.touch_count
        self.db.flush()
        return record

    def get_for_player(self, *, video_id: UUID, player: User) -> JugglingVerification:
        video = self._video(video_id)
        if video.user_id != player.id:
            raise JugglingVerificationError("VIDEO_NOT_FOUND")
        row = self.db.query(JugglingVerification).filter_by(video_id=video.id).one_or_none()
        if row is None:
            raise JugglingVerificationError("VERIFICATION_NOT_FOUND")
        return row
