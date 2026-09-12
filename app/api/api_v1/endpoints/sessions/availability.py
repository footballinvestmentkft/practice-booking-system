"""
Session Availability Bulk Query API

Provides efficient batch availability queries for session lists.
Eliminates N separate API calls by batching session availability data.

Performance target: p95 < 100ms for 20 sessions
"""
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case

from app.database import get_db
from app.models.session import Session as SessionModel
from app.models.booking import Booking, BookingStatus
from app.dependencies import get_current_user
from app.models.specialization import SpecializationType
from app.models.user import User
from app.schemas.booking import PlayerSessionAvailability
from app.services.player_participation_service import player_session_availability

router = APIRouter()


@router.get("/player/available", response_model=List[PlayerSessionAvailability])
def get_player_available_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[PlayerSessionAvailability]:
    """List sessions that pass the same canonical policy used by booking commands."""
    sessions = db.query(SessionModel).filter(
        SessionModel.target_specialization == SpecializationType.LFA_FOOTBALL_PLAYER,
        SessionModel.session_status == "scheduled",
    ).order_by(SessionModel.date_start.asc()).limit(100).all()
    result = []
    for session in sessions:
        availability = player_session_availability(
            db, player=current_user, session=session
        )
        if not availability.get("eligible"):
            continue
        result.append(PlayerSessionAvailability(
            session_id=session.id,
            title=session.title,
            date_start=session.date_start,
            date_end=session.date_end,
            category=availability["category"],
            capacity=availability["capacity"],
            confirmed=availability["confirmed"],
            available=availability["available"],
            waitlisted=availability["waitlisted"],
            booking_id=availability["booking_id"],
            participation_status=availability["participation_status"],
        ))
    return result


@router.get("/availability", response_model=Dict[int, Dict[str, Any]])
def get_sessions_availability(
    session_ids: str = Query(
        ...,
        description="Comma-separated session IDs (e.g., '1,2,3')",
        example="1,2,3"
    ),
    db: Session = Depends(get_db)
) -> Dict[int, Dict[str, Any]]:
    """
    Get availability data for multiple sessions in a single request.

    **Use case:** Display session list with real-time availability

    **Performance:**
    - Batch query: 1 DB query instead of N separate calls
    - Target: p95 < 100ms for up to 20 sessions

    **Returns:**
    ```json
    {
        "1": {
            "capacity": 20,
            "booked": 15,
            "available": 5,
            "waitlist_count": 3,
            "status": "available"
        },
        "2": {
            "capacity": 20,
            "booked": 20,
            "available": 0,
            "waitlist_count": 5,
            "status": "full"
        }
    }
    ```

    **Status values:**
    - `available`: Slots available
    - `full`: At capacity (but can join waitlist)
    - `waitlist_only`: No capacity, waitlist available
    """
    # Parse session IDs
    try:
        ids = [int(id.strip()) for id in session_ids.split(',')]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session IDs format")

    # Limit to 50 sessions per request (prevent abuse)
    if len(ids) > 50:
        raise HTTPException(
            status_code=400,
            detail="Maximum 50 sessions per request"
        )

    # Fetch sessions with capacity data
    sessions = db.query(SessionModel).filter(
        SessionModel.id.in_(ids)
    ).all()

    # Build session capacity map
    session_map = {
        session.id: {
            "capacity": session.capacity or 0,
            "session_id": session.id
        }
        for session in sessions
    }

    # Batch query booking counts
    booking_counts = db.query(
        Booking.session_id,
        func.count(Booking.id).label('total_bookings'),
        func.sum(
            case(
                (Booking.status == BookingStatus.CONFIRMED, 1),
                else_=0
            )
        ).label('confirmed_count'),
        func.sum(
            case(
                (Booking.status == BookingStatus.WAITLISTED, 1),
                else_=0
            )
        ).label('waitlist_count')
    ).filter(
        and_(
            Booking.session_id.in_(ids),
            Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.WAITLISTED])
        )
    ).group_by(Booking.session_id).all()

    # Build result map
    result = {}

    # Initialize all sessions with zero bookings
    for session_id, session_data in session_map.items():
        capacity = session_data["capacity"]
        result[session_id] = {
            "capacity": capacity,
            "booked": 0,
            "available": capacity,
            "waitlist_count": 0,
            "status": "available"
        }

    # Update with actual booking data
    for row in booking_counts:
        session_id = row.session_id
        confirmed = int(row.confirmed_count or 0)
        waitlist = int(row.waitlist_count or 0)
        capacity = session_map[session_id]["capacity"]

        available = max(0, capacity - confirmed)

        # Determine status
        if available > 0:
            status = "available"
        elif waitlist > 0:
            status = "waitlist_only"
        else:
            status = "full"

        result[session_id] = {
            "capacity": capacity,
            "booked": confirmed,
            "available": available,
            "waitlist_count": waitlist,
            "status": status
        }

    return result
