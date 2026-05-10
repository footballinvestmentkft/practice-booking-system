"""
Tournament Celery Tasks

Long-running tasks for tournament management that are executed asynchronously
via the Celery worker queue.

Task: generate_sessions_task
  Generates sessions for large tournaments (>= 128 players) asynchronously.
  Triggered by POST /tournaments/{id}/generate-sessions when player_count >= threshold.

State flow:
  PENDING → STARTED → SUCCESS | FAILURE

Usage from API code:
    from app.tasks.tournament_tasks import generate_sessions_task
    result = generate_sessions_task.apply_async(
        args=[tournament_id, parallel_fields, session_duration, break_duration,
              number_of_rounds, campus_overrides_raw],
        queue="tournaments",
    )
    task_id = result.id  # UUID string for polling

Polling:
    from celery.result import AsyncResult
    ar = AsyncResult(task_id, app=celery_app)
    ar.state     # PENDING | STARTED | SUCCESS | FAILURE
    ar.result    # dict on SUCCESS, exception on FAILURE
"""
import logging
import time
from typing import Any, Dict, Optional

from celery import Task

from app.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


class DatabaseTask(Task):
    """
    Base task class that provides a per-task SQLAlchemy session.
    The session is opened fresh for every task invocation and closed
    in the finally block regardless of success or failure.
    """
    abstract = True

    def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)


@celery_app.task(
    bind=True,
    base=DatabaseTask,
    name="app.tasks.tournament_tasks.generate_sessions_task",
    max_retries=2,
    default_retry_delay=30,  # seconds before retry
    track_started=True,
    acks_late=True,
)
def generate_sessions_task(
    self,
    tournament_id: int,
    parallel_fields: int,
    session_duration_minutes: int,
    break_minutes: int,
    number_of_rounds: int,
    campus_overrides_raw: Optional[Dict[str, Any]] = None,
    campus_ids: Optional[list] = None,
    skip_instructor_check: bool = False,
) -> Dict[str, Any]:
    """
    Celery task: generate tournament sessions asynchronously.

    Args:
        tournament_id:           Tournament (Semester) ID
        parallel_fields:         Resolved global parallel field count
        session_duration_minutes: Resolved global match duration
        break_minutes:           Resolved global break duration
        number_of_rounds:        Rounds for INDIVIDUAL_RANKING
        campus_overrides_raw:    Serialised campus_schedule_overrides dict (JSON-safe)

    Returns:
        {
            "success": bool,
            "tournament_id": int,
            "sessions_count": int,
            "message": str,
            "generation_duration_ms": float,
            "db_write_time_ms": float,
            "queue_wait_time_ms": float | None,
        }
    """
    t_task_start = time.perf_counter()

    # queue_wait_time: time from when task was dispatched to when worker picked it up.
    # Available only if the task was sent with a known eta/apply_async timestamp stored
    # in the task headers.  We read it from self.request.headers if present, otherwise
    # report None so callers know it was not measurable.
    queue_wait_ms: Optional[float] = None
    if hasattr(self.request, "headers") and self.request.headers:
        dispatched_at = self.request.headers.get("dispatched_at")
        if dispatched_at is not None:
            try:
                queue_wait_ms = round((t_task_start - float(dispatched_at)) * 1000, 1)
            except (TypeError, ValueError):
                pass

    db = SessionLocal()
    try:
        logger.info(
            "[Celery] generate_sessions_task START "
            "tournament_id=%d parallel_fields=%d session_duration=%dmin "
            "queue_wait_ms=%s",
            tournament_id, parallel_fields, session_duration_minutes,
            f"{queue_wait_ms:.1f}" if queue_wait_ms is not None else "n/a",
        )

        # ── Persist campus_schedule_overrides if provided ─────────────────────
        if campus_overrides_raw:
            from app.models.campus_schedule_config import CampusScheduleConfig as CSCModel
            from app.models.tournament_configuration import TournamentConfiguration
            config = db.query(TournamentConfiguration).filter(
                TournamentConfiguration.semester_id == tournament_id
            ).first()
            if config:
                config.campus_schedule_overrides = campus_overrides_raw
                db.flush()

        # ── Session generation (CPU + DB write) ───────────────────────────────
        from app.services.tournament_session_generator import TournamentSessionGenerator
        generator = TournamentSessionGenerator(db)

        t_gen_start = time.perf_counter()
        success, message, sessions_created = generator.generate_sessions(
            tournament_id=tournament_id,
            parallel_fields=parallel_fields,
            session_duration_minutes=session_duration_minutes,
            break_minutes=break_minutes,
            number_of_rounds=number_of_rounds,
            campus_ids=campus_ids,
            skip_instructor_check=skip_instructor_check,
        )
        t_gen_end = time.perf_counter()

        # generator.generate_sessions() includes the db.commit() internally,
        # so db_write_time covers both computation and the final bulk write.
        db_write_ms = round((t_gen_end - t_gen_start) * 1000, 1)

        sessions_count = len(sessions_created) if success else 0
        generation_duration_ms = round((t_gen_end - t_task_start) * 1000, 1)

        if not success:
            logger.error(
                "[Celery] generate_sessions_task GENERATION_FAILED "
                "tournament_id=%d message=%r generation_duration_ms=%.1f",
                tournament_id, message, generation_duration_ms,
            )
            raise RuntimeError(message)

        logger.info(
            "[Celery] generate_sessions_task SUCCESS "
            "tournament_id=%d sessions_created=%d "
            "generation_duration_ms=%.1f db_write_time_ms=%.1f queue_wait_ms=%s",
            tournament_id, sessions_count,
            generation_duration_ms, db_write_ms,
            f"{queue_wait_ms:.1f}" if queue_wait_ms is not None else "n/a",
        )

        return {
            "success": True,
            "tournament_id": tournament_id,
            "sessions_count": sessions_count,
            "message": message,
            "generation_duration_ms": generation_duration_ms,
            "db_write_time_ms": db_write_ms,
            "queue_wait_time_ms": queue_wait_ms,
        }

    except Exception as exc:
        total_ms = round((time.perf_counter() - t_task_start) * 1000, 1)
        logger.error(
            "[Celery] generate_sessions_task FAILED "
            "tournament_id=%d elapsed_ms=%.1f error=%r",
            tournament_id, total_ms, str(exc),
            exc_info=True,
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            raise
    finally:
        db.close()
