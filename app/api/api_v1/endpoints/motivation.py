"""
Motivation Assessment API Endpoints
====================================
Handles motivation/preference assessments completed after specialization unlock.
"""
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
import json

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.schemas.motivation import (
    MotivationAssessmentRequest,
    MotivationAssessmentResponse
)

router = APIRouter()


@router.post("/motivation-assessment", response_model=MotivationAssessmentResponse)
def submit_motivation_assessment(
    data: MotivationAssessmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Submit motivation/preference assessment after specialization unlock

    **Workflow:**
    1. User unlocks specialization (100 credits)
    2. User completes THIS motivation assessment (ONCE per specialization)
    3. User can start semester enrollments

    **Specialization-specific assessments:**
    - **LFA Player:** 7 skill self-ratings (1-10 scale)
    - **GānCuju:** Character type selection (Warrior/Teacher)
    - **Coach:** Age group + Role + Specialization preferences
    - **Internship:** Position selection (45 positions)

    **Storage:** Saved to `user_licenses.motivation_scores` (JSON field)
    """
    try:
        # 1. Get user's active license
        license_result = db.execute(
            text("""
                SELECT id, specialization_type, motivation_scores
                FROM user_licenses
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"user_id": current_user.id}
        ).fetchone()

        if not license_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active license found. Please unlock a specialization first."
            )

        license_id = license_result[0]
        specialization_type = license_result[1]
        existing_motivation = license_result[2]

        # 2. Validate user hasn't already completed motivation assessment
        if existing_motivation is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Motivation assessment already completed for this specialization."
            )

        # 3. Extract motivation data and validate it matches specialization
        try:
            motivation_data = data.get_motivation_data()
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )

        # Validate motivation type matches license specialization
        if data.lfa_player and specialization_type not in ["LFA_FOOTBALL_PLAYER", "LFA_PLAYER", "LFA_PLAYER_PRE", "LFA_PLAYER_YOUTH", "LFA_PLAYER_AMATEUR", "LFA_PLAYER_PRO"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"LFA Player motivation provided but license is {specialization_type}"
            )
        elif data.gancuju and specialization_type != "GANCUJU_PLAYER":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GānCuju motivation provided but license is {specialization_type}"
            )
        elif data.coach and specialization_type != "LFA_COACH":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Coach motivation provided but license is {specialization_type}"
            )
        elif data.internship and specialization_type != "INTERNSHIP":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Internship motivation provided but license is {specialization_type}"
            )

        # 4. Update user_licenses.motivation_scores with JSON data
        db.execute(
            text("""
                UPDATE user_licenses
                SET motivation_scores = :json_data,
                    motivation_last_assessed_at = NOW(),
                    motivation_assessed_by = :user_id
                WHERE id = :license_id
            """),
            {
                "json_data": json.dumps(motivation_data),
                "user_id": current_user.id,
                "license_id": license_id
            }
        )
        db.commit()

        # 5. Return success response
        return MotivationAssessmentResponse(
            success=True,
            message=f"Motivation assessment completed successfully for {specialization_type}",
            motivation_data=motivation_data
        )

    except HTTPException:
        # Re-raise HTTP exceptions without wrapping
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save motivation assessment: {str(e)}"
        )


@router.get("/motivation-assessment", response_model=Dict[str, Any])
def get_motivation_assessment(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get user's motivation assessment if completed

    Returns:
        Motivation data JSON or empty dict if not completed
    """
    try:
        result = db.execute(
            text("""
                SELECT motivation_scores, specialization_type, motivation_last_assessed_at
                FROM user_licenses
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"user_id": current_user.id}
        ).fetchone()

        if not result or result[0] is None:
            return {
                "completed": False,
                "motivation_data": None,
                "specialization_type": result[1] if result else None
            }

        # Parse JSON string to dict
        motivation_data = json.loads(result[0]) if isinstance(result[0], str) else result[0]

        # completed = True only when the 44-skill baseline self-rating has been submitted.
        # R3C onboarding sets motivation_scores (not null) but does NOT set
        # self_assessment_completed — so R3C alone does not satisfy this check.
        completed = isinstance(motivation_data, dict) and motivation_data.get("self_assessment_completed") is True

        return {
            "completed": completed,
            "motivation_data": motivation_data,
            "specialization_type": result[1],
            "assessed_at": result[2].isoformat() if result[2] else None
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve motivation assessment: {str(e)}"
        )
