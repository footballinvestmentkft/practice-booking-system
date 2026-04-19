"""Tournament finalization helper."""
import logging as _logging


def _finalize_tournament_with_rewards(tid: int, db, logger: _logging.Logger) -> None:
    """Run TournamentFinalizer to advance tournament COMPLETED → REWARDS_DISTRIBUTED.

    Non-fatal: any exception is logged and the DB transaction is rolled back.
    """
    try:
        from app.models.semester import Semester as _Semester
        from app.services.tournament.results.finalization.tournament_finalizer import TournamentFinalizer
        _t = db.query(_Semester).filter(_Semester.id == tid).first()
        if _t:
            finalizer = TournamentFinalizer(db)
            fin_result = finalizer.finalize(_t)
            if fin_result.get("success"):
                logger.info(
                    "[ops] Tournament lifecycle complete: status=%s — %s",
                    fin_result.get("tournament_status"),
                    fin_result.get("rewards_message", "no rewards message"),
                )
            else:
                logger.warning(
                    "[ops] Tournament finalization returned non-success: %s",
                    fin_result.get("message"),
                )
    except Exception as fin_exc:
        import traceback
        logger.warning("[ops] Tournament finalization failed (non-fatal): %s", fin_exc)
        logger.warning("[ops] Finalization traceback:\n%s", traceback.format_exc())
        try:
            db.rollback()
        except Exception:
            pass
