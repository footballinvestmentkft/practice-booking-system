"""Startup reference-data integrity checks — WARNING only, non-fatal."""
import logging
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_CHALLENGE_COMPATIBLE = frozenset({"memory_sequence", "target_tracking"})


def check_reference_data_integrity(db: Session) -> None:
    """Warn if VT reference data is missing. Does not raise."""
    try:
        from app.models.virtual_training import VirtualTrainingGame  # lazy — avoids circular import
        vt_total = db.query(VirtualTrainingGame).count()
        if vt_total == 0:
            logger.warning(
                "STARTUP: virtual_training_games is empty — "
                "Virtual Games hub and Challenge flow will not work. "
                "Fix: PYTHONPATH=. python scripts/seed_virtual_training_games.py"
            )
            return
        vt_compat = db.query(VirtualTrainingGame).filter(
            VirtualTrainingGame.code.in_(_CHALLENGE_COMPATIBLE),
            VirtualTrainingGame.is_active == True,  # noqa: E712
        ).count()
        if vt_compat < len(_CHALLENGE_COMPATIBLE):
            logger.warning(
                "STARTUP: Only %d/%d challenge-compatible VT games are active "
                "(expected: memory_sequence, target_tracking). "
                "/challenges/send will show no games.",
                vt_compat,
                len(_CHALLENGE_COMPATIBLE),
            )
    except Exception:
        logger.warning("STARTUP: VT reference-data check failed — continuing anyway", exc_info=True)
