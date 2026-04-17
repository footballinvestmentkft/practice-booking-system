"""
Schedule Generator API
======================
POST /api/v1/semesters/{semester_id}/generate-sessions
DELETE /api/v1/semesters/{semester_id}/sessions

Admin-only endpoints for generating and deleting auto-generated sessions
for MINI_SEASON and ACADEMY_SEASON semesters.
"""
from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_admin_user
from .....models.user import User
from .....models.semester import Semester, SemesterCategory
from .....models.semester_schedule_config import SemesterScheduleConfig
from .....models.campus import Campus
from .....models.pitch import Pitch
from .....services.scheduling.mini_season_generator import (
    MiniSeasonSessionGenerator,
    PitchConflictError,
)

router = APIRouter()

_SCHEDULING_CATEGORIES = {SemesterCategory.MINI_SEASON, SemesterCategory.ACADEMY_SEASON}


# ── Request / Response schemas ────────────────────────────────────────────────

class ScheduleGenerateRequest(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6, description="0=Monday .. 6=Sunday")
    start_time: str = Field(..., description="HH:MM local start time, e.g. '17:00'")
    duration_minutes: int = Field(90, ge=30, le=240, description="Session duration in minutes")
    sessions_per_week: int = Field(1, ge=1, le=2, description="1 or 2 sessions per week")
    campus_id: Optional[int] = Field(None, description="Campus override (optional)")
    pitch_id: Optional[int] = Field(None, description="Pitch override (optional)")
    skip_conflicts: bool = Field(False, description="Skip conflicting slots instead of hard-blocking")


class ConflictDetailSchema(BaseModel):
    date: str
    pitch_id: Optional[int]
    conflicting_session_ids: list[int]


class ScheduleGenerateResponse(BaseModel):
    semester_id: int
    sessions_created: int
    sessions_skipped: int
    conflict_details: list[ConflictDetailSchema]
    first_session_date: Optional[str]
    last_session_date: Optional[str]
    message: str


class DeleteSessionsResponse(BaseModel):
    deleted_count: int
    message: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_time(time_str: str) -> dt_time:
    """Parse 'HH:MM' → datetime.time. Raises 400 on bad format."""
    try:
        parts = time_str.split(":")
        return dt_time(int(parts[0]), int(parts[1]))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid start_time format '{time_str}'. Expected HH:MM.",
        )


def _get_semester_or_404(db: Session, semester_id: int) -> Semester:
    sem = db.query(Semester).filter(Semester.id == semester_id).first()
    if not sem:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Semester not found.")
    return sem


def _validate_semester_for_scheduling(sem: Semester) -> None:
    if sem.semester_category not in _SCHEDULING_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Session generation is only available for MINI_SEASON and ACADEMY_SEASON semesters. "
                f"This semester is {sem.semester_category}."
            ),
        )
    if not sem.start_date or not sem.end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Semester must have both start_date and end_date set before generating sessions.",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/{semester_id}/generate-sessions",
    response_model=ScheduleGenerateResponse,
    tags=["semesters", "scheduling"],
)
def generate_sessions(
    semester_id: int,
    request: ScheduleGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Generate weekly training sessions for a MINI_SEASON or ACADEMY_SEASON semester.

    Validations (in order):
      1. Semester exists → 404
      2. semester_category in (MINI_SEASON, ACADEMY_SEASON) → 400
      3. start_date + end_date set → 400
      4. sessions already generated → 409
      5. campus_id belongs to semester's location → 400
      6. pitch_id belongs to resolved campus → 400
    """
    sem = _get_semester_or_404(db, semester_id)
    _validate_semester_for_scheduling(sem)

    # 4. Already generated guard
    existing_config = sem.schedule_config_obj
    if existing_config and existing_config.sessions_generated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sessions already generated for this semester. DELETE them first.",
        )

    # 5. Validate campus_id (if provided)
    campus_id = request.campus_id
    if campus_id is not None:
        campus = db.query(Campus).filter(Campus.id == campus_id).first()
        if not campus:
            raise HTTPException(status_code=400, detail=f"Campus {campus_id} not found.")
        if sem.location_id and campus.location_id != sem.location_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Campus {campus_id} does not belong to the semester's location "
                    f"(location_id={sem.location_id})."
                ),
            )

    # 6. Validate pitch_id (if provided)
    pitch_id = request.pitch_id
    if pitch_id is not None:
        pitch = db.query(Pitch).filter(Pitch.id == pitch_id).first()
        if not pitch:
            raise HTTPException(status_code=400, detail=f"Pitch {pitch_id} not found.")
        # Resolve expected campus for pitch validation
        resolved_campus_id = campus_id or sem.campus_id
        if resolved_campus_id and pitch.campus_id != resolved_campus_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Pitch {pitch_id} does not belong to campus {resolved_campus_id}."
                ),
            )

    parsed_time = _parse_time(request.start_time)

    # Upsert SemesterScheduleConfig
    config = existing_config
    if config is None:
        config = SemesterScheduleConfig(semester_id=semester_id)
        db.add(config)

    config.day_of_week = request.day_of_week
    config.start_time = parsed_time
    config.duration_minutes = request.duration_minutes
    config.sessions_per_week = request.sessions_per_week
    config.campus_id = campus_id
    config.pitch_id = pitch_id
    config.sessions_generated = False
    db.flush()

    generator = MiniSeasonSessionGenerator(db)
    try:
        result = generator.generate(sem, config, skip_conflicts=request.skip_conflicts)
    except PitchConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "pitch_conflict",
                "conflicts": [
                    {
                        "date": exc.detail.date,
                        "pitch_id": exc.detail.pitch_id,
                        "conflicting_session_ids": exc.detail.conflicting_session_ids,
                    }
                ],
            },
        )

    db.commit()

    return ScheduleGenerateResponse(
        semester_id=semester_id,
        sessions_created=result.sessions_created,
        sessions_skipped=result.sessions_skipped,
        conflict_details=[
            ConflictDetailSchema(
                date=cd.date,
                pitch_id=cd.pitch_id,
                conflicting_session_ids=cd.conflicting_session_ids,
            )
            for cd in result.conflict_details
        ],
        first_session_date=(
            result.first_session_date.isoformat() if result.first_session_date else None
        ),
        last_session_date=(
            result.last_session_date.isoformat() if result.last_session_date else None
        ),
        message=(
            f"{result.sessions_created} sessions generated"
            + (f", {result.sessions_skipped} skipped due to conflicts" if result.sessions_skipped else "")
            + "."
        ),
    )


@router.delete(
    "/{semester_id}/sessions",
    response_model=DeleteSessionsResponse,
    tags=["semesters", "scheduling"],
)
def delete_generated_sessions(
    semester_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Delete all auto-generated sessions for a semester.

    Returns 409 if any session has Attendance records.
    """
    sem = _get_semester_or_404(db, semester_id)
    _validate_semester_for_scheduling(sem)

    generator = MiniSeasonSessionGenerator(db)
    deleted = generator.delete_generated_sessions(semester_id)
    db.commit()

    return DeleteSessionsResponse(
        deleted_count=deleted,
        message=f"{deleted} auto-generated session(s) deleted.",
    )
