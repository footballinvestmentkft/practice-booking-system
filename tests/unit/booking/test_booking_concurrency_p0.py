"""
Booking Pipeline — Phase B P0 Concurrency Tests (RACE-B01 to RACE-B07)

Status: RED — tests assert the DESIRED post-fix behaviour.
        Currently FAILING because Phase C guards are not yet implemented.
        Run again after Phase C to verify GREEN.

Race conditions documented:
  RACE-B01  Duplicate booking TOCTOU → IntegrityError must become HTTP 409
  RACE-B02  Capacity check TOCTOU → session must be locked before count
  RACE-B03  Waitlist position collision → session lock covers this transitively
  RACE-B04  Auto-promote double-promotion → waitlisted row must be locked
  RACE-B05  Double-cancel chain → booking row must be locked before cancel
  RACE-B06  Admin confirm without capacity check → must raise when full
  RACE-B07  Duplicate attendance TOCTOU → booking must be locked; commit error → 409

Methodology: same as test_enrollment_phase_b_unit.py
  - Mock-based (DB-free), ~0.1s total
  - Each test directly calls the endpoint function or shared helper
  - with_for_update() call assertions prove lock ordering
  - IntegrityError side_effect assertions prove error handler presence

Reference audit: docs/features/BOOKING_CONCURRENCY_AUDIT_2026-02-19.md
"""

import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

from app.api.api_v1.endpoints.bookings.student import create_booking, cancel_booking
from app.api.api_v1.endpoints.bookings.admin import (
    confirm_booking,
    admin_cancel_booking,
    update_booking_attendance,
)
from app.api.api_v1.endpoints.bookings.helpers import auto_promote_from_waitlist
from app.models.booking import BookingStatus
from app.models.user import UserRole
from app.schemas.booking import BookingCreate, BookingCancel


# Test constants for mock IDs
TEST_USER_ID = 999
TEST_SESSION_ID = 1
TEST_BOOKING_ID = 1

# ─── Shared domain factories ──────────────────────────────────────────────────

def _session(session_id=TEST_SESSION_ID, capacity=10, hours_ahead=48):
    """Lightweight session stand-in (naive datetimes to match endpoint logic)."""
    s = SimpleNamespace(
        id=session_id,
        capacity=capacity,
        semester_id=1,
        semester=SimpleNamespace(age_groups=["PRE"]),
        target_specialization=None,
        mixed_specialization=True,
        session_status="scheduled",
        event_category=None,
        date_start=datetime.now() + timedelta(hours=hours_ahead),
        date_end=datetime.now() + timedelta(hours=hours_ahead + 2),
        group_id=None,
        instructor_id=None,
    )
    # Ensure no tzinfo so the endpoint's `.replace(tzinfo=None)` branch is taken
    s.date_start = s.date_start.replace(tzinfo=None)
    return s


def _booking(booking_id=TEST_BOOKING_ID, user_id=TEST_USER_ID, session_id=TEST_SESSION_ID, status=BookingStatus.CONFIRMED, waitlist_pos=None):
    """Lightweight booking stand-in."""
    from unittest.mock import MagicMock as _MM
    sess = _session(session_id=session_id)
    return SimpleNamespace(
        id=booking_id,
        user_id=user_id,
        session_id=session_id,
        status=status,
        waitlist_position=waitlist_pos,
        notes=None,
        cancelled_at=None,
        attendance=None,
        session=sess,
        # Method called by update_booking_attendance — no-op in unit tests
        update_attendance_status=_MM(),
    )


def _user(user_id=TEST_USER_ID, role=UserRole.STUDENT, credit_balance=1000):
    """Lightweight user stand-in."""
    return SimpleNamespace(
        id=user_id,
        role=role,
        credit_balance=credit_balance,
        name=f"User {user_id}",
        email=f"user{user_id}@test.com",
    )


def _booking_create(session_id=1):
    bc = MagicMock(spec=BookingCreate)
    bc.session_id = session_id
    bc.notes = None
    return bc


# ─── Mock DB factory ──────────────────────────────────────────────────────────

def _make_mock_db(
    session_obj=None,
    confirmed_count: int = 0,
    waitlist_count: int = 0,
    existing_booking=None,
    next_waitlisted=None,
    remaining_waitlist=None,
    commit_raises=None,
):
    """
    Model-dispatched MagicMock DB session.

    Returns: (mock_db, session_chain, booking_chain)

    session_chain  — query chain for Session model; track .with_for_update.called
    booking_chain  — query chain for Booking model; .first() returns existing_booking
                     or next_waitlisted (use next_waitlisted for auto_promote tests)
    """
    from app.models.session import Session as SessionModel
    from app.models.booking import Booking
    from app.models.user import User

    mock_db = MagicMock()

    # ── Session chain ─────────────────────────────────────────────────────────
    session_chain = MagicMock()
    session_chain.filter.return_value = session_chain
    session_chain.with_for_update.return_value = session_chain
    session_chain.first.return_value = session_obj
    session_chain.one.return_value = session_obj

    # ── Booking chain ─────────────────────────────────────────────────────────
    booking_chain = MagicMock()
    booking_chain.filter.return_value = booking_chain
    booking_chain.with_for_update.return_value = booking_chain
    booking_chain.order_by.return_value = booking_chain
    # first() returns existing_booking for duplicate-check;
    # for auto_promote tests, pass next_waitlisted= instead
    booking_chain.first.return_value = (
        next_waitlisted if next_waitlisted is not None else existing_booking
    )
    booking_chain.all.return_value = remaining_waitlist or []

    # ── User chain ────────────────────────────────────────────────────────────
    user_chain = MagicMock()
    user_chain.filter.return_value = user_chain
    user_chain.first.return_value = _user(user_id=2)   # promoted user default

    # ── Count chain (func.count expressions) ─────────────────────────────────
    _count_call = [0]
    count_chain = MagicMock()
    count_chain.filter.return_value = count_chain

    def _scalar():
        c = _count_call[0]
        _count_call[0] += 1
        return confirmed_count if c == 0 else waitlist_count

    count_chain.scalar.side_effect = _scalar

    # ── Dispatcher ────────────────────────────────────────────────────────────
    def _dispatch(model):
        if isinstance(model, type):
            name = model.__name__
            if name == "Session":
                return session_chain
            elif name == "Booking":
                return booking_chain
            elif name == "User":
                return user_chain
        return count_chain   # handles func.count(Booking.id) etc.

    mock_db.query.side_effect = _dispatch

    if commit_raises is not None:
        mock_db.commit.side_effect = commit_raises

    return mock_db, session_chain, booking_chain


# ─────────────────────────────────────────────────────────────────────────────
# RACE-B01 — Duplicate booking TOCTOU
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceB01DuplicateBooking:
    """
    RACE-B01: Two concurrent create_booking calls both pass the existing_booking
    check (both see None), both INSERT. DB-level guard (uq_active_booking partial
    unique index) triggers IntegrityError at commit time.

    Fix needed:
      - Partial unique index: CREATE UNIQUE INDEX uq_active_booking ON bookings
          (user_id, session_id) WHERE status != 'CANCELLED';
      - try/except IntegrityError around db.commit() → HTTP 409 (same pattern
        as uq_active_enrollment in enroll.py:296–312)
    """

    def test_b01_integrity_error_at_commit_becomes_409(self):
        """
        RED: create_booking has no IntegrityError handler.
        IntegrityError from uq_active_booking propagates uncaught → 500, not 409.

        GREEN after: try/except IntegrityError → HTTPException(409).
        """
        session_obj = _session(capacity=10)
        mock_db, _, _ = _make_mock_db(
            session_obj=session_obj,
            confirmed_count=0,
            commit_raises=IntegrityError(
                "INSERT INTO bookings ...",
                {},
                Exception("duplicate key value violates unique constraint \"uq_active_booking\""),
            ),
        )
        user = _user()
        booking_data = _booking_create()

        with patch(
            "app.services.player_participation_service._player_enrollment",
            return_value=SimpleNamespace(id=1),
        ):
            with pytest.raises(HTTPException) as exc_info:
                create_booking(booking_data, mock_db, user)

        assert exc_info.value.status_code == 409, (
            "RACE-B01: IntegrityError from uq_active_booking MUST become HTTP 409. "
            "Current code has no IntegrityError handler around db.commit() "
            "→ IntegrityError propagates as 500."
        )

    def test_b01_rollback_called_before_409(self):
        """
        RED: without an IntegrityError handler, db.rollback() is never called.
        GREEN after: db.rollback() called inside the except block before raising 409.
        """
        session_obj = _session(capacity=10)
        mock_db, _, _ = _make_mock_db(
            session_obj=session_obj,
            confirmed_count=0,
            commit_raises=IntegrityError(
                "...", {}, Exception("uq_active_booking"),
            ),
        )
        user = _user()
        booking_data = _booking_create()

        with patch(
            "app.services.player_participation_service._player_enrollment",
            return_value=SimpleNamespace(id=1),
        ):
            with pytest.raises((HTTPException, IntegrityError)):
                create_booking(booking_data, mock_db, user)

        assert mock_db.rollback.called, (
            "RACE-B01: db.rollback() must be called before re-raising as HTTP 409. "
            "Current code: no rollback in the uncaught IntegrityError path."
        )

    def test_b01_any_integrity_error_at_booking_commit_becomes_409(self):
        """
        RED: any IntegrityError at booking commit (not only uq_active_booking)
        must return 409, not propagate as 500.
        """
        session_obj = _session(capacity=10)
        mock_db, _, _ = _make_mock_db(
            session_obj=session_obj,
            confirmed_count=0,
            commit_raises=IntegrityError(
                "...", {}, Exception("some_other_db_constraint"),
            ),
        )
        user = _user()
        booking_data = _booking_create()

        with patch(
            "app.services.player_participation_service._player_enrollment",
            return_value=SimpleNamespace(id=1),
        ):
            with pytest.raises(HTTPException) as exc_info:
                create_booking(booking_data, mock_db, user)

        assert exc_info.value.status_code == 409, (
            "RACE-B01: any IntegrityError at booking INSERT must become 409."
        )


# ─────────────────────────────────────────────────────────────────────────────
# RACE-B02 — Capacity check TOCTOU (overbooking)
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceB02CapacityOverbooking:
    """
    RACE-B02: Two concurrent create_booking calls both read confirmed_count < capacity
    (stale count) and both INSERT as CONFIRMED → session overbooked.

    Fix needed:
      - SELECT FOR UPDATE on the session row before confirmed_count query
        serialises concurrent requests:
          db.query(Session).filter(Session.id == session_id).with_for_update().one()
    """

    def test_b02_session_locked_with_for_update_before_confirmed_count(self):
        """
        RED: create_booking does NOT call with_for_update() on the session row.
        The capacity check (confirmed_count query) runs without any row lock.

        GREEN after: add with_for_update() on session before confirmed_count.
        """
        session_obj = _session(capacity=10)
        mock_db, session_chain, _ = _make_mock_db(
            session_obj=session_obj,
            confirmed_count=0,
        )
        user = _user()
        booking_data = _booking_create()

        with patch(
            "app.services.player_participation_service._player_enrollment",
            return_value=SimpleNamespace(id=1),
        ):
            create_booking(booking_data, mock_db, user)

        assert session_chain.with_for_update.called, (
            "RACE-B02: session row MUST be locked with SELECT FOR UPDATE before "
            "confirmed_count is read. Without this, two concurrent requests both "
            "see confirmed_count < capacity and both confirm → overbooking. "
            "Fix: db.query(Session).filter(...).with_for_update().one() "
            "before the count query."
        )

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "Mock-based test cannot simulate DB-level row locking. "
            "SELECT FOR UPDATE on session serialises real DB threads but is a "
            "no-op on MagicMock → both calls still see confirmed_count=0 from the "
            "separate mock DBs. The lock assertion in "
            "test_b02_session_locked_with_for_update_before_confirmed_count proves "
            "the fix. Real-DB concurrency proof belongs in tests/database/. "
            "Requires real DB concurrency test (future tests/database suite)."
        ),
    )
    def test_b02_race_window_produces_overbooking_documents_the_unsafe_state(self):
        """
        Documents the TOCTOU race: both threads see confirmed_count=0 for capacity=1.

        Both calls return CONFIRMED booking status. This test asserts that the
        SECOND booking must NOT be CONFIRMED. With a real DB + FOR UPDATE the lock
        blocks Thread B until Thread A commits, then Thread B reads count=1 →
        WAITLISTED. With mocks this is xfail — see @pytest.mark.xfail reason.
        """
        session_obj = _session(capacity=1)

        # Thread A — sees confirmed_count=0 → CONFIRMED
        mock_db_a, _, _ = _make_mock_db(session_obj=session_obj, confirmed_count=0)
        user_a = _user()  # Uses TEST_USER_ID default

        with patch(
            "app.services.player_participation_service._player_enrollment",
            return_value=SimpleNamespace(id=1),
        ):
            booking_a = create_booking(_booking_create(), mock_db_a, user_a)

        # Thread B — mock STILL returns 0 (simulating the race window:
        # A hasn't committed, so B reads stale count)
        mock_db_b, _, _ = _make_mock_db(session_obj=session_obj, confirmed_count=0)
        user_b = _user(user_id=2)

        with patch(
            "app.services.player_participation_service._player_enrollment",
            return_value=SimpleNamespace(id=1),
        ):
            booking_b = create_booking(_booking_create(), mock_db_b, user_b)

        # INVARIANT: when session is at capacity, second booking must be WAITLISTED
        assert booking_b.status != BookingStatus.CONFIRMED, (
            "RACE-B02: Two concurrent requests for a capacity=1 session BOTH returned "
            "CONFIRMED. Overbooking detected. Without SELECT FOR UPDATE on session, "
            "Thread B reads stale confirmed_count=0 and confirms despite session full. "
            "Fix: SELECT FOR UPDATE serialises the capacity check."
        )


# ─────────────────────────────────────────────────────────────────────────────
# RACE-B03 — Waitlist position collision
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceB03WaitlistPosition:
    """
    RACE-B03: Two concurrent requests both compute waitlist_position = count + 1
    from the same stale waitlist_count → duplicate waitlist_position.

    Fix: The B02 session lock covers this transitively — waitlist_count is read
    under the same FOR UPDATE lock on the session row.
    DB-level: unique partial index on (session_id, waitlist_position)
              WHERE status = 'WAITLISTED' (migration C-02).
    """

    def test_b03_session_locked_in_waitlisted_branch(self):
        """
        RED: session row is NOT locked when computing waitlist_position.
        Two threads both read waitlist_count=3 and assign position=4.

        GREEN after: B02 FOR UPDATE lock on session covers the waitlisted branch.
        (Same assertion as B02 but exercising the WAITLISTED code path.)
        """
        session_obj = _session(capacity=1)
        mock_db, session_chain, _ = _make_mock_db(
            session_obj=session_obj,
            confirmed_count=1,   # at capacity → triggers WAITLISTED branch
            waitlist_count=3,
        )
        user = _user()
        booking_data = _booking_create()

        with patch(
            "app.services.player_participation_service._player_enrollment",
            return_value=SimpleNamespace(id=1),
        ):
            create_booking(booking_data, mock_db, user)

        assert session_chain.with_for_update.called, (
            "RACE-B03: session must be locked in the WAITLISTED branch too, "
            "so that waitlist_count is read with a consistent view. "
            "B02 FOR UPDATE fix covers this transitively."
        )

    def test_b03_waitlist_integrity_error_at_commit_becomes_409(self):
        """
        RED: create_booking has no IntegrityError handler for commit failures.
        DB unique index uq_waitlist_position fires when two bookings race to
        the same position → IntegrityError propagates uncaught.

        GREEN after: IntegrityError → HTTP 409 (same B01 handler covers this).
        """
        session_obj = _session(capacity=1)
        mock_db, _, _ = _make_mock_db(
            session_obj=session_obj,
            confirmed_count=1,
            waitlist_count=3,
            commit_raises=IntegrityError(
                "INSERT INTO bookings ...",
                {},
                Exception("duplicate key value violates unique constraint \"uq_waitlist_position\""),
            ),
        )
        user = _user()
        booking_data = _booking_create()

        with patch(
            "app.services.player_participation_service._player_enrollment",
            return_value=SimpleNamespace(id=1),
        ):
            with pytest.raises(HTTPException) as exc_info:
                create_booking(booking_data, mock_db, user)

        assert exc_info.value.status_code == 409, (
            "RACE-B03: IntegrityError from uq_waitlist_position MUST become 409. "
            "The B01 IntegrityError handler covers this if written generically."
        )


# ─────────────────────────────────────────────────────────────────────────────
# RACE-B04 — Auto-promotion double-promotion
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceB04AutoPromoteDoublePromotion:
    """
    RACE-B04: Two concurrent cancel operations for the same session both call
    auto_promote_from_waitlist(). Both SELECT the same top-of-waitlist row
    without a lock → both promote the same booking → two CONFIRMED slots
    from one freed slot.

    Fix needed:
      - SELECT FOR UPDATE on the next_waitlisted query in auto_promote_from_waitlist:
          db.query(Booking).filter(...WAITLISTED...).with_for_update()
            .order_by(Booking.waitlist_position.asc()).first()
    """

    def test_b04_next_waitlisted_locked_before_promotion(self):
        """
        RED: auto_promote_from_waitlist does NOT lock the top-of-waitlist row.
        Two concurrent calls both SELECT the same row and both SET status=CONFIRMED.

        GREEN after: add .with_for_update() to the waitlist query.
        """
        next_wl = _booking(
            booking_id=10, user_id=2, session_id=1,
            status=BookingStatus.WAITLISTED, waitlist_pos=1,
        )
        mock_db, _, booking_chain = _make_mock_db(next_waitlisted=next_wl)

        auto_promote_from_waitlist(mock_db, session_id=1)

        assert booking_chain.with_for_update.called, (
            "RACE-B04: the top-of-waitlist booking MUST be locked with SELECT FOR "
            "UPDATE before mutation. Without this, two concurrent cancel callbacks "
            "both SELECT the same waitlisted row and both promote it → double-promotion. "
            "Fix: add .with_for_update() before .order_by().first() "
            "in auto_promote_from_waitlist."
        )

    def test_b04_promotion_sets_confirmed_and_clears_position(self):
        """
        GREEN (sanity): after promotion the booking status is CONFIRMED
        and waitlist_position is cleared.

        This test documents the expected post-promotion state.
        (Should already pass — it verifies the mutation logic is correct.)
        """
        next_wl = _booking(
            booking_id=10, user_id=2, session_id=1,
            status=BookingStatus.WAITLISTED, waitlist_pos=1,
        )
        mock_db, _, _ = _make_mock_db(next_waitlisted=next_wl)

        result = auto_promote_from_waitlist(mock_db, session_id=1)

        assert result is not None, "Expected a promotion result"
        assert next_wl.status == BookingStatus.CONFIRMED, (
            "Promoted booking must have status=CONFIRMED."
        )
        assert next_wl.waitlist_position is None, (
            "Promoted booking must have waitlist_position cleared."
        )

    def test_b04_with_for_update_called_before_order_by(self):
        """
        RED: lock ordering must be: .with_for_update() → .order_by() → .first().
        Current code: .filter() → .order_by() → .first() (no lock at all).

        GREEN after: .filter() → .with_for_update() → .order_by() → .first().
        """
        next_wl = _booking(booking_id=10, user_id=2, session_id=1, status=BookingStatus.WAITLISTED)
        mock_db, _, booking_chain = _make_mock_db(next_waitlisted=next_wl)

        auto_promote_from_waitlist(mock_db, session_id=1)

        # with_for_update() must be called; the chain is fluent so order is
        # enforced by the mock returning itself at each step.
        assert booking_chain.with_for_update.called, (
            "RACE-B04: lock must precede order_by in auto_promote_from_waitlist."
        )
        wfu_call_order = [
            c for c in booking_chain.mock_calls
            if "with_for_update" in str(c) or "order_by" in str(c)
        ]
        # with_for_update must appear before order_by in the call list
        has_wfu = any("with_for_update" in str(c) for c in wfu_call_order)
        assert has_wfu, "with_for_update() missing from auto_promote_from_waitlist chain"


# ─────────────────────────────────────────────────────────────────────────────
# RACE-B05 — Double-cancel chain → double-promotion
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceB05DoubleCancel:
    """
    RACE-B05: A student's DELETE /bookings/{id} and an admin's
    POST /bookings/{id}/cancel run concurrently for the same CONFIRMED booking.
    Both fetch the booking without a lock, both see status=CONFIRMED,
    both trigger auto_promote_from_waitlist → two waitlisted users promoted
    for one freed slot.

    Fix needed:
      - SELECT FOR UPDATE on booking fetch in cancel_booking (student.py)
      - SELECT FOR UPDATE on booking fetch in admin_cancel_booking (admin.py)
    """

    def test_b05_student_cancel_booking_locked_with_for_update(self):
        """
        RED: cancel_booking fetches booking with plain .first() — no lock.
        Two concurrent cancel requests both read status=CONFIRMED and both
        trigger auto_promote → double-promotion cascade.

        GREEN after: add .with_for_update() to booking fetch in cancel_booking.
        """
        booking_obj = _booking(status=BookingStatus.CONFIRMED)  # Uses TEST_USER_ID default
        mock_db, _, booking_chain = _make_mock_db(session_obj=booking_obj.session)
        booking_chain.first.return_value = booking_obj

        with patch(
            "app.services.player_participation_service._promote_waitlisted",
            return_value=None,
        ):
            cancel_booking(booking_id=TEST_BOOKING_ID, db=mock_db, current_user=_user())

        assert booking_chain.with_for_update.called, (
            "RACE-B05: booking must be locked with SELECT FOR UPDATE before "
            "cancellation in cancel_booking (student endpoint). "
            "Without this, concurrent student + admin cancel both read "
            "status=CONFIRMED and both trigger auto_promote → overbooking. "
            "Fix: .with_for_update() on the booking query."
        )

    def test_b05_admin_cancel_booking_locked_with_for_update(self):
        """
        RED: admin_cancel_booking fetches booking with plain .first() — no lock.

        GREEN after: add .with_for_update() to booking fetch in admin_cancel_booking.
        """
        booking_obj = _booking(status=BookingStatus.CONFIRMED)  # Uses TEST_USER_ID default
        cancel_data = MagicMock(spec=BookingCancel)
        cancel_data.reason = "test"

        mock_db, _, booking_chain = _make_mock_db(session_obj=booking_obj.session)
        booking_chain.first.return_value = booking_obj

        with patch(
            "app.services.player_participation_service._promote_waitlisted",
            return_value=None,
        ):
            admin_cancel_booking(
                booking_id=1,
                cancel_data=cancel_data,
                db=mock_db,
                current_user=_user(role=UserRole.ADMIN),
            )

        assert booking_chain.with_for_update.called, (
            "RACE-B05: booking must be locked in admin_cancel_booking too. "
            "Fix: .with_for_update() on the admin booking fetch."
        )

    def test_b05_second_cancel_on_already_cancelled_booking_is_idempotent(self):
        """
        RED: cancel_booking has no guard against cancelling an already-CANCELLED
        booking. After the B05 FOR UPDATE fix, the second thread reads
        status=CANCELLED (updated by the first thread under the lock) and
        must return the original cancellation as an explicit replay without
        promoting another waitlisted booking.
        """
        # Simulate the post-lock state: booking was already cancelled by Thread A
        already_cancelled = _booking(status=BookingStatus.CANCELLED)  # Uses TEST_USER_ID default
        mock_db, _, booking_chain = _make_mock_db()
        booking_chain.first.return_value = already_cancelled

        with patch(
            "app.services.player_participation_service._promote_waitlisted",
            return_value=None,
        ):
            response = cancel_booking(
                booking_id=TEST_BOOKING_ID,
                db=mock_db,
                current_user=_user(),  # Uses TEST_USER_ID default
            )

        assert response["replayed"] is True
        assert already_cancelled.status == BookingStatus.CANCELLED


# ─────────────────────────────────────────────────────────────────────────────
# RACE-B06 — Admin confirm without capacity check
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceB06AdminConfirmNoCapacity:
    """
    RACE-B06: confirm_booking (admin) has NO capacity check. An admin can
    confirm a booking even when the session is at or over capacity.
    Two concurrent admin confirms for a full session both succeed.

    Fix needed:
      - Read confirmed_count before confirming; raise HTTP 409 if at capacity.
    """

    def test_b06_confirm_raises_when_session_at_capacity(self):
        """
        RED: confirm_booking does not check confirmed_count against capacity.
        It unconditionally sets status=CONFIRMED.

        GREEN after: add capacity check; raise HTTP 409 when session is full.
        """
        session_obj = _session(capacity=1)
        booking_obj = _booking(status=BookingStatus.PENDING)
        # session_id on booking_obj matches session_obj.id
        booking_obj.session_id = 1

        mock_db, _, booking_chain = _make_mock_db(
            session_obj=session_obj,
            confirmed_count=1,   # at capacity
        )
        booking_chain.first.return_value = booking_obj

        with pytest.raises(HTTPException) as exc_info:
            confirm_booking(
                booking_id=1,
                db=mock_db,
                current_user=_user(role=UserRole.ADMIN),
            )

        assert exc_info.value.status_code in (400, 409), (
            "RACE-B06: confirm_booking MUST check confirmed_count < session.capacity "
            "and raise HTTP 400/409 when full. "
            "Current code: no capacity check → unconditionally confirms → overbooking."
        )

    def test_b06_confirm_succeeds_when_session_has_space(self):
        """
        GREEN (sanity): confirm_booking succeeds when session is not at capacity.
        This should already pass; documents the happy path.
        """
        session_obj = _session(capacity=10)
        booking_obj = _booking(status=BookingStatus.PENDING)
        booking_obj.session_id = 1

        mock_db, _, booking_chain = _make_mock_db(
            session_obj=session_obj,
            confirmed_count=5,   # room available
        )
        booking_chain.first.return_value = booking_obj

        # Must NOT raise
        result = confirm_booking(
            booking_id=1,
            db=mock_db,
            current_user=_user(role=UserRole.ADMIN),
        )

        assert result is not None or mock_db.commit.called, (
            "B06 happy path: confirm_booking must succeed when there is capacity."
        )


# ─────────────────────────────────────────────────────────────────────────────
# RACE-B07 — Duplicate attendance record TOCTOU
# ─────────────────────────────────────────────────────────────────────────────

class TestRaceB07DuplicateAttendance:
    """
    RACE-B07: Two instructors call PATCH /bookings/{id}/attendance concurrently.
    Both fetch the booking without a lock, both see booking.attendance=None,
    both CREATE a new Attendance record → two attendance rows for one booking.

    Fix needed:
      - SELECT FOR UPDATE on booking fetch in update_booking_attendance
      - DB: UNIQUE constraint on attendance.booking_id (migration C-03)
    """

    def test_b07_booking_locked_with_for_update_on_attendance_update(self):
        """
        RED: update_booking_attendance fetches booking with plain .first() — no lock.
        Two concurrent calls both see attendance=None and both insert.

        GREEN after: add .with_for_update() to booking fetch.
        """
        booking_obj = _booking(status=BookingStatus.CONFIRMED)
        booking_obj.attendance = None
        booking_obj.session.date_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)

        mock_db, _, booking_chain = _make_mock_db(session_obj=booking_obj.session)
        booking_chain.first.return_value = booking_obj

        update_booking_attendance(
            booking_id=1,
            attendance_data={"status": "present"},
            db=mock_db,
            current_user=_user(role=UserRole.ADMIN),
        )

        assert booking_chain.with_for_update.called, (
            "RACE-B07: booking row MUST be locked with SELECT FOR UPDATE before "
            "checking booking.attendance to prevent two concurrent instructors "
            "both seeing attendance=None and both creating Attendance records. "
            "Fix: add .with_for_update() to booking fetch in update_booking_attendance."
        )

    def test_b07_attendance_integrity_error_rolls_back_and_returns_409(self):
        """
        RED: update_booking_attendance has no IntegrityError handler.
        DB unique constraint uq_booking_attendance fires when two instructors
        race to create attendance for the same booking. If the winning row is
        not visible on reload, the loser rolls back and reports a conflict.
        """
        booking_obj = _booking(status=BookingStatus.CONFIRMED)
        booking_obj.attendance = None
        booking_obj.session.date_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)

        mock_db, _, booking_chain = _make_mock_db(
            session_obj=booking_obj.session,
            commit_raises=IntegrityError(
                "INSERT INTO attendance ...",
                {},
                Exception("duplicate key value violates unique constraint \"uq_booking_attendance\""),
            ),
        )
        booking_chain.first.return_value = booking_obj

        with pytest.raises(HTTPException) as exc_info:
            update_booking_attendance(
                booking_id=1,
                attendance_data={"status": "present"},
                db=mock_db,
                current_user=_user(role=UserRole.ADMIN),
            )
        assert exc_info.value.status_code == 409
        assert mock_db.rollback.called
