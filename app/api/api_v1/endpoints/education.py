"""Learner reads for the canonical Player Education Core."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.education import EducationCurriculumResponse, EducationTrackSummary
from app.schemas.assessment import (
    AssessmentAnswerRequest, AssessmentAnswerResponse, AssessmentAttemptResponse,
    AssessmentCompleteRequest, AssessmentOutcomeResponse, AssessmentStartRequest,
    IssuedQuestionResponse,
)
from app.services.assessment_integrity_service import (
    AssessmentIntegrityError, AssessmentIntegrityService,
)
from app.services.education_content_service import EducationContentError, EducationContentService
from app.services.player_identity_service import PlayerIdentityError, get_authorized_football_player_context


router = APIRouter()


def _authorize(db: Session, user: User) -> None:
    try:
        get_authorized_football_player_context(db, user=user)
    except PlayerIdentityError as exc:
        raise HTTPException(status_code=403, detail=exc.code) from exc


def _translate_error(exc: EducationContentError) -> HTTPException:
    if exc.code in {"PUBLISHED_RELEASE_NOT_FOUND", "TRACK_NOT_FOUND", "UNSUPPORTED_EDUCATION_PROGRAM"}:
        return HTTPException(status_code=404, detail=exc.code)
    return HTTPException(status_code=400, detail=exc.code)


def _assessment_error(exc: AssessmentIntegrityError) -> HTTPException:
    if exc.code in {"ATTEMPT_NOT_FOUND", "ISSUED_QUESTION_NOT_FOUND", "ASSESSMENT_NOT_FOUND"}:
        return HTTPException(status_code=404, detail=exc.code)
    if exc.code in {
        "ACTIVE_FOOTBALL_ENTITLEMENT_REQUIRED", "CURRENT_SEASON_ASSIGNMENT_REQUIRED",
        "CANONICAL_ELIGIBILITY_DENIED", "PLAYER_SPECIALIZATION_REQUIRED",
    }:
        return HTTPException(status_code=403, detail=exc.code)
    if exc.code in {"ATTEMPT_COMPLETED", "IDEMPOTENCY_KEY_CONFLICT", "MAX_ATTEMPTS_REACHED"}:
        return HTTPException(status_code=409, detail=exc.code)
    return HTTPException(status_code=400, detail=exc.code)


def _question(service: AssessmentIntegrityService, row) -> IssuedQuestionResponse:
    return IssuedQuestionResponse(**service.public_question(row))


def _attempt_response(service: AssessmentIntegrityService, row) -> AssessmentAttemptResponse:
    return AssessmentAttemptResponse(
        id=row.id, track_release_id=row.track_release_id, lesson_id=row.lesson_id,
        lesson_assessment_id=row.lesson_assessment_id, quiz_revision=row.quiz_revision,
        locale=row.locale, delivery_mode=row.delivery_mode, status=row.status,
        questions=[_question(service, question) for question in row.issued_questions],
    )


@router.get("/programs/{program_id}/tracks", response_model=list[EducationTrackSummary])
def list_player_tracks(
    program_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _authorize(db, current_user)
    try:
        return EducationContentService(db).list_published_tracks(program_id)
    except EducationContentError as exc:
        raise _translate_error(exc) from exc


@router.get("/tracks/{track_id}/curriculum", response_model=EducationCurriculumResponse)
def get_player_curriculum(
    track_id: UUID,
    locale: str = Query("en"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _authorize(db, current_user)
    try:
        return EducationContentService(db).get_published_curriculum(track_id, locale=locale)
    except EducationContentError as exc:
        raise _translate_error(exc) from exc


@router.post("/assessments/{assessment_id}/attempts", response_model=AssessmentAttemptResponse)
def start_assessment_attempt(
    assessment_id: UUID,
    payload: AssessmentStartRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = AssessmentIntegrityService(db)
    try:
        attempt = service.start_attempt(user=current_user, assessment_id=assessment_id, locale=payload.locale)
        db.commit()
        db.refresh(attempt)
        return _attempt_response(service, attempt)
    except AssessmentIntegrityError as exc:
        db.rollback()
        raise _assessment_error(exc) from exc


@router.post("/assessment-attempts/{attempt_id}/next-question", response_model=IssuedQuestionResponse | None)
def issue_adaptive_question(
    attempt_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = AssessmentIntegrityService(db)
    try:
        row = service.issue_next_question(user=current_user, attempt_id=attempt_id)
        db.commit()
        return _question(service, row) if row is not None else None
    except AssessmentIntegrityError as exc:
        db.rollback()
        raise _assessment_error(exc) from exc


@router.post("/assessment-attempts/{attempt_id}/answers", response_model=AssessmentAnswerResponse)
def answer_assessment_question(
    attempt_id: UUID,
    payload: AssessmentAnswerRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = AssessmentIntegrityService(db)
    try:
        row = service.answer_question(
            user=current_user, attempt_id=attempt_id,
            issued_question_id=payload.issued_question_id,
            selected_option_id=payload.selected_option_id,
            answer_text=payload.answer_text,
            idempotency_key=payload.idempotency_key,
        )
        db.commit()
        return AssessmentAnswerResponse(
            issued_question_id=row.id, accepted=True,
            is_correct=bool(row.is_correct), points_earned=row.points_earned,
        )
    except AssessmentIntegrityError as exc:
        db.rollback()
        raise _assessment_error(exc) from exc


@router.post("/assessment-attempts/{attempt_id}/complete", response_model=AssessmentOutcomeResponse)
def complete_assessment_attempt(
    attempt_id: UUID,
    payload: AssessmentCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = AssessmentIntegrityService(db)
    try:
        row = service.complete_attempt(
            user=current_user, attempt_id=attempt_id,
            idempotency_key=payload.idempotency_key,
        )
        db.commit()
        return AssessmentOutcomeResponse(
            attempt_id=row.id, status=row.status,
            total_questions=row.total_questions, answered_questions=row.answered_questions,
            correct_answers=row.correct_answers, total_points=row.total_points,
            earned_points=row.earned_points, score_percent=row.score_percent or 0.0,
            passed=row.passed, xp_awarded=row.xp_awarded,
            track_release_id=row.track_release_id,
            lesson_id=row.lesson_id, lesson_assessment_id=row.lesson_assessment_id,
            quiz_revision=row.quiz_revision, locale=row.locale,
            provenance=row.content_provenance,
        )
    except AssessmentIntegrityError as exc:
        db.rollback()
        raise _assessment_error(exc) from exc
