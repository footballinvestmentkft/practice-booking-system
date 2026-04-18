"""
Performance test fixtures — ensures test_query_budget.py runs on any database.

heavy_event_id: session-scoped fixture that provides an event ID with
  ≥32 sessions and ≥16 rankings (the "heaviest case" for the query budget tests).

Strategy:
  1. If event 31 exists in the DB (dev/staging) → use it as-is (no-op)
  2. Otherwise (CI fresh DB) → create minimal data, yield the new ID, teardown

This makes test_query_budget.py portable without changing business-logic tests.
"""
import json
from datetime import datetime, timezone

import pytest

from app.database import SessionLocal
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.session import Session as SessionModel
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_ranking import TournamentRanking

_PREFERRED_HEAVY_EVENT_ID = 31


@pytest.fixture(scope="session")
def heavy_event_id():
    """Yield an event ID guaranteed to have ≥32 sessions and ≥16 rankings."""
    db = SessionLocal()
    try:
        existing = db.query(Semester).filter(Semester.id == _PREFERRED_HEAVY_EVENT_ID).first()
        if existing is not None:
            yield _PREFERRED_HEAVY_EVENT_ID
            return

        # Fresh DB (CI) — create minimal event with required data
        sem = Semester(
            name="CI-QueryBudget-Heavy-Event",
            code="CI-QB-HEAVY",
            semester_category=SemesterCategory.ACADEMY_SEASON,
            tournament_status="REWARDS_DISTRIBUTED",
            status=SemesterStatus.COMPLETED,
            enrollment_cost=0,
        )
        db.add(sem)
        db.flush()
        event_id = sem.id

        db.add(TournamentConfiguration(
            semester_id=event_id,
            participant_type="INDIVIDUAL",
            scoring_type="PLACEMENT",
            parallel_fields=1,
            number_of_rounds=16,
        ))
        db.flush()

        for i in range(1, 17):
            db.add(TournamentRanking(
                tournament_id=event_id,
                participant_type="INDIVIDUAL",
                rank=i,
                points=100 - i * 5,
            ))

        _ts = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
        for rn in range(1, 33):
            db.add(SessionModel(
                semester_id=event_id,
                title=f"CI Round {rn}",
                round_number=rn,
                session_status="COMPLETED",
                date_start=_ts,
                date_end=_ts,
                rounds_data={"round_results": {"1": {"player_data": {}}}},
            ))

        db.commit()
        db.refresh(sem)
        yield event_id

    finally:
        # Teardown — only clean up if we created the data
        try:
            existing_check = db.query(Semester).filter(
                Semester.id == _PREFERRED_HEAVY_EVENT_ID
            ).first()
            if existing_check is not None:
                db.close()
                return  # event 31 was pre-existing — don't delete it
        except Exception:
            pass
        try:
            db.rollback()
            sem_check = db.query(Semester).filter(
                Semester.code == "CI-QB-HEAVY"
            ).first()
            if sem_check:
                sid = sem_check.id
                db.query(SessionModel).filter(SessionModel.semester_id == sid).delete()
                db.query(TournamentRanking).filter(TournamentRanking.tournament_id == sid).delete()
                db.query(TournamentConfiguration).filter(
                    TournamentConfiguration.semester_id == sid
                ).delete()
                db.delete(sem_check)
                db.commit()
        except Exception:
            pass
        finally:
            db.close()
