"""
Tournament Session Generation API Endpoint

CRITICAL: Sessions are generated ONLY after enrollment closes (tournament_status = IN_PROGRESS)

Provides:
1. Preview of session structure (before generation)
2. Actual session generation (creates sessions in DB)
"""
import uuid
import logging
import threading
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.database import get_db, SessionLocal
from app.dependencies import get_current_admin_user, get_current_admin_or_instructor_user, get_current_admin_or_instructor_user_hybrid
from app.models.user import User, UserRole
from app.models.tournament_type import TournamentType
from app.models.session import Session as SessionModel, EventCategory
from app.repositories import TournamentRepository
from app.services.tournament_session_generator import TournamentSessionGenerator

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-process background task registry (fallback when Celery/Redis unavailable)
# Keyed by task_id (UUID string).  Thread-safe via a lock.
# Status values: "pending" | "running" | "done" | "error"
# ---------------------------------------------------------------------------
_task_registry: Dict[str, Dict[str, Any]] = {}
_registry_lock = threading.Lock()


BACKGROUND_GENERATION_THRESHOLD = 128  # Player count above which async is used


def _is_celery_available() -> bool:
    """
    Test whether the Celery broker (Redis) is reachable.
    Returns False if celery/redis packages are missing or Redis is not running.
    """
    try:
        from app.celery_app import celery_app
        # Ping the broker with a 1-second timeout
        celery_app.control.ping(timeout=1)
        return True
    except Exception:
        return False


def _run_generation_in_background(
    task_id: str,
    tournament_id: int,
    parallel_fields: int,
    session_duration: int,
    break_duration: int,
    number_of_rounds: int,
    campus_overrides_raw: Optional[Dict[str, Any]],
    campus_ids: Optional[list] = None,
    number_of_legs: int = 1,
    track_home_away: bool = False,
    skip_instructor_check: bool = False,
) -> None:
    """
    Worker function executed in a daemon thread.
    Opens its own DB session (independent of the request session).
    """
    with _registry_lock:
        _task_registry[task_id]["status"] = "running"

    db: Session = SessionLocal()
    try:
        # Persist campus_schedule_overrides if provided
        if campus_overrides_raw:
            from app.models.tournament_configuration import TournamentConfiguration
            config = db.query(TournamentConfiguration).filter(
                TournamentConfiguration.semester_id == tournament_id
            ).first()
            if config:
                config.campus_schedule_overrides = campus_overrides_raw
                db.flush()

        generator = TournamentSessionGenerator(db)
        success, message, sessions_created = generator.generate_sessions(
            tournament_id=tournament_id,
            parallel_fields=parallel_fields,
            session_duration_minutes=session_duration,
            break_minutes=break_duration,
            number_of_rounds=number_of_rounds,
            campus_ids=campus_ids,
            number_of_legs=number_of_legs,
            track_home_away=track_home_away,
            skip_instructor_check=skip_instructor_check,
        )

        with _registry_lock:
            _task_registry[task_id].update({
                "status": "done" if success else "error",
                "message": message,
                "sessions_count": len(sessions_created) if success else 0,
            })
    except Exception as exc:
        with _registry_lock:
            _task_registry[task_id].update({
                "status": "error",
                "message": str(exc),
                "sessions_count": 0,
            })
    finally:
        db.close()


class CampusScheduleConfig(BaseModel):
    """Per-campus schedule overrides for multi-venue tournaments"""
    match_duration_minutes: Optional[int] = Field(default=None, ge=1, le=180, description="Override match duration for this campus")
    break_duration_minutes: Optional[int] = Field(default=None, ge=0, le=60, description="Override break duration for this campus")
    parallel_fields: Optional[int] = Field(default=None, ge=1, le=20, description="Override parallel fields for this campus")


class SessionGenerationRequest(BaseModel):
    """Request body for session generation"""
    parallel_fields: int = Field(default=1, ge=1, le=10, description="Number of fields available for parallel matches")
    session_duration_minutes: int = Field(default=90, ge=1, le=180, description="Duration of each session in minutes (business allows 1-5 min matches)")
    break_minutes: int = Field(default=15, ge=0, le=60, description="Break time between sessions in minutes")
    number_of_rounds: int = Field(default=1, ge=1, le=10, description="Number of rounds for INDIVIDUAL_RANKING tournaments (e.g., 3 attempts for 100m sprint)")
    campus_ids: Optional[List[int]] = Field(
        default=None,
        description=(
            "Explicit list of campus IDs for multi-venue group_knockout tournaments. "
            "Admin: multiple campuses allowed. "
            "Instructor: exactly 1 campus allowed — request is rejected if more than 1 ID is provided."
        )
    )
    campus_schedule_overrides: Optional[Dict[str, CampusScheduleConfig]] = Field(
        default=None,
        description=(
            "Per-campus schedule overrides keyed by campus_id (as string). "
            "Each entry can override match_duration_minutes, break_duration_minutes, and parallel_fields. "
            "Example: {\"42\": {\"match_duration_minutes\": 60, \"parallel_fields\": 3}}"
        )
    )
    number_of_legs: int = Field(
        default=1,
        ge=1,
        description=(
            "Number of legs for HEAD_TO_HEAD round robin. "
            "1 = single round (default), 2 = home & away, 3 = triple round, etc. "
            "Ignored for INDIVIDUAL_RANKING tournaments."
        ),
    )
    track_home_away: bool = Field(
        default=False,
        description=(
            "If True, even legs reverse each pairing so the home team becomes away in leg 2. "
            "Only meaningful when number_of_legs >= 2."
        ),
    )


class SessionPreview(BaseModel):
    """Preview of a single session"""
    title: str
    description: str
    date_start: str
    date_end: str
    game_type: str
    tournament_phase: str
    tournament_round: int
    tournament_match_number: int


class SessionGenerationResponse(BaseModel):
    """Response for session generation"""
    success: bool
    message: str
    tournament_id: int
    tournament_name: str
    sessions_generated_count: int
    sessions: Optional[List[Dict[str, Any]]] = None


@router.get("/{tournament_id}/preview-sessions", response_model=Dict[str, Any])
def preview_tournament_sessions(
    tournament_id: int,
    parallel_fields: int = 1,
    session_duration_minutes: int = 90,
    break_minutes: int = 15,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Dict[str, Any]:
    """
    Preview tournament session structure WITHOUT creating sessions in database

    **Authorization:** Admin only

    **Parameters:**
    - `tournament_id`: Tournament (Semester) ID
    - `parallel_fields`: Number of fields for parallel matches (default: 1)
    - `session_duration_minutes`: Session duration (default: 90)
    - `break_minutes`: Break between sessions (default: 15)

    **Returns:**
    - Tournament info
    - Tournament type config
    - Player count (enrolled)
    - Estimated sessions (preview)

    **Response Example:**
    ```json
    {
        "tournament_id": 121,
        "tournament_name": "🇭🇺 HU - Winter Cup - Budapest",
        "tournament_type": "League (Round Robin)",
        "player_count": 8,
        "parallel_fields": 1,
        "estimated_sessions": [
            {
                "title": "Winter Cup - Round 1 - Match 1",
                "date_start": "2026-01-20T09:00:00",
                "date_end": "2026-01-20T10:30:00",
                "game_type": "Round 1",
                "tournament_phase": "League",
                "tournament_round": 1,
                "tournament_match_number": 1
            }
        ],
        "total_matches": 28,
        "total_rounds": 7,
        "estimated_duration_minutes": 1260
    }
    ```
    """
    # Fetch tournament
    tournament = TournamentRepository(db).get_or_404(tournament_id)

    # Check if tournament has tournament_type_id
    if not tournament.tournament_type_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tournament does not have a tournament type configured. Cannot generate preview."
        )

    # Fetch tournament type
    tournament_type = db.query(TournamentType).filter(
        TournamentType.id == tournament.tournament_type_id
    ).first()

    if not tournament_type:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tournament type configuration not found"
        )

    # Get enrolled player count
    from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus

    player_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active == True,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
    ).count()

    if player_count < tournament_type.min_players:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not enough players enrolled. Need at least {tournament_type.min_players}, have {player_count}"
        )

    # Validate player count
    is_valid, error_msg = tournament_type.validate_player_count(player_count)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg
        )

    # Use semester's saved schedule configuration if available, otherwise use query parameters
    session_duration = tournament.match_duration_minutes if tournament.match_duration_minutes else session_duration_minutes
    break_duration = tournament.break_duration_minutes if tournament.break_duration_minutes else break_minutes

    # Generate preview (call generator service in DRY_RUN mode)
    generator = TournamentSessionGenerator(db)

    # Generate session structure (same logic as actual generation, but don't commit)
    if tournament_type.code == "league":
        sessions = generator._generate_league_sessions(
            tournament, tournament_type, player_count, parallel_fields,
            session_duration, break_duration
        )
    elif tournament_type.code == "knockout":
        sessions = generator._generate_knockout_sessions(
            tournament, tournament_type, player_count, parallel_fields,
            session_duration, break_duration
        )
    elif tournament_type.code == "group_knockout":
        sessions = generator._generate_group_knockout_sessions(
            tournament, tournament_type, player_count, parallel_fields,
            session_duration, break_duration
        )
    elif tournament_type.code == "swiss":
        sessions = generator._generate_swiss_sessions(
            tournament, tournament_type, player_count, parallel_fields,
            session_duration, break_duration
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown tournament type: {tournament_type.code}"
        )

    # Estimate duration
    estimation = tournament_type.estimate_duration(player_count, parallel_fields)

    return {
        "tournament_id": tournament_id,
        "tournament_name": tournament.name,
        "tournament_type": tournament_type.display_name,
        "player_count": player_count,
        "parallel_fields": parallel_fields,
        "estimated_sessions": sessions,
        "total_matches": estimation['total_matches'],
        "total_rounds": estimation['total_rounds'],
        "estimated_duration_minutes": estimation['estimated_duration_minutes']
    }


def _assert_campus_scope(
    current_user: User,
    campus_ids: Optional[List[int]],
    campus_schedule_overrides: Optional[dict],
    db: Optional["Session"] = None,
) -> None:
    """
    Backend guard — enforces campus-scope ownership by role.

    Rules:
      ADMIN   → multi-campus allowed (no restriction)
      INSTRUCTOR → single-campus only:
                   • campus_ids must not exceed 1 entry
                   • campus_schedule_overrides must not exceed 1 key

    Raises HTTP 403 if the instructor tries to operate across multiple campuses.
    This is a defence-in-depth measure that enforces the architectural rule at
    the API boundary, independent of any UI-level restrictions.

    Security audit: every 403 emits a WARNING log AND a SECURITY-level
    system_event record (rate-limited to 1 per 10 min per user+event_type).
    """
    if current_user.role == UserRole.ADMIN:
        return  # Admins have unrestricted campus access

    # Instructor scope: single campus only
    if campus_ids and len(campus_ids) > 1:
        logger.warning(
            "SECURITY: instructor multi-campus attempt blocked — "
            "user_id=%s email=%s role=%s campus_ids=%s",
            current_user.id, current_user.email, current_user.role, campus_ids,
        )
        if db is not None:
            try:
                from app.services.system_event_service import SystemEventService
                from app.models.system_event import SystemEventLevel, SystemEventType
                SystemEventService(db).emit(
                    SystemEventLevel.SECURITY,
                    SystemEventType.MULTI_CAMPUS_BLOCKED,
                    user_id=current_user.id,
                    role=str(current_user.role),
                    payload={
                        "email": current_user.email,
                        "campus_ids": campus_ids,
                        "campus_count": len(campus_ids),
                    },
                )
            except Exception:
                logger.warning("system_event emit failed (non-fatal)", exc_info=True)

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Instructors may only generate sessions for a single campus. "
                f"Received {len(campus_ids)} campus IDs — remove the extra entries."
            ),
        )

    if campus_schedule_overrides and len(campus_schedule_overrides) > 1:
        logger.warning(
            "SECURITY: instructor multi-campus override attempt blocked — "
            "user_id=%s email=%s role=%s override_keys=%s",
            current_user.id, current_user.email, current_user.role,
            list(campus_schedule_overrides.keys()),
        )
        if db is not None:
            try:
                from app.services.system_event_service import SystemEventService
                from app.models.system_event import SystemEventLevel, SystemEventType
                SystemEventService(db).emit(
                    SystemEventLevel.SECURITY,
                    SystemEventType.MULTI_CAMPUS_OVERRIDE_BLOCKED,
                    user_id=current_user.id,
                    role=str(current_user.role),
                    payload={
                        "email": current_user.email,
                        "override_keys": list(campus_schedule_overrides.keys()),
                        "key_count": len(campus_schedule_overrides),
                    },
                )
            except Exception:
                logger.warning("system_event emit failed (non-fatal)", exc_info=True)

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Instructors may only configure schedule overrides for a single campus. "
                f"Received {len(campus_schedule_overrides)} campus entries in campus_schedule_overrides."
            ),
        )


@router.post("/{tournament_id}/generate-sessions", response_model=Dict[str, Any])
def generate_tournament_sessions(
    tournament_id: int,
    request: SessionGenerationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user_hybrid)
) -> Dict[str, Any]:
    """
    Generate tournament sessions based on tournament type and enrolled player count.

    **Auto-scaling behaviour:**
    - < 128 players: synchronous generation, returns full result immediately
    - >= 128 players: background generation, returns `task_id` for polling via
      `GET /tournaments/{tournament_id}/generation-status/{task_id}`

    **CRITICAL CONSTRAINT:** Sessions can ONLY be generated when tournament_status = "IN_PROGRESS"
    (i.e., AFTER enrollment closes)

    **Authorization:** Admin only

    **Validations:**
    1. Tournament exists and has tournament_type_id
    2. Tournament status is IN_PROGRESS (enrollment closed)
    3. Sessions not already generated (sessions_generated = False)
    4. Sufficient player count (>= min_players for tournament type)
    5. Player count meets tournament type constraints (e.g., power-of-2 for knockout)
    """
    from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus

    # ── Campus-scope guard (role-based enforcement) ───────────────────────────
    # Must be called BEFORE any DB writes so a 403 never leaves partial state.
    _assert_campus_scope(current_user, request.campus_ids, request.campus_schedule_overrides, db)

    generator = TournamentSessionGenerator(db)

    # Check if can generate (includes all validations)
    can_generate, reason = generator.can_generate_sessions(tournament_id)
    if not can_generate:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=reason
        )

    # Fetch tournament to get schedule configuration
    tournament = TournamentRepository(db).get_or_404(tournament_id)

    # Use semester's saved schedule configuration if available, otherwise use request parameters
    session_duration = tournament.match_duration_minutes if tournament.match_duration_minutes else request.session_duration_minutes
    break_duration = tournament.break_duration_minutes if tournament.break_duration_minutes else request.break_minutes
    parallel_fields = tournament.parallel_fields if tournament.parallel_fields else request.parallel_fields
    number_of_rounds = tournament.number_of_rounds if tournament.number_of_rounds else request.number_of_rounds

    # campus_ids from request (None = use DB defaults / no multi-campus)
    request_campus_ids: Optional[List[int]] = request.campus_ids

    # Serialise campus overrides (Pydantic → plain dict for JSON storage)
    campus_overrides_raw: Optional[Dict[str, Any]] = None
    if request.campus_schedule_overrides is not None:
        campus_overrides_raw = {
            campus_id: cfg.model_dump(exclude_none=True)
            for campus_id, cfg in request.campus_schedule_overrides.items()
        }
        # Persist to DB synchronously so it's available in the background thread
        if tournament.tournament_config_obj:
            tournament.tournament_config_obj.campus_schedule_overrides = campus_overrides_raw
            db.flush()

    # Count enrolled players to decide sync vs background path
    player_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active == True,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
    ).count()

    if player_count >= BACKGROUND_GENERATION_THRESHOLD:
        # ── ASYNC PATH: prefer Celery, fall back to daemon thread ──
        use_celery = _is_celery_available()
        task_id: str

        if use_celery:
            # Celery path: reliable, survives worker restarts, retries on failure
            from app.tasks.tournament_tasks import generate_sessions_task
            import time as _time
            celery_result = generate_sessions_task.apply_async(
                args=[
                    tournament_id,
                    parallel_fields,
                    session_duration,
                    break_duration,
                    number_of_rounds,
                    campus_overrides_raw,
                ],
                queue="tournaments",
                headers={"dispatched_at": _time.perf_counter()},
            )
            task_id = celery_result.id
            backend = "celery"
            logger.info(f"[async] Celery task submitted task_id={task_id} tournament_id={tournament_id}")
        else:
            # Thread fallback: no Redis required, in-process registry
            task_id = str(uuid.uuid4())
            with _registry_lock:
                _task_registry[task_id] = {
                    "status": "pending",
                    "tournament_id": tournament_id,
                    "player_count": player_count,
                    "message": None,
                    "sessions_count": 0,
                }
            thread = threading.Thread(
                target=_run_generation_in_background,
                args=(
                    task_id,
                    tournament_id,
                    parallel_fields,
                    session_duration,
                    break_duration,
                    number_of_rounds,
                    campus_overrides_raw,
                    request_campus_ids,
                    request.number_of_legs,
                    request.track_home_away,
                ),
                daemon=True,
            )
            thread.start()
            backend = "thread"
            logger.info(f"[async] Thread task submitted task_id={task_id} tournament_id={tournament_id} (Redis unavailable)")

        return {
            "success": True,
            "async": True,
            "async_backend": backend,
            "task_id": task_id,
            "tournament_id": tournament_id,
            "tournament_name": tournament.name,
            "player_count": player_count,
            "message": (
                f"Generation started in background for {player_count} players "
                f"via {backend}. "
                f"Poll /tournaments/{tournament_id}/generation-status/{task_id} for progress."
            ),
        }

    # ── SYNC PATH: small tournament, generate immediately ──
    success, message, sessions_created = generator.generate_sessions(
        tournament_id=tournament_id,
        parallel_fields=parallel_fields,
        session_duration_minutes=session_duration,
        break_minutes=break_duration,
        number_of_rounds=number_of_rounds,
        campus_ids=request_campus_ids,
        number_of_legs=request.number_of_legs,
        track_home_away=request.track_home_away,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=message
        )

    # Refresh tournament name after generation (sessions_generated flag may change)
    tournament = TournamentRepository(db).get_or_404(tournament_id)

    return {
        "success": True,
        "async": False,
        "tournament_id": tournament_id,
        "tournament_name": tournament.name,
        "sessions_generated_count": len(sessions_created),
        "message": message,
        "sessions": sessions_created,
    }


@router.get("/{tournament_id}/generation-status/{task_id}", response_model=Dict[str, Any])
def get_generation_status(
    tournament_id: int,
    task_id: str,
    current_user: User = Depends(get_current_admin_user),
) -> Dict[str, Any]:
    """
    Poll the status of a background session-generation task.

    Works for both Celery tasks (when Redis is running) and in-process thread tasks.

    **Authorization:** Admin only

    Returns one of:
    - `{"status": "pending"}` — queued, not yet started
    - `{"status": "running"}` — generation in progress
    - `{"status": "done", "sessions_count": N, "message": "..."}` — completed successfully
    - `{"status": "error", "message": "..."}` — generation failed
    - `{"status": "retrying"}` — Celery is retrying after a transient error

    The task_id is returned by `POST /tournaments/{id}/generate-sessions` when
    player_count >= 128 and async generation is used.
    """
    # 1. Try Celery result backend first (UUID matches a Celery task)
    try:
        from celery.result import AsyncResult
        from app.celery_app import celery_app
        ar = AsyncResult(task_id, app=celery_app)
        # Celery state: PENDING | STARTED | RETRY | SUCCESS | FAILURE
        if ar.state != "PENDING" or ar.result is not None:
            # Map Celery states to our unified vocabulary
            state_map = {
                "PENDING": "pending",
                "STARTED": "running",
                "RETRY": "retrying",
                "SUCCESS": "done",
                "FAILURE": "error",
            }
            unified_status = state_map.get(ar.state, ar.state.lower())
            response: Dict[str, Any] = {
                "status": unified_status,
                "task_id": task_id,
                "tournament_id": tournament_id,
                "backend": "celery",
            }
            if ar.state == "SUCCESS" and isinstance(ar.result, dict):
                response.update({
                    "sessions_count":         ar.result.get("sessions_count", 0),
                    "message":                ar.result.get("message", ""),
                    "generation_duration_ms": ar.result.get("generation_duration_ms"),
                    "db_write_time_ms":       ar.result.get("db_write_time_ms"),
                    "queue_wait_time_ms":     ar.result.get("queue_wait_time_ms"),
                })
            elif ar.state == "FAILURE":
                response["message"] = str(ar.result)
            return response
    except Exception:
        pass  # Celery/Redis not available — fall through to thread registry

    # 2. Thread-based fallback registry
    with _registry_lock:
        entry = _task_registry.get(task_id)

    if entry is None or entry.get("tournament_id") != tournament_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found for tournament {tournament_id}"
        )

    return dict(entry)


@router.get("/{tournament_id}/sessions", response_model=List[Dict[str, Any]])
def get_tournament_sessions(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user_hybrid)
) -> List[Dict[str, Any]]:
    """
    Get all sessions for a tournament

    **Authorization:** Admin or Instructor

    Returns:
        List of session dictionaries with all session details including participant names
    """
    from app.models.user import User as UserModel
    from app.models.attendance import Attendance

    # Fetch all sessions for this tournament
    sessions = db.query(SessionModel).filter(
        SessionModel.semester_id == tournament_id
    ).order_by(SessionModel.date_start).all()

    # Batch prefetch: collect all participant IDs and session IDs up front
    all_participant_ids = list({
        uid
        for s in sessions
        for uid in (s.participant_user_ids or [])
        if uid is not None
    })
    session_ids = [s.id for s in sessions]

    # Single query for all users referenced by any session
    users_by_id = {}
    if all_participant_ids:
        users_by_id = {
            u.id: u
            for u in db.query(UserModel).filter(
                UserModel.id.in_(all_participant_ids)
            ).all()
        }

    # Single query for all attendances across all sessions
    attendances_by_key = {}
    if session_ids:
        attendances_by_key = {
            (a.session_id, a.user_id): a
            for a in db.query(Attendance).filter(
                Attendance.session_id.in_(session_ids)
            ).all()
        }

    # Convert to dict format
    sessions_list = []
    for session in sessions:
        # Build participant list from prefetched data (no DB queries inside loop)
        participants = []
        if session.participant_user_ids:
            for uid in session.participant_user_ids:
                if uid is None:
                    continue
                user = users_by_id.get(uid)
                if not user:
                    continue
                attendance = attendances_by_key.get((session.id, uid))
                participants.append({
                    "id": user.id,
                    "name": user.nickname or user.name,
                    "email": user.email,
                    "attendance_status": attendance.status.value if attendance else "PENDING",
                    "is_present": attendance.status.value == "PRESENT" if attendance else False
                })

        sessions_list.append({
            "id": session.id,
            "title": session.title,
            "description": session.description,
            "date_start": session.date_start.isoformat() if session.date_start else None,
            "date_end": session.date_end.isoformat() if session.date_end else None,
            "game_type": session.game_type,
            "tournament_phase": session.tournament_phase,
            "tournament_round": session.tournament_round,
            "tournament_match_number": session.tournament_match_number,
            "location": session.location,
            "session_type": session.session_type,
            "capacity": session.capacity,
            "is_tournament_game": session.event_category == EventCategory.MATCH,
            "auto_generated": session.auto_generated,
            "match_format": session.match_format,
            "scoring_type": session.scoring_type,
            "structure_config": session.structure_config,
            "group_identifier": session.group_identifier,
            "participant_user_ids": session.participant_user_ids,
            "participant_names": [p["name"] for p in participants if p.get("name")],
            "participants": participants,  # ✅ NEW: Full participant details with names
            "game_results": session.game_results,  # ✅ FIX: Add game_results field for Step 4
            "rounds_data": session.rounds_data,
            # ROUNDS_BASED: complete only when all rounds are submitted
            "result_submitted": (
                (lambda rd: int(rd.get("completed_rounds", 0)) >= int(rd.get("total_rounds", 1)) > 0)(
                    session.rounds_data or {}
                )
                if session.scoring_type == "ROUNDS_BASED"
                else bool(session.game_results)
            ),
        })

    return sessions_list


@router.delete("/{tournament_id}/sessions", response_model=Dict[str, Any])
def delete_generated_sessions(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Dict[str, Any]:
    """
    Delete all auto-generated sessions for a tournament (RESET functionality)

    **Authorization:** Admin only

    **Use Case:** If admin needs to regenerate sessions with different parameters

    **CAUTION:** This will delete ALL auto-generated sessions for this tournament.
    Manual sessions (auto_generated = False) are NOT deleted.

    **Response Example:**
    ```json
    {
        "success": true,
        "message": "Deleted 28 auto-generated sessions",
        "deleted_count": 28
    }
    ```
    """
    from app.models.session import Session as SessionModel
    from app.models.attendance import Attendance

    tournament = TournamentRepository(db).get_or_404(tournament_id)

    # Get session IDs that will be deleted
    session_ids_to_delete = [
        s.id for s in db.query(SessionModel).filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.auto_generated == True
        ).all()
    ]

    if not session_ids_to_delete:
        return {
            "success": True,
            "message": "No auto-generated sessions to delete",
            "deleted_count": 0
        }

    # 1. Delete attendance records first (foreign key dependency)
    attendance_deleted = db.query(Attendance).filter(
        Attendance.session_id.in_(session_ids_to_delete)
    ).delete(synchronize_session=False)

    # 2. Delete only auto-generated sessions
    deleted_count = db.query(SessionModel).filter(
        SessionModel.semester_id == tournament_id,
        SessionModel.auto_generated == True
    ).delete(synchronize_session=False)

    # 3. Reset generation flags
    tournament.sessions_generated = False
    tournament.sessions_generated_at = None

    db.commit()

    return {
        "success": True,
        "message": f"Deleted {deleted_count} auto-generated sessions",
        "deleted_count": deleted_count
    }
