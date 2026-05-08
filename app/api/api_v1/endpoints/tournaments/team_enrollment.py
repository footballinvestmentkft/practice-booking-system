"""Admin team-enrollment endpoint — POST /{tournament_id}/enroll-team."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin_user_hybrid
from app.models.user import User, UserRole
from app.services.tournament import team_service

router = APIRouter()


class TeamEnrollRequest(BaseModel):
    team_id: int


@router.post("/{tournament_id}/enroll-team", status_code=status.HTTP_200_OK)
def admin_enroll_team(
    tournament_id: int,
    body: TeamEnrollRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_hybrid),
):
    """
    Admin-only: enroll an existing team into a TEAM tournament.

    Guards (enforced by service):
    - tournament exists, participant_type == TEAM, status == ENROLLMENT_OPEN
    - team exists and is active
    - no duplicate enrollment (idempotent: returns existing enrollment on repeat call)
    - if team_enrollment_cost > 0: deducts from captain's credit balance

    Returns 200 whether the enrollment is new or pre-existing (idempotent).
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")

    enrollment = team_service.admin_enroll_team_in_tournament(
        db,
        team_id=body.team_id,
        tournament_id=tournament_id,
    )
    return {
        "enrolled": True,
        "enrollment_id": enrollment.id,
        "team_id": enrollment.team_id,
        "tournament_id": enrollment.semester_id,
        "payment_verified": enrollment.payment_verified,
    }
