"""Learner reads for the canonical Player Education Core."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.education import EducationCurriculumResponse, EducationTrackSummary
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
