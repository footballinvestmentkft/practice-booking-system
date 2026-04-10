"""
Admin Batch Enrollment Endpoint for Tournaments
Allows admins to enroll multiple players in a tournament for testing/setup purposes
"""
import time
import threading
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User, UserRole
from app.models.semester import Semester
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.license import UserLicense
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Soft rate guard (in-process, no Redis) ────────────────────────────────────
_ENROLL_RATE_WINDOW: int = 60   # seconds
_ENROLL_MAX_CALLS: int = 10     # more generous than player creation — enrollment is lighter
"""
At most _ENROLL_MAX_CALLS batch-enroll calls per admin user in any rolling
_ENROLL_RATE_WINDOW window.  Prevents accidental re-enroll loops that could
create duplicate enrollment rows or saturate index scans on large tournaments.
"""
_enroll_rate_lock = threading.Lock()
_enroll_rate_calls: Dict[int, List[Tuple[float, int]]] = defaultdict(list)


def _check_enroll_rate_limit(user_id: int, incoming_count: int) -> None:
    """Raise HTTP 429 if the admin exceeds the soft rate guard."""
    now = time.monotonic()
    window_start = now - _ENROLL_RATE_WINDOW
    with _enroll_rate_lock:
        _enroll_rate_calls[user_id] = [
            (ts, n) for ts, n in _enroll_rate_calls[user_id] if ts >= window_start
        ]
        recent = _enroll_rate_calls[user_id]
        if len(recent) >= _ENROLL_MAX_CALLS:
            oldest_ts = recent[0][0]
            retry_after = int(_ENROLL_RATE_WINDOW - (now - oldest_ts)) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: at most {_ENROLL_MAX_CALLS} "
                    f"batch-enroll calls per {_ENROLL_RATE_WINDOW}s. "
                    f"Retry after {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )
        _enroll_rate_calls[user_id].append((now, incoming_count))


class BatchEnrollRequest(BaseModel):
    player_ids: List[int]


class BatchEnrollResponse(BaseModel):
    success: bool
    enrolled_count: int
    total_players: int
    failed_players: List[int]
    message: str


@router.post("/{tournament_id}/admin/batch-enroll", response_model=BatchEnrollResponse)
def admin_batch_enroll_players(
    tournament_id: int,
    request: BatchEnrollRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Admin-only endpoint to batch enroll multiple players in a tournament

    **Authorization:** Admin role only

    **Use Case:** Testing, tournament setup, admin-managed tournaments

    **Business Rules:**
    1. Admin can enroll players regardless of tournament status
    2. Auto-creates enrollments with APPROVED status
    3. Skips credit deduction (admin privilege)
    4. Auto-assigns age_category = 'PRO' for testing
    5. Requires players to have LFA_FOOTBALL_PLAYER license

    **Returns:**
    - Total enrolled count
    - List of failed player IDs
    """
    # 1. Verify admin role
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can batch enroll players"
        )

    # 1b. Soft rate guard
    _check_enroll_rate_limit(current_user.id, len(request.player_ids))

    # 2. Verify tournament exists
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tournament {tournament_id} not found"
        )

    logger.info(f"🔧 ADMIN BATCH ENROLL - Tournament: {tournament_id}, Players: {len(request.player_ids)}")

    enrolled_count = 0
    failed_players = []

    for player_id in request.player_ids:
        try:
            # 3. Verify player exists and is a student
            player = db.query(User).filter(
                User.id == player_id,
                User.role == UserRole.STUDENT
            ).first()

            if not player:
                logger.warning(f"⚠️ Player {player_id} not found or not a student")
                failed_players.append(player_id)
                continue

            # 4. Get player's LFA_FOOTBALL_PLAYER license
            license = db.query(UserLicense).filter(
                UserLicense.user_id == player_id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER"
            ).first()

            if not license:
                logger.warning(f"⚠️ Player {player_id} has no LFA_FOOTBALL_PLAYER license")
                failed_players.append(player_id)
                continue

            # 5. Check if already enrolled
            existing = db.query(SemesterEnrollment).filter(
                SemesterEnrollment.user_id == player_id,
                SemesterEnrollment.semester_id == tournament_id,
                SemesterEnrollment.is_active == True
            ).first()

            if existing:
                logger.info(f"✓ Player {player_id} already enrolled")
                enrolled_count += 1
                continue

            # 5b. Respect max_players capacity if set on TournamentConfiguration
            max_players = tournament.max_players  # proxied from TournamentConfiguration
            if max_players is not None:
                current_count = db.query(SemesterEnrollment).filter(
                    SemesterEnrollment.semester_id == tournament_id,
                    SemesterEnrollment.is_active == True,
                    SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
                ).count()
                if current_count >= max_players:
                    logger.warning(
                        f"⚠️ Tournament {tournament_id} is full ({current_count}/{max_players}); "
                        f"skipping player {player_id}"
                    )
                    failed_players.append(player_id)
                    continue

            # 6. Create enrollment (admin privilege - auto-approved, no credit deduction)
            enrollment = SemesterEnrollment(
                user_id=player_id,
                semester_id=tournament_id,
                user_license_id=license.id,
                age_category="PRO",  # Default for testing
                age_category_overridden=False,  # Not overridden (admin default)
                request_status=EnrollmentStatus.APPROVED,
                approved_at=datetime.utcnow(),
                approved_by=current_user.id,  # Admin user
                payment_verified=True,  # Admin bypass
                is_active=True,
                enrolled_at=datetime.utcnow(),
                requested_at=datetime.utcnow()
            )

            db.add(enrollment)
            enrolled_count += 1
            logger.info(f"✅ Player {player_id} enrolled successfully")

        except Exception as e:
            logger.error(f"❌ Failed to enroll player {player_id}: {str(e)}")
            failed_players.append(player_id)
            db.rollback()
            continue

    # 7. Commit all enrollments
    try:
        db.commit()
        logger.info(f"✅ BATCH ENROLL COMPLETE: {enrolled_count}/{len(request.player_ids)} players enrolled")
    except Exception as e:
        db.rollback()
        logger.error(f"❌ BATCH ENROLL FAILED: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to commit enrollments: {str(e)}"
        )

    return BatchEnrollResponse(
        success=enrolled_count == len(request.player_ids),
        enrolled_count=enrolled_count,
        total_players=len(request.player_ids),
        failed_players=failed_players,
        message=f"Successfully enrolled {enrolled_count}/{len(request.player_ids)} players"
    )
