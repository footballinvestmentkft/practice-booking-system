"""
Phase 6.1 — Critical Attack Surface Tests
==========================================

Static analysis findings (2026-04-17):
  - IDOR on enrollments: triple-filter(id, user_id, is_active) prevents cross-user access
  - IDOR on session booking cancel: filter(user_id, session_id) prevents cross-user access
  - Admin gate: _admin_guard() applied uniformly on all /admin/* routes
  - SD gate: get_current_sport_director_user_web role check fires before route body
  - Rate limiting (IP): sliding window functional; blocks at 100 req/60s per IP
  - Rate limiting (user): JWT decode is a TODO stub → per-user limit never fires ⚠️
  - Concurrent enrollment: atomic SQL + IntegrityError fallback prevents duplicate enroll

These tests PROVE the defences fire; they do not re-implement the logic.

IDOR-01  User B cannot withdraw User A's enrollment → 303 + Enrollment not found
IDOR-02  User B cannot cancel User A's session booking → 303 + booking_not_found
PRIVESC-01  STUDENT probing 6 admin routes → all 403
PRIVESC-02  get_current_sport_director_user_web rejects STUDENT role → HTTPException(403)
RATELIMIT-01  Sliding window blocks at limit (unit test of algorithm)
RATELIMIT-02  JWT decode TODO stub → per-user limit non-functional (documented finding)
RACE-01  5 concurrent enrollment POSTs → exactly 1 success, credit deducted once
"""
import asyncio
import uuid
import pytest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db, SessionLocal
from app.dependencies import get_current_user_web, get_current_sport_director_user_web
from app.middleware.security import RateLimitMiddleware
from app.models.booking import Booking, BookingStatus
from app.models.license import UserLicense
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.session import Session as SessionModel, SessionType
from app.models.user import User, UserRole
from app.core.security import get_password_hash


# ── SAVEPOINT-isolated DB fixture ─────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db():
    """PostgreSQL session with per-test SAVEPOINT isolation."""
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSession()
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, txn):
        if txn.nested and not txn._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_student(db: Session) -> User:
    u = User(
        email=f"atk-{uuid.uuid4().hex[:8]}@lfa.com",
        name=f"ATK Student {uuid.uuid4().hex[:4]}",
        password_hash=get_password_hash("Test1234!"),
        role=UserRole.STUDENT,
        is_active=True,
        onboarding_completed=True,
        credit_balance=0,
        payment_verified=True,
    )
    db.add(u)
    db.flush()
    return u


def _db_override(db: Session):
    def _inner():
        yield db
    return _inner


def _now_bp() -> datetime:
    return datetime.now(ZoneInfo("Europe/Budapest")).replace(tzinfo=None)


# ── IDOR Tests ────────────────────────────────────────────────────────────────

class TestIDOR:

    def test_idor_01_cannot_withdraw_other_users_enrollment(self, test_db: Session):
        """IDOR-01: User B cannot withdraw User A's enrollment.

        Attack vector: User B knows User A's enrollment_id (sequential IDs are guessable).
        User B POSTs /semesters/withdraw-enrollment with enrollment_id=A's id.

        Guard: programs.py:264-268 — triple filter(id, user_id=B.id, is_active=True)
        returns None when user_id doesn't match → redirect with "Enrollment not found".

        Asserts:
          - 303 redirect (guard fires, not 500 or 200)
          - "Enrollment+not+found" in location (not withdrawn)
          - User A's enrollment is still active in DB
        """
        user_a = _make_student(test_db)
        user_b = _make_student(test_db)

        sem = Semester(
            code=f"IDOR01-{uuid.uuid4().hex[:8].upper()}",
            name="IDOR-01 Test Semester",
            semester_category=SemesterCategory.MINI_SEASON,
            status=SemesterStatus.ONGOING,
            start_date=date(2027, 1, 1),
            end_date=date(2027, 6, 30),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        test_db.add(sem)
        test_db.flush()

        lic_a = UserLicense(
            user_id=user_a.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            is_active=True,
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(lic_a)
        test_db.flush()

        enrollment_a = SemesterEnrollment(
            user_id=user_a.id,
            semester_id=sem.id,
            user_license_id=lic_a.id,
            request_status=EnrollmentStatus.APPROVED,
            is_active=True,
        )
        test_db.add(enrollment_a)
        test_db.flush()

        app.dependency_overrides[get_db] = _db_override(test_db)
        app.dependency_overrides[get_current_user_web] = lambda: user_b  # attacker
        client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})

        try:
            resp = client.post(
                "/semesters/withdraw-enrollment",
                data={"enrollment_id": str(enrollment_a.id)},
                follow_redirects=False,
            )
            assert resp.status_code == 303, (
                f"IDOR-01: expected 303, got {resp.status_code}"
            )
            loc = resp.headers.get("location", "")
            assert "Enrollment+not+found" in loc or "error" in loc, (
                f"IDOR-01: expected error in location, got '{loc}'\n"
                f"If empty or success: User B may have withdrawn User A's enrollment — IDOR."
            )

            # User A's enrollment must still be active
            test_db.expire_all()
            enrollment_check = test_db.query(SemesterEnrollment).filter_by(
                id=enrollment_a.id
            ).first()
            assert enrollment_check is not None and enrollment_check.is_active is True, (
                "IDOR-01: User A's enrollment was modified — cross-user withdrawal SUCCEEDED."
            )
        finally:
            app.dependency_overrides.clear()

    def test_idor_02_cannot_cancel_other_users_session_booking(self, test_db: Session):
        """IDOR-02: User B cannot cancel User A's session booking via web route.

        Attack vector: User B knows the session_id where User A has a booking.
        User B POSTs /sessions/cancel/{session_id}.

        Guard: sessions.py:278-281 — Booking.filter(user_id=B.id, session_id) → None
        → redirect with error=booking_not_found (User A's booking is untouched).

        Asserts:
          - 303 redirect with booking_not_found or error in location
          - User A's booking is still CONFIRMED in DB
        """
        user_a = _make_student(test_db)
        user_b = _make_student(test_db)

        sem = Semester(
            code=f"IDOR02-{uuid.uuid4().hex[:8].upper()}",
            name="IDOR-02 Test Semester",
            semester_category=SemesterCategory.MINI_SEASON,
            status=SemesterStatus.ONGOING,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=90),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        test_db.add(sem)
        test_db.flush()

        now = _now_bp()
        session_obj = SessionModel(
            title="IDOR-02 Target Session",
            semester_id=sem.id,
            session_type=SessionType.on_site,
            date_start=now + timedelta(hours=24),
            date_end=now + timedelta(hours=25),
        )
        test_db.add(session_obj)
        test_db.flush()

        booking_a = Booking(
            user_id=user_a.id,
            session_id=session_obj.id,
            status=BookingStatus.CONFIRMED,
        )
        test_db.add(booking_a)
        test_db.flush()

        app.dependency_overrides[get_db] = _db_override(test_db)
        app.dependency_overrides[get_current_user_web] = lambda: user_b  # attacker
        client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})

        try:
            resp = client.post(
                f"/sessions/cancel/{session_obj.id}",
                follow_redirects=False,
            )
            assert resp.status_code == 303, (
                f"IDOR-02: expected 303, got {resp.status_code}"
            )
            loc = resp.headers.get("location", "")
            assert "booking_not_found" in loc or "error" in loc, (
                f"IDOR-02: expected booking_not_found or error in location, got '{loc}'\n"
                f"If success: User B may have cancelled User A's booking — IDOR."
            )

            # User A's booking must still exist and be CONFIRMED
            test_db.expire_all()
            booking_check = test_db.query(Booking).filter_by(id=booking_a.id).first()
            assert booking_check is not None, (
                "IDOR-02: User A's booking was deleted — cross-user cancel SUCCEEDED."
            )
            assert booking_check.status == BookingStatus.CONFIRMED, (
                f"IDOR-02: booking status changed to {booking_check.status} — "
                "cross-user cancel SUCCEEDED."
            )
        finally:
            app.dependency_overrides.clear()


# ── Vertical Privilege Escalation Tests ───────────────────────────────────────

class TestPrivilegeEscalation:

    def test_privesc_01_student_probes_admin_routes_all_403(self, test_db: Session):
        """PRIVESC-01: STUDENT probing 6 admin routes → all 403.

        Comprehensive coverage: verifies _admin_guard() is uniformly applied
        across GET and POST admin routes beyond the single route in RBAC-01.

        Routes probed:
          GET  /admin/users            — user management
          GET  /admin/semesters        — semester list
          GET  /admin/enrollments      — enrollment management
          GET  /admin/invitation-codes — invitation code management
          POST /admin/users/create     — user creation (guard fires before body parse)
          GET  /admin/payments         — payment management

        Asserts: every response.status_code == 403.
        """
        student = _make_student(test_db)

        app.dependency_overrides[get_db] = _db_override(test_db)
        app.dependency_overrides[get_current_user_web] = lambda: student
        client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})

        # (method, path, body) — POST routes need valid form data so FastAPI
        # body validation passes and the auth guard is actually reached.
        admin_routes = [
            ("GET",  "/admin/users",            {}),
            ("GET",  "/admin/semesters",         {}),
            ("GET",  "/admin/enrollments",       {}),
            ("GET",  "/admin/invitation-codes",  {}),
            ("GET",  "/admin/payments",          {}),
            # Valid form body so 422 (body validation) doesn't mask 403 (auth guard)
            ("POST", "/admin/users/create", {
                "name": "Probe User",
                "email": f"probe-{uuid.uuid4().hex[:6]}@lfa.com",
                "role": "student",
                "password": "Test1234!",
                "date_of_birth": "2000-01-01",
            }),
        ]

        try:
            for method, route, body in admin_routes:
                resp = client.get(route) if method == "GET" else client.post(route, data=body)
                assert resp.status_code == 403, (
                    f"PRIVESC-01: {method} {route} as STUDENT → expected 403, "
                    f"got {resp.status_code}\n"
                    f"_admin_guard() missing or bypassed on this route — "
                    f"vertical privilege escalation POSSIBLE."
                )
        finally:
            app.dependency_overrides.clear()

    def test_privesc_02_sd_dependency_rejects_student_role(self, test_db: Session):
        """PRIVESC-02: get_current_sport_director_user_web raises 403 for STUDENT.

        Unit test of the Sport Director dependency directly.

        The SD dep calls get_current_user_web(request, db) as a DIRECT Python call
        (not via Depends), so dependency_overrides on get_current_user_web have no
        effect at the route level for SD routes. This test calls the dep function
        directly with a mocked get_current_user_web that returns a STUDENT user,
        then verifies the role check fires.

        Guard: app/dependencies.py — role not in {ADMIN, SPORT_DIRECTOR} → 403.

        Asserts: HTTPException(status_code=403) raised for STUDENT role.
        """
        student = _make_student(test_db)
        mock_request = MagicMock()

        async def _run():
            with patch(
                "app.dependencies.get_current_user_web",
                new=AsyncMock(return_value=student),
            ):
                await get_current_sport_director_user_web(mock_request, test_db)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(_run())

        assert exc_info.value.status_code == 403, (
            f"PRIVESC-02: SD dep returned status {exc_info.value.status_code} for STUDENT, "
            f"expected 403.\n"
            f"If no exception raised: student can reach Sport Director routes — "
            f"vertical privilege escalation POSSIBLE."
        )


# ── Rate Limiting Tests ────────────────────────────────────────────────────────

class TestRateLimiting:

    def test_ratelimit_01_sliding_window_blocks_at_limit(self):
        """RATELIMIT-01: IP sliding window blocks at configured limit (unit test).

        Directly instantiates RateLimitMiddleware and calls _check_rate_limit()
        without HTTP overhead. Uses limit=10 for a fast, deterministic test.

        Asserts:
          - Requests 1..10: allowed (returns True)
          - Requests 11 and 12: blocked (returns False)

        Confirms: security.py:155-167 sliding window algorithm is functional.
        """
        rl = RateLimitMiddleware(app=None, calls=10, window_seconds=60)
        test_ip = f"10.99.{uuid.uuid4().int % 254 + 1}.1"  # unique per run

        async def _run():
            return [
                await rl._check_rate_limit(test_ip, None, 10, 60, "/test")
                for _ in range(12)
            ]

        results = asyncio.run(_run())

        assert all(results[:10]), (
            f"RATELIMIT-01: first 10 requests should be allowed; got {results[:10]}"
        )
        assert not any(results[10:]), (
            f"RATELIMIT-01: requests 11-12 should be blocked; got {results[10:]}"
        )

    def test_ratelimit_02_jwt_decode_stub_leaves_per_user_limit_inactive(self):
        """RATELIMIT-02: JWT decode is a TODO stub → per-user rate limit never fires.

        Finding: security.py:122-134 contains a TODO comment:
            # TODO: Implement JWT token decoding to extract user_id
            return None  # always returns None

        Consequence:
          - user_id is ALWAYS None, even for authenticated Bearer requests
          - The per_user_calls (200/min) branch is NEVER evaluated
          - All traffic (authenticated or not) hits the per-IP limit (100/min)
          - A malicious user behind a proxy can abuse the higher per-user limit
            they are NOT entitled to (since it's never checked)

        Mitigation: implement JWT decode in security.py:129-131 to extract user_id
        from the Authorization: Bearer header, then the per-user branch fires
        and authenticated users get their 200/min allowance while unauthenticated
        traffic stays at 100/min.

        Asserts:
          - _get_user_id returns None for a Bearer-authenticated mock request
          - IP-based limit still fires normally (sole active protection)
        """
        rl = RateLimitMiddleware(app=None, calls=5, window_seconds=60)

        # Part 1: _get_user_id always returns None (JWT decode not implemented)
        mock_request = MagicMock()
        mock_request.headers = {"authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.test.token"}

        async def _check_user_id():
            return await rl._get_user_id(mock_request)

        user_id = asyncio.run(_check_user_id())

        assert user_id is None, (
            f"RATELIMIT-02: _get_user_id returned {user_id!r}, expected None.\n"
            f"JWT decoding is now IMPLEMENTED — update this test to verify "
            f"per-user limit (200/min) is also active. "
            f"Remove this test if the finding is resolved."
        )

        # Part 2: IP limit still protects (sole active mechanism)
        test_ip = f"10.98.{uuid.uuid4().int % 254 + 1}.1"

        async def _ip_limit_check():
            return [
                await rl._check_rate_limit(test_ip, None, 5, 60, "/semesters/request-enrollment")
                for _ in range(7)
            ]

        ip_results = asyncio.run(_ip_limit_check())
        assert all(ip_results[:5]), "IP limit: first 5 should pass"
        assert not any(ip_results[5:]), "IP limit: 6th+ should be blocked"


# ── Concurrent Enrollment Race Tests ──────────────────────────────────────────

@pytest.mark.slow
class TestConcurrentEnrollmentRace:

    def test_race_01_concurrent_enrollment_single_success(self):
        """RACE-01: 5 concurrent enrollment POSTs → exactly 1 success.

        Uses REAL DB (no SAVEPOINT) — required for genuine concurrency testing.
        Five threads fire simultaneously to the same endpoint for the same user.

        Guards exercised in sequence:
          1. Application check: programs.py:185-195 (check-before-act, race-prone alone)
          2. Atomic SQL deduction: programs.py:202-211
             UPDATE user SET credit_balance = balance - cost WHERE balance >= cost
          3. IntegrityError fallback: programs.py:228-230 — DB unique constraint on
             SemesterEnrollment(user_id, semester_id) catches any race that slips past.

        Setup uses a real committed transaction (not SAVEPOINT) so that all threads
        can see the user and semester. Auth override loads the user fresh per-request
        from each thread's own DB session, keeping SQLAlchemy session isolation clean.

        Asserts:
          - Exactly 1 of 5 responses is a clean 303 (no "error" in location)
          - Other 4 have "Already+enrolled" or "Insufficient+credits" in location
          - DB: exactly 1 active SemesterEnrollment row
          - DB: credit_balance deducted exactly once (initial - cost)

        Cleanup: all test data committed to real DB is deleted in the finally block.
        """
        N_THREADS = 5
        INITIAL_BALANCE = 1000
        ENROLL_COST = 100

        # ── Real DB setup (committed, not SAVEPOINT) ──────────────────────────
        setup_db = SessionLocal()
        user_id = sem_id = lic_id = None

        try:
            user = User(
                email=f"race-{uuid.uuid4().hex[:8]}@lfa.com",
                name="RACE Concurrent Student",
                password_hash=get_password_hash("Test1234!"),
                role=UserRole.STUDENT,
                is_active=True,
                onboarding_completed=True,
                credit_balance=INITIAL_BALANCE,
                payment_verified=True,
            )
            setup_db.add(user)
            setup_db.flush()

            sem = Semester(
                code=f"RACE-{uuid.uuid4().hex[:8].upper()}",
                name="RACE Concurrent Enrollment Semester",
                semester_category=SemesterCategory.MINI_SEASON,
                status=SemesterStatus.ONGOING,
                start_date=date(2027, 9, 1),
                end_date=date(2028, 1, 31),
                enrollment_cost=ENROLL_COST,
                specialization_type="LFA_FOOTBALL_PLAYER",
            )
            setup_db.add(sem)
            setup_db.flush()

            lic = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                is_active=True,
                started_at=datetime.now(timezone.utc),
            )
            setup_db.add(lic)
            setup_db.commit()  # Real commit — visible to all threads

            user_id = user.id
            sem_id  = sem.id
            lic_id  = lic.id

        except Exception:
            setup_db.rollback()
            setup_db.close()
            raise
        finally:
            setup_db.close()

        # ── Auth override: fresh user per request, from its own DB session ────
        # Each thread gets its own session via get_db (not overridden).
        # The override re-queries the user by ID inside that session so that
        # db.refresh(user) in the route handler works safely.
        _uid = user_id

        async def _fresh_auth(db: Session = Depends(get_db)):
            return db.query(User).filter(User.id == _uid).first()

        app.dependency_overrides[get_current_user_web] = _fresh_auth
        # NOTE: get_db is NOT overridden — real pool sessions used per request

        def _enroll(_):
            client = TestClient(
                app, headers={"Authorization": "Bearer test-csrf-bypass"}
            )
            return client.post(
                "/semesters/request-enrollment",
                data={"semester_id": str(sem_id)},
                follow_redirects=False,
            )

        try:
            # ── Fire N_THREADS concurrent requests ────────────────────────────
            with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
                responses = [
                    f.result()
                    for f in [pool.submit(_enroll, i) for i in range(N_THREADS)]
                ]

            # ── All responses must be 303 ─────────────────────────────────────
            for i, resp in enumerate(responses):
                assert resp.status_code == 303, (
                    f"RACE-01: thread {i} got {resp.status_code}, expected 303"
                )

            locations = [r.headers.get("location", "") for r in responses]
            successes = [loc for loc in locations if "error" not in loc]
            blocked   = [
                loc for loc in locations
                if "Already+enrolled" in loc or "Insufficient+credits" in loc
            ]

            assert len(successes) == 1, (
                f"RACE-01: expected exactly 1 successful enrollment, got {len(successes)}.\n"
                f"All locations: {locations}\n"
                f"If >1: duplicate enrollment may be possible — "
                f"atomic guard or IntegrityError fallback may have failed."
            )
            assert len(blocked) == N_THREADS - 1, (
                f"RACE-01: expected {N_THREADS - 1} blocked, got {len(blocked)}.\n"
                f"All locations: {locations}"
            )

            # ── DB state: exactly 1 active enrollment ─────────────────────────
            verify_db = SessionLocal()
            try:
                n_enroll = verify_db.query(SemesterEnrollment).filter(
                    SemesterEnrollment.user_id == user_id,
                    SemesterEnrollment.semester_id == sem_id,
                    SemesterEnrollment.is_active == True,
                ).count()
                assert n_enroll == 1, (
                    f"RACE-01: expected 1 active enrollment in DB, found {n_enroll}.\n"
                    f"Duplicate rows = race condition was NOT prevented."
                )

                final_balance = (
                    verify_db.query(User.credit_balance)
                    .filter(User.id == user_id)
                    .scalar()
                )
                expected = INITIAL_BALANCE - ENROLL_COST
                assert final_balance == expected, (
                    f"RACE-01: credit_balance expected {expected}, got {final_balance}.\n"
                    f"If < expected: credits deducted multiple times — "
                    f"atomic SQL deduction guard failed."
                )
            finally:
                verify_db.close()

        finally:
            app.dependency_overrides.clear()

            # ── Cleanup real DB ────────────────────────────────────────────────
            cleanup = SessionLocal()
            try:
                if user_id:
                    # Order: CreditTransaction → SemesterEnrollment → UserLicense → User
                    from app.models.credit_transaction import CreditTransaction
                    cleanup.query(CreditTransaction).filter(
                        CreditTransaction.user_license_id == lic_id
                    ).delete(synchronize_session=False)
                    cleanup.query(SemesterEnrollment).filter(
                        SemesterEnrollment.user_id == user_id
                    ).delete(synchronize_session=False)
                    cleanup.query(UserLicense).filter(
                        UserLicense.id == lic_id
                    ).delete(synchronize_session=False)
                    cleanup.query(User).filter(
                        User.id == user_id
                    ).delete(synchronize_session=False)
                if sem_id:
                    cleanup.query(Semester).filter(
                        Semester.id == sem_id
                    ).delete(synchronize_session=False)
                cleanup.commit()
            except Exception:
                cleanup.rollback()
            finally:
                cleanup.close()
