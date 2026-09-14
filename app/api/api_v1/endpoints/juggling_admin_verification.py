"""Admin-only PC6A human review and canonical juggling verification."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin_user
from app.models.user import User
from app.schemas.juggling import (
    HumanJugglingVerificationRequest,
    JugglingVerificationCandidateOut,
    JugglingVerificationOut,
)
from app.services.juggling.feature_flag import require_juggling_enabled
from app.services.juggling.verification_service import (
    ContactReviewDecision,
    HumanVerificationCommand,
    JugglingVerificationError,
    JugglingVerificationService,
)


router = APIRouter()


def _http_error(exc: JugglingVerificationError) -> HTTPException:
    if exc.code in {"VIDEO_NOT_FOUND"}:
        return HTTPException(status_code=404, detail=exc.code)
    if exc.code in {"ADMIN_REVIEWER_REQUIRED", "ADMIN_REVIEW_CONSENT_REQUIRED"}:
        return HTTPException(status_code=403, detail=exc.code)
    if exc.code in {
        "ANNOTATION_REVISION_MISMATCH", "VERIFICATION_REPLAY_MISMATCH",
        "VIDEO_NOT_READY_FOR_REVIEW",
    }:
        return HTTPException(status_code=409, detail=exc.code)
    return HTTPException(status_code=422, detail=exc.code)


@router.get(
    "/videos/{video_id}/verification-candidate",
    response_model=JugglingVerificationCandidateOut,
    dependencies=[Depends(require_juggling_enabled)],
)
def get_verification_candidate(
    video_id: UUID,
    db: Session = Depends(get_db),
    reviewer: User = Depends(get_current_admin_user),
):
    try:
        return JugglingVerificationService(db).get_candidate(
            video_id=video_id, reviewer=reviewer,
        )
    except JugglingVerificationError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/videos/{video_id}/verify",
    response_model=JugglingVerificationOut,
    dependencies=[Depends(require_juggling_enabled)],
)
def verify_juggling_video(
    video_id: UUID,
    body: HumanJugglingVerificationRequest,
    db: Session = Depends(get_db),
    reviewer: User = Depends(get_current_admin_user),
):
    command = HumanVerificationCommand(
        player_user_id=body.player_user_id,
        expected_annotation_revision_hash=body.expected_annotation_revision_hash,
        event_decisions=tuple(ContactReviewDecision(
            event_id=row.event_id,
            decision=row.decision,
            include_in_sequence=row.include_in_sequence,
            corrected_contact_type=row.corrected_contact_type,
            note=row.note,
        ) for row in body.event_decisions),
        status=body.status,
        sequence_start_ms=body.sequence_start_ms,
        sequence_end_ms=body.sequence_end_ms,
        termination_reason=body.termination_reason,
        confidence=body.confidence,
    )
    try:
        record = JugglingVerificationService(db).verify_human(
            video_id=video_id, reviewer=reviewer, command=command,
        )
        db.commit()
        db.refresh(record)
        return record
    except JugglingVerificationError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
