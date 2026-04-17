"""
MiniSeasonSessionGenerator
===========================
Generates weekly training sessions for MINI_SEASON and ACADEMY_SEASON semesters.

Design decisions (Phase 2 approved plan, 2026-04-17):
  D-B  Pitch conflict: hard-block by default; skip_conflicts=True escape hatch.
  D-C  All generated sessions use semester.master_instructor_id.
  D-D  Campus/pitch priority: config.pitch_id → config.campus_id → semester.campus_id → NULL.
  D-E  sessions_per_week ∈ {1, 2}; 2nd session offset = duration_minutes + 15 min.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.semester import Semester
from ...models.semester_schedule_config import SemesterScheduleConfig
from ...models.session import Session as SessionModel, EventCategory, SessionParticipantType, SessionType
from ...models.pitch import Pitch
from ...models.attendance import Attendance


@dataclass
class ConflictDetail:
    date: str
    pitch_id: Optional[int]
    conflicting_session_ids: list[int]


@dataclass
class GenerationResult:
    sessions_created: int
    sessions_skipped: int
    conflict_details: list[ConflictDetail]
    session_ids: list[int]
    first_session_date: Optional[datetime]
    last_session_date: Optional[datetime]


class PitchConflictError(Exception):
    def __init__(self, detail: ConflictDetail):
        self.detail = detail


class MiniSeasonSessionGenerator:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        semester: Semester,
        config: SemesterScheduleConfig,
        skip_conflicts: bool = False,
    ) -> GenerationResult:
        """
        Generate weekly sessions for the semester.

        Raises PitchConflictError (not caught here — caller decides commit/rollback).
        """
        # 1. Resolve campus + pitch (D-D priority chain)
        campus_id = config.campus_id or semester.campus_id
        pitch_id = (
            config.pitch_id
            or (self._resolve_pitch(campus_id) if campus_id else None)
        )

        # 2. Find first occurrence of target weekday >= semester.start_date
        days_ahead = (config.day_of_week - semester.start_date.weekday()) % 7
        current_date = semester.start_date + timedelta(days=days_ahead)

        sessions_created: list[SessionModel] = []
        sessions_skipped: list[ConflictDetail] = []
        week_num = 1

        # 3. Weekly loop
        while current_date <= semester.end_date:
            for offset_idx in range(config.sessions_per_week):
                minute_offset = offset_idx * (config.duration_minutes + 15)
                date_start = (
                    datetime.combine(current_date, config.start_time)
                    + timedelta(minutes=minute_offset)
                )
                date_end = date_start + timedelta(minutes=config.duration_minutes)

                # 4. Pitch conflict check
                conflicts = self._check_pitch_conflicts(pitch_id, date_start, date_end)
                if conflicts:
                    detail = ConflictDetail(
                        date=current_date.isoformat(),
                        pitch_id=pitch_id,
                        conflicting_session_ids=[c.id for c in conflicts],
                    )
                    if skip_conflicts:
                        sessions_skipped.append(detail)
                        continue
                    else:
                        raise PitchConflictError(detail=detail)

                suffix = f" S{offset_idx + 1}" if config.sessions_per_week > 1 else ""
                session = SessionModel(
                    title=f"{semester.name} — Week {week_num}{suffix}",
                    semester_id=semester.id,
                    campus_id=campus_id,
                    pitch_id=pitch_id,
                    instructor_id=semester.master_instructor_id,
                    date_start=date_start,
                    date_end=date_end,
                    event_category=EventCategory.TRAINING,
                    session_participant_type=SessionParticipantType.INDIVIDUAL,
                    session_type=SessionType.on_site,
                    session_status="scheduled",
                    auto_generated=True,
                    target_specialization=semester.specialization_type,
                    base_xp=75,
                    credit_cost=0,
                    rounds_data={},
                )
                self.db.add(session)
                sessions_created.append(session)

            current_date += timedelta(weeks=1)
            week_num += 1

        self.db.flush()

        # 5. Update config state
        config.sessions_generated = True
        config.sessions_generated_at = datetime.utcnow()
        config.sessions_count = len(sessions_created)
        self.db.flush()

        return GenerationResult(
            sessions_created=len(sessions_created),
            sessions_skipped=len(sessions_skipped),
            conflict_details=sessions_skipped,
            session_ids=[s.id for s in sessions_created],
            first_session_date=sessions_created[0].date_start if sessions_created else None,
            last_session_date=sessions_created[-1].date_start if sessions_created else None,
        )

    def delete_generated_sessions(self, semester_id: int) -> int:
        """
        Delete all auto-generated sessions for a semester.

        Raises HTTP 409 if any session has Attendance records.
        Returns the count of deleted sessions.
        """
        attended = (
            self.db.query(Attendance)
            .join(SessionModel, Attendance.session_id == SessionModel.id)
            .filter(
                SessionModel.semester_id == semester_id,
                SessionModel.auto_generated == True,
            )
            .count()
        )
        if attended > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete: {attended} attendance record(s) exist for generated sessions.",
            )

        count = (
            self.db.query(SessionModel)
            .filter(
                SessionModel.semester_id == semester_id,
                SessionModel.auto_generated == True,
            )
            .delete(synchronize_session=False)
        )

        # Reset config state
        config = (
            self.db.query(SemesterScheduleConfig)
            .filter_by(semester_id=semester_id)
            .first()
        )
        if config:
            config.sessions_generated = False
            config.sessions_generated_at = None
            config.sessions_count = None

        return count

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_pitch_conflicts(
        self,
        pitch_id: Optional[int],
        date_start: datetime,
        date_end: datetime,
    ) -> list[SessionModel]:
        if pitch_id is None:
            return []
        return (
            self.db.query(SessionModel)
            .filter(
                SessionModel.pitch_id == pitch_id,
                SessionModel.date_start < date_end,
                SessionModel.date_end > date_start,
                SessionModel.session_status != "cancelled",
            )
            .all()
        )

    def _resolve_pitch(self, campus_id: int) -> Optional[int]:
        """Return id of the first active pitch in campus, ordered by pitch_number."""
        pitch = (
            self.db.query(Pitch)
            .filter(Pitch.campus_id == campus_id, Pitch.is_active == True)
            .order_by(Pitch.pitch_number.asc())
            .first()
        )
        return pitch.id if pitch else None
