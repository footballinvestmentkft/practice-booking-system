"""
Tournament web routes — cookie auth HTML frontend
Mirrors Streamlit Tournament_Monitor / Tournament_Manager flow.

Student routes:
    GET  /tournaments              — browse ENROLLMENT_OPEN tournaments
    POST /tournaments/{id}/enroll  — enroll (auto-approved, deducts credits)
    POST /tournaments/{id}/unenroll — withdraw (50 % refund)

Instructor routes:
    GET  /instructor/tournaments   — view assigned tournaments + participants

Admin routes:
    GET  /admin/tournaments                — all tournaments list + create form
    POST /admin/tournaments                — create new tournament
    POST /admin/tournaments/{id}/start     — ENROLLMENT_CLOSED → IN_PROGRESS
    POST /admin/tournaments/{id}/cancel    — any → CANCELLED
    POST /admin/tournaments/{id}/delete    — permanent delete
    POST /admin/tournaments/{id}/rollback  — IN_PROGRESS → ENROLLMENT_CLOSED (stuck recovery)
"""
from datetime import datetime, date
from pathlib import Path
from typing import Optional
import uuid

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, or_, update as sql_update
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web, get_current_admin_user_hybrid, get_current_admin_or_instructor_user_hybrid
from ....models.booking import Booking, BookingStatus
from ....models.campus import Campus
from ....models.credit_transaction import CreditTransaction
from ....models.game_preset import GamePreset
from ....models.license import UserLicense
from ....models.location import Location
from ....models.semester import Semester, SemesterStatus, SemesterCategory
from ....models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ....models.session import Session as SessionModel, EventCategory
from ....models.tournament_ranking import TournamentRanking
from ....models.team import Team, TeamMember, TournamentTeamEnrollment, TournamentPlayerCheckin
from ....models.club import Club
from ....models.instructor_assignment import (
    InstructorAssignment,
    InstructorAssignmentRequest,
    InstructorAvailabilityWindow,
    AssignmentRequestStatus,
    LocationMasterInstructor,
    MasterOfferStatus,
)
from ....models.tournament_type import TournamentType
from ....models.tournament_configuration import TournamentConfiguration
from ....models.tournament_instructor_slot import TournamentInstructorSlot, SlotRole, SlotStatus
from ....models.user import User, UserRole
from ....services.tournament import team_service as _team_service
import app.services.tournament.instructor_planning_service as _ip_service
import app.services.tournament.attendance_service as _att_service
import app.services.tournament.enrollment_service as _enroll_service
from ....services.age_category_service import (
    calculate_age_at_season_start,
    get_automatic_age_category,
    get_current_season_year,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_player_age_category(user: User) -> str:
    """Derive AMATEUR/PRE/YOUTH/PRO age category from user DOB. Defaults to AMATEUR."""
    if not user.date_of_birth:
        return "AMATEUR"
    season_year = get_current_season_year()
    age_at = calculate_age_at_season_start(user.date_of_birth, season_year)
    return get_automatic_age_category(age_at) or "AMATEUR"


def _admin_only(user: User):
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")


def _publish_instructor_change(
    tournament_id: int,
    slot: TournamentInstructorSlot,
    db: Session,
) -> None:
    """Best-effort WS broadcast of instructor status change."""
    try:
        from app.core.redis_pubsub import publish_tournament_update
        instructor_name = slot.instructor.name if slot.instructor else f"User #{slot.instructor_id}"
        absent_field_slots = db.query(TournamentInstructorSlot).filter(
            TournamentInstructorSlot.semester_id == slot.semester_id,
            TournamentInstructorSlot.role == SlotRole.FIELD.value,
            TournamentInstructorSlot.status == SlotStatus.ABSENT.value,
        ).count()
        publish_tournament_update(tournament_id, {
            "type":               "instructor_status_change",
            "slot_id":            slot.id,
            "instructor_name":    instructor_name,
            "role":               slot.role,
            "pitch_id":           slot.pitch_id,
            "new_status":         slot.status,
            "fallback_available": absent_field_slots > 0,
        })
    except Exception:
        pass  # Redis down — silent fail


# ── Aggregate all sub-module routers ──────────────────────────────────────────

from . import browse, camps, instructor, lifecycle, edit, instructors_admin, teams, players, instructor_planning, attendance

router = APIRouter()
for _mod in [browse, camps, instructor, lifecycle, edit, instructors_admin, teams, players, instructor_planning, attendance]:
    router.include_router(_mod.router)
