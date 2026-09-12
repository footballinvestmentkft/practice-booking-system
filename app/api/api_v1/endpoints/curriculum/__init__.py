"""Legacy read compatibility backed by the canonical Player hierarchy.

The old raw-SQL mutation/progress routers are deliberately not registered:
they referenced a schema outside the repository migration chain and formed a
second hierarchy authority.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.education_content_service import EducationContentError, EducationContentService
from app.services.player_identity_service import PlayerIdentityError, get_authorized_football_player_context


router = APIRouter()


@router.get("/track/{specialization_id}")
def get_curriculum_track(
    specialization_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        get_authorized_football_player_context(db, user=current_user)
        rows = EducationContentService(db).list_published_tracks(specialization_id)
    except PlayerIdentityError as exc:
        raise HTTPException(status_code=403, detail=exc.code) from exc
    except EducationContentError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    if not rows:
        raise HTTPException(status_code=404, detail="PUBLISHED_RELEASE_NOT_FOUND")
    return rows[0]


@router.get("/track/{specialization_id}/lessons")
def get_curriculum_lessons(
    specialization_id: str,
    locale: str = Query("en"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    track = get_curriculum_track(specialization_id, current_user, db)
    try:
        tree = EducationContentService(db).get_published_curriculum(track["id"], locale=locale)
    except EducationContentError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return [lesson for module in tree["modules"] for lesson in module["lessons"]]
