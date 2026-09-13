"""Player read endpoint for the canonical verified juggling result."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.juggling import JugglingVerificationOut
from app.services.juggling.feature_flag import require_juggling_enabled
from app.services.juggling.verification_service import JugglingVerificationError, JugglingVerificationService


router = APIRouter()


@router.get(
    "/me/juggling/videos/{video_id}/verification",
    response_model=JugglingVerificationOut,
    dependencies=[Depends(require_juggling_enabled)],
)
def get_my_juggling_verification(
    video_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return JugglingVerificationService(db).get_for_player(
            video_id=video_id, player=current_user,
        )
    except JugglingVerificationError as exc:
        status = 404 if exc.code in {"VIDEO_NOT_FOUND", "VERIFICATION_NOT_FOUND"} else 422
        raise HTTPException(status_code=status, detail=exc.code) from exc
