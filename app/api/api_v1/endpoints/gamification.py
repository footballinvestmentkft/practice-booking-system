from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user
from ....models.user import User, UserRole
from ....services.player_identity_service import get_active_football_entitlement
from ....services.gamification import GamificationService
from ....schemas.gamification import UserGamificationResponse

router = APIRouter()


@router.get("/me", response_model=UserGamificationResponse)
def get_my_gamification_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get current user's gamification data including achievements, stats, and status
    """
    gamification_service = GamificationService(db)
    data = gamification_service.get_user_gamification_data(current_user.id)
    
    return UserGamificationResponse(**data)


@router.get("/user/{user_id}", response_model=UserGamificationResponse)
def get_user_gamification_data(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get specific user's gamification data (Admin/Instructor only)
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    
    # Check if target user exists
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    gamification_service = GamificationService(db)
    data = gamification_service.get_user_gamification_data(user_id)
    
    return UserGamificationResponse(**data)


@router.post("/refresh/{user_id}")
def refresh_user_achievements(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Manually refresh a user's achievements (Admin only)
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    # Check if target user exists
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    if get_active_football_entitlement(db, user_id=user_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Canonical Player progression is outcome-driven; refresh is not permitted",
        )
    
    gamification_service = GamificationService(db)
    achievements = gamification_service.check_and_award_semester_achievements(user_id)
    
    return {
        "message": f"Refreshed achievements for {target_user.name}",
        "new_achievements": len(achievements),
        "achievements": [
            {
                "title": ach.title,
                "description": ach.description,
                "icon": ach.icon
            }
            for ach in achievements
        ]
    }
