"""
OPS Scenario Endpoint

Admin-only endpoint for triggering operational scenarios (smoke tests, scale tests,
large-field monitor runs). Contains all simulation helpers and the run_ops_scenario
FastAPI route.

Extracted from generator.py as part of file-size refactoring (generator.py was 2475 lines).
Boundary: generator.py lines 886–2475.
"""
import logging as _logging
import json as _json
from typing import Dict, List, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.api_v1.endpoints.auth import get_current_user
from app.models.user import User, UserRole

from .schemas import OpsScenarioRequest, OpsScenarioResponse, _OPS_CONFIRM_THRESHOLD
from ._session_helpers import _get_tournament_sessions
from ._simulate_group import _simulate_tournament_results
from ._simulate_ir import _calculate_ir_rankings
from ._finalization import _finalize_tournament_with_rewards

router = APIRouter()

_ops_logger = _logging.getLogger(__name__)


# ============================================================================
# OPS SCENARIO ENDPOINT
# ============================================================================

@router.post("/ops/run-scenario", response_model=OpsScenarioResponse)
def run_ops_scenario(
    request: OpsScenarioRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OpsScenarioResponse:
    """
    Trigger an admin ops scenario from the Tournament Monitor UI.

    **Authorization:** Admin only

    **Safety gate:** player_count >= 128 requires confirmed=True to prevent
    accidental large-scale data generation.

    **Scenario: large_field_monitor**
    1. Seeds N LFA_FOOTBALL_PLAYER users (skips existing ones)
    2. Creates a knockout tournament
    3. Batch-enrolls all N players
    4. Triggers session generation (async for N >= 128)

    The caller can poll `GET /tournaments/{id}/generation-status/{task_id}`
    to track progress.
    """
    import time as _time
    import uuid as _uuid
    from datetime import datetime as _dt, timedelta as _td

    # ── Auth ─────────────────────────────────────────────────────────────────
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can trigger ops scenarios",
        )

    # ── Dry-run: validate only, no DB writes (checked before safety gate) ───────
    if request.dry_run:
        return OpsScenarioResponse(
            triggered=False,
            scenario=request.scenario,
            dry_run=True,
            message=(
                f"dry_run: validation passed — "
                f"scenario={request.scenario}, player_count={request.player_count}, "
                f"confirmed={request.confirmed}"
            ),
        )

    # ── Effective player count ────────────────────────────────────────────────
    # player_count is always the TARGET (total including pinned + auto-fill).
    # player_ids are the PINNED subset; remaining slots are filled from seed pool.
    # Fallback to len(player_ids) only if player_count was not provided.
    _effective_count = request.player_count or (len(request.player_ids) if request.player_ids else 0)

    # ── Safety gate (only applies to real runs) ───────────────────────────────
    if _effective_count >= _OPS_CONFIRM_THRESHOLD and not request.confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Large-scale operation ({_effective_count} players) requires confirmed=True. "
                "Set confirmed=True to proceed."
            ),
        )

    # ── Resolve tournament name ───────────────────────────────────────────────
    ts_label = _dt.utcnow().strftime("%Y%m%d-%H%M%S")
    if request.tournament_name:
        tournament_name = request.tournament_name
    elif _effective_count >= _OPS_CONFIRM_THRESHOLD:
        tournament_name = f"OPS-LF-{_effective_count}-{ts_label}"
    else:
        tournament_name = f"OPS-SMOKE-{_effective_count}-{ts_label}"

    # ── Step 1: Resolve player pool ───────────────────────────────────────────
    import uuid as _uuid
    from datetime import timezone as _tz
    from app.models.user import User as _User, UserRole as _UserRole
    from app.models.license import UserLicense
    from app.models.specialization import SpecializationType as _SpecType

    # Generate a run-specific short ID for logging purposes
    _run_id = _uuid.uuid4().hex[:8]  # e.g. "a3f2b1c0"

    if request.player_ids:
        # ── Manual / hybrid player selection ──────────────────────────────
        _ops_logger.info(
            "[ops] player_ids provided (%d) effective_count=%d scenario=%s admin=%s run_id=%s",
            len(request.player_ids), _effective_count, request.scenario, current_user.email, _run_id,
        )
        # 1. Validate the manually picked players
        valid_rows = (
            db.query(_User.id, _User.name, _User.email)
            .filter(
                _User.id.in_(request.player_ids),
                _User.is_active == True,
            )
            .order_by(_User.id)
            .all()
        )
        found_ids = {row.id for row in valid_rows}
        missing = [uid for uid in request.player_ids if uid not in found_ids]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"player_ids not found or inactive: {missing}",
            )
        manual_ids = [row.id for row in valid_rows]

        # 2. Hybrid fill: if target count > manual count, top-up from seed pool
        remaining = _effective_count - len(manual_ids)
        if remaining > 0:
            fill_rows = (
                db.query(_User.id)
                .join(UserLicense, UserLicense.user_id == _User.id)
                .filter(
                    _User.email.like("%@lfa-seed.hu"),
                    _User.is_active == True,
                    UserLicense.is_active == True,
                    ~_User.id.in_(set(manual_ids)),
                )
                .order_by(_User.id)
                .limit(remaining)
                .all()
            )
            if len(fill_rows) < remaining:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Hybrid fill: need {remaining} more seed players but only "
                        f"{len(fill_rows)} available. Reduce target count or add more seed users."
                    ),
                )
            seeded_ids = manual_ids + [r.id for r in fill_rows]
            _ops_logger.info(
                "[ops] Hybrid: %d manual + %d seed fill = %d total (run_id=%s)",
                len(manual_ids), remaining, len(seeded_ids), _run_id,
            )
        else:
            # Manual-only: exactly the picked players
            seeded_ids = manual_ids
            _ops_logger.info(
                "[ops] Manual-only: %d players (run_id=%s)", len(seeded_ids), _run_id,
            )
    else:
        # ── Auto mode: query @lfa-seed.hu pool ────────────────────────────
        if request.player_count == 0:
            # No players needed - skip seed pool validation
            seeded_ids = []
            _ops_logger.info(
                "[ops] player_count=0 - skipping seed pool query (run_id=%s)", _run_id
            )
        else:
            _ops_logger.info(
                "[ops] Querying %d @lfa-seed.hu players for scenario=%s admin=%s run_id=%s",
                request.player_count, request.scenario, current_user.email, _run_id,
            )
            seed_rows = (
                db.query(_User.id, _User.name, _User.email)
                .join(UserLicense, UserLicense.user_id == _User.id)
                .filter(
                    _User.email.like("%@lfa-seed.hu"),
                    _User.is_active == True,
                    UserLicense.is_active == True,
                )
                .order_by(_User.id)
                .all()
            )
            seed_user_ids = [row.id for row in seed_rows]

            if not seed_user_ids:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        f"No active @lfa-seed.hu users found with licenses. "
                        f"Run 'python scripts/seed_star_players.py' to create seed users first."
                    ),
                )

            if request.player_count > len(seed_user_ids):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Cannot enroll {request.player_count} players: only {len(seed_user_ids)} "
                        f"@lfa-seed.hu seed users available. Increase seed user count or reduce player_count."
                    ),
                )

            # ✅ DETERMINISTIC: Take first N players from ordered pool
            seeded_ids = seed_user_ids[:request.player_count]
            _ops_logger.info(
                "[ops] Using %d existing seed players (pool size: %d, run_id=%s)",
                len(seeded_ids), len(seed_user_ids), _run_id
            )
            _ops_logger.debug(
                "[ops] Sample seed users: %s",
                [(r.id, r.name, r.email) for r in seed_rows[:5]]
            )

    # ── Step 2: Create tournament ─────────────────────────────────────────────
    from app.models.semester import Semester as _Semester, SemesterStatus as _SemStatus
    from app.models.tournament_type import TournamentType as _TType
    from app.models.tournament_configuration import TournamentConfiguration as _TCfg
    from app.models.tournament_reward_config import TournamentRewardConfig as _TRwd
    from app.models.tournament_achievement import TournamentSkillMapping as _TSkill

    # ── Resolve tournament type (HEAD_TO_HEAD only) ───────────────────────────
    tt = None
    if request.tournament_format == "HEAD_TO_HEAD":
        tournament_type_code = request.tournament_type_code or "knockout"
        tt = db.query(_TType).filter(_TType.code == tournament_type_code).first()
        if not tt:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Tournament type '{tournament_type_code}' not found in DB. Run seed_tournament_types first.",
            )

    grandmaster = db.query(_User).filter(
        _User.role == _UserRole.INSTRUCTOR,
        _User.email == "grandmaster@lfa.com",
    ).first()

    tc_ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    tournament = _Semester(
        code=f"OPS-{_run_id}-{tc_ts}",
        name=tournament_name,
        start_date=_dt.now().date(),
        end_date=(_dt.now() + _td(days=30)).date(),
        status=_SemStatus.ONGOING,        # lifecycle enum
        tournament_status=request.initial_tournament_status,  # Use parameter (default: IN_PROGRESS)
        master_instructor_id=grandmaster.id if grandmaster else None,
        enrollment_cost=request.enrollment_cost,  # Enrollment cost from request (default: 0)
        age_group=request.age_group,              # Age group from request (default: PRO)
    )
    db.add(tournament)
    db.flush()

    # Tournament configuration — format-aware
    if request.tournament_format == "HEAD_TO_HEAD":
        t_cfg = _TCfg(
            semester_id=tournament.id,
            tournament_type_id=tt.id,
            participant_type="INDIVIDUAL",
            is_multi_day=False,
            max_players=request.max_players or _effective_count,  # Use override if provided
            parallel_fields=1,
            scoring_type="HEAD_TO_HEAD",
            number_of_rounds=request.number_of_rounds or 1,
        )
    else:
        # INDIVIDUAL_RANKING: no tournament_type, use scoring_type from request
        _scoring = request.scoring_type or "PLACEMENT"
        t_cfg = _TCfg(
            semester_id=tournament.id,
            tournament_type_id=None,
            participant_type="INDIVIDUAL",
            is_multi_day=False,
            max_players=request.max_players or _effective_count,  # Use override if provided
            parallel_fields=1,
            scoring_type=_scoring,
            ranking_direction=request.ranking_direction,
            number_of_rounds=request.number_of_rounds or 1,
        )
    db.add(t_cfg)
    db.flush()

    # Reward config — use user-provided config or OPS default
    _reward_cfg = request.reward_config or {
        "first_place":   {"xp": 2000, "credits": 1000},
        "second_place":  {"xp": 1200, "credits": 500},
        "third_place":   {"xp": 800,  "credits": 250},
        "participation": {"xp": 100,  "credits": 0},
    }
    db.add(_TRwd(
        semester_id=tournament.id,
        reward_policy_name="custom",
        reward_config=_reward_cfg,
    ))

    # Skill mappings + game config — use preset if provided, else default list
    if request.game_preset_id:
        from app.models.game_preset import GamePreset as _GamePreset
        from app.models.game_configuration import GameConfiguration as _GameCfg
        _preset = db.query(_GamePreset).filter(
            _GamePreset.id == request.game_preset_id,
            _GamePreset.is_active == True,
        ).first()
        if _preset:
            db.add(_GameCfg(
                semester_id=tournament.id,
                game_preset_id=_preset.id,
                game_config=_preset.game_config,
            ))
            _avg_w = 1.0
            if _preset.skill_weights:
                _vals = list(_preset.skill_weights.values())
                _avg_w = sum(_vals) / len(_vals) if _vals else 1.0
            for _skill in (_preset.skills_tested or []):
                _frac = (_preset.skill_weights or {}).get(_skill, _avg_w)
                _react = round(_frac / _avg_w, 2) if _avg_w else 1.0
                _react = max(0.1, min(5.0, _react))
                db.add(_TSkill(semester_id=tournament.id, skill_name=_skill, weight=_react))
            _ops_logger.info(
                "[ops] Game preset '%s' applied: %d skills", _preset.code, len(_preset.skills_tested or [])
            )
        else:
            _ops_logger.warning("[ops] game_preset_id=%d not found, using default skills", request.game_preset_id)
            for skill in ["PASSING", "DRIBBLING", "FINISHING"]:
                db.add(_TSkill(semester_id=tournament.id, skill_name=skill, weight=1.0))
    else:
        for skill in ["PASSING", "DRIBBLING", "FINISHING"]:
            db.add(_TSkill(semester_id=tournament.id, skill_name=skill, weight=1.0))

    db.commit()
    tid = tournament.id
    _ops_logger.info("[ops] Tournament created: id=%d name=%r", tid, tournament_name)

    # ── Step 3: Batch-enroll players ─────────────────────────────────────────
    from app.models.semester_enrollment import SemesterEnrollment as _Enroll, EnrollmentStatus as _ES
    from app.models.license import UserLicense as _Lic

    enrolled_count = 0
    for player_id in seeded_ids:
        existing = db.query(_Enroll).filter(
            _Enroll.user_id == player_id,
            _Enroll.semester_id == tid,
            _Enroll.is_active == True,
        ).first()
        if existing:
            enrolled_count += 1
            continue
        lic = db.query(_Lic).filter(
            _Lic.user_id == player_id,
            _Lic.specialization_type == "LFA_FOOTBALL_PLAYER",
        ).first()
        if not lic:
            continue
        enroll = _Enroll(
            user_id=player_id,
            semester_id=tid,
            user_license_id=lic.id,
            age_category="PRO",
            request_status=_ES.APPROVED,
            approved_at=_dt.utcnow(),
            approved_by=current_user.id,
            payment_verified=True,
            is_active=True,
            enrolled_at=_dt.utcnow(),
            requested_at=_dt.utcnow(),
            # OPS scenarios bypass the real 15-min check-in window:
            # auto-confirm all players as checked-in at enrollment time
            tournament_checked_in_at=_dt.utcnow(),
        )
        db.add(enroll)
        enrolled_count += 1

    db.commit()
    _ops_logger.info("[ops] %d/%d players enrolled", enrolled_count, len(seeded_ids))

    # ── Step 4: Trigger session generation (CONDITIONAL) ─────────────────────
    from app.api.api_v1.endpoints.tournaments.generate_sessions import (
        _is_celery_available,
        _run_generation_in_background,
        _task_registry,
        _registry_lock,
        BACKGROUND_GENERATION_THRESHOLD,
    )
    import threading as _threading

    # ✅ MULTI-CAMPUS SUPPORT: Use explicit campus_ids from request (auto-discovery removed)
    from app.models.campus import Campus as _Campus
    campus_ids = request.campus_ids
    active_campuses = db.query(_Campus.id).filter(
        _Campus.id.in_(campus_ids),
        _Campus.is_active == True,
    ).all()
    active_ids = {c.id for c in active_campuses}
    invalid_ids = [cid for cid in campus_ids if cid not in active_ids]
    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Campus IDs {invalid_ids} not found or inactive."
        )
    _ops_logger.info("[ops] Using %d explicit campuses for distributed sessions: %s",
                     len(campus_ids), campus_ids)

    # Persist one CampusScheduleConfig row per physical campus so the monitor
    # UI can show named campus cards (1 field per campus in the display).
    # parallel_fields=None → falls back to the global value in session_generator,
    # so sessions are distributed across all campus-fields (field_numbers 1..N).
    if campus_ids:
        from app.models.campus_schedule_config import CampusScheduleConfig as _CSC
        for _cid in campus_ids:
            _existing = db.query(_CSC).filter_by(tournament_id=tid, campus_id=_cid).first()
            if not _existing:
                db.add(_CSC(
                    tournament_id=tid,
                    campus_id=_cid,
                    parallel_fields=1,   # Default to 1 field per campus (nullable=True but CHECK constraint requires >= 1)
                    is_active=True,
                ))
        # Sync campus_id onto the Semester so generation_validator passes its campus check.
        # MUST commit (not just flush): background thread opens its own SessionLocal()
        # and would see campus_id=NULL if we only flush within the current transaction.
        if not getattr(tournament, 'campus_id', None):
            tournament.campus_id = campus_ids[0]
        db.commit()

    campus_overrides_raw = None
    # 1 field per physical campus — distributes sessions across campus-field slots.
    # Without this, every session lands on field_number=1 regardless of campus count.
    parallel_fields = len(campus_ids) if campus_ids else 1
    session_duration = 90
    break_duration = 15
    # INDIVIDUAL_RANKING: use requested rounds (default 1)
    # HEAD_TO_HEAD knockout: 10 rounds supports up to 1024 players (log2(1024)=10)
    if request.tournament_format == "INDIVIDUAL_RANKING":
        number_of_rounds = request.number_of_rounds or 1
    else:
        number_of_rounds = 10

    task_id: Optional[str] = None

    # Check if auto_generate_sessions is enabled (default True)
    if request.auto_generate_sessions:
        # Proceed with session generation (existing logic)
        if request.player_count >= BACKGROUND_GENERATION_THRESHOLD:
            if _is_celery_available():
                from app.tasks.tournament_tasks import generate_sessions_task
                celery_result = generate_sessions_task.apply_async(
                    args=[tid, parallel_fields, session_duration, break_duration,
                          number_of_rounds, campus_overrides_raw, campus_ids],
                    queue="tournaments",
                    headers={"dispatched_at": _time.perf_counter()},
                )
                task_id = celery_result.id
                _ops_logger.info("[ops] Celery task dispatched task_id=%s", task_id)
            else:
                task_id = str(_uuid.uuid4())
                with _registry_lock:
                    _task_registry[task_id] = {
                        "status": "pending",
                        "tournament_id": tid,
                        "player_count": request.player_count,
                        "message": None,
                        "sessions_count": 0,
                    }
                _threading.Thread(
                    target=_run_generation_in_background,
                    args=(task_id, tid, parallel_fields, session_duration,
                          break_duration, number_of_rounds, campus_overrides_raw, campus_ids),
                    daemon=True,
                ).start()
                _ops_logger.info("[ops] Thread task dispatched task_id=%s", task_id)
        else:
            # Sync generation for small counts
            from app.services.tournament.session_generation.session_generator import (
                TournamentSessionGenerator,
            )
            from app.models.semester_enrollment import SemesterEnrollment as _SE2, EnrollmentStatus as _ES2

            enrolled_user_ids = [
                r[0] for r in db.query(_SE2.user_id).filter(
                    _SE2.semester_id == tid,
                    _SE2.is_active == True,
                    _SE2.request_status == _ES2.APPROVED,
                ).all()
            ]
            generator = TournamentSessionGenerator(db)
            _gen_ok, _gen_msg, _ = generator.generate_sessions(
                tournament_id=tid,
                parallel_fields=parallel_fields,
                session_duration_minutes=session_duration,
                break_minutes=break_duration,
                number_of_rounds=number_of_rounds,
                campus_ids=campus_ids,
            )
            task_id = "sync-done"
            if not _gen_ok:
                _ops_logger.error(
                    "[ops] Sync generation FAILED for %d players: %s",
                    request.player_count, _gen_msg,
                )
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Session generation failed: {_gen_msg}. "
                        f"Tournament id={tid} was created but has 0 sessions. "
                        f"Adjust player_count or tournament_type_code and retry."
                    ),
                )
            _ops_logger.info(
                "[ops] Sync generation done for %d players: %s",
                request.player_count, _gen_msg,
            )

            # ── Step 4.1: Auto-simulate results (skipped for manual/observe modes) ──
            sim_ok = request.simulation_mode in ("auto_immediate", "accelerated")
            sim_msg = "skipped (manual mode)"
            if sim_ok:
                sim_ok, sim_msg = _simulate_tournament_results(
                    db=db,
                    tournament_id=tid,
                    logger=_ops_logger,
                )
            if sim_ok:
                _ops_logger.info("[ops] Auto-result simulation: %s", sim_msg)

                # ── Step 4.2: Calculate rankings to populate leaderboard ─────────────
                try:
                    from app.services.tournament.ranking.strategies.factory import RankingStrategyFactory
                    from app.models.tournament_ranking import TournamentRanking

                    # Get tournament format and type
                    tournament = db.query(_Semester).filter(_Semester.id == tid).first()
                    tournament_format = tournament.format if tournament.format else "HEAD_TO_HEAD"
                    tournament_type_code = None
                    if tournament.tournament_config_obj and tournament.tournament_config_obj.tournament_type:
                        tournament_type_code = tournament.tournament_config_obj.tournament_type.code

                    # Get all sessions for ranking calculation
                    sessions = _get_tournament_sessions(db, tid)

                    if tournament_format == "INDIVIDUAL_RANKING":
                        rankings = _calculate_ir_rankings(tournament, sessions, _ops_logger)
                        strategy = True  # Sentinel so the insert block runs
                    elif tournament_type_code:
                        # HEAD_TO_HEAD: use tournament type-based strategy
                        strategy = RankingStrategyFactory.create(
                            tournament_format=tournament_format,
                            tournament_type_code=tournament_type_code,
                        )
                    else:
                        _ops_logger.warning("[ops] Cannot calculate rankings: unknown format/type")
                        strategy = None

                    if strategy is not None:
                        if tournament_format != "INDIVIDUAL_RANKING":
                            # H2H strategies expect (sessions, db) and return List[Dict]
                            rankings = strategy.calculate_rankings(sessions, db)

                        # Delete existing rankings (idempotency)
                        db.query(TournamentRanking).filter(
                            TournamentRanking.tournament_id == tid
                        ).delete()

                        # Insert new rankings
                        for ranking_data in rankings:
                            ranking_record = TournamentRanking(
                                tournament_id=tid,
                                user_id=ranking_data["user_id"],
                                participant_type="INDIVIDUAL",
                                rank=ranking_data["rank"],
                                # IR strategies return "final_value"; H2H returns "points"
                                points=ranking_data.get("points") or ranking_data.get("final_value", 0),
                                wins=ranking_data.get("wins", 0),
                                losses=ranking_data.get("losses", 0),
                                draws=ranking_data.get("ties", 0),
                                goals_for=ranking_data.get("goals_scored", 0),
                                goals_against=ranking_data.get("goals_conceded", 0),
                            )
                            db.add(ranking_record)

                        db.commit()
                        _ops_logger.info("[ops] Rankings calculated: %d players ranked", len(rankings))

                except Exception as rank_exc:
                    import traceback
                    _ops_logger.warning("[ops] Ranking calculation failed (non-fatal): %s", rank_exc)
                    _ops_logger.warning("[ops] Ranking calculation traceback:\n%s", traceback.format_exc())
                    db.rollback()

                # ── Step 4.3: Finalize tournament + auto-distribute rewards ───────────
                # Runs TournamentFinalizer to set COMPLETED → REWARDS_DISTRIBUTED lifecycle
                _finalize_tournament_with_rewards(tid, db, _ops_logger)

            else:
                _ops_logger.warning("[ops] Auto-result simulation skipped or failed (non-fatal): %s", sim_msg)
    else:
        # Manual mode: Skip session generation
        task_id = "manual-mode-skipped"
        _ops_logger.info(
            "[ops] Session generation SKIPPED (manual mode) - "
            "tournament %d created with 0 sessions",
            tid
        )

    # ── Step 5: Audit log ─────────────────────────────────────────────────────
    audit_log_id: Optional[int] = None
    try:
        from app.services.audit_service import AuditService
        from app.models.audit_log import AuditAction
        audit_svc = AuditService(db)
        log_entry = audit_svc.log(
            action=AuditAction.OPS_SCENARIO_TRIGGERED,
            user_id=current_user.id,
            resource_type="tournament",
            resource_id=tid,
            details={
                "scenario": request.scenario,
                "player_count": request.player_count,
                "enrolled_count": enrolled_count,
                "triggered_by_email": current_user.email,
                "dry_run": False,
                "confirmed": request.confirmed,
                "task_id": task_id,
            },
        )
        audit_log_id = log_entry.id if log_entry else None
    except Exception as audit_exc:
        _ops_logger.warning("[ops] Audit log failed (non-fatal): %s", audit_exc)

    # Count sessions created (query after generation)
    from app.models.session import Session as _SessionModel, EventCategory as _EventCategory
    _session_count = db.query(_SessionModel).filter(
        _SessionModel.semester_id == tid,
        _SessionModel.event_category == _EventCategory.MATCH,
    ).count()

    return OpsScenarioResponse(
        triggered=True,
        scenario=request.scenario,
        tournament_id=tid,
        tournament_name=tournament_name,
        task_id=task_id,
        enrolled_count=enrolled_count,
        session_count=_session_count,
        dry_run=False,
        audit_log_id=audit_log_id,
        message=(
            f"Ops scenario '{request.scenario}' launched: "
            f"tournament_id={tid}, {enrolled_count} players enrolled, "
            f"{_session_count} sessions created, task_id={task_id}"
        ),
    )
