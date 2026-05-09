"""
P0 Fix — IN_PROGRESS Rollback Proof (T3 / T4 / T5)

Proves that flushed-but-uncommitted DB changes (status update, session DELETEs,
attendance DELETEs) are atomically undone when SQLAlchemy rolls back the
transaction — which is exactly what FastAPI's get_db() dependency does when
HTTPException propagates out of the endpoint.

Design rationale
----------------
The fix in lifecycle.py replaces two `print(warning)` branches with
`raise HTTPException(400, ...)`.  Because `db.commit()` is called on line 546
AFTER the regeneration block, raising inside the block guarantees that the
commit is never reached.  FastAPI then calls `db.close()` via the `get_db()`
finally-clause, which issues a PostgreSQL ROLLBACK.

These tests exercise that ROLLBACK guarantee directly, without going through
the full endpoint stack.  They prove:

  T3 — status change flushed to the session is reverted by rollback
  T4 — session DELETEs flushed to the session are reverted by rollback
  T5 — attendance DELETEs (cascade from sessions) are reverted by rollback

Each test follows the same three-phase structure:
  1. Commit initial rows (real state — survives a later ROLLBACK because the
     SAVEPOINT fixture's inner SAVEPOINT was committed).
  2. Flush mutations (simulates what lifecycle.py does before the raise).
  3. Call db.rollback(), then assert original rows are intact.

The postgres_db fixture (tests/unit/conftest.py) wraps every test in an outer
transaction that is rolled back at teardown, so no rows escape to the shared DB.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text


# ── Shared IDs — unlikely to collide with any seeded data ────────────────────
_SEM_ID = 99950
_USER_ID = 99950


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _insert_user(db, user_id: int = _USER_ID) -> None:
    db.execute(text("""
        INSERT INTO users
          (id, name, email, password_hash, role,
           payment_verified, credit_balance, credit_purchased,
           xp_balance, nda_accepted, parental_consent)
        VALUES
          (:id, 'RollbackProof User', :email, 'x', 'STUDENT',
           false, 0, 0, 0, false, false)
        ON CONFLICT (id) DO NOTHING
    """), {"id": user_id, "email": f"rbproof_{user_id}@test.com"})


def _insert_tournament(db, sem_id: int = _SEM_ID) -> None:
    db.execute(text("""
        INSERT INTO semesters
          (id, code, name, start_date, end_date, status, enrollment_cost,
           tournament_status)
        VALUES
          (:id, :code, 'Rollback Proof Tournament',
           '2026-06-01', '2026-06-30', 'ONGOING', 0,
           'CHECK_IN_OPEN')
        ON CONFLICT (id) DO NOTHING
    """), {"id": sem_id, "code": f"RBPROOF-{sem_id}"})


def _insert_session(db, sem_id: int = _SEM_ID) -> int:
    """Insert a minimal auto_generated session and return its id."""
    row = db.execute(text("""
        INSERT INTO sessions
          (title, date_start, date_end, session_type,
           semester_id, credit_cost, auto_generated)
        VALUES
          ('Rollback Proof Session',
           '2026-06-15 10:00:00', '2026-06-15 12:00:00',
           'on_site', :sem_id, 0, true)
        RETURNING id
    """), {"sem_id": sem_id}).fetchone()
    return row[0]


def _insert_attendance(db, session_id: int, user_id: int = _USER_ID) -> int:
    """Insert a minimal attendance record and return its id."""
    row = db.execute(text("""
        INSERT INTO attendance (user_id, session_id)
        VALUES (:uid, :sid)
        RETURNING id
    """), {"uid": user_id, "sid": session_id}).fetchone()
    return row[0]


def _count_sessions(db, sem_id: int = _SEM_ID) -> int:
    return db.execute(
        text("SELECT COUNT(*) FROM sessions WHERE semester_id = :id AND auto_generated = true"),
        {"id": sem_id},
    ).scalar()


def _count_attendance(db, session_ids: list[int]) -> int:
    if not session_ids:
        return 0
    return db.execute(
        text("SELECT COUNT(*) FROM attendance WHERE session_id = ANY(:ids)"),
        {"ids": session_ids},
    ).scalar()


def _get_tournament_status(db, sem_id: int = _SEM_ID) -> str:
    return db.execute(
        text("SELECT tournament_status FROM semesters WHERE id = :id"),
        {"id": sem_id},
    ).scalar()


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestInProgressRollbackProof:
    """
    Proves PostgreSQL flush+rollback atomicity for the three mutation types that
    lifecycle.py performs before the P0 raise.

    All tests follow: commit initial state → flush mutation → rollback → assert restored.
    """

    def test_t3_status_flush_reverted_by_rollback(self, postgres_db):
        """T3: flushed tournament_status update is undone by rollback.

        Mirrors: lifecycle.py line 285 sets tournament_status='IN_PROGRESS' via ORM flush.
        When HTTPException is raised, get_db() calls db.close() → ROLLBACK.
        Status must revert to 'CHECK_IN_OPEN'.
        """
        _insert_tournament(postgres_db)
        postgres_db.commit()

        # Confirm initial status
        assert _get_tournament_status(postgres_db) == "CHECK_IN_OPEN"

        # Simulate lifecycle.py: flush status change (not yet committed)
        postgres_db.execute(
            text("UPDATE semesters SET tournament_status = 'IN_PROGRESS' WHERE id = :id"),
            {"id": _SEM_ID},
        )
        postgres_db.flush()

        # Mutation is visible within the transaction
        assert _get_tournament_status(postgres_db) == "IN_PROGRESS"

        # HTTPException would fire here in the real endpoint → get_db() rollback
        postgres_db.rollback()

        # Status restored — ROLLBACK undid the flush
        assert _get_tournament_status(postgres_db) == "CHECK_IN_OPEN"

    def test_t4_session_deletes_reverted_by_rollback(self, postgres_db):
        """T4: flushed session DELETEs are undone by rollback.

        Mirrors: lifecycle.py lines 495–500 delete auto_generated sessions via flush.
        When HTTPException is raised, the DELETE is still inside the open transaction
        and is fully reversed by ROLLBACK.
        """
        _insert_user(postgres_db)
        _insert_tournament(postgres_db)
        sid1 = _insert_session(postgres_db)
        sid2 = _insert_session(postgres_db)
        postgres_db.commit()

        assert _count_sessions(postgres_db) == 2

        # Simulate lifecycle.py session DELETE flush
        postgres_db.execute(
            text("DELETE FROM sessions WHERE semester_id = :id AND auto_generated = true"),
            {"id": _SEM_ID},
        )
        postgres_db.flush()

        # DELETE is visible within the transaction — count drops to 0
        assert _count_sessions(postgres_db) == 0

        # HTTPException fires → get_db() rollback
        postgres_db.rollback()

        # Sessions restored — ROLLBACK undid the flush
        assert _count_sessions(postgres_db) == 2

    def test_t5_attendance_deletes_reverted_by_rollback(self, postgres_db):
        """T5: flushed attendance DELETEs (preceding session DELETEs) are undone by rollback.

        Mirrors: lifecycle.py lines 494–495 delete attendance records before sessions.
        Both DELETEs are flushed inside the same open transaction.  ROLLBACK restores both.
        """
        _insert_user(postgres_db)
        _insert_tournament(postgres_db)
        sid1 = _insert_session(postgres_db)
        sid2 = _insert_session(postgres_db)
        att1 = _insert_attendance(postgres_db, sid1)
        att2 = _insert_attendance(postgres_db, sid2)
        postgres_db.commit()

        assert _count_attendance(postgres_db, [sid1, sid2]) == 2

        # Simulate lifecycle.py: flush attendance DELETE, then session DELETE
        postgres_db.execute(
            text("DELETE FROM attendance WHERE session_id = ANY(:ids)"),
            {"ids": [sid1, sid2]},
        )
        postgres_db.execute(
            text("DELETE FROM sessions WHERE semester_id = :id AND auto_generated = true"),
            {"id": _SEM_ID},
        )
        postgres_db.flush()

        # Both DELETEs visible in transaction
        assert _count_attendance(postgres_db, [sid1, sid2]) == 0
        assert _count_sessions(postgres_db) == 0

        # HTTPException fires → get_db() rollback
        postgres_db.rollback()

        # Both restored
        assert _count_sessions(postgres_db) == 2
        assert _count_attendance(postgres_db, [sid1, sid2]) == 2
