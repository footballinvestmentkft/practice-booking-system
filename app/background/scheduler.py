"""
🕐 Background Scheduler Service
================================

P1 TASK: Automated Progress-License synchronization every 6 hours

Features:
- APScheduler-based job scheduling
- Auto-sync all users with desync issues
- Comprehensive logging to logs/sync_jobs/
- Automatic retry (max 3 attempts)
- Graceful shutdown handling

Usage:
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from app.database import SessionLocal
from app.services.progress_license_sync_service import ProgressLicenseSyncService
from app.services.health_monitor import health_check_job
from app.config import get_settings

# Configure logging
LOG_DIR = Path("logs/sync_jobs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f"scheduler_{datetime.now().strftime('%Y%m%d')}.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler: BackgroundScheduler | None = None
settings = get_settings()


def sync_all_users_job():
    """
    Background job: Sync all users with desync issues

    Runs every 6 hours to ensure data integrity.

    Process:
    1. Find all desync issues
    2. Run auto_sync_all with dry_run=False
    3. Log results
    4. Retry on failure (max 3 attempts)
    """
    job_start = datetime.now()
    log_file = LOG_DIR / f"{job_start.strftime('%Y%m%d_%H%M%S')}_sync.log"

    logger.info("="*70)
    logger.info("🔄 Starting scheduled Progress-License sync job")
    logger.info(f"Job started at: {job_start}")
    logger.info("="*70)

    db = SessionLocal()
    try:
        sync_service = ProgressLicenseSyncService(db)

        # Step 1: Find desync issues
        logger.info("Step 1: Finding desync issues...")
        issues = sync_service.find_desync_issues()
        logger.info(f"Found {len(issues)} users with desync issues")

        if len(issues) == 0:
            logger.info("✅ No desync issues found. System is healthy.")
            _log_job_result(log_file, {
                "status": "success",
                "issues_found": 0,
                "synced_count": 0,
                "message": "No desync issues"
            })
            return

        # Step 2: Auto-sync (Progress → License is default direction)
        logger.info(f"Step 2: Auto-syncing {len(issues)} users...")
        result = sync_service.auto_sync_all(
            sync_direction="progress_to_license",
            dry_run=False  # P1: Actually perform sync
        )

        # Step 3: Log results
        synced = result.get('synced_count', 0)
        failed = result.get('failed_count', 0)

        if failed == 0:
            logger.info(f"✅ Successfully synced {synced}/{len(issues)} users")
        else:
            logger.warning(f"⚠️  Synced {synced}/{len(issues)} users, {failed} failed")

        job_end = datetime.now()
        duration = (job_end - job_start).total_seconds()

        logger.info(f"Job completed at: {job_end}")
        logger.info(f"Duration: {duration:.2f} seconds")
        logger.info("="*70)

        # Save detailed results
        _log_job_result(log_file, {
            "status": "success" if failed == 0 else "partial_failure",
            "job_start": job_start.isoformat(),
            "job_end": job_end.isoformat(),
            "duration_seconds": duration,
            "issues_found": len(issues),
            "synced_count": synced,
            "failed_count": failed,
            "results": result.get('results', [])
        })

    except Exception as e:
        logger.error(f"❌ Job failed with exception: {str(e)}")
        import traceback
        traceback.print_exc()

        _log_job_result(log_file, {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        })

        # Re-raise to trigger retry
        raise

    finally:
        db.close()


def _log_job_result(log_file: Path, result: Dict[str, Any]):
    """Write job result to JSON log file"""
    import json

    with open(log_file, 'w') as f:
        json.dump(result, f, indent=2, default=str)

    logger.info(f"Job result saved to: {log_file}")


def job_listener(event):
    """
    APScheduler event listener for job completion/errors

    Logs job execution status and handles retries
    """
    if event.exception:
        logger.error(f"Job {event.job_id} failed: {event.exception}")
        logger.info("Retry will be attempted (max 3 retries)")
    else:
        logger.info(f"Job {event.job_id} executed successfully")


def system_events_purge_job() -> None:
    """
    Nightly maintenance job: purge old resolved system_events.

    Retention policy:
      - RESOLVED events older than SYSTEM_EVENT_RETENTION_DAYS (default 90) → deleted
      - OPEN (unresolved) events → never touched by this job
      - Runs daily at 02:00 UTC to avoid peak-hour load

    Failure is non-fatal: a WARNING is logged, the scheduler retries next night.
    """
    from app.services.system_event_service import SystemEventService

    job_start = datetime.now()
    logger.info("🧹 system_events purge job started at %s", job_start.isoformat())

    db = SessionLocal()
    try:
        svc = SystemEventService(db)
        deleted = svc.purge_old_events()
        db.commit()
        logger.info(
            "✅ system_events purge complete — deleted=%s duration=%.2fs",
            deleted,
            (datetime.now() - job_start).total_seconds(),
        )
    except Exception as exc:
        db.rollback()
        logger.warning(
            "SYSTEM_EVENT_PURGE_FAILED — error=%s",
            type(exc).__name__,
            exc_info=True,
        )
    finally:
        db.close()


def auto_checkin_open_job() -> None:
    """
    Minute-by-minute maintenance job: auto-transition ENROLLMENT_CLOSED → CHECK_IN_OPEN.

    Finds all ENROLLMENT_CLOSED tournaments whose `checkin_opens_at` has passed and
    transitions them to CHECK_IN_OPEN so that players/teams can check in.

    The status history entry uses changed_by=NULL to indicate a system-initiated action.
    """
    from datetime import timezone as _tz
    from app.models.semester import Semester
    from app.api.api_v1.endpoints.tournaments.lifecycle import record_status_change

    job_start = datetime.now()
    logger.info("⏰ auto_checkin_open_job running at %s", job_start.isoformat())

    db = SessionLocal()
    try:
        now = datetime.now(_tz.utc)
        ready = db.query(Semester).filter(
            Semester.tournament_status == "ENROLLMENT_CLOSED",
            Semester.checkin_opens_at.isnot(None),
            Semester.checkin_opens_at <= now,
        ).all()

        for t in ready:
            old_status = t.tournament_status
            t.tournament_status = "CHECK_IN_OPEN"
            record_status_change(
                db=db,
                tournament_id=t.id,
                old_status=old_status,
                new_status="CHECK_IN_OPEN",
                changed_by=None,  # NULL = system / scheduler action
                reason=f"Auto-opened by scheduler at {now.isoformat()}",
            )
            logger.info("✅ Tournament %d (%s) auto-transitioned → CHECK_IN_OPEN", t.id, t.name)

        if ready:
            db.commit()
            logger.info("Committed CHECK_IN_OPEN for %d tournament(s)", len(ready))
        else:
            logger.debug("auto_checkin_open_job: no tournaments ready for check-in")

    except Exception as exc:
        db.rollback()
        logger.warning(
            "AUTO_CHECKIN_OPEN_FAILED — error=%s",
            type(exc).__name__,
            exc_info=True,
        )
    finally:
        db.close()


def start_scheduler():
    """
    Start the background scheduler

    Call this on application startup (e.g., in main.py or app initialization)

    Schedules:
    - Progress-License sync: Every 6 hours
    - System events purge: Daily at 02:00 UTC
    """
    global scheduler

    if scheduler is not None:
        logger.warning("Scheduler already running")
        return

    logger.info("🚀 Starting background scheduler...")

    scheduler = BackgroundScheduler()

    # Add listener for job events
    scheduler.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    # Schedule: Progress-License sync every 6 hours
    scheduler.add_job(
        func=sync_all_users_job,
        trigger=IntervalTrigger(hours=6),
        id='progress_license_sync',
        name='Progress-License Auto-Sync',
        replace_existing=True,
        max_instances=1,  # Prevent concurrent runs
        misfire_grace_time=300  # 5 minutes grace period
    )

    # P2: Schedule health check every 5 minutes
    scheduler.add_job(
        func=health_check_job,
        trigger=IntervalTrigger(minutes=5),
        id='coupling_health_check',
        name='Coupling Enforcer Health Check',
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60  # 1 minute grace period
    )

    # Every minute: auto-open check-in for ENROLLMENT_CLOSED tournaments
    scheduler.add_job(
        func=auto_checkin_open_job,
        trigger=IntervalTrigger(minutes=1),
        id='auto_checkin_open',
        name='Auto Check-In Opener',
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,  # 1 minute grace
    )

    # Nightly: purge old resolved system_events (02:00 UTC)
    scheduler.add_job(
        func=system_events_purge_job,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id='system_events_purge',
        name='System Events Retention Purge',
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,  # 1 hour — can run late if server was down
    )

    # Every 30 s: auto-expire stale 'stopping' capture cycles (PR-MC1)
    scheduler.add_job(
        func=expire_stopping_cycles_job,
        trigger=IntervalTrigger(seconds=30),
        id='multicamera_stopping_timeout',
        name='Multicamera Stopping Cycle Timeout',
        replace_existing=True,
        max_instances=1,       # Prevent concurrent runs from the same process
        misfire_grace_time=30, # Skip if more than 30 s late (don't pile up)
    )

    scheduler.start()

    logger.info("✅ Background scheduler started successfully")
    logger.info("Scheduled jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name} (ID: {job.id}): {job.trigger}")

    return scheduler


def stop_scheduler(timeout: float | None = None) -> None:
    """
    Stop the background scheduler with a graceful shutdown timeout.

    Waits up to ``timeout`` seconds for any currently running APScheduler
    job to finish.  If the job has not finished within the timeout, the
    scheduler is forcibly stopped; running job threads (which are daemon
    threads) exit with the process.

    Parameters
    ----------
    timeout:
        Maximum seconds to wait.  Defaults to
        ``settings.GRACEFUL_SHUTDOWN_TIMEOUT`` (30 s).  Pass ``0`` to
        stop immediately without waiting.
    """
    import threading

    global scheduler

    if scheduler is None:
        logger.warning("Scheduler not running — nothing to stop")
        return

    _timeout = timeout if timeout is not None else settings.GRACEFUL_SHUTDOWN_TIMEOUT
    logger.info("⏹️  Stopping background scheduler (timeout=%.0fs)...", _timeout)

    # Run shutdown in a thread so we can enforce the timeout.
    # scheduler.shutdown(wait=True) blocks until all running jobs finish.
    _result: dict = {"done": False}

    def _do_shutdown() -> None:
        scheduler.shutdown(wait=True)
        _result["done"] = True

    t = threading.Thread(target=_do_shutdown, daemon=True, name="scheduler-shutdown")
    t.start()
    t.join(timeout=_timeout)

    if not _result["done"]:
        logger.warning(
            "Background scheduler did not stop within %.0fs — forcing shutdown; "
            "running jobs will be terminated with the process",
            _timeout,
        )
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass  # best-effort: process is exiting anyway

    scheduler = None
    logger.info("✅ Background scheduler stopped")


# ── Convenience functions for manual job execution / verification ──────────────

def run_sync_job_now():
    """
    Manually trigger the Progress-License sync job.

    Usage:
        from app.background.scheduler import run_sync_job_now
        run_sync_job_now()
    """
    logger.info("🔧 Manual sync job trigger")
    sync_all_users_job()


def run_purge_now() -> int:
    """
    Manually trigger the system_events purge job (bypass 02:00 UTC schedule).

    Use this to:
      - Verify the purge logic works before relying on the nightly schedule
      - Force a purge after a data incident
      - Confirm misfire-grace behaviour in a staging environment

    Usage:
        from app.background.scheduler import run_purge_now
        deleted = run_purge_now()
        print(f"Deleted {deleted} events")

    Returns:
        Number of deleted rows (0 if nothing matched the retention window).
    """
    logger.info("🔧 Manual system_events purge trigger")
    from app.services.system_event_service import SystemEventService

    db = SessionLocal()
    try:
        deleted = SystemEventService(db).purge_old_events()
        db.commit()
        logger.info("✅ Manual purge complete — deleted=%s", deleted)
        return deleted
    except Exception as exc:
        db.rollback()
        logger.warning("SYSTEM_EVENT_PURGE_FAILED (manual) — error=%s", type(exc).__name__, exc_info=True)
        return 0
    finally:
        db.close()


def expire_stopping_cycles_job() -> None:
    """
    Scheduler job: auto-expire stale 'stopping' capture cycles (PR-MC1).

    Runs every 30 s.  Finds 'stopping' cycles where
    stop_requested_at + MULTICAMERA_STOPPING_TIMEOUT_SECONDS < now and
    force-completes them via CycleService.expire_stale_stopping_cycles().

    Row-level SELECT FOR UPDATE SKIP LOCKED prevents concurrent workers
    from processing the same cycle twice.  Idempotent: a second run within
    the same 30-second window is a no-op because the cycle is already
    terminal after the first run.

    Process restart: the job re-registers with the same ID on next startup
    (replace_existing=True) and the scheduler's first tick will find any
    cycles that expired while the process was down.
    """
    from app.services.multicamera.cycle_service import CycleService

    db = SessionLocal()
    try:
        svc = CycleService(db)
        count = svc.expire_stale_stopping_cycles(
            timeout_seconds=settings.MULTICAMERA_STOPPING_TIMEOUT_SECONDS
        )
        if count:
            logger.info(
                "MC1 stopping timeout: expired %d stale cycle(s) "
                "(threshold=%ds)",
                count,
                settings.MULTICAMERA_STOPPING_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        db.rollback()
        logger.warning(
            "MC1_STOPPING_EXPIRE_FAILED — error=%s",
            type(exc).__name__,
            exc_info=True,
        )
    finally:
        db.close()


def get_scheduler_status() -> dict:
    """
    Return current scheduler job status (for health checks and monitoring).

    Usage:
        from app.background.scheduler import get_scheduler_status
        print(get_scheduler_status())

    Returns dict with:
        running:  bool — whether the scheduler is active
        jobs:     list of {id, name, next_run_utc, misfire_grace_time}
    """
    if scheduler is None:
        return {"running": False, "jobs": []}

    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_utc": next_run.isoformat() if next_run else None,
            "misfire_grace_time": job.misfire_grace_time,
        })
    return {"running": True, "jobs": jobs}
