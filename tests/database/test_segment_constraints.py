"""
Database constraint tests for session_segments + session_segment_results.

Verifies migration 2026_04_21_1100:
  1. Tables exist with required columns
  2. uq_segment_session_position: (session_id, position) duplicate → IntegrityError
  3. uq_segment_result_seg_att: (segment_id, attendance_id) duplicate → IntegrityError
  4. uq_segment_result_idempotency: duplicate non-null idempotency_key → IntegrityError
  5. CASCADE DELETE: deleting session_segments row cascades to session_segment_results

Self-contained: seeds minimal rows (user → attendance via sessions/semesters/groups chain
is expensive, so attendance is seeded via direct INSERT with NULL FK guards where possible).
All test rows are cleaned up in a finally block.
"""

import os
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/lfa_intern_system"
)

_CI_EMAIL = "ci_segment_constraint_test@test.invalid"


def _seed_user(conn):
    """Insert or retrieve minimal test user. Returns user_id."""
    return conn.execute(text("""
        INSERT INTO users
            (name, email, password_hash, role,
             payment_verified, credit_balance, credit_purchased,
             xp_balance, nda_accepted, parental_consent)
        VALUES
            ('CI Segment Constraint', :email, 'dummy_hash_ci_seg', 'STUDENT',
             false, 0, 0, 0, false, false)
        ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """), {"email": _CI_EMAIL}).scalar()


def _seed_semester(conn):
    """Insert a minimal semester row. Returns semester_id."""
    import uuid
    code = f"CI-SEG-{uuid.uuid4().hex[:8].upper()}"
    return conn.execute(text("""
        INSERT INTO semesters
            (code, name, start_date, end_date, status, semester_category, enrollment_cost)
        VALUES
            (:code, 'CI Seg Constraint Semester', '2099-01-01', '2099-12-31',
             'READY_FOR_ENROLLMENT', 'ACADEMY_SEASON', 0)
        RETURNING id
    """), {"code": code}).scalar()


def _seed_session(conn, semester_id):
    """Insert a minimal session row. Returns session_id."""
    return conn.execute(text("""
        INSERT INTO sessions
            (title, date_start, date_end, session_type, semester_id, credit_cost, auto_generated)
        VALUES
            ('CI Seg Session', '2099-06-01 10:00', '2099-06-01 11:00',
             'on_site', :sid, 1, false)
        RETURNING id
    """), {"sid": semester_id}).scalar()


def _seed_segment(conn, session_id, position=1):
    """Insert a minimal session_segment row. Returns segment_id."""
    return conn.execute(text("""
        INSERT INTO session_segments
            (session_id, position, label, is_active)
        VALUES
            (:sid, :pos, 'CI Drill', true)
        RETURNING id
    """), {"sid": session_id, "pos": position}).scalar()


def _seed_attendance(conn, user_id, session_id):
    """Insert a minimal attendance row. Returns attendance_id."""
    return conn.execute(text("""
        INSERT INTO attendance
            (user_id, session_id, status)
        VALUES
            (:uid, :ssid, 'present')
        RETURNING id
    """), {"uid": user_id, "ssid": session_id}).scalar()


def _seed_segment_result(conn, segment_id, attendance_id, session_id, user_id,
                          idempotency_key=None):
    """Insert a minimal session_segment_result row. Returns row id."""
    if idempotency_key is None:
        idempotency_key = f"seg_{segment_id}_att_{attendance_id}"
    return conn.execute(text("""
        INSERT INTO session_segment_results
            (segment_id, attendance_id, session_id, user_id,
             skill_deltas, xp_awarded, idempotency_key)
        VALUES
            (:seg, :att, :ssid, :uid,
             '{}', 10, :ikey)
        RETURNING id
    """), {
        "seg": segment_id,
        "att": attendance_id,
        "ssid": session_id,
        "uid": user_id,
        "ikey": idempotency_key,
    }).scalar()


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_segment_tables_exist():
    """Both new tables must be present after migration 2026_04_21_1100."""
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        for table in ("session_segments", "session_segment_results"):
            row = conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :t
            """), {"t": table}).fetchone()
            assert row is not None, f"Table {table!r} does not exist"


def test_segment_position_unique_per_session():
    """uq_segment_session_position: (session_id, position) must be unique."""
    engine = create_engine(DATABASE_URL)
    with engine.begin() as conn:
        uid = _seed_user(conn)
        sem_id = _seed_semester(conn)
        sess_id = _seed_session(conn, sem_id)

    try:
        with engine.connect() as conn:
            _seed_segment(conn, sess_id, position=1)
            conn.commit()
            sp = conn.begin_nested()
            try:
                _seed_segment(conn, sess_id, position=1)  # duplicate position
                pytest.fail("Duplicate (session_id, position) must be blocked")
            except IntegrityError as e:
                sp.rollback()
                assert "uq_segment_session_position" in str(e), (
                    f"Expected uq_segment_session_position, got: {e}"
                )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM session_segments WHERE session_id = :s"),
                         {"s": sess_id})
            conn.execute(text("DELETE FROM sessions WHERE id = :s"), {"s": sess_id})
            conn.execute(text("DELETE FROM semesters WHERE id = :s"), {"s": sem_id})
            conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": _CI_EMAIL})


def test_segment_result_composite_unique():
    """uq_segment_result_seg_att: (segment_id, attendance_id) must be unique."""
    engine = create_engine(DATABASE_URL)
    with engine.begin() as conn:
        uid = _seed_user(conn)
        sem_id = _seed_semester(conn)
        sess_id = _seed_session(conn, sem_id)
        seg_id = _seed_segment(conn, sess_id)
        att_id = _seed_attendance(conn, uid, sess_id)

    try:
        with engine.connect() as conn:
            _seed_segment_result(conn, seg_id, att_id, sess_id, uid,
                                  idempotency_key="ci_idem_composite_1")
            conn.commit()
            sp = conn.begin_nested()
            try:
                _seed_segment_result(conn, seg_id, att_id, sess_id, uid,
                                      idempotency_key="ci_idem_composite_2")
                pytest.fail("Duplicate (segment_id, attendance_id) must be blocked")
            except IntegrityError as e:
                sp.rollback()
                assert "uq_segment_result_seg_att" in str(e), (
                    f"Expected uq_segment_result_seg_att, got: {e}"
                )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM session_segment_results WHERE session_id = :s"),
                         {"s": sess_id})
            conn.execute(text("DELETE FROM attendance WHERE session_id = :s"), {"s": sess_id})
            conn.execute(text("DELETE FROM session_segments WHERE session_id = :s"),
                         {"s": sess_id})
            conn.execute(text("DELETE FROM sessions WHERE id = :s"), {"s": sess_id})
            conn.execute(text("DELETE FROM semesters WHERE id = :s"), {"s": sem_id})
            conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": _CI_EMAIL})


def test_segment_result_idempotency_key_unique():
    """uq_segment_result_idempotency: duplicate non-null idempotency_key must be blocked."""
    engine = create_engine(DATABASE_URL)
    with engine.begin() as conn:
        uid = _seed_user(conn)
        sem_id = _seed_semester(conn)
        sess_id = _seed_session(conn, sem_id)
        seg_id = _seed_segment(conn, sess_id, position=1)
        seg_id2 = _seed_segment(conn, sess_id, position=2)
        att_id = _seed_attendance(conn, uid, sess_id)

    _ikey = "ci_idem_key_unique_test_pr_c"
    try:
        with engine.connect() as conn:
            _seed_segment_result(conn, seg_id, att_id, sess_id, uid,
                                  idempotency_key=_ikey)
            conn.commit()
            sp = conn.begin_nested()
            try:
                # Different (segment_id, attendance_id) but same idempotency_key
                _seed_segment_result(conn, seg_id2, att_id, sess_id, uid,
                                      idempotency_key=_ikey)
                pytest.fail("Duplicate idempotency_key must be blocked")
            except IntegrityError as e:
                sp.rollback()
                assert "uq_segment_result_idempotency" in str(e), (
                    f"Expected uq_segment_result_idempotency, got: {e}"
                )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM session_segment_results WHERE session_id = :s"),
                         {"s": sess_id})
            conn.execute(text("DELETE FROM attendance WHERE session_id = :s"), {"s": sess_id})
            conn.execute(text("DELETE FROM session_segments WHERE session_id = :s"),
                         {"s": sess_id})
            conn.execute(text("DELETE FROM sessions WHERE id = :s"), {"s": sess_id})
            conn.execute(text("DELETE FROM semesters WHERE id = :s"), {"s": sem_id})
            conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": _CI_EMAIL})


def test_segment_cascade_delete():
    """Deleting a session_segment cascades to its session_segment_results."""
    engine = create_engine(DATABASE_URL)
    with engine.begin() as conn:
        uid = _seed_user(conn)
        sem_id = _seed_semester(conn)
        sess_id = _seed_session(conn, sem_id)
        seg_id = _seed_segment(conn, sess_id)
        att_id = _seed_attendance(conn, uid, sess_id)
        _seed_segment_result(conn, seg_id, att_id, sess_id, uid)

    try:
        with engine.begin() as conn:
            # Verify result exists before delete
            count_before = conn.execute(text(
                "SELECT COUNT(*) FROM session_segment_results WHERE segment_id = :s"
            ), {"s": seg_id}).scalar()
            assert count_before == 1, "Precondition: result row must exist"

            # Delete the segment — CASCADE must remove the result row
            conn.execute(text("DELETE FROM session_segments WHERE id = :s"), {"s": seg_id})

        with engine.connect() as conn:
            count_after = conn.execute(text(
                "SELECT COUNT(*) FROM session_segment_results WHERE segment_id = :s"
            ), {"s": seg_id}).scalar()
            assert count_after == 0, (
                "CASCADE DELETE from session_segments must remove session_segment_results"
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM session_segment_results WHERE session_id = :s"),
                         {"s": sess_id})
            conn.execute(text("DELETE FROM session_segments WHERE session_id = :s"),
                         {"s": sess_id})
            conn.execute(text("DELETE FROM attendance WHERE session_id = :s"), {"s": sess_id})
            conn.execute(text("DELETE FROM sessions WHERE id = :s"), {"s": sess_id})
            conn.execute(text("DELETE FROM semesters WHERE id = :s"), {"s": sem_id})
            conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": _CI_EMAIL})
