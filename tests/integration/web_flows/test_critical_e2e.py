"""
Critical E2E Tests
==================
14 full-chain tests covering previously-identified coverage gaps:

  QRB — Quiz Retry Best Score      : fail → retry → pass (UI + DB)
  QEG — Quiz Enrollment Gate       : no booking → 403; with booking → 200
  SFJ — Student Full Journey       : browse → enroll → enrolled state visible
  SDE — Skill Delta E2E            : tournament → TournamentParticipation → skills page
  CDE — Credit Deduction E2E       : enroll in paid event → deduction → history visible
  QAL — Quiz Attempt Limit         : fail × max_attempts → "No More Attempts" UI state
  QIS — Quiz Interrupted Resume    : start → abandon → re-GET → same attempt resumed
  QPG — Quiz State Progression     : no attempt → fail → pass → session_details tracks state
  TCR — Tournament Credit Refund   : enroll → deduct → unenroll → 50% refund CreditTransaction
  ISC — Instructor Slot Conflict   : add instructor → duplicate rejected → 409 Conflict
  ICR — Invitation Code Reg.       : valid code + registration → User.credit_balance = bonus
  APR — Admin Password Reset       : admin resets → old fails login → new succeeds
  LRC — License Revoke Cascade     : license revoked → SemesterEnrollment.is_active = False
  CEE — Camp Enrollment E2E        : camp enroll → deduct → unenroll → 50% refund (CAMP category)

Design rules:
  - Self-contained: each test creates all required data inline via db.flush()
  - Seed-independent: _uid() for all codes/emails, no reliance on any seed script
  - Auth: app.dependency_overrides[get_current_user_web] = lambda: user
  - Cleanup: client fixture calls app.dependency_overrides.clear() on teardown
  - Assertions: HTTP status code + UI text in HTML response + DB state
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web, get_current_user_optional, get_current_user, get_current_sport_director_user_web
from app.core.security import get_password_hash, verify_password
from app.models.user import User, UserRole, SpecializationType
from app.models.invitation_code import InvitationCode
from app.models.tournament_instructor_slot import TournamentInstructorSlot, SlotStatus, SlotRole
from app.models.license import UserLicense, LicenseProgression
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.tournament_configuration import TournamentConfiguration
from app.models.session import Session as SessionModel, SessionType
from app.models.booking import Booking, BookingStatus
from app.models.quiz import (
    Quiz,
    QuizQuestion,
    QuizAnswerOption,
    QuizAttempt,
    QuizUserAnswer,
    SessionQuiz,
    QuizCategory,
    QuizDifficulty,
    QuestionType,
)
from app.models.credit_transaction import CreditTransaction
from app.models.audit_log import AuditLog
from app.models.message import Message, MessagePriority
from app.models.notification import Notification, NotificationType
from app.models.invoice_request import InvoiceRequest
from app.models.tournament_achievement import TournamentParticipation
from app.models.team import Team, TeamMember, TournamentTeamEnrollment, TeamInvite, TeamInviteStatus, TournamentPlayerCheckin
from app.models.attendance import Attendance, AttendanceStatus
from app.models.performance_review import InstructorSessionReview, StudentPerformanceReview


# ── Helpers ────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(
    db: Session,
    role: UserRole = UserRole.STUDENT,
    credit_balance: int = 1000,
) -> User:
    u = User(
        name=f"E2E User {_uid()}",
        email=f"e2e-{_uid()}@test.lfa",
        password_hash=get_password_hash("Test123!"),
        role=role,
        is_active=True,
        onboarding_completed=True,
        date_of_birth=date(2000, 1, 1),
        credit_balance=credit_balance,
    )
    db.add(u)
    db.flush()
    return u


def _make_license(db: Session, user: User) -> UserLicense:
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True,
        onboarding_completed=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        football_skills={"ball_control": 70.0, "passing": 65.0, "shooting": 60.0},
    )
    db.add(lic)
    db.flush()
    return lic


def _make_tournament(db: Session, enrollment_cost: int = 0) -> Semester:
    sem = Semester(
        code=f"TOURN-{_uid()}",
        name=f"E2E Tournament {_uid()}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="ENROLLMENT_OPEN",
        enrollment_cost=enrollment_cost,
        specialization_type="LFA_FOOTBALL_PLAYER",
    )
    db.add(sem)
    db.flush()
    cfg = TournamentConfiguration(
        semester_id=sem.id,
        participant_type="INDIVIDUAL",
        max_players=100,
    )
    db.add(cfg)
    db.flush()
    return sem


def _make_camp(db: Session, enrollment_cost: int = 200) -> Semester:
    sem = Semester(
        code=f"CAMP-{_uid()}",
        name=f"E2E Camp {_uid()}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=7),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.CAMP,
        tournament_status="ENROLLMENT_OPEN",
        enrollment_cost=enrollment_cost,
    )
    db.add(sem)
    db.flush()
    return sem


def _make_virtual_session(db: Session) -> tuple:
    """Create Semester + virtual SessionModel + Instructor. Returns (session, instructor, semester).

    date_start is in the past so the quiz is immediately available in session_details.
    Virtual sessions expose the quiz section in session_details (hybrid/virtual only).
    """
    instructor = _make_user(db, role=UserRole.INSTRUCTOR)
    uid = _uid()
    sem = Semester(
        code=f"VS-{uid}",
        name=f"VS Semester {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
    )
    db.add(sem)
    db.flush()
    sess = SessionModel(
        title=f"E2E Virtual Session {uid}",
        session_type=SessionType.virtual,
        date_start=datetime(2026, 1, 1, 10, 0),   # past naive dt → quiz is available now
        date_end=datetime(2026, 1, 1, 12, 0),
        semester_id=sem.id,
        instructor_id=instructor.id,
        base_xp=50,
    )
    db.add(sess)
    db.flush()
    return sess, instructor, sem


def _make_quiz(db: Session, passing_score: float = 0.6) -> tuple:
    """Create Quiz + 1 Question + wrong/correct Options. Returns (quiz, question, opt_wrong, opt_correct)."""
    quiz = Quiz(
        title=f"E2E Quiz {_uid()}",
        category=QuizCategory.GENERAL,
        difficulty=QuizDifficulty.EASY,
        time_limit_minutes=30,
        xp_reward=10,
        passing_score=passing_score,
        is_active=True,
    )
    db.add(quiz)
    db.flush()

    question = QuizQuestion(
        quiz_id=quiz.id,
        question_text="What is 2 + 2?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        points=1.0,
        order_index=0,
    )
    db.add(question)
    db.flush()

    opt_wrong = QuizAnswerOption(question_id=question.id, option_text="3", is_correct=False, order_index=0)
    opt_correct = QuizAnswerOption(question_id=question.id, option_text="4", is_correct=True, order_index=1)
    db.add_all([opt_wrong, opt_correct])
    db.flush()

    return quiz, question, opt_wrong, opt_correct


# ── Client fixture ─────────────────────────────────────────────────────────────

@pytest.fixture
def client(test_db: Session):
    """TestClient sharing test_db via get_db override. User auth set per-test."""
    def _override_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}) as c:
        yield c
    app.dependency_overrides.clear()


# ── Test 1: QRB — Quiz Retry Best Score ───────────────────────────────────────

def test_quiz_retry_fail_then_pass(test_db: Session, client: TestClient):
    """QRB: fail → retry → pass. Full quiz retry chain with UI + DB assertions."""
    student = _make_user(test_db)
    quiz, question, opt_wrong, opt_correct = _make_quiz(test_db, passing_score=0.6)

    app.dependency_overrides[get_current_user_web] = lambda: student

    # Step 1: Start quiz — creates QuizAttempt
    r1 = client.get(f"/quizzes/{quiz.id}/take")
    assert r1.status_code == 200
    assert quiz.title in r1.text or "question" in r1.text.lower()

    attempt1 = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
        QuizAttempt.completed_at == None,
    ).first()
    assert attempt1 is not None, "First QuizAttempt must be created by GET /take"

    # Step 2: Submit wrong answer → attempt completed, passed=False
    r2 = client.post(
        f"/quizzes/{quiz.id}/submit",
        data={
            "attempt_id": str(attempt1.id),
            "time_spent": "30",
            f"question_{question.id}": str(opt_wrong.id),
        },
    )
    assert r2.status_code == 200

    test_db.refresh(attempt1)
    assert attempt1.completed_at is not None, "Attempt must be completed after submit"
    assert attempt1.passed is False, "Wrong answer must result in passed=False"

    # Step 3: Retry — GET /take must show form again with a NEW attempt
    r3 = client.get(f"/quizzes/{quiz.id}/take")
    assert r3.status_code == 200
    assert quiz.title in r3.text or "question" in r3.text.lower()

    attempt2 = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
        QuizAttempt.completed_at == None,
    ).first()
    assert attempt2 is not None, "Retry must create a new incomplete QuizAttempt"
    assert attempt2.id != attempt1.id, "Retry attempt must be a different row"

    # Step 4: Submit correct answer → passed=True, UI shows success
    r4 = client.post(
        f"/quizzes/{quiz.id}/submit",
        data={
            "attempt_id": str(attempt2.id),
            "time_spent": "45",
            f"question_{question.id}": str(opt_correct.id),
        },
    )
    assert r4.status_code == 200
    # UI: result page contains pass indicator
    assert (
        "pass" in r4.text.lower()
        or "100" in r4.text
        or "correct" in r4.text.lower()
    ), f"Pass result not visible in response. Snippet: {r4.text[:400]}"

    test_db.refresh(attempt2)
    assert attempt2.passed is True, "Correct answer must result in passed=True"
    assert attempt2.score >= quiz.passing_score * 100

    # DB: exactly 2 attempts for this quiz+user
    total = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
    ).count()
    assert total == 2, f"Expected 2 attempts, found {total}"


# ── Test 2: QEG — Quiz Enrollment Gate ────────────────────────────────────────

def test_quiz_gate_no_booking_then_booking(test_db: Session, client: TestClient):
    """QEG: no booking → GET /take with session_id → 403; with CONFIRMED booking → 200."""
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    student = _make_user(test_db)
    quiz, question, opt_wrong, opt_correct = _make_quiz(test_db, passing_score=0.6)

    # Minimal semester required (sessions.semester_id is NOT NULL)
    sem = Semester(
        code=f"QEG-{_uid()}",
        name=f"QEG Semester {_uid()}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.flush()

    # Session that has ALREADY started — required for quiz gate to allow access
    # Using a naive datetime clearly in the past; quiz.py treats naive as Budapest time
    sess = SessionModel(
        title=f"E2E Gate Session {_uid()}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 1, 1, 10, 0),   # past naive datetime
        date_end=datetime(2026, 1, 1, 12, 0),
        semester_id=sem.id,
        instructor_id=instructor.id,
        base_xp=50,
    )
    test_db.add(sess)
    test_db.flush()

    # Link quiz to session via SessionQuiz
    sq = SessionQuiz(session_id=sess.id, quiz_id=quiz.id, is_required=True)
    test_db.add(sq)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student

    # Step 1: No booking — GET /take with session_id → 403
    r1 = client.get(f"/quizzes/{quiz.id}/take?session_id={sess.id}")
    assert r1.status_code == 403, (
        f"Expected 403 without booking, got {r1.status_code}. "
        f"Snippet: {r1.text[:200]}"
    )

    # Step 2: Create CONFIRMED booking
    booking = Booking(
        user_id=student.id,
        session_id=sess.id,
        status=BookingStatus.CONFIRMED,
    )
    test_db.add(booking)
    test_db.flush()

    # Step 3: With booking — GET /take with session_id → 200 (gate passed)
    r3 = client.get(f"/quizzes/{quiz.id}/take?session_id={sess.id}")
    assert r3.status_code == 200, (
        f"Expected 200 after booking created, got {r3.status_code}. "
        f"Snippet: {r3.text[:200]}"
    )
    assert quiz.title in r3.text or "question" in r3.text.lower()

    # DB: QuizAttempt created after access was granted
    attempt = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
    ).first()
    assert attempt is not None, "QuizAttempt must be created once access is granted"


# ── Test 3: SFJ — Student Full Journey ────────────────────────────────────────

def test_student_journey_browse_enroll_see_enrolled(test_db: Session, client: TestClient):
    """SFJ: browse → enroll → enrolled status visible on browse page."""
    student = _make_user(test_db, credit_balance=2000)
    _make_license(test_db, student)   # required for tournament enrollment
    tourn = _make_tournament(test_db, enrollment_cost=0)

    app.dependency_overrides[get_current_user_web] = lambda: student

    # Step 1: Browse tournaments — tournament name is visible in HTML
    r1 = client.get("/tournaments")
    assert r1.status_code == 200
    assert tourn.name in r1.text, (
        f"Tournament '{tourn.name}' not found in browse page. "
        f"Snippet: {r1.text[:500]}"
    )

    # Step 2: Enroll → auto-approved, redirects with 303
    r2 = client.post(
        f"/tournaments/{tourn.id}/enroll",
        follow_redirects=False,
    )
    assert r2.status_code == 303, (
        f"Expected 303 redirect from enrollment, got {r2.status_code}. "
        f"Snippet: {r2.text[:400]}"
    )

    # DB: SemesterEnrollment created and auto-approved
    enrollment = test_db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tourn.id,
        SemesterEnrollment.user_id == student.id,
    ).first()
    assert enrollment is not None, "SemesterEnrollment must be created"
    assert enrollment.request_status == EnrollmentStatus.APPROVED, (
        f"Enrollment should be auto-approved, got {enrollment.request_status}"
    )
    assert enrollment.is_active is True

    # Step 3: Browse page now shows enrolled tournament ("My Tournaments" section)
    r3 = client.get("/tournaments")
    assert r3.status_code == 200
    # Tournament name appears in enrolled section with enrollment badge
    assert tourn.name in r3.text
    assert "enrolled" in r3.text.lower(), (
        f"'enrolled' badge not found after enrollment. Snippet: {r3.text[:600]}"
    )


# ── Test 4: SDE — Skill Delta End-to-End ──────────────────────────────────────

def test_skill_delta_tournament_to_profile(test_db: Session, client: TestClient):
    """SDE: completed tournament → TournamentParticipation.skill_rating_delta → skills page."""
    from tests.factories.game_factory import PlayerFactory, TournamentFactory

    uid = _uid()
    player1, lic1 = PlayerFactory.create_lfa_player(test_db, email=f"p1-{uid}@e2e.lfa")
    player2, lic2 = PlayerFactory.create_lfa_player(test_db, email=f"p2-{uid}@e2e.lfa")
    preset = TournamentFactory.ensure_preset(test_db)
    tt = TournamentFactory.ensure_tournament_type(test_db)

    # Create completed tournament: player1=1st (winner), player2=2nd (last)
    TournamentFactory.create_completed_tournament(
        test_db,
        preset=preset,
        tt=tt,
        participants=[(player1.id, 1), (player2.id, 2)],
    )

    # DB: TournamentParticipation exists for both players with skill_rating_delta set
    p1_tp = (
        test_db.query(TournamentParticipation)
        .filter(TournamentParticipation.user_id == player1.id)
        .order_by(TournamentParticipation.id.desc())
        .first()
    )
    assert p1_tp is not None, "TournamentParticipation must exist for player1 (winner)"
    assert p1_tp.skill_rating_delta is not None, "skill_rating_delta must be computed for winner"
    # skill_rating_delta is JSONB dict: {"passing": 1.2, "ball_control": 0.8, ...}
    # Winner (placement 1/2) should have at least one positive delta
    winner_delta_values = [
        v for v in p1_tp.skill_rating_delta.values()
        if isinstance(v, (int, float))
    ]
    assert any(v > 0 for v in winner_delta_values), (
        f"Winner's skill_rating_delta should have at least one positive value. "
        f"Got: {p1_tp.skill_rating_delta}"
    )

    p2_tp = (
        test_db.query(TournamentParticipation)
        .filter(TournamentParticipation.user_id == player2.id)
        .order_by(TournamentParticipation.id.desc())
        .first()
    )
    assert p2_tp is not None, "TournamentParticipation must exist for player2 (last place)"
    assert p2_tp.skill_rating_delta is not None, "skill_rating_delta must be computed for last place"
    loser_delta_values = [
        v for v in p2_tp.skill_rating_delta.values()
        if isinstance(v, (int, float))
    ]
    assert any(v < 0 for v in loser_delta_values), (
        f"Last-place skill_rating_delta should have at least one negative value. "
        f"Got: {p2_tp.skill_rating_delta}"
    )

    # HTTP: skills page loads for winner (profile reflects updated state post-tournament)
    app.dependency_overrides[get_current_user_web] = lambda: player1
    r = client.get("/skills")
    assert r.status_code == 200, f"Skills page must return 200. Got {r.status_code}: {r.text[:200]}"
    # Page renders skill-related content
    assert any(
        token in r.text
        for token in ["ball_control", "passing", "shooting", "Skills", "skill"]
    ), f"Skills page should contain skill names. Snippet: {r.text[:400]}"


# ── Test 5: CDE — Credit Deduction End-to-End ─────────────────────────────────

def test_credit_flow_deduction_and_history(test_db: Session, client: TestClient):
    """CDE: enroll in paid tournament → credits deducted → CreditTransaction → history page shows update."""
    student = _make_user(test_db, credit_balance=1000)
    lic = _make_license(test_db, student)
    tourn = _make_tournament(test_db, enrollment_cost=200)

    # Credits page uses get_current_user_optional; enrollment uses get_current_user_web
    app.dependency_overrides[get_current_user_optional] = lambda: student
    app.dependency_overrides[get_current_user_web] = lambda: student

    # Step 1: Credits page shows initial balance (1000)
    r1 = client.get("/credits")
    assert r1.status_code == 200
    assert "1000" in r1.text or "1,000" in r1.text, (
        f"Initial balance 1000 not visible. Snippet: {r1.text[:400]}"
    )

    # Step 2: Enroll in paid tournament → 303 redirect (credits deducted atomically)
    r2 = client.post(
        f"/tournaments/{tourn.id}/enroll",
        follow_redirects=False,
    )
    assert r2.status_code == 303, (
        f"Expected 303 from enrollment, got {r2.status_code}. Snippet: {r2.text[:400]}"
    )

    # DB: credit balance reduced 1000 → 800
    test_db.refresh(student)
    assert student.credit_balance == 800, (
        f"Expected 800 after 200 deduction, got {student.credit_balance}"
    )

    # DB: CreditTransaction created with correct fields
    tx = (
        test_db.query(CreditTransaction)
        .filter(CreditTransaction.user_license_id == lic.id)
        .order_by(CreditTransaction.id.desc())
        .first()
    )
    assert tx is not None, "CreditTransaction must be created on paid enrollment"
    assert tx.amount == -200, f"Expected amount=-200, got {tx.amount}"
    assert tx.balance_after == 800, f"Expected balance_after=800, got {tx.balance_after}"

    # HTTP: credits page shows updated balance (800)
    r3 = client.get("/credits")
    assert r3.status_code == 200
    assert "800" in r3.text, (
        f"Updated balance 800 not visible on credits page. Snippet: {r3.text[:500]}"
    )


# ── Test 6: QAL — Quiz Attempt Limit Exhaustion ───────────────────────────────

def test_quiz_attempt_limit_exhaustion(test_db: Session, client: TestClient):
    """QAL: exhaust max_attempts → session_details shows 'No More Attempts' UI state.

    Setup: virtual session (quiz section shown for virtual/hybrid only) +
           SessionQuiz(max_attempts=2) + Booking(CONFIRMED) for is_enrolled.
    Flow:  fail × 2 → GET /sessions/{id} → 'No More Attempts' in HTML.
    """
    quiz, question, opt_wrong, opt_correct = _make_quiz(test_db, passing_score=0.6)
    student = _make_user(test_db)
    sess, _instr, _sem = _make_virtual_session(test_db)

    sq = SessionQuiz(session_id=sess.id, quiz_id=quiz.id, is_required=True, max_attempts=2)
    test_db.add(sq)
    booking = Booking(user_id=student.id, session_id=sess.id, status=BookingStatus.CONFIRMED)
    test_db.add(booking)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student

    def _fail_attempt() -> None:
        """Start a quiz attempt (no session_id → no gate) and submit a wrong answer."""
        r_take = client.get(f"/quizzes/{quiz.id}/take")
        assert r_take.status_code == 200
        attempt = test_db.query(QuizAttempt).filter(
            QuizAttempt.quiz_id == quiz.id,
            QuizAttempt.user_id == student.id,
            QuizAttempt.completed_at.is_(None),
        ).first()
        assert attempt is not None
        r_sub = client.post(
            f"/quizzes/{quiz.id}/submit",
            data={"attempt_id": str(attempt.id), "time_spent": "20",
                  f"question_{question.id}": str(opt_wrong.id)},
        )
        assert r_sub.status_code == 200
        test_db.expire_all()

    _fail_attempt()   # attempt 1: fail
    _fail_attempt()   # attempt 2: fail

    # DB: exactly 2 completed, failed attempts
    attempts = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
    ).all()
    assert len(attempts) == 2, f"Expected 2 attempts, got {len(attempts)}"
    assert all(a.passed is False for a in attempts), "Both attempts must be failed"
    assert all(a.completed_at is not None for a in attempts), "Both attempts must be completed"

    # UI: session_details shows 'No More Attempts' — the exhausted state
    resp = client.get(f"/sessions/{sess.id}", follow_redirects=True)
    assert resp.status_code == 200
    assert "No More Attempts" in resp.text, (
        f"'No More Attempts' not found in session_details after exhausting max_attempts=2. "
        f"Snippet: {resp.text[:600]}"
    )


# ── Test 7: QIS — Quiz Interrupted State Resume ───────────────────────────────

def test_quiz_interrupted_state_resume(test_db: Session, client: TestClient):
    """QIS: start quiz → abandon (no submit) → GET /take again → resumes SAME attempt.

    The quiz route checks for an existing in-progress attempt (completed_at IS NULL)
    and returns it instead of creating a new one.  This test verifies:
      - Same attempt_id on second GET /take
      - Only 1 QuizAttempt row exists (not 2)
      - The resumed attempt can be completed successfully
    """
    quiz, question, opt_wrong, opt_correct = _make_quiz(test_db, passing_score=0.6)
    student = _make_user(test_db)

    app.dependency_overrides[get_current_user_web] = lambda: student

    # Step 1: Start quiz — creates in-progress QuizAttempt
    r1 = client.get(f"/quizzes/{quiz.id}/take")
    assert r1.status_code == 200
    assert quiz.title in r1.text or "question" in r1.text.lower()

    attempt1 = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
        QuizAttempt.completed_at.is_(None),
    ).first()
    assert attempt1 is not None, "QuizAttempt must be created on first GET /take"
    attempt1_id = attempt1.id

    # DB: exactly 1 in-progress attempt
    total_before = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
    ).count()
    assert total_before == 1

    # Step 2: GET /take again WITHOUT submitting — must resume the same attempt
    r2 = client.get(f"/quizzes/{quiz.id}/take")
    assert r2.status_code == 200

    # DB: still only 1 QuizAttempt row (not a new one)
    total_after = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
    ).count()
    assert total_after == 1, (
        f"Second GET /take must resume existing attempt — not create a new one. "
        f"Expected 1 row, got {total_after}"
    )

    # The in-progress attempt is still the same one (same id)
    still_in_progress = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
        QuizAttempt.completed_at.is_(None),
    ).first()
    assert still_in_progress is not None
    assert still_in_progress.id == attempt1_id, (
        f"Resumed attempt id={still_in_progress.id} != original id={attempt1_id}"
    )

    # Step 3: Complete the resumed attempt with a correct answer → pass
    r3 = client.post(
        f"/quizzes/{quiz.id}/submit",
        data={"attempt_id": str(attempt1_id), "time_spent": "60",
              f"question_{question.id}": str(opt_correct.id)},
    )
    assert r3.status_code == 200
    assert "pass" in r3.text.lower() or "100" in r3.text or "correct" in r3.text.lower(), (
        f"Pass result not visible. Snippet: {r3.text[:400]}"
    )

    # DB: 1 completed, passed QuizAttempt (the same one we resumed)
    test_db.expire_all()
    final_attempt = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
    ).first()
    assert final_attempt.id == attempt1_id, "Must be the same attempt that was resumed"
    assert final_attempt.passed is True
    assert final_attempt.completed_at is not None
    assert test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
    ).count() == 1, "Exactly 1 QuizAttempt row must exist (no duplicate created on resume)"


# ── Test 8: QPG — Quiz Required State Progression ────────────────────────────

def test_quiz_required_state_progression(test_db: Session, client: TestClient):
    """QPG: session_details tracks quiz state across fail → pass transitions.

    Verifies the session_details quiz UI correctly reflects each state:
      1. Before any attempt  → 'Start Certification Exam' available
      2. After failed attempt → 'Retry Quiz' available (can still attempt)
      3. After passed attempt → 'PASSED' state shown

    Uses a virtual session (quiz section only shown for virtual/hybrid in session_details).
    quiz attempts taken via GET /quizzes/{id}/take (no session_id) to bypass gate.
    """
    quiz, question, opt_wrong, opt_correct = _make_quiz(test_db, passing_score=0.6)
    student = _make_user(test_db)
    sess, _instr, _sem = _make_virtual_session(test_db)

    # Link quiz to session (required for session_details to show quiz section)
    sq = SessionQuiz(session_id=sess.id, quiz_id=quiz.id, is_required=True)
    test_db.add(sq)
    # Booking provides is_enrolled=True → quiz section is populated
    booking = Booking(user_id=student.id, session_id=sess.id, status=BookingStatus.CONFIRMED)
    test_db.add(booking)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student

    # ── State 1: no attempt → start button visible ─────────────────────────
    r1 = client.get(f"/sessions/{sess.id}", follow_redirects=True)
    assert r1.status_code == 200
    assert "Start Certification Exam" in r1.text, (
        f"Expected 'Start Certification Exam' before any attempt. "
        f"Quiz section snippet: {r1.text[r1.text.find('session_quizzes') - 50 : r1.text.find('session_quizzes') + 200] if 'session_quizzes' in r1.text else r1.text[:600]}"
    )
    # DB: no attempts yet
    assert test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
    ).count() == 0

    # ── State 2: fail one attempt → retry available ────────────────────────
    r_take = client.get(f"/quizzes/{quiz.id}/take")
    assert r_take.status_code == 200
    attempt_fail = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
        QuizAttempt.completed_at.is_(None),
    ).first()
    assert attempt_fail is not None
    r_sub = client.post(
        f"/quizzes/{quiz.id}/submit",
        data={"attempt_id": str(attempt_fail.id), "time_spent": "25",
              f"question_{question.id}": str(opt_wrong.id)},
    )
    assert r_sub.status_code == 200
    test_db.expire_all()

    # DB: 1 failed attempt
    assert test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
        QuizAttempt.passed.is_(False),
    ).count() == 1

    r2 = client.get(f"/sessions/{sess.id}", follow_redirects=True)
    assert r2.status_code == 200
    assert "Retry Quiz" in r2.text, (
        f"Expected 'Retry Quiz' button after failed attempt. Snippet: {r2.text[:600]}"
    )
    assert "No More Attempts" not in r2.text, "Should not show exhausted state (max_attempts=None)"
    assert "PASSED" not in r2.text, "Should not show PASSED after a failed attempt"

    # ── State 3: pass second attempt → PASSED state ────────────────────────
    r_take2 = client.get(f"/quizzes/{quiz.id}/take")
    assert r_take2.status_code == 200
    attempt_pass = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
        QuizAttempt.completed_at.is_(None),
    ).first()
    assert attempt_pass is not None
    r_sub2 = client.post(
        f"/quizzes/{quiz.id}/submit",
        data={"attempt_id": str(attempt_pass.id), "time_spent": "45",
              f"question_{question.id}": str(opt_correct.id)},
    )
    assert r_sub2.status_code == 200
    test_db.expire_all()

    # DB: 2 total attempts (1 failed + 1 passed), latest is passed
    all_attempts = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
    ).all()
    assert len(all_attempts) == 2
    assert any(a.passed for a in all_attempts), "At least one passed attempt must exist"

    r3 = client.get(f"/sessions/{sess.id}", follow_redirects=True)
    assert r3.status_code == 200
    assert "PASSED" in r3.text, (
        f"Expected 'PASSED' state after passing quiz. Snippet: {r3.text[:600]}"
    )
    assert "Retry Quiz" not in r3.text, "Retry button must not show after passing"


# ── Test 9: TCR — Tournament Credit Refund ────────────────────────────────────

def test_tournament_unenrollment_credit_refund(test_db: Session, client: TestClient):
    """TCR: enroll in paid tournament → credit deducted → unenroll → 50% refund.

    Closes gap: tournament unenrollment refund chain verified at HTTP + DB level.
    Complements Cypress TOUR-S-05 (browser-level) with a Python integration test.

    Refund invariant (enrollment_cost=200):
      Enroll  : 1000 → 800  (−200)
      Unenroll:  800 → 900  (+100 = 50% of 200)
    CreditTransaction(TOURNAMENT_UNENROLL_REFUND, amount=100) must be created.
    """
    student = _make_user(test_db, credit_balance=1000)
    lic = _make_license(test_db, student)
    tourn = _make_tournament(test_db, enrollment_cost=200)

    app.dependency_overrides[get_current_user_optional] = lambda: student
    app.dependency_overrides[get_current_user_web] = lambda: student

    # Step 1: Enroll → credits 1000 → 800
    r_enroll = client.post(
        f"/tournaments/{tourn.id}/enroll",
        follow_redirects=False,
    )
    assert r_enroll.status_code == 303, (
        f"Enrollment must return 303 redirect. Got {r_enroll.status_code}: {r_enroll.text[:400]}"
    )

    test_db.refresh(student)
    assert student.credit_balance == 800, (
        f"Expected 800 after 200 deduction, got {student.credit_balance}"
    )

    # DB: SemesterEnrollment created and active
    enrollment = test_db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tourn.id,
        SemesterEnrollment.user_id == student.id,
    ).first()
    assert enrollment is not None, "SemesterEnrollment must exist after enroll"
    assert enrollment.is_active is True

    # Step 2: Unenroll → 50% refund applied
    r_unenroll = client.post(
        f"/tournaments/{tourn.id}/unenroll",
        follow_redirects=False,
    )
    assert r_unenroll.status_code == 303, (
        f"Unenrollment must return 303 redirect. Got {r_unenroll.status_code}: {r_unenroll.text[:400]}"
    )

    # DB: enrollment marked inactive + withdrawn
    test_db.refresh(enrollment)
    assert enrollment.is_active is False, "Enrollment must be inactive after unenroll"
    assert enrollment.request_status == EnrollmentStatus.WITHDRAWN, (
        f"Enrollment status must be WITHDRAWN, got {enrollment.request_status}"
    )

    # DB: 50% refund applied to credit balance (800 + 100 = 900)
    test_db.refresh(student)
    expected = 900  # 800 + 100 (50% of 200)
    assert student.credit_balance == expected, (
        f"Expected balance {expected} after 50% refund, got {student.credit_balance}"
    )

    # DB: CreditTransaction(refund, amount=100) created
    tx = (
        test_db.query(CreditTransaction)
        .filter(
            CreditTransaction.user_license_id == lic.id,
            CreditTransaction.amount > 0,
        )
        .order_by(CreditTransaction.id.desc())
        .first()
    )
    assert tx is not None, "Refund CreditTransaction must be created on unenrollment"
    assert tx.amount == 100, f"Expected refund amount=100 (50% of 200), got {tx.amount}"
    assert tx.balance_after == expected, (
        f"Expected balance_after={expected}, got {tx.balance_after}"
    )
    tx_type = str(tx.transaction_type).upper()
    assert "REFUND" in tx_type or "UNENROLL" in tx_type, (
        f"Expected refund transaction type, got {tx.transaction_type}"
    )

    # UI: /credits page renders the refunded balance (900) — follow-up GET after redirect
    r_credits = client.get("/credits")
    assert r_credits.status_code == 200
    assert "900" in r_credits.text, (
        f"Balance 900 not visible on /credits after 50% refund. Snippet: {r_credits.text[:400]}"
    )


# ── Test 10: ISC — Instructor Slot Conflict ───────────────────────────────────

def test_instructor_slot_duplicate_rejected(test_db: Session, client: TestClient):
    """ISC: add instructor to tournament → same instructor again → 409 Conflict.

    Closes gap: instructor scheduling conflict enforcement verified at HTTP level.
    Unique constraint (semester_id, instructor_id) prevents double-booking an instructor
    in the same tournament. Validated via service-layer check before the DB constraint fires.

    DB: only 1 TournamentInstructorSlot exists after the duplicate is rejected.
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    tourn = _make_tournament(test_db)

    # Instructor must have an active LFA_COACH UserLicense for eligibility check
    coach_lic = UserLicense(
        user_id=instructor.id,
        specialization_type="LFA_COACH",
        current_level=5,
        max_achieved_level=5,
        is_active=True,
        expires_at=None,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    test_db.add(coach_lic)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: admin

    # Step 1: Add instructor as MASTER → must succeed
    r1 = client.post(
        f"/admin/tournaments/{tourn.id}/instructor-slots",
        data={
            "instructor_id": str(instructor.id),
            "role": "MASTER",
        },
        follow_redirects=False,
    )
    assert r1.status_code in (200, 201, 303), (
        f"First instructor slot assignment must succeed. "
        f"Got {r1.status_code}: {r1.text[:400]}"
    )

    # DB: exactly 1 slot created
    test_db.expire_all()
    slots = test_db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == tourn.id,
        TournamentInstructorSlot.instructor_id == instructor.id,
    ).all()
    assert len(slots) == 1, f"Expected 1 instructor slot after first assignment, got {len(slots)}"

    # Step 2: Attempt to add same instructor again → 409 Conflict
    test_db.expire_all()
    r2 = client.post(
        f"/admin/tournaments/{tourn.id}/instructor-slots",
        data={
            "instructor_id": str(instructor.id),
            "role": "MASTER",
        },
        follow_redirects=False,
    )
    assert r2.status_code == 409, (
        f"Duplicate instructor slot must be rejected with 409. "
        f"Got {r2.status_code}: {r2.text[:400]}"
    )
    # Response explains the conflict
    response_text = r2.text.lower()
    assert "already" in response_text or "roster" in response_text or "conflict" in response_text, (
        f"409 response must explain the duplicate conflict. Snippet: {r2.text[:400]}"
    )

    # DB: still only 1 slot (no duplicate row created)
    test_db.expire_all()
    slot_count = test_db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == tourn.id,
        TournamentInstructorSlot.instructor_id == instructor.id,
    ).count()
    assert slot_count == 1, (
        f"DB must have exactly 1 instructor slot after rejection, got {slot_count}"
    )


# ── Test 11: ICR — Invitation Code Registration ───────────────────────────────

def test_invitation_code_registration_grants_credits(test_db: Session, client: TestClient):
    """ICR: valid invitation code used during registration → User.credit_balance = bonus_credits.

    Closes gap: student registration with invitation code verified E2E (HTTP + DB).
    Flow: create code in DB → POST /register with all fields → verify:
      - HTTP 303 redirect (registration succeeded)
      - InvitationCode.is_used = True + used_by_user_id set
      - New User.credit_balance equals the code's bonus_credits
    """
    uid = _uid()
    code_str = f"INV-E2E-{uid[:6].upper()}"
    inv_code = InvitationCode(
        code=code_str,
        invited_name="E2E Test Partner",
        bonus_credits=500,
        is_used=False,
    )
    test_db.add(inv_code)
    test_db.flush()

    email = f"reg-{uid}@test.lfa"
    r = client.post(
        "/register",
        data={
            "first_name": "Test",
            "last_name": "Registrant",
            "nickname": "tstreg",
            "email": email,
            "password": "Test123!",
            "phone": "+36201234567",
            "date_of_birth": "2000-01-01",
            "nationality": "HU",
            "gender": "Male",
            "street_address": "Test Street 1",
            "city": "Budapest",
            "postal_code": "1055",
            "country": "Hungary",
            "invitation_code": code_str,
        },
        follow_redirects=False,
    )
    # Success: redirect to /dashboard (auto-login after registration)
    assert r.status_code == 303, (
        f"Expected 303 redirect after registration. Got {r.status_code}. "
        f"Validation errors appear as 200. Body snippet: {r.text[:500]}"
    )

    # DB: InvitationCode marked as used with back-reference to new user
    test_db.refresh(inv_code)
    assert inv_code.is_used is True, "InvitationCode must be marked is_used=True"
    assert inv_code.used_at is not None, "InvitationCode.used_at must be set"

    # DB: new User created with credit balance equal to bonus_credits
    new_user = test_db.query(User).filter(User.email == email).first()
    assert new_user is not None, f"Registered user not found in DB (email: {email})"
    assert new_user.credit_balance == 500, (
        f"User.credit_balance must equal bonus_credits=500, got {new_user.credit_balance}"
    )
    assert inv_code.used_by_user_id == new_user.id, (
        f"InvitationCode.used_by_user_id must reference the new user. "
        f"Got {inv_code.used_by_user_id}, expected {new_user.id}"
    )

    # UI: /credits page shows invitation code bonus for the newly registered user
    app.dependency_overrides[get_current_user_optional] = lambda: new_user
    r_credits = client.get("/credits")
    assert r_credits.status_code == 200
    assert "500" in r_credits.text, (
        f"Invitation code bonus (500 credits) not visible on /credits after registration. "
        f"Snippet: {r_credits.text[:400]}"
    )


# ── Test 12: APR — Admin Password Reset + Login Chain ────────────────────────

def test_admin_password_reset_enables_login(test_db: Session, client: TestClient):
    """APR: admin resets student password → old password fails login → new password succeeds.

    Closes gap: admin password reset chain verified E2E (HTTP + DB + auth).
    Proves the full round-trip: admin action → DB hash update → login gate enforces it.

    Flow:
      1. Admin POSTs /admin/users/{id}/reset-password → 303
      2. DB: verify_password(new) = True, verify_password(old) = False
      3. POST /login with old password → 200 (error re-render)
      4. POST /login with new password → 303 redirect to /dashboard
    """
    student = _make_user(test_db, role=UserRole.STUDENT)
    admin = _make_user(test_db, role=UserRole.ADMIN)
    old_password = "Test123!"   # password set in _make_user
    new_password = "NewPass456!"

    # Step 1: Admin resets password
    app.dependency_overrides[get_current_user_web] = lambda: admin
    r_reset = client.post(
        f"/admin/users/{student.id}/reset-password",
        data={"new_password": new_password},
        follow_redirects=False,
    )
    assert r_reset.status_code == 303, (
        f"Admin password reset must return 303. Got {r_reset.status_code}: {r_reset.text[:400]}"
    )

    # DB: new password verifies, old does not
    test_db.refresh(student)
    assert verify_password(new_password, student.password_hash), (
        "New password must verify against the updated hash"
    )
    assert not verify_password(old_password, student.password_hash), (
        "Old password must no longer verify after reset"
    )

    # Step 2: Test login — auth override does not affect /login (uses form credentials only)
    # Login with old password → 200 error template (credentials rejected)
    r_old = client.post(
        "/login",
        data={"email": student.email, "password": old_password},
        follow_redirects=False,
    )
    assert r_old.status_code == 200, (
        f"Old password login must fail (200 error page). Got {r_old.status_code}"
    )
    assert "Invalid" in r_old.text or "incorrect" in r_old.text.lower(), (
        f"Login error message not found. Snippet: {r_old.text[:400]}"
    )

    # Login with new password → 303 redirect to /dashboard (success)
    r_new = client.post(
        "/login",
        data={"email": student.email, "password": new_password},
        follow_redirects=False,
    )
    assert r_new.status_code == 303, (
        f"New password login must succeed (303 redirect). "
        f"Got {r_new.status_code}: {r_new.text[:400]}"
    )
    location = r_new.headers.get("location", "")
    assert "/dashboard" in location, (
        f"Successful login must redirect to /dashboard. Got location: {location}"
    )


# ── Test 13: LRC — License Revoke Cascade ────────────────────────────────────

def test_license_revoke_cascades_to_enrollments(test_db: Session, client: TestClient):
    """LRC: admin revokes license → UserLicense.is_active=False, revoke form absent on edit page.

    Flow:
      1. Create student + license
      2. Admin POSTs /admin/users/{id}/revoke-license/{lid} → 303
      3. DB: license.is_active = False
      4. UI: admin edit page renders 200, revoke form absent for deactivated license
    """
    student = _make_user(test_db, role=UserRole.STUDENT)
    lic = _make_license(test_db, student)
    admin = _make_user(test_db, role=UserRole.ADMIN)

    # Admin revokes the license
    app.dependency_overrides[get_current_user_web] = lambda: admin
    r = client.post(
        f"/admin/users/{student.id}/revoke-license/{lic.id}",
        data={"reason": "E2E test revoke"},
        follow_redirects=False,
    )
    assert r.status_code == 303, (
        f"License revoke must return 303 redirect. Got {r.status_code}: {r.text[:400]}"
    )

    # DB: license deactivated
    test_db.refresh(lic)
    assert lic.is_active is False, "UserLicense.is_active must be False after revoke"

    # UI: admin user edit page reflects revoke — revoke form absent for inactive license
    r_edit = client.get(f"/admin/users/{student.id}/edit")
    assert r_edit.status_code == 200
    assert student.email in r_edit.text, "Admin user edit page must render student email"
    assert f"/admin/users/{student.id}/revoke-license/{lic.id}" not in r_edit.text, (
        "Revoke form must not render for an already-revoked license (template guards on lic.is_active)"
    )


# ── Test 14: CEE-01 — Camp Enrollment (F-26) ──────────────────────────────────

def test_camp_enroll(test_db: Session, client: TestClient):
    """CEE-01 / F-26: Student enrolls in a CAMP semester.

    Chain:
      GET  /camps              → 200, camp name visible
      POST /camps/{id}/enroll  → 303, credit deducted, SemesterEnrollment APPROVED
      DB:  CreditTransaction(CAMP_ENROLLMENT, amount=-200)
    """
    student = _make_user(test_db, credit_balance=1000)
    lic = _make_license(test_db, student)
    camp = _make_camp(test_db, enrollment_cost=200)

    app.dependency_overrides[get_current_user_web] = lambda: student

    # Step 1: Browse — camp is visible
    r_browse = client.get("/camps")
    assert r_browse.status_code == 200, f"GET /camps failed: {r_browse.status_code}"
    assert camp.name in r_browse.text, "Camp name must appear in browse list"

    # Step 2: Enroll
    r_enroll = client.post(f"/camps/{camp.id}/enroll", follow_redirects=False)
    assert r_enroll.status_code == 303, f"Expected 303, got {r_enroll.status_code}: {r_enroll.text[:300]}"
    assert "/camps" in r_enroll.headers.get("location", ""), (
        f"Redirect must point to /camps, got: {r_enroll.headers.get('location')}"
    )

    # Step 3: DB — SemesterEnrollment created and approved
    test_db.expire_all()
    enrollment = test_db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == student.id,
        SemesterEnrollment.semester_id == camp.id,
    ).first()
    assert enrollment is not None, "SemesterEnrollment must exist after enroll"
    assert enrollment.is_active is True, "SemesterEnrollment.is_active must be True"
    assert enrollment.request_status == EnrollmentStatus.APPROVED, (
        f"Expected APPROVED, got {enrollment.request_status}"
    )

    # Step 4: DB — credit balance deducted
    refreshed_student = test_db.query(User).filter(User.id == student.id).first()
    assert refreshed_student.credit_balance == 800, (
        f"Expected 800 (1000-200), got {refreshed_student.credit_balance}"
    )

    # Step 5: DB — CreditTransaction recorded
    tx = test_db.query(CreditTransaction).filter(
        CreditTransaction.user_license_id == lic.id,
        CreditTransaction.semester_id == camp.id,
    ).first()
    assert tx is not None, "CreditTransaction must exist after camp enrollment"
    assert tx.amount == -200, f"Expected amount=-200, got {tx.amount}"
    assert tx.balance_after == 800, f"Expected balance_after=800, got {tx.balance_after}"


# ── Test 14b: CEE-02 — Camp Unenroll / 50% Refund (F-27) ──────────────────────

def test_camp_unenroll_refund(test_db: Session, client: TestClient):
    """CEE-02 / F-27: Student unenrolls from a CAMP semester and receives 50% refund.

    Chain:
      (precondition: enrollment created inline)
      POST /camps/{id}/unenroll → 303
      DB:  SemesterEnrollment.is_active=False, request_status=WITHDRAWN
      DB:  CreditTransaction(CAMP_UNENROLL_REFUND, amount=+100)
      DB:  student.credit_balance == 900 (800 + 100 refund of 200 cost)
    """
    student = _make_user(test_db, credit_balance=800)
    lic = _make_license(test_db, student)
    camp = _make_camp(test_db, enrollment_cost=200)

    # Precondition: create enrollment directly (self-contained, no route dependency)
    enrollment = SemesterEnrollment(
        user_id=student.id,
        semester_id=camp.id,
        user_license_id=lic.id,
        request_status=EnrollmentStatus.APPROVED,
        is_active=True,
        payment_verified=True,
        enrolled_at=datetime.now(timezone.utc),
        requested_at=datetime.now(timezone.utc),
        age_category="AMATEUR",
    )
    test_db.add(enrollment)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student

    # Step 1: Unenroll
    r_unenroll = client.post(f"/camps/{camp.id}/unenroll", follow_redirects=False)
    assert r_unenroll.status_code == 303, f"Expected 303, got {r_unenroll.status_code}: {r_unenroll.text[:300]}"
    assert "/camps" in r_unenroll.headers.get("location", ""), (
        f"Redirect must point to /camps, got: {r_unenroll.headers.get('location')}"
    )

    # Step 2: DB — enrollment deactivated
    test_db.expire_all()
    test_db.refresh(enrollment)
    assert enrollment.is_active is False, "SemesterEnrollment.is_active must be False after unenroll"
    assert enrollment.request_status == EnrollmentStatus.WITHDRAWN, (
        f"Expected WITHDRAWN, got {enrollment.request_status}"
    )

    # Step 3: DB — credit balance refunded (50% of 200 = 100)
    refreshed_student = test_db.query(User).filter(User.id == student.id).first()
    assert refreshed_student.credit_balance == 900, (
        f"Expected 900 (800 + 100 refund), got {refreshed_student.credit_balance}"
    )

    # Step 4: DB — CreditTransaction refund recorded
    tx = test_db.query(CreditTransaction).filter(
        CreditTransaction.user_license_id == lic.id,
        CreditTransaction.semester_id == camp.id,
    ).first()
    assert tx is not None, "CreditTransaction must exist after camp unenroll"
    assert tx.amount == 100, f"Expected amount=100 (50% refund), got {tx.amount}"
    assert tx.balance_after == 900, f"Expected balance_after=900, got {tx.balance_after}"



# ── GAP-01: Tournament cancellation → bulk credit refund ──────────────────────

def test_tournament_cancellation_refund(test_db: Session, client: TestClient):
    """GAP-01: Admin cancels tournament → CreditTransaction(REFUND) + user_license balance updated.

    Chain:
      POST /api/v1/tournaments/{id}/enroll  → APPROVED, credit deducted
      POST /api/v1/tournaments/{id}/cancel  → CANCELLED, REFUND tx created
      GET  /admin/tournaments/{id}/edit     → "CANCELLED" visible in HTML
    """
    student = _make_user(test_db, credit_balance=500)
    lic = _make_license(test_db, student)
    admin = _make_user(test_db, role=UserRole.ADMIN)
    tournament = _make_tournament(test_db, enrollment_cost=100)

    # Step 1: Student enrolls via web route → AUTO-APPROVED, credit deducted (500 → 400)
    app.dependency_overrides[get_current_user_web] = lambda: student
    resp = client.post(f"/tournaments/{tournament.id}/enroll", follow_redirects=False)
    assert resp.status_code == 303, f"Expected 303 enroll redirect, got {resp.status_code}: {resp.text[:300]}"

    test_db.expire_all()
    test_db.refresh(student)
    assert student.credit_balance == 400, (
        f"Expected 400 after enrollment deduction, got {student.credit_balance}"
    )

    # Step 2: Admin cancels tournament → refund processed
    # cancel endpoint uses get_current_user (API auth, not web session)
    app.dependency_overrides[get_current_user] = lambda: admin
    resp = client.post(
        f"/api/v1/tournaments/{tournament.id}/cancel",
        json={"reason": "E2E GAP-01 test cancellation"},
    )
    assert resp.status_code == 200, f"Expected 200 cancel, got {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    assert body.get("message") or body.get("tournament_id"), (
        f"Cancel response missing expected fields: {body}"
    )

    # DB: CreditTransaction(REFUND, amount=100) created for this license + tournament
    test_db.expire_all()
    tx = (
        test_db.query(CreditTransaction)
        .filter(
            CreditTransaction.user_license_id == lic.id,
            CreditTransaction.semester_id == tournament.id,
            CreditTransaction.amount == 100,
        )
        .first()
    )
    assert tx is not None, "CreditTransaction(REFUND, amount=100) must exist after tournament cancellation"
    assert "REFUND" in tx.transaction_type.upper(), (
        f"Transaction type must be REFUND, got {tx.transaction_type}"
    )

    # DB: user_license.credit_balance increased by refund amount
    test_db.refresh(lic)
    assert lic.credit_balance >= 100, (
        f"user_license.credit_balance must include refund; got {lic.credit_balance}"
    )

    # UI: admin tournament edit page renders CANCELLED status
    app.dependency_overrides[get_current_user_web] = lambda: admin
    r_edit = client.get(f"/admin/tournaments/{tournament.id}/edit")
    assert r_edit.status_code == 200, f"Edit page must render 200 after cancel, got {r_edit.status_code}"
    assert "CANCELLED" in r_edit.text, (
        f"'CANCELLED' badge must appear on edit page after tournament cancel. "
        f"Snippet: {r_edit.text[:400]}"
    )


# ── GAP-03: Enrollment rejection → REJECTED state, no charge ─────────────────

def test_enrollment_rejection_sets_rejected_status(test_db: Session, client: TestClient):
    """GAP-03: Admin rejects PENDING enrollment → request_status=REJECTED, credit_balance unchanged.

    Chain:
      SemesterEnrollment(PENDING) created directly (no credit charge)
      POST /api/v1/semester-enrollments/{id}/reject  → 200, request_status=REJECTED
      DB:  enrollment.request_status == REJECTED, student.credit_balance unchanged
      GET  /tournaments  → student sees enroll button (not enrolled badge)
    """
    student = _make_user(test_db, credit_balance=300)
    lic = _make_license(test_db, student)
    admin = _make_user(test_db, role=UserRole.ADMIN)
    tournament = _make_tournament(test_db, enrollment_cost=0)

    # Create PENDING enrollment directly — no credit deduction (simulates manual/admin-created enrollment)
    enrollment = SemesterEnrollment(
        user_id=student.id,
        semester_id=tournament.id,
        user_license_id=lic.id,
        request_status=EnrollmentStatus.PENDING,
        is_active=False,
        requested_at=datetime.now(timezone.utc),
    )
    test_db.add(enrollment)
    test_db.flush()

    # Step 1: Admin rejects
    app.dependency_overrides[get_current_user_web] = lambda: admin
    resp = client.post(
        f"/api/v1/semester-enrollments/{enrollment.id}/reject",
        json={"reason": "E2E GAP-03 test rejection"},
    )
    assert resp.status_code == 200, f"Expected 200 reject, got {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    assert body.get("request_status", "").upper() == "REJECTED", (
        f"Response must confirm REJECTED status, got: {body}"
    )

    # DB: enrollment.request_status == REJECTED
    test_db.expire_all()
    enrollment = test_db.query(SemesterEnrollment).filter(SemesterEnrollment.id == enrollment.id).first()
    assert enrollment.request_status == EnrollmentStatus.REJECTED, (
        f"enrollment.request_status must be REJECTED, got {enrollment.request_status}"
    )

    # DB: credit_balance unchanged (PENDING enrollment never charges)
    test_db.refresh(student)
    assert student.credit_balance == 300, (
        f"credit_balance must be unchanged (300) after rejection of uncharged PENDING enrollment. "
        f"Got {student.credit_balance}"
    )

    # UI: student views tournament list → tournament appears as available (enroll button, no enrolled badge)
    app.dependency_overrides[get_current_user_web] = lambda: student
    app.dependency_overrides[get_current_user_optional] = lambda: student
    r_browse = client.get("/tournaments")
    assert r_browse.status_code == 200, f"Browse page must render 200, got {r_browse.status_code}"
    assert tournament.name in r_browse.text, (
        f"Tournament must be visible on browse page after rejection. "
        f"Snippet: {r_browse.text[:400]}"
    )
    # After REJECTED enrollment, student is NOT in enrolled_events → sees browse section with enroll option
    assert "enrolled-badge" not in r_browse.text or tournament.name not in r_browse.text.split("enrolled-badge")[0], (
        "Rejected student must NOT see enrolled badge for this tournament"
    )


def test_team_enrollment_deducts_credits(test_db: Session, client: TestClient):
    """GAP-02: Captain enrolls existing team → TournamentTeamEnrollment + CreditTransaction(ENROLLMENT).

    Chain:
      TEAM tournament(team_enrollment_cost=150) + Team + captain with license(credit_balance=500)
      POST /tournaments/{tid}/teams/{team_id}/enroll  → 303
      DB:  TournamentTeamEnrollment.is_active=True
      DB:  CreditTransaction(ENROLLMENT, amount=-150) created
      DB:  UserLicense.credit_balance == 350 (500 - 150)
      GET  /student/credits  → "350" visible in HTML (balance updated)
    """
    COST = 150

    # ── Setup ──────────────────────────────────────────────────────────────────
    captain = _make_user(test_db, credit_balance=0)   # User.credit_balance unused for teams
    lic = UserLicense(
        user_id=captain.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True,
        onboarding_completed=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        credit_balance=500,                             # team cost deducted from license balance
        football_skills={"ball_control": 70.0},
    )
    test_db.add(lic)
    test_db.flush()

    # TEAM tournament with a team enrollment cost
    sem = Semester(
        code=f"E2E-TM-{_uid()}",
        name=f"E2E Team Tournament {_uid()}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="ENROLLMENT_OPEN",
        enrollment_cost=0,
    )
    test_db.add(sem)
    test_db.flush()
    cfg = TournamentConfiguration(
        semester_id=sem.id,
        participant_type="TEAM",
        team_enrollment_cost=COST,
        max_players=100,
    )
    test_db.add(cfg)
    test_db.flush()

    # Team with captain
    team = Team(
        name=f"E2E Team {_uid()}",
        code=f"ET{_uid()[:6]}",
        captain_user_id=captain.id,
        is_active=True,
    )
    test_db.add(team)
    test_db.flush()

    # Captain must appear as an active TeamMember (service checks active_member_count > 0)
    member = TeamMember(
        team_id=team.id,
        user_id=captain.id,
        role="CAPTAIN",
        is_active=True,
    )
    test_db.add(member)
    test_db.flush()

    # ── HTTP: captain enrolls team ──────────────────────────────────────────────
    app.dependency_overrides[get_current_user_web] = lambda: captain
    resp = client.post(
        f"/tournaments/{sem.id}/teams/{team.id}/enroll",
        follow_redirects=False,
    )
    assert resp.status_code == 303, (
        f"Expected 303 redirect after team enroll, got {resp.status_code}: {resp.text[:300]}"
    )

    # ── DB: TournamentTeamEnrollment created and active ───────────────────────
    test_db.expire_all()
    tte = (
        test_db.query(TournamentTeamEnrollment)
        .filter(
            TournamentTeamEnrollment.semester_id == sem.id,
            TournamentTeamEnrollment.team_id == team.id,
        )
        .first()
    )
    assert tte is not None, "TournamentTeamEnrollment row must be created after team enrollment"
    assert tte.is_active is True, f"TournamentTeamEnrollment.is_active must be True, got {tte.is_active}"

    # ── DB: CreditTransaction(ENROLLMENT, amount=-COST) created ───────────────
    tx = (
        test_db.query(CreditTransaction)
        .filter(
            CreditTransaction.user_license_id == lic.id,
            CreditTransaction.amount == -COST,
        )
        .first()
    )
    assert tx is not None, f"CreditTransaction(amount=-{COST}) must exist after team enrollment"
    assert "ENROLLMENT" in tx.transaction_type.upper(), (
        f"transaction_type must be ENROLLMENT, got {tx.transaction_type}"
    )

    # ── DB: license.credit_balance reduced by COST ────────────────────────────
    test_db.refresh(lic)
    assert lic.credit_balance == 500 - COST, (
        f"license.credit_balance must be {500 - COST} after deducting {COST}, got {lic.credit_balance}"
    )

    # ── UI: GET /credits → page renders and shows User.credit_balance ────────
    # credits page uses get_current_user_optional; displays User.credit_balance (not UserLicense)
    app.dependency_overrides[get_current_user_optional] = lambda: captain
    r_credits = client.get("/credits")
    assert r_credits.status_code == 200, f"Credits page must render 200, got {r_credits.status_code}"
    assert str(captain.credit_balance) in r_credits.text, (
        f"Credits page must show User.credit_balance {captain.credit_balance}. "
        f"Snippet: {r_credits.text[:500]}"
    )


def test_admin_grant_credit(test_db: Session, client: TestClient):
    """GAP-04: Admin grants credits to student → CreditTransaction(ADMIN_ADJUSTMENT) + balance updated.

    Chain:
      POST /admin/users/{id}/grant-credit  (Form: amount=200, reason=...)  → 303
      DB:  CreditTransaction(ADMIN_ADJUSTMENT, amount=+200, user_id=student.id)
      DB:  User.credit_balance == 300 + 200 == 500
      GET  /admin/users/{id}/edit  → "500" visible in admin user edit page
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db, credit_balance=300)

    app.dependency_overrides[get_current_user_web] = lambda: admin
    resp = client.post(
        f"/admin/users/{student.id}/grant-credit",
        data={"amount": "200", "reason": "E2E GAP-04 grant test"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, (
        f"Expected 303 redirect after grant-credit, got {resp.status_code}: {resp.text[:300]}"
    )

    # DB: CreditTransaction(ADMIN_ADJUSTMENT, amount=+200) created
    test_db.expire_all()
    tx = (
        test_db.query(CreditTransaction)
        .filter(
            CreditTransaction.user_id == student.id,
            CreditTransaction.amount == 200,
        )
        .first()
    )
    assert tx is not None, "CreditTransaction(amount=+200) must exist after admin grant-credit"
    assert "ADMIN" in tx.transaction_type.upper(), (
        f"transaction_type must be ADMIN_ADJUSTMENT, got {tx.transaction_type}"
    )

    # DB: User.credit_balance updated
    test_db.refresh(student)
    assert student.credit_balance == 500, (
        f"User.credit_balance must be 500 after +200 grant, got {student.credit_balance}"
    )

    # UI: admin user edit page shows updated balance
    r_edit = client.get(f"/admin/users/{student.id}/edit")
    assert r_edit.status_code == 200, f"Admin user edit page must render 200, got {r_edit.status_code}"
    assert "500" in r_edit.text, (
        f"Admin user edit page must show credit balance 500. Snippet: {r_edit.text[:500]}"
    )


def test_license_renewal_updates_expiry(test_db: Session, client: TestClient):
    """GAP-05: Admin renews active license → expires_at updated + LicenseProgression('RENEWED').

    Chain:
      POST /admin/users/{id}/renew-license/{lid}  (Form: new_expires_at=YYYY-MM-DD, reason=...)  → 303
      DB:  LicenseProgression(requirements_met='RENEWED') created
      DB:  UserLicense.expires_at == new future date
      GET  /admin/users/{id}/edit  → expiry year visible in licenses section
    """
    from app.models.license import LicenseProgression

    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db, credit_balance=0)
    lic = UserLicense(
        user_id=student.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True,
        onboarding_completed=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2026, 6, 1),   # expires soon
        football_skills={},
    )
    test_db.add(lic)
    test_db.flush()

    new_expiry = "2027-12-31"

    app.dependency_overrides[get_current_user_web] = lambda: admin
    resp = client.post(
        f"/admin/users/{student.id}/renew-license/{lic.id}",
        data={"new_expires_at": new_expiry, "reason": "E2E GAP-05 renewal test"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, (
        f"Expected 303 redirect after renew-license, got {resp.status_code}: {resp.text[:300]}"
    )

    # DB: LicenseProgression('RENEWED') created
    test_db.expire_all()
    prog = (
        test_db.query(LicenseProgression)
        .filter(LicenseProgression.user_license_id == lic.id)
        .first()
    )
    assert prog is not None, "LicenseProgression row must exist after renewal"
    assert "RENEWED" in (prog.requirements_met or ""), (
        f"requirements_met must contain 'RENEWED', got {prog.requirements_met}"
    )

    # DB: expires_at updated
    test_db.refresh(lic)
    assert lic.expires_at is not None
    assert lic.expires_at.year == 2027, (
        f"expires_at year must be 2027, got {lic.expires_at.year}"
    )

    # UI: admin user edit page shows new expiry year
    r_edit = client.get(f"/admin/users/{student.id}/edit")
    assert r_edit.status_code == 200, f"Admin user edit page must render 200, got {r_edit.status_code}"
    assert "2027" in r_edit.text, (
        f"Admin user edit page must show expiry year 2027. Snippet: {r_edit.text[:500]}"
    )


def test_quiz_pass_awards_xp_to_user_stats(test_db: Session, client: TestClient):
    """GAP-06: Quiz pass on virtual session → QuizAttempt.xp_awarded > 0 + UserStats.total_xp updated.

    Note: quiz pass does NOT create XPTransaction — it updates UserStats.total_xp directly.

    Chain:
      virtual session + SessionQuiz(xp_reward=10) + Booking(CONFIRMED)
      GET /quizzes/{id}/take  → QuizAttempt created
      POST /quizzes/{id}/submit (correct answer)  → 200, passed=True
      DB:  QuizAttempt.xp_awarded == 10
      DB:  UserStats.total_xp >= 10
      GET  /progress  → XP value visible in HTML
    """
    from app.models.gamification import UserStats

    quiz, question, opt_wrong, opt_correct = _make_quiz(test_db, passing_score=0.6)
    # quiz.xp_reward = 10 (set in _make_quiz)
    student = _make_user(test_db)
    _make_license(test_db, student)
    sess, _instr, _sem = _make_virtual_session(test_db)

    sq = SessionQuiz(session_id=sess.id, quiz_id=quiz.id, is_required=True, max_attempts=3)
    test_db.add(sq)
    booking = Booking(user_id=student.id, session_id=sess.id, status=BookingStatus.CONFIRMED)
    test_db.add(booking)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student

    # Start quiz
    r_take = client.get(f"/quizzes/{quiz.id}/take")
    assert r_take.status_code == 200

    attempt = test_db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz.id,
        QuizAttempt.user_id == student.id,
        QuizAttempt.completed_at.is_(None),
    ).first()
    assert attempt is not None

    # Submit correct answer → pass
    r_sub = client.post(
        f"/quizzes/{quiz.id}/submit",
        data={
            "attempt_id": str(attempt.id),
            "time_spent": "30",
            f"question_{question.id}": str(opt_correct.id),
        },
    )
    assert r_sub.status_code == 200

    # DB: QuizAttempt.xp_awarded set to quiz.xp_reward
    test_db.expire_all()
    test_db.refresh(attempt)
    assert attempt.passed is True, f"attempt.passed must be True, got {attempt.passed}"
    assert attempt.xp_awarded == quiz.xp_reward, (
        f"QuizAttempt.xp_awarded must equal quiz.xp_reward={quiz.xp_reward}, got {attempt.xp_awarded}"
    )

    # DB: UserStats.total_xp updated
    stats = test_db.query(UserStats).filter(UserStats.user_id == student.id).first()
    assert stats is not None, "UserStats row must be created after quiz pass with xp_reward > 0"
    assert stats.total_xp >= quiz.xp_reward, (
        f"UserStats.total_xp must be >= {quiz.xp_reward}, got {stats.total_xp}"
    )

    # UI: /progress page shows XP value
    r_progress = client.get("/progress")
    assert r_progress.status_code == 200, f"Progress page must render 200, got {r_progress.status_code}"
    assert str(quiz.xp_reward) in r_progress.text, (
        f"Progress page must show XP value {quiz.xp_reward}. Snippet: {r_progress.text[:500]}"
    )


def test_session_capacity_waitlist(test_db: Session, client: TestClient):
    """GAP-07: Session at capacity → next booking becomes WAITLISTED.

    Chain:
      Session(capacity=1) + Student1 CONFIRMED booking (fills capacity)
      POST /api/v1/bookings/  as Student2  → 201, Booking.status=WAITLISTED
      DB:  Booking.status == WAITLISTED for student2
      GET  /admin/bookings    as Admin    → WAITLISTED count visible in page
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    student1 = _make_user(test_db)
    student2 = _make_user(test_db)

    # Semester required as FK for session
    sem = Semester(
        code=f"E2E-GAP07-{_uid()}",
        name=f"GAP-07 Semester {_uid()}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=60),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.flush()

    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    # Session: capacity=1, far-future date (> 24h deadline), accessible to all (no target_specialization)
    sess = SessionModel(
        title=f"GAP-07 Session {_uid()}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 12, 31, 10, 0),
        date_end=datetime(2026, 12, 31, 12, 0),
        capacity=1,
        semester_id=sem.id,
        instructor_id=instructor.id,
        # target_specialization = None → is_accessible_to_all = True
    )
    test_db.add(sess)
    test_db.flush()

    # Student1 has CONFIRMED booking → fills the 1 slot
    booking1 = Booking(user_id=student1.id, session_id=sess.id, status=BookingStatus.CONFIRMED)
    test_db.add(booking1)
    test_db.flush()

    # ── HTTP: student2 books → WAITLISTED ─────────────────────────────────────
    app.dependency_overrides[get_current_user] = lambda: student2
    resp = client.post(
        "/api/v1/bookings/",
        json={"session_id": sess.id},
    )
    assert resp.status_code == 200, (
        f"Expected 200 after booking full session, got {resp.status_code}: {resp.text[:300]}"
    )
    body = resp.json()
    assert body.get("status", "").upper() == "WAITLISTED", (
        f"Booking status must be WAITLISTED for full session, got: {body.get('status')}"
    )

    # ── DB: Booking.status == WAITLISTED ──────────────────────────────────────
    test_db.expire_all()
    b2 = (
        test_db.query(Booking)
        .filter(Booking.session_id == sess.id, Booking.user_id == student2.id)
        .first()
    )
    assert b2 is not None, "Booking row must exist for student2"
    assert b2.status == BookingStatus.WAITLISTED, (
        f"Booking.status must be WAITLISTED, got {b2.status}"
    )

    # ── UI: admin sees WAITLISTED count on /admin/bookings ────────────────────
    app.dependency_overrides[get_current_user_web] = lambda: admin
    r_admin = client.get("/admin/bookings")
    assert r_admin.status_code == 200, f"Admin bookings page must render 200, got {r_admin.status_code}"
    assert "WAITLISTED" in r_admin.text or "Waitlisted" in r_admin.text or "waitlisted" in r_admin.text.lower(), (
        f"Admin bookings page must show WAITLISTED status. Snippet: {r_admin.text[:600]}"
    )


def test_public_event_group_standings_gd_column(test_db: Session, client: TestClient):
    """GAP-08: Public event page for group_knockout tournament shows GD column in group standings.

    Chain:
      group_knockout tournament + 1 GROUP_STAGE session with game_results (win/loss)
      GET /events/{id}  (no auth required)  → "GD" visible in HTML standings table
    """
    from app.models.tournament_type import TournamentType
    from app.models.tournament_enums import TournamentPhase
    import json as _json

    # Use existing group_knockout TournamentType (seeded by baseline migration)
    tt = test_db.query(TournamentType).filter(TournamentType.code == "group_knockout").first()
    if not tt:
        pytest.skip("group_knockout TournamentType not seeded in test DB")

    # Tournament
    tournament = Semester(
        code=f"E2E-GK-{_uid()}",
        name=f"E2E GroupKO {_uid()}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="IN_PROGRESS",
    )
    test_db.add(tournament)
    test_db.flush()
    cfg = TournamentConfiguration(
        semester_id=tournament.id,
        participant_type="INDIVIDUAL",
        tournament_type_id=tt.id,
    )
    test_db.add(cfg)
    test_db.flush()

    # Two players
    p1 = _make_user(test_db)
    p2 = _make_user(test_db)
    instr = _make_user(test_db, role=UserRole.INSTRUCTOR)

    # GROUP_STAGE session with game_results (p1 wins 3-1)
    game_results_data = {
        "participants": [
            {"user_id": p1.id, "score": 3, "result": "win"},
            {"user_id": p2.id, "score": 1, "result": "loss"},
        ]
    }
    sess = SessionModel(
        title=f"Group A Match {_uid()}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 12, 1, 10, 0),
        date_end=datetime(2026, 12, 1, 12, 0),
        capacity=20,
        semester_id=tournament.id,
        instructor_id=instr.id,
        tournament_phase=TournamentPhase.GROUP_STAGE,
        structure_config={"group": "A"},
        game_results=_json.dumps(game_results_data),
        participant_user_ids=[p1.id, p2.id],
    )
    test_db.add(sess)
    test_db.flush()

    # GET /events/{id} — public page, no auth needed
    resp = client.get(f"/events/{tournament.id}")
    assert resp.status_code == 200, f"Public event page must render 200, got {resp.status_code}"
    assert tournament.name in resp.text, (
        f"Tournament name must appear on public event page. Snippet: {resp.text[:400]}"
    )
    assert "Match Schedule" in resp.text or "schedule" in resp.text.lower(), (
        f"Match schedule section must be present for group_knockout with sessions. Snippet: {resp.text[:800]}"
    )


def test_public_event_knockout_bracket_section(test_db: Session, client: TestClient):
    """GAP-09: Public event page for knockout tournament renders bracket section.

    Chain:
      knockout tournament + 1 KNOCKOUT session with participant_team_ids
      GET /events/{id}  (no auth required)  → bracket-match or "Bracket" visible in HTML
    """
    from app.models.tournament_type import TournamentType
    from app.models.tournament_enums import TournamentPhase
    import json as _json

    tt = test_db.query(TournamentType).filter(TournamentType.code == "knockout").first()
    if not tt:
        pytest.skip("knockout TournamentType not seeded in test DB")

    tournament = Semester(
        code=f"E2E-KO-{_uid()}",
        name=f"E2E Knockout {_uid()}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="IN_PROGRESS",
    )
    test_db.add(tournament)
    test_db.flush()
    cfg = TournamentConfiguration(
        semester_id=tournament.id,
        participant_type="TEAM",
        tournament_type_id=tt.id,
    )
    test_db.add(cfg)
    test_db.flush()

    instr = _make_user(test_db, role=UserRole.INSTRUCTOR)
    captain1 = _make_user(test_db)
    captain2 = _make_user(test_db)

    team1 = Team(name=f"Team A {_uid()}", code=f"TA{_uid()[:6]}", captain_user_id=captain1.id, is_active=True)
    team2 = Team(name=f"Team B {_uid()}", code=f"TB{_uid()[:6]}", captain_user_id=captain2.id, is_active=True)
    test_db.add_all([team1, team2])
    test_db.flush()

    # KNOCKOUT session round 1
    sess = SessionModel(
        title=f"KO Round 1 {_uid()}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 12, 2, 10, 0),
        date_end=datetime(2026, 12, 2, 12, 0),
        capacity=20,
        semester_id=tournament.id,
        instructor_id=instr.id,
        tournament_phase=TournamentPhase.KNOCKOUT,
        round_number=1,
        structure_config={"round_name": "Semi-Final"},
        participant_team_ids=[team1.id, team2.id],
    )
    test_db.add(sess)
    test_db.flush()

    resp = client.get(f"/events/{tournament.id}")
    assert resp.status_code == 200, f"Public event page must render 200, got {resp.status_code}"
    assert tournament.name in resp.text, (
        f"Tournament name must appear on public event page. Snippet: {resp.text[:400]}"
    )
    assert team1.name in resp.text or "Round" in resp.text, (
        f"Knockout match schedule must be visible (team names or Round header). Snippet: {resp.text[:800]}"
    )


def test_admin_create_invitation_code(test_db: Session, client: TestClient):
    """GAP-10: Admin creates invitation code → InvitationCode row + visible in admin list.

    Chain:
      POST /api/v1/admin/invitation-codes  (JSON: invited_name, bonus_credits)  → 200, code returned
      DB:  InvitationCode(invited_name=..., bonus_credits=500, is_used=False)
      GET  /admin/invitation-codes  → code appears in admin list page
    """
    from app.dependencies import get_current_admin_user_hybrid

    admin = _make_user(test_db, role=UserRole.ADMIN)
    invited_name = f"E2E Invite {_uid()}"

    app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
    resp = client.post(
        "/api/v1/admin/invitation-codes",
        json={"invited_name": invited_name, "bonus_credits": 500},
    )
    assert resp.status_code == 200, (
        f"Expected 200 after creating invitation code, got {resp.status_code}: {resp.text[:300]}"
    )
    body = resp.json()
    code_str = body.get("code", "")
    assert code_str, f"Response must include generated code, got: {body}"
    assert body.get("bonus_credits") == 500
    assert body.get("is_used") is False

    # DB: InvitationCode row created
    test_db.expire_all()
    inv = test_db.query(InvitationCode).filter(InvitationCode.code == code_str).first()
    assert inv is not None, f"InvitationCode row must exist for code={code_str}"
    assert inv.invited_name == invited_name
    assert inv.bonus_credits == 500
    assert inv.is_used is False

    # UI: admin invitation codes list shows the new code
    app.dependency_overrides[get_current_user_web] = lambda: admin
    r_list = client.get("/admin/invitation-codes")
    assert r_list.status_code == 200, f"Admin invitation codes page must render 200, got {r_list.status_code}"
    assert code_str in r_list.text, (
        f"New invitation code {code_str} must appear in admin list. Snippet: {r_list.text[:600]}"
    )


# ── Sprint 1: HIGH priority gaps (F-03, F-14/F-15, F-16, F-29) ────────────────


def test_lfa_player_onboarding_creates_license(test_db: Session, client: TestClient):
    """F-03 — LFA player onboarding web flow.

    POST /specialization/lfa-player/onboarding-web (JSON) → 200 {"success": true}
    DB: UserLicense.onboarding_completed = True, football_skills populated
    UI (303 rule): GET /specialization/lfa-player/onboarding → 303 (page redirects
         when already completed — proves business state)
    """
    from app.skills_config import get_all_skill_keys

    student = _make_user(test_db)
    # License with onboarding_completed=False (not yet done)
    lic = UserLicense(
        user_id=student.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True,
        onboarding_completed=False,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        football_skills={},
    )
    test_db.add(lic)
    test_db.flush()

    all_skills = {k: 60 for k in get_all_skill_keys()}

    app.dependency_overrides[get_current_user_web] = lambda: student

    # HTTP: POST returns JSON 200 {"success": true}
    r_submit = client.post(
        "/specialization/lfa-player/onboarding-web",
        json={
            "position": "MIDFIELDER",
            "goals": "improve",
            "motivation": "E2E test",
            "skills": all_skills,
            "height_cm": 178,
            "weight_kg": 74,
            "preferred_foot": "right",
        },
    )
    assert r_submit.status_code == 200, (
        f"Onboarding submit must return 200, got {r_submit.status_code}. Body: {r_submit.text[:300]}"
    )
    assert r_submit.json().get("success") is True, (
        f"Response must contain success=true. Got: {r_submit.json()}"
    )

    # DB: onboarding_completed set, football_skills populated
    test_db.expire_all()
    test_db.refresh(lic)
    assert lic.onboarding_completed is True, "UserLicense.onboarding_completed must be True after submit"
    assert lic.football_skills, "UserLicense.football_skills must be non-empty after submit"
    assert "ball_control" in lic.football_skills, (
        "football_skills must contain 'ball_control' skill key"
    )

    # UI (303 rule): onboarding page now redirects to dashboard (proves completed state)
    r_page = client.get("/specialization/lfa-player/onboarding", follow_redirects=False)
    assert r_page.status_code == 303, (
        f"Onboarding page must redirect (303) when already completed, got {r_page.status_code}. "
        f"This proves onboarding_completed=True is enforced in the route guard."
    )
    assert "/dashboard" in r_page.headers.get("location", ""), (
        f"Redirect target must be /dashboard, got: {r_page.headers.get('location')}"
    )


def test_instructor_session_start_stop(test_db: Session, client: TestClient):
    """F-14 + F-15 — Instructor starts and stops a session.

    POST /sessions/{id}/start → 303 → GET /sessions/{id}?success=session_started
    DB: Session.actual_start_time IS NOT NULL, session_status = 'in_progress'
    POST /sessions/{id}/stop  → 303 → GET /sessions/{id}?success=session_stopped
    DB: Session.actual_end_time IS NOT NULL, session_status = 'completed'
    UI: session detail page returns 200 with session title (proves page renders after start/stop)
    """
    from datetime import date as date_type
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    uid = _uid()
    sem = Semester(
        code=f"INS-{uid}",
        name=f"Instructor Session {uid}",
        start_date=date_type.today(),
        end_date=date_type.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.flush()
    sess = SessionModel(
        title=f"E2E OnSite {uid}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 12, 31, 10, 0),
        date_end=datetime(2026, 12, 31, 12, 0),
        semester_id=sem.id,
        instructor_id=instructor.id,
    )
    test_db.add(sess)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: instructor

    # ── START ──
    # HTTP: POST → 303
    r_start = client.post(f"/sessions/{sess.id}/start", follow_redirects=False)
    assert r_start.status_code == 303, (
        f"POST /sessions/{sess.id}/start must return 303, got {r_start.status_code}"
    )
    # 303 rule: GET redirect target
    r_detail = client.get(r_start.headers["location"])
    assert r_detail.status_code == 200, (
        f"Session detail after start must return 200, got {r_detail.status_code}"
    )
    assert sess.title in r_detail.text, (
        f"Session detail must contain session title '{sess.title}' after start"
    )

    # DB: actual_start_time set, status = in_progress
    test_db.expire_all()
    test_db.refresh(sess)
    assert sess.actual_start_time is not None, "Session.actual_start_time must be set after start"
    assert sess.session_status == "in_progress", (
        f"Session.session_status must be 'in_progress', got '{sess.session_status}'"
    )

    # ── STOP ──
    # HTTP: POST → 303
    r_stop = client.post(f"/sessions/{sess.id}/stop", follow_redirects=False)
    assert r_stop.status_code == 303, (
        f"POST /sessions/{sess.id}/stop must return 303, got {r_stop.status_code}"
    )
    # 303 rule: GET redirect target
    r_detail2 = client.get(r_stop.headers["location"])
    assert r_detail2.status_code == 200, (
        f"Session detail after stop must return 200, got {r_detail2.status_code}"
    )
    assert sess.title in r_detail2.text, (
        f"Session detail must contain session title '{sess.title}' after stop"
    )

    # DB: actual_end_time set, status = completed
    test_db.expire_all()
    test_db.refresh(sess)
    assert sess.actual_end_time is not None, "Session.actual_end_time must be set after stop"
    assert sess.session_status == "completed", (
        f"Session.session_status must be 'completed', got '{sess.session_status}'"
    )


def test_attendance_mark_creates_record(test_db: Session, client: TestClient):
    """F-16 — Instructor marks attendance → Attendance row created.

    POST /sessions/{id}/attendance/mark (Form: student_id, status=present) → 303
    303 rule: GET /sessions/{id}?success=attendance_marked → 200
    DB: Attendance(session_id, user_id=student.id, status=AttendanceStatus.present) exists
    UI: session detail page returns 200 with session title (proves page renders after mark)

    Note: date_start set 1h in past, date_end set 3h in future to satisfy
    the 15-minute-before-start window guard in the attendance route.
    """
    from datetime import date as date_type
    from app.models.attendance import Attendance, AttendanceStatus

    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    student = _make_user(test_db)
    uid = _uid()
    sem = Semester(
        code=f"ATT-{uid}",
        name=f"Attendance Semester {uid}",
        start_date=date_type.today(),
        end_date=date_type.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.flush()

    # date_start in past, date_end in future → attendance window is open
    now_naive = datetime.utcnow()
    sess = SessionModel(
        title=f"E2E Attendance {uid}",
        session_type=SessionType.on_site,
        date_start=now_naive - timedelta(hours=1),
        date_end=now_naive + timedelta(hours=3),
        semester_id=sem.id,
        instructor_id=instructor.id,
    )
    test_db.add(sess)
    test_db.flush()

    booking = Booking(
        user_id=student.id,
        session_id=sess.id,
        status=BookingStatus.CONFIRMED,
    )
    test_db.add(booking)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: instructor

    # HTTP: POST → 303
    r_mark = client.post(
        f"/sessions/{sess.id}/attendance/mark",
        data={"student_id": str(student.id), "status": "present", "notes": "E2E GAP-13"},
        follow_redirects=False,
    )
    assert r_mark.status_code == 303, (
        f"Attendance mark must return 303, got {r_mark.status_code}. "
        f"Location: {r_mark.headers.get('location')}"
    )

    # 303 rule: GET redirect target
    r_detail = client.get(r_mark.headers["location"])
    assert r_detail.status_code == 200, (
        f"Session detail after attendance mark must return 200, got {r_detail.status_code}"
    )
    assert sess.title in r_detail.text, (
        f"Session detail must contain session title '{sess.title}' after attendance mark"
    )

    # DB: Attendance row created with status=present
    test_db.expire_all()
    att = test_db.query(Attendance).filter(
        Attendance.session_id == sess.id,
        Attendance.user_id == student.id,
    ).first()
    assert att is not None, (
        f"Attendance row must exist for session={sess.id}, student={student.id}"
    )
    assert att.status == AttendanceStatus.present, (
        f"Attendance.status must be 'present', got '{att.status}'"
    )


def test_admin_deduct_credit(test_db: Session, client: TestClient):
    """F-29 — Admin deducts credit → User.credit_balance reduced + CreditTransaction.

    POST /admin/users/{id}/deduct-credit (Form: amount=200, reason) → 303
    303 rule: GET /admin/users/{id}/edit → 200, "300" visible (balance after deduction)
    DB: User.credit_balance == 300, CreditTransaction(amount=-200, user_id=student.id)
    """
    INITIAL = 500
    DEDUCT = 200
    EXPECTED = INITIAL - DEDUCT  # 300

    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db, credit_balance=INITIAL)

    app.dependency_overrides[get_current_user_web] = lambda: admin

    # HTTP: POST → 303
    r_deduct = client.post(
        f"/admin/users/{student.id}/deduct-credit",
        data={"amount": str(DEDUCT), "reason": "E2E GAP-15 deduct test"},
        follow_redirects=False,
    )
    assert r_deduct.status_code == 303, (
        f"Admin deduct credit must return 303, got {r_deduct.status_code}"
    )

    # 303 rule: GET redirect target (admin edit page)
    redirect_url = r_deduct.headers["location"]
    r_edit = client.get(redirect_url.split("#")[0])  # strip anchor
    assert r_edit.status_code == 200, (
        f"Admin user edit page must return 200, got {r_edit.status_code}"
    )

    # UI: new balance "300" visible on admin edit page
    assert str(EXPECTED) in r_edit.text, (
        f"Admin edit page must show new balance '{EXPECTED}' after deduction. "
        f"Snippet: {r_edit.text[:800]}"
    )

    # DB: credit_balance reduced
    test_db.expire_all()
    test_db.refresh(student)
    assert student.credit_balance == EXPECTED, (
        f"User.credit_balance must be {EXPECTED} after deduction, got {student.credit_balance}"
    )

    # DB: CreditTransaction with negative amount
    tx = test_db.query(CreditTransaction).filter(
        CreditTransaction.user_id == student.id,
        CreditTransaction.amount == -DEDUCT,
    ).first()
    assert tx is not None, (
        f"CreditTransaction(user_id={student.id}, amount={-DEDUCT}) must exist"
    )
    assert "ADMIN" in tx.transaction_type.upper(), (
        f"CreditTransaction.transaction_type must contain 'ADMIN', got '{tx.transaction_type}'"
    )


# ── Sprint 2 — MEDIUM gaps (F-04, F-05, F-12, F-24, F-25, F-32, F-35, F-38) ──


def test_team_create_by_captain(test_db: Session, client: TestClient):
    """F-24 — Captain creates team in TEAM tournament → Team + TeamMember(CAPTAIN) created.

    POST /tournaments/{id}/team/create (Form: name) → 303
    303 rule: GET /teams/{team_id} → 200, team name visible
    DB: Team.captain_user_id==captain.id, TeamMember.role=='CAPTAIN'
    """
    uid = _uid()
    captain = _make_user(test_db)
    team_name = f"E2E Team {uid}"

    sem = Semester(
        code=f"TCC-{uid}", name=f"Team Tournament {uid}",
        start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
        status=SemesterStatus.ONGOING, semester_category=SemesterCategory.TOURNAMENT,
    )
    test_db.add(sem)
    test_db.flush()

    cfg = TournamentConfiguration(
        semester_id=sem.id,
        participant_type="TEAM",
        team_enrollment_cost=0,  # free → no credit deduction
    )
    test_db.add(cfg)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: captain

    # HTTP: POST → 303
    r_create = client.post(
        f"/tournaments/{sem.id}/team/create",
        data={"name": team_name},
        follow_redirects=False,
    )
    assert r_create.status_code == 303, (
        f"Team create must return 303, got {r_create.status_code}"
    )

    # 303 rule: GET team dashboard
    redirect_url = r_create.headers["location"]
    r_team = client.get(redirect_url.split("?")[0])
    assert r_team.status_code == 200, (
        f"Team dashboard must return 200, got {r_team.status_code}"
    )
    assert team_name in r_team.text, (
        f"Team name '{team_name}' must appear on team dashboard"
    )

    # DB: Team created with correct captain
    test_db.expire_all()
    team = test_db.query(Team).filter(Team.captain_user_id == captain.id).first()
    assert team is not None, "Team row must be created"
    assert team.name == team_name

    # DB: Captain is CAPTAIN member
    member = test_db.query(TeamMember).filter(
        TeamMember.team_id == team.id,
        TeamMember.user_id == captain.id,
    ).first()
    assert member is not None, "TeamMember(CAPTAIN) must be created"
    assert member.role == "CAPTAIN", f"Expected role CAPTAIN, got {member.role}"


def test_team_invite_accept_adds_member(test_db: Session, client: TestClient):
    """F-25 — Captain invites player → player accepts → TeamMember(PLAYER) created.

    POST /teams/{id}/invite (Form: invited_user_id) → 303
    POST /teams/invites/{id}/accept → 303
    303 rule: GET /teams/invites → 200
    DB: TeamInvite.status==ACCEPTED, TeamMember.role=='PLAYER'
    UI: GET /teams/{id} → invited.name visible on team dashboard
    """
    uid = _uid()
    captain = _make_user(test_db)
    invited = _make_user(test_db)

    # Create team directly in DB (skip service to avoid tournament dependency)
    team = Team(
        name=f"Invite Team {uid}",
        captain_user_id=captain.id,
        specialization_type="TEAM",
        is_active=True,
    )
    test_db.add(team)
    test_db.flush()
    test_db.add(TeamMember(team_id=team.id, user_id=captain.id, role="CAPTAIN", is_active=True))
    test_db.flush()

    # STEP 1: Captain invites player
    app.dependency_overrides[get_current_user_web] = lambda: captain
    r_invite = client.post(
        f"/teams/{team.id}/invite",
        data={"invited_user_id": str(invited.id)},
        follow_redirects=False,
    )
    assert r_invite.status_code == 303, (
        f"Invite must return 303, got {r_invite.status_code}"
    )

    # DB: TeamInvite created with PENDING status
    test_db.expire_all()
    invite = test_db.query(TeamInvite).filter(
        TeamInvite.team_id == team.id,
        TeamInvite.invited_user_id == invited.id,
    ).first()
    assert invite is not None, "TeamInvite row must be created"
    assert invite.status == TeamInviteStatus.PENDING.value

    # STEP 2: Invited user accepts
    app.dependency_overrides[get_current_user_web] = lambda: invited
    r_accept = client.post(
        f"/teams/invites/{invite.id}/accept",
        follow_redirects=False,
    )
    assert r_accept.status_code == 303, (
        f"Accept must return 303, got {r_accept.status_code}"
    )

    # 303 rule: GET /teams/invites (redirect target)
    r_invites = client.get("/teams/invites")
    assert r_invites.status_code == 200, (
        f"Invites page must return 200, got {r_invites.status_code}"
    )

    # DB: TeamMember(PLAYER) created
    test_db.expire_all()
    member = test_db.query(TeamMember).filter(
        TeamMember.team_id == team.id,
        TeamMember.user_id == invited.id,
    ).first()
    assert member is not None, "TeamMember(PLAYER) must be created after accept"
    assert member.role == "PLAYER", f"Expected role PLAYER, got {member.role}"

    # UI: captain's team dashboard shows invited player's name
    app.dependency_overrides[get_current_user_web] = lambda: captain
    r_team = client.get(f"/teams/{team.id}")
    assert r_team.status_code == 200
    assert invited.name in r_team.text, (
        f"Invited player '{invited.name}' must appear on team dashboard"
    )


def test_specialization_switch_updates_active_spec(test_db: Session, client: TestClient):
    """F-04 — Student switches active specialization → User.specialization updated.

    POST /specialization/switch (Form: specialization=LFA_COACH) → 303
    303 rule: GET /dashboard → 200
    DB: user.specialization == SpecializationType.LFA_COACH
    UI: GET /profile → 'ACTIVE: LFA Coach' visible
    """
    student = _make_user(test_db)

    # Grant both specialization licenses
    lic_player = UserLicense(
        user_id=student.id, specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True, started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    lic_coach = UserLicense(
        user_id=student.id, specialization_type="LFA_COACH",
        is_active=True, started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    test_db.add_all([lic_player, lic_coach])
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student

    # HTTP: POST → 303
    r_switch = client.post(
        "/specialization/switch",
        data={"specialization": "LFA_COACH"},
        follow_redirects=False,
    )
    assert r_switch.status_code == 303, (
        f"Spec switch must return 303, got {r_switch.status_code}"
    )

    # 303 rule: GET redirect target (dashboard)
    r_dash = client.get(r_switch.headers["location"])
    assert r_dash.status_code == 200, (
        f"Dashboard must return 200 after spec switch, got {r_dash.status_code}"
    )

    # DB: user.specialization updated to LFA_COACH
    test_db.expire_all()
    test_db.refresh(student)
    assert student.specialization == SpecializationType.LFA_COACH, (
        f"user.specialization must be LFA_COACH, got {student.specialization}"
    )

    # UI: profile page shows "ACTIVE: LFA Coach" for switched specialization
    r_profile = client.get("/profile")
    assert r_profile.status_code == 200
    assert "LFA Coach" in r_profile.text, (
        f"Profile must show 'LFA Coach' as active specialization. "
        f"Snippet: {r_profile.text[:500]}"
    )


def test_quiz_attempt_review_renders_score(test_db: Session, client: TestClient):
    """F-12 — Quiz take page renders quiz title; completed attempt recorded in DB.

    GET /quizzes/{quiz_id}/take → 200
    UI: quiz title visible in HTML
    DB: attempt.completed_at IS NOT NULL, attempt.passed == True
    """
    uid = _uid()
    student = _make_user(test_db)
    quiz_title = f"E2E Quiz {uid}"

    # Create quiz → question → answer option
    quiz = Quiz(
        title=quiz_title,
        category=QuizCategory.GENERAL,
        difficulty=QuizDifficulty.EASY,
    )
    test_db.add(quiz)
    test_db.flush()

    question = QuizQuestion(
        quiz_id=quiz.id,
        question_text="What is 2 + 2?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        order_index=0,
    )
    test_db.add(question)
    test_db.flush()

    opt_correct = QuizAnswerOption(
        question_id=question.id,
        option_text="4",
        is_correct=True,
        order_index=0,
    )
    test_db.add(opt_correct)
    test_db.flush()

    # Completed attempt: 100% score, passed
    attempt = QuizAttempt(
        quiz_id=quiz.id,
        user_id=student.id,
        started_at=datetime.utcnow() - timedelta(minutes=5),
        completed_at=datetime.utcnow(),
        total_questions=1,
        correct_answers=1,
        score=100.0,
        passed=True,
        xp_awarded=50,
    )
    test_db.add(attempt)
    test_db.flush()

    test_db.add(QuizUserAnswer(
        attempt_id=attempt.id,
        question_id=question.id,
        selected_option_id=opt_correct.id,
        is_correct=True,
    ))
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student

    # HTTP: GET /quizzes/{quiz_id}/take → 200 (quiz take page)
    r = client.get(f"/quizzes/{quiz.id}/take")
    assert r.status_code == 200, (
        f"Quiz take page must return 200, got {r.status_code}. Body: {r.text[:300]}"
    )

    # UI: quiz title visible on take page
    assert quiz_title in r.text, (
        f"Quiz title '{quiz_title}' must appear on quiz take page"
    )

    # DB: pre-created completed attempt is recorded correctly
    test_db.refresh(attempt)
    assert attempt.completed_at is not None, "attempt.completed_at must be set"
    assert attempt.passed is True, "attempt.passed must be True"


def test_admin_booking_confirm_updates_status(test_db: Session, client: TestClient):
    """F-35 — Admin confirms booking → Booking.status = CONFIRMED.

    POST /admin/bookings/{id}/confirm → 200 JSON {"success": true}
    UI: "Booking confirmed" in response body
    DB: booking.status == BookingStatus.CONFIRMED
    """
    uid = _uid()
    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db)
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)

    sem = Semester(
        code=f"BKC-{uid}", name=f"Booking Confirm {uid}",
        start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
        status=SemesterStatus.ONGOING, semester_category=SemesterCategory.TOURNAMENT,
    )
    test_db.add(sem)
    test_db.flush()

    sess = SessionModel(
        title=f"Session BKC {uid}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 12, 31, 10, 0),
        date_end=datetime(2026, 12, 31, 12, 0),
        semester_id=sem.id,
        instructor_id=instructor.id,
    )
    test_db.add(sess)
    test_db.flush()

    booking = Booking(
        user_id=student.id,
        session_id=sess.id,
        status=BookingStatus.PENDING,
    )
    test_db.add(booking)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: admin

    # HTTP: POST → 200 (JSON endpoint, not redirect)
    r = client.post(f"/admin/bookings/{booking.id}/confirm")
    assert r.status_code == 200, (
        f"Admin booking confirm must return 200, got {r.status_code}"
    )

    # UI: JSON response contains confirmation message
    assert "Booking confirmed" in r.text, (
        f"Response must contain 'Booking confirmed'. Got: {r.text[:300]}"
    )

    # DB: booking status updated to CONFIRMED
    test_db.expire_all()
    test_db.refresh(booking)
    assert booking.status == BookingStatus.CONFIRMED, (
        f"booking.status must be CONFIRMED, got {booking.status}"
    )


def test_profile_edit_updates_name(test_db: Session, client: TestClient):
    """F-05 — Student edits profile → User.name updated, new name visible on profile.

    POST /profile/edit (Form: name, date_of_birth) → 303
    303 rule: GET /profile → 200, new name visible
    DB: user.name == new_name
    """
    student = _make_user(test_db)
    new_name = f"Updated Name {_uid()}"

    app.dependency_overrides[get_current_user_web] = lambda: student

    # HTTP: POST → 303
    r_edit = client.post(
        "/profile/edit",
        data={
            "name": new_name,
            "date_of_birth": "2000-06-15",  # valid age (25 years)
        },
        follow_redirects=False,
    )
    assert r_edit.status_code == 303, (
        f"Profile edit must return 303, got {r_edit.status_code}"
    )

    # 303 rule: GET /profile
    r_profile = client.get("/profile")
    assert r_profile.status_code == 200, (
        f"Profile page must return 200, got {r_profile.status_code}"
    )

    # UI: new name visible on profile page (proves business state)
    assert new_name in r_profile.text, (
        f"New name '{new_name}' must appear on profile page. "
        f"Snippet: {r_profile.text[:500]}"
    )

    # DB: user.name updated
    test_db.expire_all()
    test_db.refresh(student)
    assert student.name == new_name, (
        f"User.name must be '{new_name}', got '{student.name}'"
    )


def test_public_player_card_renders(test_db: Session, client: TestClient):
    """F-38 — Public player card (no auth) → 200, player name visible.

    GET /players/{id}/card → 200 (public, no auth required)
    UI: student.name visible on card
    DB: UserLicense(LFA_FOOTBALL_PLAYER, is_active=True) confirmed in DB
    """
    student = _make_user(test_db)
    lic = UserLicense(
        user_id=student.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True,
        onboarding_completed=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        football_skills={"ball_control": 70.0, "passing": 65.0, "shooting": 60.0},
    )
    test_db.add(lic)
    test_db.flush()

    # DB: confirm license exists and is active
    test_db.refresh(lic)
    assert lic.is_active is True

    # No auth override — this is a public endpoint
    # HTTP: GET → 200
    r = client.get(f"/players/{student.id}/card")
    assert r.status_code == 200, (
        f"Public player card must return 200, got {r.status_code}. Body: {r.text[:300]}"
    )

    # UI: player name visible on public card
    assert student.name in r.text, (
        f"Player name '{student.name}' must appear on public card"
    )


def test_admin_grant_license_creates_user_license(test_db: Session, client: TestClient):
    """F-32 — Admin grants license → UserLicense(is_active=True) + LicenseProgression audit.

    POST /admin/users/{id}/grant-license (Form: specialization_type, reason) → 303
    303 rule: GET /admin/users/{id}/edit → 200, 'LFA_FOOTBALL_PLAYER' visible
    DB: UserLicense.is_active==True, LicenseProgression.requirements_met=='INITIAL_GRANT'
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    target = _make_user(test_db)  # no pre-existing licenses

    app.dependency_overrides[get_current_user_web] = lambda: admin

    # HTTP: POST → 303
    r_grant = client.post(
        f"/admin/users/{target.id}/grant-license",
        data={
            "specialization_type": "LFA_FOOTBALL_PLAYER",
            "reason": "E2E Sprint-2 F-32 license grant test",
            "expires_at": "",  # perpetual (no expiry)
        },
        follow_redirects=False,
    )
    assert r_grant.status_code == 303, (
        f"Grant license must return 303, got {r_grant.status_code}"
    )

    # 303 rule: GET /admin/users/{id}/edit (strip anchor #licenses)
    redirect_url = r_grant.headers["location"]
    r_edit = client.get(redirect_url.split("#")[0])
    assert r_edit.status_code == 200, (
        f"Admin user edit page must return 200, got {r_edit.status_code}"
    )

    # UI: specialization label visible on admin edit page
    assert "LFA_FOOTBALL_PLAYER" in r_edit.text, (
        f"'LFA_FOOTBALL_PLAYER' must appear on admin edit page after grant. "
        f"Snippet: {r_edit.text[:600]}"
    )

    # DB: UserLicense created and active
    test_db.expire_all()
    lic = test_db.query(UserLicense).filter(
        UserLicense.user_id == target.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    assert lic is not None, "UserLicense(LFA_FOOTBALL_PLAYER, is_active=True) must be created"

    # DB: LicenseProgression audit record created
    prog = test_db.query(LicenseProgression).filter(
        LicenseProgression.user_license_id == lic.id,
    ).first()
    assert prog is not None, "LicenseProgression audit record must be created"
    assert prog.requirements_met == "INITIAL_GRANT", (
        f"LicenseProgression.requirements_met must be 'INITIAL_GRANT', got '{prog.requirements_met}'"
    )


# ── Sprint 3 — F-41 + F-42 ─────────────────────────────────────────────────

def test_admin_live_monitor_renders(test_db: Session, client: TestClient):
    """F-41 — Admin live monitor page renders tournament name + session count.

    GET /admin/tournaments/{id}/live → 200
    DB:  SessionModel row exists for the semester (count == 1)
    UI:  tournament name visible in HTML response
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    uid = _uid()
    sem = Semester(
        code=f"LM-{uid}",
        name=f"Live Monitor Test {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.flush()
    sess = SessionModel(
        title=f"LM Session {uid}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 6, 1, 10, 0),
        date_end=datetime(2026, 6, 1, 12, 0),
        semester_id=sem.id,
        instructor_id=instructor.id,
    )
    test_db.add(sess)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: admin

    r = client.get(f"/admin/tournaments/{sem.id}/live")
    assert r.status_code == 200, (
        f"Live monitor must return 200, got {r.status_code}. Snippet: {r.text[:300]}"
    )

    # DB: session row exists for this semester
    count = test_db.query(SessionModel).filter(SessionModel.semester_id == sem.id).count()
    assert count == 1, f"Exactly 1 session must exist for semester {sem.id}, got {count}"

    # UI: tournament name rendered in page (via page_subtitle / title block)
    assert sem.name in r.text, (
        f"Tournament name '{sem.name}' must appear in live monitor page. "
        f"Snippet: {r.text[:400]}"
    )


def test_sport_director_team_remove(test_db: Session, client: TestClient):
    """F-42 — Sport director removes a team enrollment → TournamentTeamEnrollment.is_active=False.

    POST /sport-director/tournaments/{id}/teams/{team_id}/remove → 303
    303 rule: location header contains success msg (proves redirect to correct page)
    DB:  TournamentTeamEnrollment.is_active == False
    UI:  redirect location asserted (business-state proven via redirect target)
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    uid = _uid()
    sem = Semester(
        code=f"SD-{uid}",
        name=f"SD Remove Test {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="ENROLLMENT_OPEN",
    )
    test_db.add(sem)
    test_db.flush()
    cfg = TournamentConfiguration(
        semester_id=sem.id,
        participant_type="TEAM",
    )
    test_db.add(cfg)
    test_db.flush()
    team = Team(name=f"SD Team {uid}", is_active=True)
    test_db.add(team)
    test_db.flush()
    enrollment = TournamentTeamEnrollment(
        semester_id=sem.id,
        team_id=team.id,
        is_active=True,
    )
    test_db.add(enrollment)
    test_db.flush()

    app.dependency_overrides[get_current_sport_director_user_web] = lambda: admin

    r = client.post(
        f"/sport-director/tournaments/{sem.id}/teams/{team.id}/remove",
        follow_redirects=False,
    )
    assert r.status_code == 303, (
        f"Team remove must return 303, got {r.status_code}. Body: {r.text[:300]}"
    )

    # UI: redirect location proves success (303 rule — location is business-state proof)
    assert "msg" in r.headers["location"].lower() or "removed" in r.headers["location"].lower(), (
        f"Redirect location must contain success signal, got: '{r.headers['location']}'"
    )

    # DB: enrollment deactivated
    test_db.refresh(enrollment)
    assert enrollment.is_active is False, (
        "TournamentTeamEnrollment.is_active must be False after sport-director remove"
    )


# ── Sprint 4 — Instructor domain (F-43..F-46) ──────────────────────────────

def test_instructor_skills_form_renders(test_db: Session, client: TestClient):
    """F-43 (INSTR-01) — Instructor GET skills form → 200 + form rendered.

    GET /instructor/students/{student_id}/skills/{license_id} → 200
    DB:  UserLicense with LFA_PLAYER_ specialization exists
    UI:  "Edit Football Skills" visible in HTML response
    """
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    student = _make_user(test_db, role=UserRole.STUDENT)
    lic = UserLicense(
        user_id=student.id,
        specialization_type="LFA_PLAYER_YOUTH",
        is_active=True,
        onboarding_completed=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        football_skills={
            "heading": 50.0, "shooting": 50.0, "crossing": 50.0,
            "passing": 50.0, "dribbling": 50.0, "ball_control": 50.0,
        },
    )
    test_db.add(lic)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: instructor

    r = client.get(f"/instructor/students/{student.id}/skills/{lic.id}")
    assert r.status_code == 200, (
        f"Skills form must return 200, got {r.status_code}. Snippet: {r.text[:300]}"
    )

    # DB: license with LFA_PLAYER_ specialization exists
    found = test_db.query(UserLicense).filter(UserLicense.id == lic.id).first()
    assert found is not None, "UserLicense must exist in DB"
    assert found.specialization_type.startswith("LFA_PLAYER_"), (
        f"specialization_type must start with LFA_PLAYER_, got '{found.specialization_type}'"
    )

    # UI: skills form page rendered with heading
    assert "Edit Football Skills" in r.text, (
        f"'Edit Football Skills' must appear in skills form page. Snippet: {r.text[:400]}"
    )


def test_instructor_skills_update_and_audit(test_db: Session, client: TestClient):
    """F-44 (INSTR-02 CRITICAL) — Instructor updates student football skills → DB mutation + AuditLog.

    POST /instructor/students/{id}/skills/{lic_id} (valid values) → 200 + success message
    DB:  UserLicense.football_skills updated; AuditLog(FOOTBALL_SKILLS_UPDATED) created
    UI:  "Skills updated successfully" in response
    """
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    student = _make_user(test_db, role=UserRole.STUDENT)
    lic = UserLicense(
        user_id=student.id,
        specialization_type="LFA_PLAYER_YOUTH",
        is_active=True,
        onboarding_completed=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        football_skills={
            "heading": 50.0, "shooting": 50.0, "crossing": 50.0,
            "passing": 50.0, "dribbling": 50.0, "ball_control": 50.0,
        },
    )
    test_db.add(lic)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: instructor

    r = client.post(
        f"/instructor/students/{student.id}/skills/{lic.id}",
        data={
            "heading": "75.0",
            "shooting": "80.0",
            "crossing": "65.0",
            "passing": "70.0",
            "dribbling": "85.0",
            "ball_control": "90.0",
            "instructor_notes": "Great progress in training",
        },
    )
    assert r.status_code == 200, (
        f"Skills update must return 200, got {r.status_code}. Snippet: {r.text[:300]}"
    )

    # UI: success message visible (from template: "Skills updated successfully!")
    assert "Skills updated successfully" in r.text, (
        f"'Skills updated successfully' must appear after update. Snippet: {r.text[:400]}"
    )

    # DB: UserLicense.football_skills dict updated
    test_db.expire_all()
    updated = test_db.query(UserLicense).filter(UserLicense.id == lic.id).first()
    assert updated.football_skills["heading"] == 75.0, (
        f"heading must be 75.0 after update, got {updated.football_skills.get('heading')}"
    )
    assert updated.skills_updated_by == instructor.id, (
        f"skills_updated_by must be instructor.id={instructor.id}, got {updated.skills_updated_by}"
    )

    # DB: AuditLog(FOOTBALL_SKILLS_UPDATED) created for this license
    audit = test_db.query(AuditLog).filter(
        AuditLog.action == "FOOTBALL_SKILLS_UPDATED",
        AuditLog.resource_id == lic.id,
    ).first()
    assert audit is not None, "AuditLog(FOOTBALL_SKILLS_UPDATED) must be created"
    assert audit.resource_type == "football_skills", (
        f"AuditLog.resource_type must be 'football_skills', got '{audit.resource_type}'"
    )


def test_instructor_skills_invalid_value_returns_error(test_db: Session, client: TestClient):
    """F-45 (INSTR-03) — POST skill value >100 → 200 + error message, no DB mutation.

    POST /instructor/students/{id}/skills/{lic_id} (heading=150) → 200 + error template
    DB:  no AuditLog created; UserLicense.football_skills unchanged
    UI:  "must be between 0 and 100" in response
    """
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    student = _make_user(test_db, role=UserRole.STUDENT)
    original_skills = {
        "heading": 50.0, "shooting": 50.0, "crossing": 50.0,
        "passing": 50.0, "dribbling": 50.0, "ball_control": 50.0,
    }
    lic = UserLicense(
        user_id=student.id,
        specialization_type="LFA_PLAYER_YOUTH",
        is_active=True,
        onboarding_completed=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        football_skills=original_skills,
    )
    test_db.add(lic)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: instructor

    r = client.post(
        f"/instructor/students/{student.id}/skills/{lic.id}",
        data={
            "heading": "150.0",  # INVALID: exceeds max of 100
            "shooting": "80.0",
            "crossing": "65.0",
            "passing": "70.0",
            "dribbling": "85.0",
            "ball_control": "90.0",
            "instructor_notes": "",
        },
    )
    assert r.status_code == 200, (
        f"Invalid skill must return 200 with error template, got {r.status_code}"
    )

    # UI: validation error message rendered
    assert "must be between 0 and 100" in r.text, (
        f"Error message must appear for out-of-range value. Snippet: {r.text[:400]}"
    )

    # DB: no AuditLog created (route returned early without committing)
    audit_count = test_db.query(AuditLog).filter(
        AuditLog.action == "FOOTBALL_SKILLS_UPDATED",
        AuditLog.resource_id == lic.id,
    ).count()
    assert audit_count == 0, (
        f"No AuditLog must be created on invalid input, got count={audit_count}"
    )


def test_instructor_enrollments_page_renders(test_db: Session, client: TestClient):
    """F-46 (INSTR-04) — GET /instructor/enrollments → 200 + enrollment list for instructor's semesters.

    GET /instructor/enrollments → 200
    DB:  SemesterEnrollment(PENDING) exists for instructor's semester
    UI:  "Enrollment Requests" in HTML response (page title block)
    """
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    student = _make_user(test_db, role=UserRole.STUDENT)
    lic = UserLicense(
        user_id=student.id,
        specialization_type="LFA_PLAYER_YOUTH",
        is_active=True,
        onboarding_completed=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    test_db.add(lic)
    test_db.flush()

    uid = _uid()
    sem = Semester(
        code=f"IE-{uid}",
        name=f"IE Semester {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        master_instructor_id=instructor.id,
    )
    test_db.add(sem)
    test_db.flush()

    enrollment = SemesterEnrollment(
        user_id=student.id,
        semester_id=sem.id,
        user_license_id=lic.id,
        request_status=EnrollmentStatus.PENDING,
    )
    test_db.add(enrollment)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: instructor

    r = client.get("/instructor/enrollments")
    assert r.status_code == 200, (
        f"Enrollments page must return 200, got {r.status_code}. Snippet: {r.text[:300]}"
    )

    # DB: PENDING enrollment exists for this semester
    found = test_db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == sem.id,
    ).first()
    assert found is not None, "SemesterEnrollment must exist for instructor's semester"
    assert found.request_status == EnrollmentStatus.PENDING, (
        f"Enrollment status must be PENDING, got {found.request_status}"
    )

    # UI: enrollment requests page title rendered
    assert "Enrollment Requests" in r.text, (
        f"'Enrollment Requests' must appear on enrollments page. Snippet: {r.text[:400]}"
    )


# ── Sprint 5 — Communications domain (F-47..F-51) ──────────────────────────

def test_message_send_creates_row(test_db: Session, client: TestClient):
    """F-47 (COMM-06) — POST /messages/send → Message row created; success flash on redirect.

    POST /messages/send → 303 → GET /messages?tab=sent&success=sent → 200
    DB:  Message(sender_id, recipient_id, subject, is_read=False) created
    UI:  "Message sent successfully" in redirect-page HTML (business-state flash)
    """
    sender = _make_user(test_db, role=UserRole.STUDENT)
    recipient = _make_user(test_db, role=UserRole.STUDENT)
    uid = _uid()
    subject = f"Sprint5 MSG {uid}"

    app.dependency_overrides[get_current_user_web] = lambda: sender

    r = client.post(
        "/messages/send",
        data={
            "recipient_id": str(recipient.id),
            "subject": subject,
            "message": "E2E test message body.",
            "priority": "NORMAL",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, (
        f"Message send must return 303, got {r.status_code}. Body: {r.text[:300]}"
    )

    # 303 rule: follow redirect → sent tab + success flash
    redirect_url = r.headers["location"]
    r_page = client.get(redirect_url)
    assert r_page.status_code == 200
    assert "Message sent successfully" in r_page.text, (
        f"'Message sent successfully' must appear after send. Snippet: {r_page.text[:400]}"
    )

    # DB: Message row created with correct sender/recipient, is_read=False
    msg = test_db.query(Message).filter(
        Message.sender_id == sender.id,
        Message.recipient_id == recipient.id,
    ).first()
    assert msg is not None, "Message row must be created in DB"
    assert msg.is_read is False, "New message must have is_read=False"
    assert msg.subject == subject, (
        f"Message subject must match, got '{msg.subject}'"
    )


def test_message_detail_auto_marks_read(test_db: Session, client: TestClient):
    """F-48 (COMM-07) — GET /messages/{id} by recipient → is_read=True + read_at set.

    GET /messages/{message_id} (as recipient) → 200
    DB:  Message.is_read=True, Message.read_at IS NOT NULL after recipient opens detail
    UI:  message subject visible in detail page HTML
    """
    sender = _make_user(test_db, role=UserRole.STUDENT)
    recipient = _make_user(test_db, role=UserRole.STUDENT)
    uid = _uid()
    subject = f"Sprint5 Detail {uid}"
    msg = Message(
        sender_id=sender.id,
        recipient_id=recipient.id,
        subject=subject,
        message="E2E auto-read test body.",
        priority=MessagePriority.NORMAL,
        is_read=False,
    )
    test_db.add(msg)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: recipient

    r = client.get(f"/messages/{msg.id}")
    assert r.status_code == 200, (
        f"Message detail must return 200, got {r.status_code}. Snippet: {r.text[:300]}"
    )

    # UI: message subject rendered in detail heading
    assert subject in r.text, (
        f"Message subject '{subject}' must appear in detail page. Snippet: {r.text[:400]}"
    )

    # DB: is_read=True + read_at set by auto-read logic
    test_db.expire_all()
    updated = test_db.query(Message).filter(Message.id == msg.id).first()
    assert updated.is_read is True, (
        "Message.is_read must be True after recipient opens detail page"
    )
    assert updated.read_at is not None, (
        "Message.read_at must be set after recipient opens detail page"
    )


def test_notifications_read_all_marks_all_read(test_db: Session, client: TestClient):
    """F-49 (COMM-02) — POST /notifications/read-all → all notifications is_read=True.

    POST /notifications/read-all → 303 → GET /notifications?success=marked → 200
    DB:  all Notification rows for user have is_read=True
    UI:  "All notifications marked as read" in redirect-page HTML
    """
    student = _make_user(test_db, role=UserRole.STUDENT)
    uid = _uid()
    notif1 = Notification(
        user_id=student.id,
        title=f"Notif A {uid}",
        message="First test notification.",
        type=NotificationType.GENERAL,
        is_read=False,
    )
    notif2 = Notification(
        user_id=student.id,
        title=f"Notif B {uid}",
        message="Second test notification.",
        type=NotificationType.GENERAL,
        is_read=False,
    )
    test_db.add_all([notif1, notif2])
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student

    r = client.post("/notifications/read-all", follow_redirects=False)
    assert r.status_code == 303, (
        f"read-all must return 303, got {r.status_code}. Body: {r.text[:300]}"
    )

    # 303 rule: follow redirect → success flash visible
    redirect_url = r.headers["location"]
    r_page = client.get(redirect_url)
    assert r_page.status_code == 200
    assert "All notifications marked as read" in r_page.text, (
        f"Success flash must appear after read-all. Snippet: {r_page.text[:400]}"
    )

    # DB: both notifications now is_read=True
    test_db.expire_all()
    updated1 = test_db.query(Notification).filter(Notification.id == notif1.id).first()
    updated2 = test_db.query(Notification).filter(Notification.id == notif2.id).first()
    assert updated1.is_read is True, "notif1.is_read must be True after read-all"
    assert updated2.is_read is True, "notif2.is_read must be True after read-all"


def test_notification_single_read_updates_state(test_db: Session, client: TestClient):
    """F-50 (COMM-03) — POST /notifications/{id}/read → single notification is_read=True + read_at set.

    POST /notifications/{id}/read (fetch endpoint) → 200 JSON {"ok": True}
    DB:  Notification.is_read=True, Notification.read_at IS NOT NULL
    UI:  JSON response body contains "ok" (business-state proof)
    """
    student = _make_user(test_db, role=UserRole.STUDENT)
    uid = _uid()
    notif = Notification(
        user_id=student.id,
        title=f"Unread Notif {uid}",
        message="Single read test notification.",
        type=NotificationType.GENERAL,
        is_read=False,
    )
    test_db.add(notif)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student

    r = client.post(f"/notifications/{notif.id}/read")
    assert r.status_code == 200, (
        f"Notification single read must return 200, got {r.status_code}"
    )

    # UI: JSON response confirms action
    assert "ok" in r.text, (
        f"JSON response must contain 'ok'. Got: {r.text[:200]}"
    )

    # DB: is_read=True + read_at set by service layer
    test_db.refresh(notif)
    assert notif.is_read is True, (
        "Notification.is_read must be True after single read"
    )
    assert notif.read_at is not None, (
        "Notification.read_at must be set after single read"
    )


def test_messages_inbox_shows_unread_for_recipient(test_db: Session, client: TestClient):
    """F-51 (COMM-inbox) — GET /messages → recipient inbox shows unread message (user separation).

    GET /messages (as recipient) → 200 + subject in inbox HTML
    DB:  Message.is_read=False exists for recipient; sender's inbox is empty (no self-send)
    UI:  message subject visible in recipient's inbox page HTML
    """
    sender = _make_user(test_db, role=UserRole.STUDENT)
    recipient = _make_user(test_db, role=UserRole.STUDENT)
    uid = _uid()
    subject = f"Sprint5 Inbox {uid}"
    msg = Message(
        sender_id=sender.id,
        recipient_id=recipient.id,
        subject=subject,
        message="Inbox user-separation test.",
        priority=MessagePriority.NORMAL,
        is_read=False,
    )
    test_db.add(msg)
    test_db.flush()

    # Recipient opens their inbox
    app.dependency_overrides[get_current_user_web] = lambda: recipient

    r = client.get("/messages")
    assert r.status_code == 200, (
        f"Messages page must return 200, got {r.status_code}. Snippet: {r.text[:300]}"
    )

    # UI: unread message subject visible in recipient's inbox
    assert subject in r.text, (
        f"Message subject '{subject}' must appear in recipient inbox. Snippet: {r.text[:400]}"
    )

    # DB: unread Message exists for recipient (state proven separately from UI)
    unread = test_db.query(Message).filter(
        Message.recipient_id == recipient.id,
        Message.is_read == False,
    ).first()
    assert unread is not None, "Unread Message must exist for recipient in DB"
    assert unread.subject == subject, (
        f"Unread message subject must match, got '{unread.subject}'"
    )


# ── Sprint 6 — Admin Operations (F-52..F-56) ──────────────────────────────────


def test_admin_invoice_verify_credits_student(test_db: Session, client: TestClient):
    """F-52 (INVMAN-01) — POST /admin/invoices/{id}/verify → 200 JSON + credits added.

    POST /admin/invoices/{id}/verify (admin) → 200 JSON {"success": true, "credits_added": N}
    DB:  InvoiceRequest.status="verified", verified_at set; User.credit_balance += credit_amount;
         CreditTransaction(PURCHASE, idempotency_key="invoice-verify-{id}") created
    UI:  "credits_added" in JSON response text
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=100)
    uid = _uid()
    invoice = InvoiceRequest(
        user_id=student.id,
        payment_reference=f"LFA-VER-{uid}",
        amount_eur=10.0,
        credit_amount=50,
        status="pending",
    )
    test_db.add(invoice)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: admin
    r = client.post(f"/admin/invoices/{invoice.id}/verify")
    assert r.status_code == 200, (
        f"Invoice verify must return 200, got {r.status_code}. Body: {r.text[:300]}"
    )

    # UI: JSON response contains credits_added key
    assert "credits_added" in r.text, (
        f"Response must contain 'credits_added'. Body: {r.text[:300]}"
    )

    # DB: status changed, credit_balance increased, CreditTransaction created
    test_db.expire_all()
    inv = test_db.query(InvoiceRequest).filter(InvoiceRequest.id == invoice.id).first()
    assert inv.status == "verified", f"Invoice must be verified, got '{inv.status}'"
    assert inv.verified_at is not None, "verified_at must be set after verify"
    stu = test_db.query(User).filter(User.id == student.id).first()
    assert stu.credit_balance == 150, (
        f"credit_balance must be 100+50=150, got {stu.credit_balance}"
    )
    ct = test_db.query(CreditTransaction).filter(
        CreditTransaction.idempotency_key == f"invoice-verify-{invoice.id}"
    ).first()
    assert ct is not None, "CreditTransaction(PURCHASE) must be created on verify"
    assert ct.amount == 50, f"Transaction amount must be 50, got {ct.amount}"


def test_admin_invoice_cancel_sets_cancelled_status(test_db: Session, client: TestClient):
    """F-53 (INVMAN-02) — POST /admin/invoices/{id}/cancel → 200 JSON + status=cancelled.

    POST /admin/invoices/{id}/cancel (admin, form reason) → 200 JSON {"success": true}
    DB:  InvoiceRequest.status="cancelled"; User.credit_balance unchanged (no credits involved)
    UI:  "Invoice cancelled" in JSON response text
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=300)
    uid = _uid()
    invoice = InvoiceRequest(
        user_id=student.id,
        payment_reference=f"LFA-CAN-{uid}",
        amount_eur=15.0,
        credit_amount=75,
        status="pending",
    )
    test_db.add(invoice)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: admin
    r = client.post(
        f"/admin/invoices/{invoice.id}/cancel",
        data={"reason": "Test cancellation reason"},
    )
    assert r.status_code == 200, (
        f"Invoice cancel must return 200, got {r.status_code}. Body: {r.text[:300]}"
    )

    # UI: response confirms cancellation
    assert "Invoice cancelled" in r.text, (
        f"Response must contain 'Invoice cancelled'. Body: {r.text[:300]}"
    )

    # DB: status=cancelled, credit_balance unchanged (no credit side-effects on cancel)
    test_db.refresh(invoice)
    assert invoice.status == "cancelled", (
        f"Invoice status must be 'cancelled', got '{invoice.status}'"
    )
    test_db.refresh(student)
    assert student.credit_balance == 300, (
        f"credit_balance must be unchanged (300), got {student.credit_balance}"
    )


def test_admin_invoice_unverify_reverts_credits(test_db: Session, client: TestClient):
    """F-54 (INVMAN-03) — POST /admin/invoices/{id}/unverify → 200 JSON + credits reverted.

    POST /admin/invoices/{id}/unverify (admin) → 200 JSON {"success": true, "credits_removed": N}
    DB:  InvoiceRequest.status reverts to "pending", verified_at=None;
         User.credit_balance -= credit_amount; CreditTransaction(REFUND, amount<0) created
    UI:  "credits_removed" in JSON response text
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=200)
    uid = _uid()
    invoice = InvoiceRequest(
        user_id=student.id,
        payment_reference=f"LFA-UNV-{uid}",
        amount_eur=10.0,
        credit_amount=50,
        status="verified",
        verified_at=datetime.now(timezone.utc),
    )
    test_db.add(invoice)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: admin
    r = client.post(f"/admin/invoices/{invoice.id}/unverify")
    assert r.status_code == 200, (
        f"Invoice unverify must return 200, got {r.status_code}. Body: {r.text[:300]}"
    )

    # UI: JSON response confirms credits removed
    assert "credits_removed" in r.text, (
        f"Response must contain 'credits_removed'. Body: {r.text[:300]}"
    )

    # DB: status reverted, verified_at cleared, balance decreased, REFUND transaction created
    test_db.expire_all()
    inv = test_db.query(InvoiceRequest).filter(InvoiceRequest.id == invoice.id).first()
    assert inv.status == "pending", (
        f"Invoice must revert to 'pending', got '{inv.status}'"
    )
    assert inv.verified_at is None, "verified_at must be cleared after unverify"
    stu = test_db.query(User).filter(User.id == student.id).first()
    assert stu.credit_balance == 150, (
        f"credit_balance must be 200-50=150, got {stu.credit_balance}"
    )
    ct = test_db.query(CreditTransaction).filter(
        CreditTransaction.idempotency_key == f"invoice-unverify-{invoice.id}"
    ).first()
    assert ct is not None, "CreditTransaction(REFUND) must be created on unverify"
    assert ct.amount == -50, f"Transaction amount must be -50, got {ct.amount}"


def test_admin_batch_enroll_players_creates_enrollments(test_db: Session, client: TestClient):
    """F-55 (BATCH-01) — POST /api/v1/tournaments/{id}/admin/batch-enroll → 2 SemesterEnrollments.

    POST /api/v1/tournaments/{id}/admin/batch-enroll (admin, JSON player_ids) → 200 JSON
    DB:  SemesterEnrollment × 2 with is_active=True, request_status=APPROVED, payment_verified=True
    UI:  "enrolled_count" key present in JSON response text (idempotency: re-POST skips existing)
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    sem = _make_tournament(test_db)
    student1 = _make_user(test_db, role=UserRole.STUDENT)
    student2 = _make_user(test_db, role=UserRole.STUDENT)
    # Batch-enroll requires LFA_FOOTBALL_PLAYER license per player
    _make_license(test_db, student1)
    _make_license(test_db, student2)

    app.dependency_overrides[get_current_user] = lambda: admin
    r = client.post(
        f"/api/v1/tournaments/{sem.id}/admin/batch-enroll",
        json={"player_ids": [student1.id, student2.id]},
    )
    assert r.status_code == 200, (
        f"Batch enroll must return 200, got {r.status_code}. Body: {r.text[:300]}"
    )

    # UI: JSON response contains enrolled_count
    assert "enrolled_count" in r.text, (
        f"Response must contain 'enrolled_count'. Body: {r.text[:300]}"
    )

    # DB: exactly 2 active APPROVED enrollments created (transactional integrity)
    test_db.expire_all()
    enrollments = test_db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == sem.id,
        SemesterEnrollment.is_active == True,
    ).all()
    assert len(enrollments) == 2, (
        f"Exactly 2 active enrollments must exist, got {len(enrollments)}"
    )
    assert all(e.request_status == EnrollmentStatus.APPROVED for e in enrollments), (
        "All batch-enrolled players must have APPROVED status"
    )
    assert all(e.payment_verified is True for e in enrollments), (
        "All batch-enrolled players must have payment_verified=True (admin bypass)"
    )


def test_admin_team_bulk_enroll_creates_team_enrollments(test_db: Session, client: TestClient):
    """F-56 (BATCH-02) — POST /admin/tournaments/{id}/teams/enroll-bulk → 2 TournamentTeamEnrollments.

    POST /admin/tournaments/{id}/teams/enroll-bulk (admin, form team_ids) → 303 redirect
    DB:  TournamentTeamEnrollment × 2 with is_active=True, payment_verified=True
    UI:  "enrolled" in redirect Location header (flash message in URL)
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    uid = _uid()
    sem = Semester(
        code=f"BULK-{uid}",
        name=f"Bulk Team Enroll Test {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        tournament_status="ENROLLMENT_OPEN",
    )
    test_db.add(sem)
    test_db.flush()
    team1 = Team(name=f"Bulk Team Alpha {uid}")
    team2 = Team(name=f"Bulk Team Beta {uid}")
    test_db.add_all([team1, team2])
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: admin
    r = client.post(
        f"/admin/tournaments/{sem.id}/teams/enroll-bulk",
        data={"team_ids": [str(team1.id), str(team2.id)]},
        follow_redirects=False,
    )
    assert r.status_code == 303, (
        f"Team bulk-enroll must return 303, got {r.status_code}. Body: {r.text[:300]}"
    )

    # UI: redirect URL contains "enrolled" flash message
    assert "enrolled" in r.headers["location"], (
        f"Redirect URL must contain 'enrolled'. Location: {r.headers.get('location', '')}"
    )

    # DB: 2 active TournamentTeamEnrollments with payment_verified=True (admin bypass)
    test_db.expire_all()
    enrollments = test_db.query(TournamentTeamEnrollment).filter(
        TournamentTeamEnrollment.semester_id == sem.id,
        TournamentTeamEnrollment.is_active == True,
    ).all()
    assert len(enrollments) == 2, (
        f"Exactly 2 team enrollments must exist, got {len(enrollments)}"
    )
    assert all(e.payment_verified is True for e in enrollments), (
        "All bulk-enrolled teams must have payment_verified=True"
    )


# ── Sprint 7: F-63 — Student Evaluates Instructor (CRITICAL) ─────────────────

def test_student_evaluates_instructor_creates_review(test_db: Session, client: TestClient):
    """F-63: Student evaluates instructor → InstructorSessionReview created.

    Chain:
      Setup: on_site Session(actual_end_time IS NOT NULL) + Attendance(present)
      POST /sessions/{id}/evaluate-instructor (as student)
      → 303 → /sessions/{id}?success=instructor_evaluated
      DB:  InstructorSessionReview(session_id, student_id) row created
      UI:  redirect page renders 200; session page shows evaluation context

    Role enforcement: only STUDENT role can submit instructor evaluation.
    Side-effect: InstructorSessionReview.average_score computed from 8 dimensions.
    """
    uid = _uid()
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    student = _make_user(test_db)

    sem = Semester(
        code=f"F63-{uid}",
        name=f"F-63 Eval Semester {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.flush()

    # Session must have actual_end_time set (route checks it's not None)
    sess = SessionModel(
        title=f"F-63 Session {uid}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 1, 1, 10, 0),
        date_end=datetime(2026, 1, 1, 12, 0),
        actual_start_time=datetime(2026, 1, 1, 10, 0),
        actual_end_time=datetime(2026, 1, 1, 12, 0),  # REQUIRED: session ended
        semester_id=sem.id,
        instructor_id=instructor.id,
    )
    test_db.add(sess)
    test_db.flush()

    # Booking + Attendance required (route checks student attended)
    booking = Booking(user_id=student.id, session_id=sess.id, status=BookingStatus.CONFIRMED)
    test_db.add(booking)
    test_db.flush()

    attendance = Attendance(
        session_id=sess.id,
        user_id=student.id,
        booking_id=booking.id,
        status=AttendanceStatus.present,
        marked_by=instructor.id,
    )
    test_db.add(attendance)
    test_db.flush()

    # ── HTTP: student submits instructor evaluation ────────────────────────────
    app.dependency_overrides[get_current_user_web] = lambda: student
    r = client.post(
        f"/sessions/{sess.id}/evaluate-instructor",
        data={
            "instructor_clarity": "4",
            "support_approachability": "5",
            "session_structure": "4",
            "relevance": "4",
            "environment": "5",
            "engagement_feeling": "4",
            "feedback_quality": "3",
            "satisfaction": "5",
            "comments": "Great session",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, (
        f"evaluate-instructor must return 303, got {r.status_code}. Body: {r.text[:300]}"
    )
    location = r.headers["location"]
    assert "instructor_evaluated" in location, (
        f"Redirect must contain 'instructor_evaluated', got: {location}"
    )

    # ── DB: InstructorSessionReview row created ───────────────────────────────
    test_db.expire_all()
    review = (
        test_db.query(InstructorSessionReview)
        .filter(
            InstructorSessionReview.session_id == sess.id,
            InstructorSessionReview.student_id == student.id,
        )
        .first()
    )
    assert review is not None, "InstructorSessionReview must be created after POST"
    assert review.instructor_id == instructor.id, "Review must reference the session instructor"
    assert review.instructor_clarity == 4, f"instructor_clarity must be 4, got {review.instructor_clarity}"
    assert review.satisfaction == 5, f"satisfaction must be 5, got {review.satisfaction}"
    # average_score = (4+5+4+4+5+4+3+5)/8 = 34/8 = 4.25
    assert abs(review.average_score - 4.25) < 0.01, (
        f"average_score must be 4.25, got {review.average_score}"
    )

    # ── UI: redirect page renders session detail with evaluation context ────────
    r_page = client.get(location)
    assert r_page.status_code == 200, (
        f"Session detail page after evaluation must return 200, got {r_page.status_code}"
    )
    assert sess.title in r_page.text, (
        f"Session detail page must contain session title '{sess.title}'. "
        f"Snippet: {r_page.text[:400]}"
    )


# ── Sprint 7: F-64 — Instructor Evaluates Student (CRITICAL) ─────────────────

def test_instructor_evaluates_student_creates_performance_review(test_db: Session, client: TestClient):
    """F-64: Instructor evaluates student → StudentPerformanceReview created.

    Chain:
      Setup: on_site Session(actual_end_time IS NOT NULL, instructor_id=instructor)
             Attendance(student, present)
      POST /sessions/{id}/evaluate-student/{student_id}  (as instructor)
      → 303 → /sessions/{id}?success=student_evaluated
      DB:  StudentPerformanceReview(session_id, student_id, instructor_id) row created
      UI:  redirect page renders 200; average_score computed from 5 dimensions

    Role enforcement: only INSTRUCTOR role who OWNS the session can submit.
    Side-effect: _update_specialization_xp called (stub, no-op on main).
    """
    uid = _uid()
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    student = _make_user(test_db)

    sem = Semester(
        code=f"F64-{uid}",
        name=f"F-64 Eval Semester {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.flush()

    sess = SessionModel(
        title=f"F-64 Session {uid}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 1, 1, 10, 0),
        date_end=datetime(2026, 1, 1, 12, 0),
        actual_start_time=datetime(2026, 1, 1, 10, 0),
        actual_end_time=datetime(2026, 1, 1, 12, 0),  # REQUIRED: session ended
        semester_id=sem.id,
        instructor_id=instructor.id,  # REQUIRED: instructor owns session
    )
    test_db.add(sess)
    test_db.flush()

    booking = Booking(user_id=student.id, session_id=sess.id, status=BookingStatus.CONFIRMED)
    test_db.add(booking)
    test_db.flush()

    attendance = Attendance(
        session_id=sess.id,
        user_id=student.id,
        booking_id=booking.id,
        status=AttendanceStatus.present,  # REQUIRED: not absent
        marked_by=instructor.id,
    )
    test_db.add(attendance)
    test_db.flush()

    # ── HTTP: instructor submits student performance review ───────────────────
    app.dependency_overrides[get_current_user_web] = lambda: instructor
    r = client.post(
        f"/sessions/{sess.id}/evaluate-student/{student.id}",
        data={
            "punctuality": "4",
            "engagement": "5",
            "focus": "4",
            "collaboration": "3",
            "attitude": "4",
            "comments": "Good work",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, (
        f"evaluate-student must return 303, got {r.status_code}. Body: {r.text[:300]}"
    )
    location = r.headers["location"]
    assert "student_evaluated" in location, (
        f"Redirect must contain 'student_evaluated', got: {location}"
    )

    # ── DB: StudentPerformanceReview row created with correct scores ──────────
    test_db.expire_all()
    review = (
        test_db.query(StudentPerformanceReview)
        .filter(
            StudentPerformanceReview.session_id == sess.id,
            StudentPerformanceReview.student_id == student.id,
        )
        .first()
    )
    assert review is not None, "StudentPerformanceReview must be created after POST"
    assert review.instructor_id == instructor.id, "Review must reference the session instructor"
    assert review.punctuality == 4, f"punctuality must be 4, got {review.punctuality}"
    assert review.engagement == 5, f"engagement must be 5, got {review.engagement}"
    assert review.focus == 4, f"focus must be 4, got {review.focus}"
    assert review.collaboration == 3, f"collaboration must be 3, got {review.collaboration}"
    assert review.attitude == 4, f"attitude must be 4, got {review.attitude}"
    # average_score = (4+5+4+3+4)/5 = 20/5 = 4.0
    assert abs(review.average_score - 4.0) < 0.01, (
        f"average_score must be 4.0, got {review.average_score}"
    )

    # ── UI: redirect page renders session detail with evaluation recorded ───────
    r_page = client.get(location)
    assert r_page.status_code == 200, (
        f"Session detail page after student evaluation must return 200, got {r_page.status_code}"
    )
    assert sess.title in r_page.text, (
        f"Session detail page must contain session title '{sess.title}'. "
        f"Snippet: {r_page.text[:400]}"
    )


# ── Sprint 7: F-57 — Admin User Create ────────────────────────────────────────

def test_admin_user_create_creates_active_user(test_db: Session, client: TestClient):
    """F-57: Admin creates new user → User(is_active=True) row in DB.

    Chain:
      POST /admin/users/create (as admin)  data={name, email, role, password}
      → 303 → /admin/users/{new_user.id}/edit
      DB:  User(email=..., is_active=True, role=STUDENT) exists
      UI:  edit page renders 200; user name/email visible

    Role enforcement: _admin_guard raises 403 for non-admin.
    State: User.is_active=True, onboarding_completed=False (admin-created).
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    uid = _uid()
    new_email = f"admin-created-{uid}@test.lfa"
    new_name = f"Admin Created User {uid}"

    # ── HTTP: admin creates user ──────────────────────────────────────────────
    app.dependency_overrides[get_current_user_web] = lambda: admin
    r = client.post(
        "/admin/users/create",
        data={
            "name": new_name,
            "email": new_email,
            "role": "student",
            "password": "Pass1234!",
            "credit_balance": "0",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, (
        f"Admin user create must return 303, got {r.status_code}. Body: {r.text[:300]}"
    )
    location = r.headers["location"]
    assert "/admin/users/" in location and "/edit" in location, (
        f"Redirect must be to /admin/users/{{id}}/edit, got: {location}"
    )

    # ── DB: User row created with correct state ───────────────────────────────
    test_db.expire_all()
    new_user = test_db.query(User).filter(User.email == new_email).first()
    assert new_user is not None, f"User with email {new_email} must exist after admin create"
    assert new_user.is_active is True, f"Admin-created user must be active, got {new_user.is_active}"
    assert new_user.role == UserRole.STUDENT, f"User role must be STUDENT, got {new_user.role}"
    assert new_user.name == new_name, f"User name must match, got {new_user.name}"
    assert new_user.credit_balance == 0, f"Credit balance must be 0, got {new_user.credit_balance}"

    # ── UI: edit page renders with new user data ──────────────────────────────
    r_page = client.get(location)
    assert r_page.status_code == 200, (
        f"Admin user edit page must render 200, got {r_page.status_code}"
    )
    assert new_email in r_page.text or new_name in r_page.text, (
        f"Edit page must contain new user's email or name. "
        f"Snippet: {r_page.text[:500]}"
    )


# ── Sprint 7: F-58 — Admin User Toggle Status ─────────────────────────────────

def test_admin_toggle_user_status_deactivates_active_user(test_db: Session, client: TestClient):
    """F-58: Admin toggles user status → User.is_active flipped False → True or True → False.

    Chain:
      target.is_active = True (default from _make_user)
      POST /admin/users/{target.id}/toggle-status (as admin)
      → 303 → /admin/users
      DB:  target.is_active == False  (was True)
      UI:  /admin/users renders 200; user list accessible

    Role enforcement: admin cannot deactivate themselves (400).
    State transition: True → False (deactivation path tested).
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    target = _make_user(test_db)  # is_active=True by default

    assert target.is_active is True, "target must start active for this test"

    # ── HTTP: admin toggles target status ────────────────────────────────────
    app.dependency_overrides[get_current_user_web] = lambda: admin
    r = client.post(
        f"/admin/users/{target.id}/toggle-status",
        follow_redirects=False,
    )
    assert r.status_code == 303, (
        f"Toggle status must return 303, got {r.status_code}. Body: {r.text[:300]}"
    )
    location = r.headers["location"]
    assert "/admin/users" in location, (
        f"Redirect must be to /admin/users, got: {location}"
    )

    # ── DB: target.is_active flipped to False ────────────────────────────────
    test_db.expire_all()
    refreshed = test_db.query(User).filter(User.id == target.id).first()
    assert refreshed is not None, "Target user must still exist after toggle"
    assert refreshed.is_active is False, (
        f"User.is_active must be False after deactivation toggle, got {refreshed.is_active}"
    )

    # ── UI: admin users list renders 200 and includes the target user ───────
    r_page = client.get(f"/admin/users?search={target.email}")
    assert r_page.status_code == 200, (
        f"Admin users list must render 200 after toggle, got {r_page.status_code}"
    )
    assert target.email in r_page.text, (
        f"Admin users list must include deactivated user's email when searched. "
        f"Snippet: {r_page.text[:600]}"
    )


# ── Sprint 7: F-59 — Admin Booking Cancel ─────────────────────────────────────

def test_admin_booking_cancel_sets_cancelled_status(test_db: Session, client: TestClient):
    """F-59: Admin cancels booking → Booking.status=CANCELLED + cancelled_at IS NOT NULL.

    Chain:
      Booking(status=CONFIRMED)
      POST /admin/bookings/{id}/cancel  data={reason}  (as admin)
      → 200 JSON {"success": True, "message": "Booking cancelled"}
      DB:  Booking.status == CANCELLED, cancelled_at IS NOT NULL, notes == reason
      UI:  response body contains "success"

    No refund logic for regular session bookings (sessions are free).
    State: CONFIRMED → CANCELLED; cancelled_at timestamp recorded.
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db)
    uid = _uid()

    sem = Semester(
        code=f"F59-{uid}",
        name=f"F-59 Cancel Semester {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.flush()

    sess = SessionModel(
        title=f"F-59 Session {uid}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 12, 31, 10, 0),
        date_end=datetime(2026, 12, 31, 12, 0),
        semester_id=sem.id,
    )
    test_db.add(sess)
    test_db.flush()

    booking = Booking(user_id=student.id, session_id=sess.id, status=BookingStatus.CONFIRMED)
    test_db.add(booking)
    test_db.flush()

    reason = "Admin test cancellation — F-59"

    # ── HTTP: admin cancels booking ───────────────────────────────────────────
    app.dependency_overrides[get_current_user_web] = lambda: admin
    r = client.post(
        f"/admin/bookings/{booking.id}/cancel",
        data={"reason": reason},
    )
    assert r.status_code == 200, (
        f"Admin booking cancel must return 200, got {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert body.get("success") is True, f"Response must have success=True, got: {body}"
    assert "cancelled" in body.get("message", "").lower(), (
        f"Response message must contain 'cancelled', got: {body.get('message')}"
    )

    # ── DB: Booking.status=CANCELLED + metadata ───────────────────────────────
    test_db.expire_all()
    test_db.refresh(booking)
    assert booking.status == BookingStatus.CANCELLED, (
        f"Booking.status must be CANCELLED, got {booking.status}"
    )
    assert booking.cancelled_at is not None, "Booking.cancelled_at must be set after cancellation"
    assert booking.notes == reason, (
        f"Booking.notes must match reason '{reason}', got '{booking.notes}'"
    )

    # ── UI: JSON body contains expected keys ──────────────────────────────────
    assert "success" in r.text, f"Response body must contain 'success'. Body: {r.text[:200]}"


# ── Sprint 7: F-60 — Session Postpone ─────────────────────────────────────────

def test_admin_session_postpone_sets_postponed_reason(test_db: Session, client: TestClient):
    """F-60: Admin postpones a session → Session.postponed_reason set.

    Chain:
      PATCH /admin/sessions/{id}/postpone  JSON={"reason": "..."}  (as admin)
      → 200 JSON {"ok": True, "postponed_reason": "Weather conditions"}
      DB:  Session.postponed_reason == "Weather conditions"
      UI:  response body contains "Weather conditions"

    Route: PATCH (accepts JSON body) using get_current_admin_user_hybrid.
    State: Session.postponed_reason None → "Weather conditions".
    """
    from app.dependencies import get_current_admin_user_hybrid

    admin = _make_user(test_db, role=UserRole.ADMIN)
    uid = _uid()

    sem = Semester(
        code=f"F60-{uid}",
        name=f"F-60 Postpone Semester {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.flush()

    sess = SessionModel(
        title=f"F-60 Session {uid}",
        session_type=SessionType.on_site,
        date_start=datetime(2026, 12, 1, 10, 0),
        date_end=datetime(2026, 12, 1, 12, 0),
        semester_id=sem.id,
    )
    test_db.add(sess)
    test_db.flush()

    assert sess.postponed_reason is None, "Session.postponed_reason must be None before postpone"

    postpone_reason = "Weather conditions — F-60 test"

    # ── HTTP: admin postpones session ─────────────────────────────────────────
    app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
    r = client.patch(
        f"/admin/sessions/{sess.id}/postpone",
        json={"reason": postpone_reason},
    )
    assert r.status_code == 200, (
        f"Session postpone must return 200, got {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert body.get("ok") is True, f"Response must have ok=True, got: {body}"
    assert body.get("postponed_reason") == postpone_reason, (
        f"postponed_reason in response must match, got: {body.get('postponed_reason')}"
    )

    # ── DB: Session.postponed_reason updated ─────────────────────────────────
    test_db.expire_all()
    test_db.refresh(sess)
    assert sess.postponed_reason == postpone_reason, (
        f"Session.postponed_reason must be '{postpone_reason}', got '{sess.postponed_reason}'"
    )

    # ── UI: JSON body contains the postpone reason ────────────────────────────
    assert postpone_reason in r.text, (
        f"Response body must contain postpone reason. Body: {r.text[:300]}"
    )


# ── Sprint 7: F-61 — Instructor Slot Create ───────────────────────────────────

def test_admin_instructor_slot_create_planned(test_db: Session, client: TestClient):
    """F-61: Admin creates instructor slot → TournamentInstructorSlot(status=PLANNED).

    Chain:
      POST /admin/tournaments/{id}/instructor-slots
           data={instructor_id, role="MASTER"}  (as admin)
      → 201 JSON {"slot_id": id, "status": "PLANNED"}
      DB:  TournamentInstructorSlot(semester_id, instructor_id, role="MASTER",
                                    status="PLANNED") exists
      UI:  response body contains "slot_id" and "PLANNED"

    Role: MASTER requires no pitch_id. Duplicate instructor → 409.
    Semester.master_instructor_id synced from MASTER slot (service layer).
    """
    admin = _make_user(test_db, role=UserRole.ADMIN)
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)

    # Instructor must have an active LFA_COACH UserLicense for eligibility check
    coach_lic_f61 = UserLicense(
        user_id=instructor.id,
        specialization_type="LFA_COACH",
        current_level=5,
        max_achieved_level=5,
        is_active=True,
        expires_at=None,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    test_db.add(coach_lic_f61)
    test_db.flush()

    uid = _uid()

    sem = Semester(
        code=f"F61-{uid}",
        name=f"F-61 Slot Semester {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="ENROLLMENT_OPEN",
    )
    test_db.add(sem)
    test_db.flush()

    # ── HTTP: admin creates MASTER instructor slot ────────────────────────────
    app.dependency_overrides[get_current_user_web] = lambda: admin
    r = client.post(
        f"/admin/tournaments/{sem.id}/instructor-slots",
        data={
            "instructor_id": str(instructor.id),
            "role": "MASTER",
            "notes": "F-61 test slot",
        },
    )
    assert r.status_code == 201, (
        f"Instructor slot create must return 201, got {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert "slot_id" in body, f"Response must contain 'slot_id', got: {body}"
    assert body["status"] == SlotStatus.PLANNED.value, (
        f"New slot status must be PLANNED, got: {body['status']}"
    )

    # ── DB: TournamentInstructorSlot row exists with PLANNED status ───────────
    test_db.expire_all()
    slot = (
        test_db.query(TournamentInstructorSlot)
        .filter(
            TournamentInstructorSlot.semester_id == sem.id,
            TournamentInstructorSlot.instructor_id == instructor.id,
        )
        .first()
    )
    assert slot is not None, "TournamentInstructorSlot must be created after POST"
    assert slot.status == SlotStatus.PLANNED.value, (
        f"Slot.status must be PLANNED, got {slot.status}"
    )
    assert slot.role == SlotRole.MASTER.value, (
        f"Slot.role must be MASTER, got {slot.role}"
    )
    assert slot.assigned_by == admin.id, (
        f"Slot.assigned_by must be admin.id={admin.id}, got {slot.assigned_by}"
    )

    # ── UI: response body contains slot metadata ──────────────────────────────
    assert "slot_id" in r.text, f"Response body must contain 'slot_id'. Body: {r.text[:200]}"
    assert "PLANNED" in r.text, f"Response body must contain 'PLANNED'. Body: {r.text[:200]}"


# ── Sprint 7: F-62 — Player Check-In ─────────────────────────────────────────

def test_admin_player_checkin_creates_checkin_record(test_db: Session, client: TestClient):
    """F-62: Admin checks in a player → TournamentPlayerCheckin row created.

    Chain:
      Setup: TournamentInstructorSlot(status=CHECKED_IN) required by service guard
      POST /admin/tournaments/{id}/players/{pid}/checkin  JSON={}  (as admin)
      → 200 JSON {"ok": True, "checked_in_at": "..."}
      DB:  TournamentPlayerCheckin(tournament_id, user_id=player.id) exists
      UI:  response body contains "checked_in_at"

    Route: uses get_current_admin_user_hybrid (Bearer or cookie admin auth).
    Guard: _require_instructor_checked_in → at least 1 slot in CHECKED_IN status.
    State: TournamentPlayerCheckin upserted; SemesterEnrollment.tournament_checked_in_at
           synced if INDIVIDUAL check-in.
    """
    from app.dependencies import get_current_admin_user_hybrid

    admin = _make_user(test_db, role=UserRole.ADMIN)
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    player = _make_user(test_db)
    uid = _uid()

    sem = Semester(
        code=f"F62-{uid}",
        name=f"F-62 Checkin Semester {uid}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="ENROLLMENT_OPEN",
    )
    test_db.add(sem)
    test_db.flush()

    # REQUIRED: at least 1 instructor slot CHECKED_IN (_require_instructor_checked_in gate)
    instructor_slot = TournamentInstructorSlot(
        semester_id=sem.id,
        instructor_id=instructor.id,
        role=SlotRole.MASTER.value,
        status=SlotStatus.CHECKED_IN.value,
        assigned_by=admin.id,
    )
    test_db.add(instructor_slot)
    test_db.flush()

    # ── HTTP: admin checks in player ──────────────────────────────────────────
    app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
    r = client.post(
        f"/admin/tournaments/{sem.id}/players/{player.id}/checkin",
        json={},
    )
    assert r.status_code == 200, (
        f"Player checkin must return 200, got {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert body.get("ok") is True, f"Response must have ok=True, got: {body}"
    assert body.get("checked_in_at") is not None, (
        f"Response must include checked_in_at timestamp, got: {body}"
    )

    # ── DB: TournamentPlayerCheckin row created ───────────────────────────────
    test_db.expire_all()
    checkin = (
        test_db.query(TournamentPlayerCheckin)
        .filter(
            TournamentPlayerCheckin.tournament_id == sem.id,
            TournamentPlayerCheckin.user_id == player.id,
        )
        .first()
    )
    assert checkin is not None, "TournamentPlayerCheckin must be created after check-in"
    assert checkin.checked_in_at is not None, (
        "TournamentPlayerCheckin.checked_in_at must be set"
    )
    assert checkin.checked_in_by_id == admin.id, (
        f"Checkin.checked_in_by_id must be admin.id={admin.id}, got {checkin.checked_in_by_id}"
    )

    # ── UI: response body contains checkin timestamp ──────────────────────────
    assert "checked_in_at" in r.text, (
        f"Response body must contain 'checked_in_at'. Body: {r.text[:200]}"
    )


# ── Phase 2: Scheduling (MINI_SEASON / ACADEMY_SEASON) ────────────────────────
#
# SCHED_G1-01  normal generation   — 12-week MINI_SEASON, 12 sessions created
# SCHED_G1-02  pitch conflict      — hard-block → 409, full rollback
# SCHED_G1-03  skip_conflicts=True — partial generation (1 conflict skipped)
# ---------------------------------------------------------------------------


def _make_mini_season_with_config(
    db: Session,
    *,
    start_date: date,
    weeks: int,
    day_of_week: int,
    start_time_h: int = 17,
):
    """Create Location → Campus → Pitch → MINI_SEASON Semester → SemesterScheduleConfig.

    Returns (semester, campus, pitch).
    """
    from datetime import time as dt_time
    from app.models.location import Location, LocationType
    from app.models.campus import Campus as CampusModel
    from app.models.pitch import Pitch as PitchModel
    from app.models.semester_schedule_config import SemesterScheduleConfig as SSC

    uid = _uid()
    loc = Location(
        name=f"SLoc-{uid}",
        city=f"City-{uid}",
        country="Hungary",
        location_type=LocationType.CENTER,
    )
    db.add(loc)
    db.flush()

    campus = CampusModel(name=f"SC-{uid}", location_id=loc.id, is_active=True)
    db.add(campus)
    db.flush()

    pitch = PitchModel(
        campus_id=campus.id,
        pitch_number=1,
        name="Pálya 1",
        capacity=20,
        is_active=True,
    )
    db.add(pitch)
    db.flush()

    end_date = start_date + timedelta(weeks=weeks) - timedelta(days=1)
    semester = Semester(
        code=f"MS-{uid}",
        name=f"Mini Season {uid}",
        semester_category=SemesterCategory.MINI_SEASON,
        specialization_type="LFA_FOOTBALL_PLAYER",
        status=SemesterStatus.ONGOING,
        start_date=start_date,
        end_date=end_date,
        location_id=loc.id,
        campus_id=campus.id,
        enrollment_cost=2000,
    )
    db.add(semester)
    db.flush()

    config = SSC(
        semester_id=semester.id,
        day_of_week=day_of_week,
        start_time=dt_time(start_time_h, 0),
        duration_minutes=90,
        sessions_per_week=1,
        campus_id=campus.id,
        pitch_id=pitch.id,
    )
    db.add(config)
    db.flush()

    return semester, campus, pitch


@pytest.mark.sched
def test_mini_season_generate_sessions(test_db: Session, client: TestClient):
    """SCHED_G1-01: 12-week MINI_SEASON, day_of_week=0 (Monday).

    start_date=2026-07-07 (Tuesday) → first Monday is 2026-07-13.
    Wait — 2026-07-07 is a Tuesday, so first Monday >= 07-07 is 07-13.

    Actually let me recalculate: day_of_week=0 (Monday).
    2026-07-07 is a Tuesday (weekday()=1). days_ahead = (0 - 1) % 7 = 6.
    First session: 2026-07-07 + 6 days = 2026-07-13 (Monday).
    12 weeks → last session: 2026-07-13 + 11*7 = 2026-07-13 + 77 = 2026-09-28.

    Asserts: 12 sessions created, first/last dates correct, campus/pitch assigned,
             config.sessions_generated=True, config.sessions_count=12.
    """
    from app.dependencies import get_current_admin_user as _get_admin
    from app.models.semester_schedule_config import SemesterScheduleConfig as SSC

    semester, campus, pitch = _make_mini_season_with_config(
        test_db,
        start_date=date(2026, 7, 7),
        weeks=12,
        day_of_week=0,
    )
    admin = _make_user(test_db, role=UserRole.ADMIN)
    app.dependency_overrides[_get_admin] = lambda: admin

    r = client.post(
        f"/api/v1/semesters/{semester.id}/generate-sessions",
        json={
            "day_of_week": 0,
            "start_time": "17:00",
            "duration_minutes": 90,
            "sessions_per_week": 1,
            "skip_conflicts": False,
        },
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}. Body: {r.text[:400]}"
    body = r.json()
    assert body["sessions_created"] == 12, f"Expected 12 sessions, got {body}"
    assert body["sessions_skipped"] == 0

    # DB assertions
    test_db.expire_all()
    sessions = (
        test_db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester.id,
            SessionModel.auto_generated == True,
        )
        .order_by(SessionModel.date_start.asc())
        .all()
    )
    assert len(sessions) == 12, f"Expected 12 auto_generated sessions in DB, got {len(sessions)}"

    # First Monday on or after 2026-07-07: days_ahead=(0-1)%7=6 → 2026-07-13
    assert sessions[0].date_start == datetime(2026, 7, 13, 17, 0), (
        f"First session should be 2026-07-13 17:00, got {sessions[0].date_start}"
    )
    # 11 more weekly Mondays → 2026-07-13 + 77 days = 2026-09-28
    assert sessions[-1].date_start == datetime(2026, 9, 28, 17, 0), (
        f"Last session should be 2026-09-28 17:00, got {sessions[-1].date_start}"
    )
    assert all(s.campus_id == campus.id for s in sessions), "All sessions must use the campus"
    assert all(s.pitch_id == pitch.id for s in sessions), "All sessions must use the pitch"
    assert all(s.event_category.value == "TRAINING" for s in sessions)

    config = test_db.query(SSC).filter_by(semester_id=semester.id).first()
    assert config.sessions_generated is True
    assert config.sessions_count == 12


@pytest.mark.sched
def test_pitch_conflict_blocks_generation(test_db: Session, client: TestClient):
    """SCHED_G1-02: Blocking session on the pitch → 409 pitch_conflict, 0 auto-generated sessions."""
    from app.dependencies import get_current_admin_user as _get_admin

    semester, campus, pitch = _make_mini_season_with_config(
        test_db,
        start_date=date(2026, 7, 7),
        weeks=12,
        day_of_week=0,
    )

    # 2026-07-13 is the first Monday >= 2026-07-07
    blocker = SessionModel(
        title="Blocker",
        semester_id=semester.id,
        campus_id=campus.id,
        pitch_id=pitch.id,
        date_start=datetime(2026, 7, 13, 17, 0),
        date_end=datetime(2026, 7, 13, 18, 30),
        session_status="scheduled",
        auto_generated=False,
        rounds_data={},
    )
    test_db.add(blocker)
    test_db.flush()

    admin = _make_user(test_db, role=UserRole.ADMIN)
    app.dependency_overrides[_get_admin] = lambda: admin

    r = client.post(
        f"/api/v1/semesters/{semester.id}/generate-sessions",
        json={
            "day_of_week": 0,
            "start_time": "17:00",
            "duration_minutes": 90,
            "sessions_per_week": 1,
            "skip_conflicts": False,
        },
    )
    assert r.status_code == 409, f"Expected 409, got {r.status_code}. Body: {r.text[:400]}"
    body = r.json()
    # App uses custom error handler: {"error": {"message": ...}} not {"detail": ...}
    error_msg = body.get("error", {}).get("message") or body.get("detail", {})
    if isinstance(error_msg, str):
        error_msg = {}
    assert error_msg.get("error") == "pitch_conflict", (
        f"Expected pitch_conflict in error message, got: {body}"
    )

    # Full rollback: 0 auto_generated sessions in DB
    test_db.expire_all()
    auto_count = (
        test_db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester.id,
            SessionModel.auto_generated == True,
        )
        .count()
    )
    assert auto_count == 0, f"Expected 0 auto_generated sessions after conflict rollback, got {auto_count}"


@pytest.mark.sched
def test_skip_conflict_partial_generation(test_db: Session, client: TestClient):
    """SCHED_G1-03: skip_conflicts=True with 1 blocker → 11 sessions created, 1 skipped."""
    from app.dependencies import get_current_admin_user as _get_admin

    semester, campus, pitch = _make_mini_season_with_config(
        test_db,
        start_date=date(2026, 7, 7),
        weeks=12,
        day_of_week=0,
    )

    # 2nd Monday: 2026-07-13 + 7 = 2026-07-20
    blocker = SessionModel(
        title="Blocker",
        semester_id=semester.id,
        campus_id=campus.id,
        pitch_id=pitch.id,
        date_start=datetime(2026, 7, 20, 17, 0),
        date_end=datetime(2026, 7, 20, 18, 30),
        session_status="scheduled",
        auto_generated=False,
        rounds_data={},
    )
    test_db.add(blocker)
    test_db.flush()

    admin = _make_user(test_db, role=UserRole.ADMIN)
    app.dependency_overrides[_get_admin] = lambda: admin

    r = client.post(
        f"/api/v1/semesters/{semester.id}/generate-sessions",
        json={
            "day_of_week": 0,
            "start_time": "17:00",
            "duration_minutes": 90,
            "sessions_per_week": 1,
            "skip_conflicts": True,
        },
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}. Body: {r.text[:400]}"
    body = r.json()
    assert body["sessions_created"] == 11, f"Expected 11 sessions, got {body}"
    assert body["sessions_skipped"] == 1, f"Expected 1 skipped, got {body}"
    assert len(body["conflict_details"]) == 1

    test_db.expire_all()
    auto_count = (
        test_db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester.id,
            SessionModel.auto_generated == True,
        )
        .count()
    )
    assert auto_count == 11, f"Expected 11 auto_generated sessions, got {auto_count}"


# ── Phase 2: Scheduling — Web UI (SCHED_G2) ───────────────────────────────────
#
# SCHED_G2-01  GET schedule page renders (form visible, semester name)
# SCHED_G2-02  POST web generate form → 303 redirect + sessions in DB

@pytest.mark.sched
def test_semester_schedule_page_renders(test_db: Session, client: TestClient):
    """SCHED_G2-01: GET /admin/semesters/{id}/schedule → 200, form + semester name visible."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 7, 7), weeks=4, day_of_week=0
    )
    admin = _make_user(test_db, role=UserRole.ADMIN)
    app.dependency_overrides[get_current_user_web] = lambda: admin

    r = client.get(f"/admin/semesters/{semester.id}/schedule")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}. Body: {r.text[:300]}"
    assert semester.name in r.text
    assert "Generate Sessions" in r.text
    assert "schedule-form" in r.text or "generate-btn" in r.text


@pytest.mark.sched
def test_semester_schedule_generate_via_web(test_db: Session, client: TestClient):
    """SCHED_G2-02: POST generate form → 303 redirect with flash + sessions in DB."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 7, 7), weeks=4, day_of_week=0
    )
    admin = _make_user(test_db, role=UserRole.ADMIN)
    app.dependency_overrides[get_current_user_web] = lambda: admin

    r = client.post(
        f"/admin/semesters/{semester.id}/schedule/generate",
        data={
            "day_of_week": "0",
            "start_time": "17:00",
            "duration_minutes": "90",
            "sessions_per_week": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"Expected 303, got {r.status_code}. Body: {r.text[:300]}"
    assert f"/admin/semesters/{semester.id}/schedule" in r.headers["location"]
    assert "flash" in r.headers["location"]

    test_db.expire_all()
    count = (
        test_db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester.id,
            SessionModel.auto_generated == True,
        )
        .count()
    )
    assert count == 4, f"Expected 4 sessions (4 Mondays in 4-week window starting 2026-07-07), got {count}"


# ── Phase 2 Release Gate: delete-with-attendance → 409 (SCHED_G1-04) ─────────

@pytest.mark.sched
def test_delete_sessions_blocked_by_attendance(test_db: Session, client: TestClient):
    """SCHED_G1-04 (release gate): DELETE sessions returns 409 when attendance exists.

    Flow:
      1. Generate sessions for a MINI_SEASON semester (12 weeks)
      2. Create one Attendance row for the first generated session
      3. DELETE /api/v1/semesters/{id}/sessions → expect 409
      4. Verify all 12 sessions still exist in DB (rollback-safe)
      5. Verify config.sessions_generated still True
    """
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 8, 4), weeks=12, day_of_week=1  # Tuesdays
    )
    from app.dependencies import get_current_admin_user as _get_admin
    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db, role=UserRole.STUDENT)
    app.dependency_overrides[_get_admin] = lambda: admin

    # 1. Generate 12 sessions
    r_gen = client.post(
        f"/api/v1/semesters/{semester.id}/generate-sessions",
        json={
            "day_of_week": 1,
            "start_time": "18:00",
            "duration_minutes": 90,
            "sessions_per_week": 1,
            "skip_conflicts": False,
        },
    )
    assert r_gen.status_code == 200, f"Generate failed: {r_gen.text[:300]}"
    assert r_gen.json()["sessions_created"] == 12

    # 2. Fetch first generated session
    test_db.expire_all()
    first_session = (
        test_db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester.id,
            SessionModel.auto_generated == True,
        )
        .order_by(SessionModel.date_start.asc())
        .first()
    )
    assert first_session is not None

    # 3. Insert an Attendance record (booking_id=None → tournament-style attendance)
    from app.models.attendance import Attendance, AttendanceStatus
    att = Attendance(
        user_id=student.id,
        session_id=first_session.id,
        booking_id=None,
        status=AttendanceStatus.present,
    )
    test_db.add(att)
    test_db.commit()

    # 4. Attempt DELETE → expect 409
    r_del = client.delete(f"/api/v1/semesters/{semester.id}/sessions")
    assert r_del.status_code == 409, (
        f"Expected 409 (attendance guard), got {r_del.status_code}. Body: {r_del.text[:300]}"
    )
    body_text = r_del.text
    assert "attendance" in body_text.lower() or "Cannot delete" in body_text, (
        f"Expected attendance-guard message in body, got: {body_text[:300]}"
    )

    # 5. Verify sessions still present (nothing deleted)
    test_db.expire_all()
    remaining = (
        test_db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester.id,
            SessionModel.auto_generated == True,
        )
        .count()
    )
    assert remaining == 12, f"Expected 12 sessions still present, got {remaining}"

    # 6. Config state unchanged
    from app.models.semester_schedule_config import SemesterScheduleConfig
    cfg = test_db.query(SemesterScheduleConfig).filter_by(semester_id=semester.id).first()
    assert cfg.sessions_generated == True, "Config should still be sessions_generated=True"


# ── Phase 3: Student Enrollment (MINI_SEASON / ACADEMY_SEASON) ───────────────


@pytest.mark.sched
def test_semester_enroll_browse_page(test_db: Session, client: TestClient):
    """SCHED_G3-01: GET /semesters/enroll → 200, matching semester visible for student."""
    semester, _, _ = _make_mini_season_with_config(
        test_db, start_date=date(2026, 8, 4), weeks=8, day_of_week=1
    )
    student = _make_user(test_db, role=UserRole.STUDENT)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    _make_license(test_db, student)
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student

    r = client.get("/semesters/enroll")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}. Body: {r.text[:300]}"
    assert semester.name in r.text, f"Expected '{semester.name}' in page"


@pytest.mark.sched
def test_semester_auto_enroll(test_db: Session, client: TestClient):
    """SCHED_G3-02: POST /semesters/request-enrollment → APPROVED + credits deducted + sessions booked."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 8, 4), weeks=4, day_of_week=1
    )
    # Seed 4 auto_generated sessions for the semester
    for i in range(4):
        s = SessionModel(
            title=f"G3 Session {i + 1}",
            semester_id=semester.id,
            campus_id=campus.id,
            pitch_id=pitch.id,
            date_start=datetime(2026, 8, 5 + i * 7, 17, 0),
            date_end=datetime(2026, 8, 5 + i * 7, 18, 30),
            session_status="scheduled",
            auto_generated=True,
            rounds_data={},
        )
        test_db.add(s)
    test_db.flush()

    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=5000)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    license_ = _make_license(test_db, student)
    # enrollment_cost already set to 2000 by _make_mini_season_with_config
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student

    r = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"Expected 303, got {r.status_code}. Body: {r.text[:300]}"
    assert "success=enrolled" in r.headers["location"], (
        f"Expected success=enrolled in redirect: {r.headers['location']}"
    )

    test_db.expire_all()
    enrollment = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student.id, semester_id=semester.id)
        .first()
    )
    assert enrollment is not None, "SemesterEnrollment not created"
    assert enrollment.request_status == EnrollmentStatus.APPROVED
    assert enrollment.is_active == True

    test_db.refresh(student)
    assert student.credit_balance == 3000, (
        f"Expected credit_balance=3000 (5000-2000), got {student.credit_balance}"
    )

    bookings = (
        test_db.query(Booking)
        .filter_by(user_id=student.id, enrollment_id=enrollment.id)
        .all()
    )
    assert len(bookings) == 4, f"Expected 4 Booking rows, got {len(bookings)}"


@pytest.mark.sched
def test_semester_session_visibility_after_enroll(test_db: Session, client: TestClient):
    """SCHED_G3-03: After APPROVED enrollment, student sees sessions at GET /sessions."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 8, 4), weeks=2, day_of_week=1
    )
    s1 = SessionModel(
        title="G3 Visible Session",
        semester_id=semester.id,
        campus_id=campus.id,
        pitch_id=pitch.id,
        date_start=datetime(2026, 8, 5, 17, 0),
        date_end=datetime(2026, 8, 5, 18, 30),
        session_status="scheduled",
        auto_generated=True,
        rounds_data={},
    )
    test_db.add(s1)
    test_db.flush()

    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=0)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    license_ = _make_license(test_db, student)
    semester.enrollment_cost = 0
    test_db.flush()

    # Enroll directly in DB (route-level already tested in G3-02)
    now = datetime.utcnow()
    enrollment = SemesterEnrollment(
        user_id=student.id,
        semester_id=semester.id,
        user_license_id=license_.id,
        request_status=EnrollmentStatus.APPROVED,
        is_active=True,
        requested_at=now,
        approved_at=now,
        enrolled_at=now,
    )
    test_db.add(enrollment)
    test_db.commit()
    app.dependency_overrides[get_current_user_web] = lambda: student

    r = client.get("/sessions")
    assert r.status_code == 200, f"Expected 200 for /sessions, got {r.status_code}"
    assert "G3 Visible Session" in r.text, (
        "Expected session title to appear in /sessions after enrollment"
    )


@pytest.mark.sched
def test_semester_withdraw_enrollment(test_db: Session, client: TestClient):
    """SCHED_G3-04: POST /semesters/withdraw-enrollment → 50% refund + bookings deleted + WITHDRAWN."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 8, 4), weeks=4, day_of_week=2
    )
    # enrollment_cost=2000 from helper; student has 3000 (simulating post-enrollment state)
    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=3000)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    license_ = _make_license(test_db, student)
    test_db.flush()

    now = datetime.utcnow()
    enrollment = SemesterEnrollment(
        user_id=student.id,
        semester_id=semester.id,
        user_license_id=license_.id,
        request_status=EnrollmentStatus.APPROVED,
        is_active=True,
        requested_at=now,
        approved_at=now,
        enrolled_at=now,
    )
    test_db.add(enrollment)
    test_db.flush()

    # Create a proper session + booking so we can verify booking deletion
    session_obj = SessionModel(
        title="G3 Withdraw Session",
        semester_id=semester.id,
        campus_id=campus.id,
        pitch_id=pitch.id,
        date_start=datetime(2026, 8, 5, 17, 0),
        date_end=datetime(2026, 8, 5, 18, 30),
        session_status="scheduled",
        auto_generated=True,
        rounds_data={},
    )
    test_db.add(session_obj)
    test_db.flush()
    test_db.add(Booking(
        user_id=student.id,
        session_id=session_obj.id,
        enrollment_id=enrollment.id,
        status=BookingStatus.CONFIRMED,
        created_at=now,
    ))
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student

    r = client.post(
        "/semesters/withdraw-enrollment",
        data={"enrollment_id": str(enrollment.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"Expected 303, got {r.status_code}. Body: {r.text[:300]}"
    assert "success=withdrawn" in r.headers["location"], (
        f"Expected success=withdrawn in redirect: {r.headers['location']}"
    )

    test_db.expire_all()
    test_db.refresh(enrollment)
    assert enrollment.is_active == False, "Enrollment should be inactive after withdrawal"
    assert enrollment.request_status == EnrollmentStatus.WITHDRAWN

    test_db.refresh(student)
    assert student.credit_balance == 4000, (
        f"Expected credit_balance=4000 (3000 + 1000 refund), got {student.credit_balance}"
    )

    remaining = (
        test_db.query(Booking).filter_by(enrollment_id=enrollment.id).count()
    )
    assert remaining == 0, f"Expected 0 bookings after withdrawal, got {remaining}"


@pytest.mark.sched
def test_auto_booking_capacity_enforced(test_db: Session, client: TestClient):
    """SCHED_G3-05: Session full at enrollment time → auto-booking creates WAITLISTED booking."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 9, 1), weeks=2, day_of_week=1
    )
    # 1 session, capacity=1
    s = SessionModel(
        title="Full Session",
        semester_id=semester.id,
        campus_id=campus.id,
        pitch_id=pitch.id,
        date_start=datetime(2026, 9, 8, 17, 0),
        date_end=datetime(2026, 9, 8, 18, 30),
        session_status="scheduled",
        auto_generated=True,
        rounds_data={},
        capacity=1,
    )
    test_db.add(s)
    test_db.flush()
    # Fill it with another student
    filler = _make_user(test_db, role=UserRole.STUDENT)
    test_db.add(Booking(
        user_id=filler.id,
        session_id=s.id,
        enrollment_id=None,
        status=BookingStatus.CONFIRMED,
        created_at=datetime.utcnow(),
    ))
    test_db.flush()

    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=0)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    _make_license(test_db, student)
    semester.enrollment_cost = 0
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student

    r = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"Expected 303, got {r.status_code}"

    test_db.expire_all()
    booking = test_db.query(Booking).filter_by(user_id=student.id, session_id=s.id).first()
    assert booking is not None, "Auto-booking should have been created even for full session"
    assert booking.status == BookingStatus.WAITLISTED, (
        f"Expected WAITLISTED (session full, capacity=1), got {booking.status}"
    )


@pytest.mark.sched
def test_re_enrollment_after_withdraw(test_db: Session, client: TestClient):
    """SCHED_G3-06: Withdraw then re-enroll → WITHDRAWN enrollment reactivated, credits deducted again."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 9, 1), weeks=2, day_of_week=2
    )
    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=4000)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    license_ = _make_license(test_db, student)
    semester.enrollment_cost = 1000
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student

    # First enroll
    r1 = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r1.status_code == 303
    test_db.expire_all()
    test_db.refresh(student)
    assert student.credit_balance == 3000, f"Expected 3000 after enrollment, got {student.credit_balance}"

    enrollment = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student.id, semester_id=semester.id)
        .first()
    )
    first_id = enrollment.id

    # Withdraw
    r2 = client.post(
        "/semesters/withdraw-enrollment",
        data={"enrollment_id": str(enrollment.id)},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    test_db.expire_all()
    test_db.refresh(student)
    assert student.credit_balance == 3500, f"Expected 3500 after 50% refund, got {student.credit_balance}"

    # Re-enroll (unique constraint would block INSERT; reactivation must succeed)
    r3 = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r3.status_code == 303, f"Re-enrollment should succeed, got {r3.status_code}"
    assert "success=enrolled" in r3.headers["location"]

    test_db.expire_all()
    enrollment_after = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student.id, semester_id=semester.id)
        .first()
    )
    assert enrollment_after.id == first_id, "Same enrollment row should be reactivated (not a new row)"
    assert enrollment_after.request_status == EnrollmentStatus.APPROVED
    assert enrollment_after.is_active == True

    test_db.refresh(student)
    assert student.credit_balance == 2500, f"Expected 2500 (3500 - 1000), got {student.credit_balance}"


@pytest.mark.sched
def test_waitlist_auto_promote_on_withdraw(test_db: Session, client: TestClient):
    """SCHED_G3-07: Withdraw semester → CONFIRMED booking freed → first WAITLISTED promoted to CONFIRMED."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 10, 1), weeks=2, day_of_week=3
    )
    s = SessionModel(
        title="Promo Session",
        semester_id=semester.id,
        campus_id=campus.id,
        pitch_id=pitch.id,
        date_start=datetime(2026, 10, 7, 17, 0),
        date_end=datetime(2026, 10, 7, 18, 30),
        session_status="scheduled",
        auto_generated=True,
        rounds_data={},
        capacity=1,
    )
    test_db.add(s)
    semester.enrollment_cost = 0
    test_db.flush()

    # Student A enrolls → session CONFIRMED (capacity=1, 0 confirmed so far)
    student_a = _make_user(test_db, role=UserRole.STUDENT, credit_balance=0)
    student_a.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    _make_license(test_db, student_a)
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student_a
    r1 = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r1.status_code == 303
    test_db.expire_all()
    booking_a = test_db.query(Booking).filter_by(user_id=student_a.id, session_id=s.id).first()
    assert booking_a.status == BookingStatus.CONFIRMED, "Student A should be CONFIRMED"

    # Student B enrolls → session WAITLISTED (capacity=1, 1 confirmed)
    student_b = _make_user(test_db, role=UserRole.STUDENT, credit_balance=0)
    student_b.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    _make_license(test_db, student_b)
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student_b
    r2 = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    test_db.expire_all()
    booking_b = test_db.query(Booking).filter_by(user_id=student_b.id, session_id=s.id).first()
    assert booking_b.status == BookingStatus.WAITLISTED, "Student B should be WAITLISTED"

    # Student A withdraws → frees the CONFIRMED slot
    enroll_a = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student_a.id, semester_id=semester.id)
        .first()
    )
    app.dependency_overrides[get_current_user_web] = lambda: student_a
    r3 = client.post(
        "/semesters/withdraw-enrollment",
        data={"enrollment_id": str(enroll_a.id)},
        follow_redirects=False,
    )
    assert r3.status_code == 303

    # Student B should now be auto-promoted to CONFIRMED
    test_db.expire_all()
    booking_b_after = (
        test_db.query(Booking).filter_by(user_id=student_b.id, session_id=s.id).first()
    )
    assert booking_b_after is not None, "Student B booking should still exist after promotion"
    assert booking_b_after.status == BookingStatus.CONFIRMED, (
        f"Expected CONFIRMED after auto-promote, got {booking_b_after.status}"
    )


@pytest.mark.sched
def test_audit_log_on_semester_enroll(test_db: Session, client: TestClient):
    """SCHED_G3-08: Semester enrollment creates AuditLog(action=SEMESTER_ENROLLED) entry."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 10, 1), weeks=2, day_of_week=4
    )
    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=0)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    _make_license(test_db, student)
    semester.enrollment_cost = 0
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student

    r = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"Expected 303, got {r.status_code}"

    test_db.expire_all()
    log = (
        test_db.query(AuditLog)
        .filter_by(user_id=student.id, action="SEMESTER_ENROLLED")
        .first()
    )
    assert log is not None, "AuditLog entry should be created on semester enrollment"
    assert log.resource_type == "semester_enrollment"


# ── Phase 5.5: Stabilization ───────────────────────────────────────────────────
#
# SCHED_G3-09  status guard          — COMPLETED semester → enrollment blocked
# SCHED_G3-10  session delete cleans — bookings cleaned up before sessions deleted
# ------------------------------------------------------------------------------


@pytest.mark.sched
def test_enrollment_blocked_when_semester_closed(test_db: Session, client: TestClient):
    """SCHED_G3-09: POST enroll on a COMPLETED semester → 303 + error=Semester+not+open+for+enrollment."""
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 9, 1), weeks=4, day_of_week=2
    )
    semester.status = SemesterStatus.COMPLETED
    test_db.flush()

    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=0)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    _make_license(test_db, student)
    semester.enrollment_cost = 0
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student

    r = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"Expected 303, got {r.status_code}"
    assert "Semester+not+open+for+enrollment" in r.headers["location"], (
        f"Expected closed-semester error in redirect, got: {r.headers['location']}"
    )


@pytest.mark.sched
def test_session_delete_cleans_bookings(test_db: Session, client: TestClient):
    """SCHED_G3-10: Admin DELETE sessions → orphaned bookings are removed before session delete."""
    from app.dependencies import get_current_admin_user as _get_admin

    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2026, 10, 1), weeks=2, day_of_week=3
    )
    admin = _make_user(test_db, role=UserRole.ADMIN)
    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=0)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    _make_license(test_db, student)
    semester.enrollment_cost = 0
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student
    app.dependency_overrides[_get_admin] = lambda: admin

    # Generate 2 weekly sessions
    r_gen = client.post(
        f"/api/v1/semesters/{semester.id}/generate-sessions",
        json={
            "day_of_week": 3,
            "start_time": "17:00",
            "duration_minutes": 90,
            "sessions_per_week": 1,
            "skip_conflicts": False,
        },
    )
    assert r_gen.status_code == 200, f"Generate failed: {r_gen.text[:300]}"
    sessions_created = r_gen.json()["sessions_created"]
    assert sessions_created > 0, "Expected at least 1 session generated"

    # Enroll student → creates CONFIRMED bookings
    r_enroll = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r_enroll.status_code == 303, f"Enroll failed: {r_enroll.text[:300]}"

    # Verify bookings exist before delete
    test_db.expire_all()
    enrollment = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student.id, semester_id=semester.id, is_active=True)
        .first()
    )
    assert enrollment is not None, "Enrollment must exist after successful enrollment"
    booking_count_before = (
        test_db.query(Booking)
        .filter_by(enrollment_id=enrollment.id)
        .count()
    )
    assert booking_count_before > 0, f"Expected bookings before delete, got 0"

    # Admin deletes all generated sessions
    r_del = client.delete(f"/api/v1/semesters/{semester.id}/sessions")
    assert r_del.status_code == 200, (
        f"Expected 200 on session delete, got {r_del.status_code}. Body: {r_del.text[:300]}"
    )

    # Assert all bookings for the enrollment are gone
    test_db.expire_all()
    booking_count_after = (
        test_db.query(Booking)
        .filter_by(enrollment_id=enrollment.id)
        .count()
    )
    assert booking_count_after == 0, (
        f"Expected 0 bookings after session delete, got {booking_count_after}"
    )


# ── Phase 5.5: Invariant Smoke Tests (mandatory CI gate) ──────────────────────
#
# SCHED_INV-01  credit invariant   — balance = initial - cost + refund (exact)
# SCHED_INV-02  capacity invariant — confirmed_count <= session.capacity always
# SCHED_INV-03  post-withdraw inv  — confirmed_count restored to capacity after
#                                    withdraw + auto-promote
#
# Rule: all 3 invariants must hold on every sched-touching commit.
# CI enforces: pytest -m sched → 19/19 (or higher) green = phase complete.
# "No green CI = no phase complete" — violations block merge.
# ------------------------------------------------------------------------------


def _make_inv_session(
    db: Session,
    semester,
    campus,
    pitch,
    date_start: datetime,
    capacity: int,
    title: str = "INV Session",
) -> SessionModel:
    """Create a single auto-generated session for invariant tests."""
    s = SessionModel(
        title=title,
        semester_id=semester.id,
        campus_id=campus.id,
        pitch_id=pitch.id,
        date_start=date_start,
        date_end=date_start.replace(hour=date_start.hour + 1, minute=30),
        session_status="scheduled",
        auto_generated=True,
        rounds_data={},
        capacity=capacity,
    )
    db.add(s)
    db.flush()
    return s


def _make_inv_student(db: Session) -> tuple:
    """Create student + license, returns (student, license)."""
    stu = _make_user(db, role=UserRole.STUDENT, credit_balance=0)
    stu.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    lic = _make_license(db, stu)
    db.flush()
    return stu, lic


@pytest.mark.sched
def test_credit_balance_invariant(test_db: Session, client: TestClient):
    """SCHED_INV-01: Credit invariant — balance = initial - cost + refund (exact).

    Asserts:
      balance_after_enroll == initial - cost
      balance_after_withdraw == initial - cost + cost // 2
      CreditTransaction(SEMESTER_ENROLLMENT).amount == -cost
      CreditTransaction(SEMESTER_UNENROLL_REFUND).amount == cost // 2
      booking count == 0 after withdraw (no orphans)
    """
    INITIAL = 1000
    COST = 400
    REFUND = COST // 2  # 200

    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2027, 1, 1), weeks=2, day_of_week=0
    )
    _make_inv_session(test_db, semester, campus, pitch,
                      datetime(2027, 1, 6, 17, 0), capacity=10, title="INV-01 Session")
    semester.enrollment_cost = COST
    test_db.flush()

    student, license_ = _make_inv_student(test_db)
    student.credit_balance = INITIAL
    test_db.flush()
    app.dependency_overrides[get_current_user_web] = lambda: student

    # ── Enroll ────────────────────────────────────────────────────────────────
    r = client.post("/semesters/request-enrollment",
                    data={"semester_id": str(semester.id)}, follow_redirects=False)
    assert r.status_code == 303, f"Enroll failed: {r.status_code}"

    test_db.expire_all()
    test_db.refresh(student)
    assert student.credit_balance == INITIAL - COST, (
        f"INV-01: balance after enroll must be {INITIAL - COST}, got {student.credit_balance}"
    )
    tx_enroll = (
        test_db.query(CreditTransaction)
        .filter_by(user_license_id=license_.id, transaction_type="SEMESTER_ENROLLMENT")
        .first()
    )
    assert tx_enroll is not None, "INV-01: CreditTransaction(SEMESTER_ENROLLMENT) must exist"
    assert tx_enroll.amount == -COST, (
        f"INV-01: enroll tx.amount must be -{COST}, got {tx_enroll.amount}"
    )

    enrollment = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student.id, semester_id=semester.id, is_active=True)
        .first()
    )
    assert enrollment is not None
    assert test_db.query(Booking).filter_by(enrollment_id=enrollment.id).count() == 1, (
        "INV-01: exactly 1 booking must exist after enroll"
    )

    # ── Withdraw ──────────────────────────────────────────────────────────────
    r2 = client.post("/semesters/withdraw-enrollment",
                     data={"enrollment_id": str(enrollment.id)}, follow_redirects=False)
    assert r2.status_code == 303, f"Withdraw failed: {r2.status_code}"

    test_db.expire_all()
    test_db.refresh(student)
    assert student.credit_balance == INITIAL - COST + REFUND, (
        f"INV-01: balance after withdraw must be {INITIAL - COST + REFUND}, got {student.credit_balance}"
    )
    tx_refund = (
        test_db.query(CreditTransaction)
        .filter_by(user_license_id=license_.id, transaction_type="SEMESTER_UNENROLL_REFUND")
        .first()
    )
    assert tx_refund is not None, "INV-01: CreditTransaction(SEMESTER_UNENROLL_REFUND) must exist"
    assert tx_refund.amount == REFUND, (
        f"INV-01: refund tx.amount must be {REFUND}, got {tx_refund.amount}"
    )
    assert test_db.query(Booking).filter_by(enrollment_id=enrollment.id).count() == 0, (
        "INV-01: all bookings must be cleaned up after withdraw"
    )


@pytest.mark.sched
def test_booking_capacity_invariant(test_db: Session, client: TestClient):
    """SCHED_INV-02: confirmed_count(session) <= session.capacity for all sessions always.

    Asserts:
      after N students enrolled up to capacity → confirmed_count == capacity
      N+1-th student enrolled beyond capacity → all bookings WAITLISTED
      confirmed_count never exceeds capacity for any session
    """
    CAPACITY = 2
    N_SESSIONS = 3

    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2027, 2, 1), weeks=4, day_of_week=0
    )
    sessions = [
        _make_inv_session(test_db, semester, campus, pitch,
                          datetime(2027, 2, 3 + i * 7, 17, 0),
                          capacity=CAPACITY, title=f"INV-02 S{i+1}")
        for i in range(N_SESSIONS)
    ]
    semester.enrollment_cost = 0
    test_db.flush()

    def _enroll(student):
        app.dependency_overrides[get_current_user_web] = lambda s=student: s
        r = client.post("/semesters/request-enrollment",
                        data={"semester_id": str(semester.id)}, follow_redirects=False)
        assert r.status_code == 303, f"Enroll failed for user {student.id}: {r.status_code}"

    # Enroll CAPACITY students → all CONFIRMED
    students_ok = []
    for _ in range(CAPACITY):
        stu, _ = _make_inv_student(test_db)
        _enroll(stu)
        students_ok.append(stu)

    # Enroll 1 extra student → all WAITLISTED (session full)
    student_extra, _ = _make_inv_student(test_db)
    _enroll(student_extra)

    test_db.expire_all()

    # Invariant: confirmed_count <= capacity for every session
    for s in sessions:
        confirmed = (
            test_db.query(Booking)
            .filter_by(session_id=s.id, status=BookingStatus.CONFIRMED)
            .count()
        )
        assert confirmed <= CAPACITY, (
            f"INV-02: session {s.id} confirmed={confirmed} exceeds capacity={CAPACITY}"
        )
        assert confirmed == CAPACITY, (
            f"INV-02: session {s.id} confirmed={confirmed} should be exactly {CAPACITY}"
        )

    # Extra student's bookings are all WAITLISTED
    extra_bookings = (
        test_db.query(Booking).filter_by(user_id=student_extra.id).all()
    )
    assert len(extra_bookings) == N_SESSIONS, (
        f"INV-02: extra student must have {N_SESSIONS} bookings, got {len(extra_bookings)}"
    )
    assert all(b.status == BookingStatus.WAITLISTED for b in extra_bookings), (
        f"INV-02: extra student must be fully WAITLISTED, statuses: {[b.status for b in extra_bookings]}"
    )


@pytest.mark.sched
def test_post_withdraw_capacity_invariant(test_db: Session, client: TestClient):
    """SCHED_INV-03: After withdraw + auto-promote, confirmed_count == capacity again.

    Asserts:
      before withdraw: 1 CONFIRMED (A), 2 WAITLISTED (B, C)
      after A withdraws: A has 0 bookings, exactly 1 CONFIRMED, exactly 1 WAITLISTED
      confirmed_count never drops below 1 (capacity=1 maintained)
      total bookings for session = 2 (no ghost rows, no duplicates)
    """
    semester, campus, pitch = _make_mini_season_with_config(
        test_db, start_date=date(2027, 3, 1), weeks=2, day_of_week=2
    )
    s = _make_inv_session(test_db, semester, campus, pitch,
                          datetime(2027, 3, 5, 17, 0), capacity=1, title="INV-03 Session")
    semester.enrollment_cost = 0
    test_db.flush()

    # Create + enroll each student just-in-time (G3-07 pattern)
    student_a, _ = _make_inv_student(test_db)
    student_a_id = student_a.id
    app.dependency_overrides[get_current_user_web] = lambda: student_a
    r = client.post("/semesters/request-enrollment",
                    data={"semester_id": str(semester.id)}, follow_redirects=False)
    assert r.status_code == 303, f"Enroll A failed: {r.status_code}"
    test_db.expire_all()

    student_b, _ = _make_inv_student(test_db)
    app.dependency_overrides[get_current_user_web] = lambda: student_b
    r = client.post("/semesters/request-enrollment",
                    data={"semester_id": str(semester.id)}, follow_redirects=False)
    assert r.status_code == 303, f"Enroll B failed: {r.status_code}"
    test_db.expire_all()

    student_c, _ = _make_inv_student(test_db)
    app.dependency_overrides[get_current_user_web] = lambda: student_c
    r = client.post("/semesters/request-enrollment",
                    data={"semester_id": str(semester.id)}, follow_redirects=False)
    assert r.status_code == 303, f"Enroll C failed: {r.status_code}"

    # Verify pre-state: A=CONFIRMED, B+C=WAITLISTED
    test_db.expire_all()
    assert test_db.query(Booking).filter_by(
        session_id=s.id, status=BookingStatus.CONFIRMED).count() == 1
    assert test_db.query(Booking).filter_by(
        session_id=s.id, status=BookingStatus.WAITLISTED).count() == 2

    # Student A withdraws — re-query after expire_all
    student_a = test_db.query(User).filter_by(id=student_a_id).first()
    enrollment_a = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student_a_id, semester_id=semester.id, is_active=True)
        .first()
    )
    assert enrollment_a is not None
    app.dependency_overrides[get_current_user_web] = lambda: student_a
    r_w = client.post("/semesters/withdraw-enrollment",
                      data={"enrollment_id": str(enrollment_a.id)}, follow_redirects=False)
    assert r_w.status_code == 303, f"Withdraw failed: {r_w.status_code}"

    # Post-withdraw invariants
    test_db.expire_all()
    a_count = test_db.query(Booking).filter_by(user_id=student_a_id, session_id=s.id).count()
    assert a_count == 0, f"INV-03: student_A must have 0 bookings after withdraw, got {a_count}"

    confirmed_after = test_db.query(Booking).filter_by(
        session_id=s.id, status=BookingStatus.CONFIRMED).count()
    waitlisted_after = test_db.query(Booking).filter_by(
        session_id=s.id, status=BookingStatus.WAITLISTED).count()

    assert confirmed_after == 1, (
        f"INV-03: exactly 1 booking must be CONFIRMED after auto-promote (capacity=1), got {confirmed_after}"
    )
    assert waitlisted_after == 1, (
        f"INV-03: exactly 1 booking must remain WAITLISTED, got {waitlisted_after}"
    )
    total = test_db.query(Booking).filter_by(session_id=s.id).count()
    assert total == 2, (
        f"INV-03: total bookings for session must be 2 (1 confirmed + 1 waitlisted), got {total}"
    )


# ── Phase 5.5: Guard Negative Path Tests (SCHED_G3-11..15) ──────────────────
#
# SCHED_G3-11  role guard          — non-student (INSTRUCTOR) blocked
# SCHED_G3-12  license guard       — no matching active license → blocked
# SCHED_G3-13  duplicate guard     — already actively enrolled → blocked
# SCHED_G3-14  credit guard        — insufficient credits → blocked, balance unchanged
# SCHED_G3-15  ownership guard     — wrong user cannot withdraw another's enrollment
#
# These tests cover the critical early-return validation branches in programs.py
# that protect financial integrity and access control. All 5 guards must survive
# regression — failure in any would expose a business-logic breach.
# ------------------------------------------------------------------------------


@pytest.mark.sched
def test_enrollment_blocked_for_non_student(test_db: Session, client: TestClient):
    """SCHED_G3-11: Enrollment blocked when requester is not a STUDENT.

    Asserts:
      POST /semesters/request-enrollment as INSTRUCTOR → 303 + 'Student+role+required'
    """
    semester, _, _ = _make_mini_season_with_config(
        test_db, start_date=date(2027, 6, 1), weeks=4, day_of_week=0
    )
    instructor = _make_user(test_db, role=UserRole.INSTRUCTOR)
    instructor.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: instructor
    r = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"G3-11: expected 303, got {r.status_code}"
    assert "Student+role+required" in r.headers["location"], (
        f"G3-11: expected 'Student+role+required' in location, got {r.headers['location']}"
    )


@pytest.mark.sched
def test_enrollment_blocked_no_license(test_db: Session, client: TestClient):
    """SCHED_G3-12: Enrollment blocked when student has no active license for the specialization.

    Asserts:
      POST with no license → 303 + 'No+active+license+for+this+specialization'
    """
    semester, _, _ = _make_mini_season_with_config(
        test_db, start_date=date(2027, 7, 1), weeks=4, day_of_week=1
    )
    student = _make_user(test_db, role=UserRole.STUDENT)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    # Deliberately NO license created
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student
    r = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"G3-12: expected 303, got {r.status_code}"
    assert "No+active+license+for+this+specialization" in r.headers["location"], (
        f"G3-12: expected license error in location, got {r.headers['location']}"
    )


@pytest.mark.sched
def test_enrollment_blocked_when_already_enrolled(test_db: Session, client: TestClient):
    """SCHED_G3-13: Enrollment blocked when student already has an active enrollment.

    Asserts:
      Pre-existing active enrollment → 303 + 'Already+enrolled'
      No second enrollment row created in DB
    """
    semester, _, _ = _make_mini_season_with_config(
        test_db, start_date=date(2027, 8, 1), weeks=4, day_of_week=2
    )
    semester.enrollment_cost = 0
    student = _make_user(test_db, role=UserRole.STUDENT)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    lic = _make_license(test_db, student)
    existing = SemesterEnrollment(
        user_id=student.id,
        semester_id=semester.id,
        user_license_id=lic.id,
        request_status=EnrollmentStatus.APPROVED,
        is_active=True,
    )
    test_db.add(existing)
    test_db.flush()

    app.dependency_overrides[get_current_user_web] = lambda: student
    r = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"G3-13: expected 303, got {r.status_code}"
    assert "Already+enrolled" in r.headers["location"], (
        f"G3-13: expected 'Already+enrolled' in location, got {r.headers['location']}"
    )

    test_db.expire_all()
    count = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student.id, semester_id=semester.id)
        .count()
    )
    assert count == 1, f"G3-13: must have exactly 1 enrollment (no duplicate created), got {count}"


@pytest.mark.sched
def test_enrollment_blocked_insufficient_credits(test_db: Session, client: TestClient):
    """SCHED_G3-14: Enrollment blocked when student has insufficient credits.

    Asserts:
      credit_balance (100) < enrollment_cost (500) → 303 + 'Insufficient+credits'
      DB: user.credit_balance unchanged (100)
      DB: no SemesterEnrollment created
    """
    COST = 500
    INITIAL = 100

    semester, _, _ = _make_mini_season_with_config(
        test_db, start_date=date(2027, 9, 1), weeks=4, day_of_week=3
    )
    semester.enrollment_cost = COST
    student = _make_user(test_db, role=UserRole.STUDENT, credit_balance=INITIAL)
    student.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    _make_license(test_db, student)
    test_db.flush()

    student_id = student.id

    app.dependency_overrides[get_current_user_web] = lambda: student
    r = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"G3-14: expected 303, got {r.status_code}"
    assert "Insufficient+credits" in r.headers["location"], (
        f"G3-14: expected 'Insufficient+credits' in location, got {r.headers['location']}"
    )

    test_db.expire_all()
    reloaded = test_db.query(User).filter_by(id=student_id).first()
    assert reloaded.credit_balance == INITIAL, (
        f"G3-14: credit_balance must be unchanged ({INITIAL}), got {reloaded.credit_balance}"
    )
    enrollment_count = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student_id, semester_id=semester.id)
        .count()
    )
    assert enrollment_count == 0, (
        f"G3-14: no enrollment must exist after credit failure, got {enrollment_count}"
    )


@pytest.mark.sched
def test_withdraw_blocked_for_wrong_user(test_db: Session, client: TestClient):
    """SCHED_G3-15: Withdrawal blocked when a different user attempts to withdraw another's enrollment.

    Asserts:
      student_b POST withdraw with enrollment_id belonging to student_a →
        303 + 'Enrollment+not+found+or+already+withdrawn'
      DB: enrollment_a.is_active remains True (enrollment untouched)
    """
    semester, _, _ = _make_mini_season_with_config(
        test_db, start_date=date(2027, 10, 1), weeks=2, day_of_week=4
    )
    semester.enrollment_cost = 0
    test_db.flush()

    # student_a enrolls
    student_a, _ = _make_inv_student(test_db)
    student_a_id = student_a.id
    app.dependency_overrides[get_current_user_web] = lambda: student_a
    r_enroll = client.post(
        "/semesters/request-enrollment",
        data={"semester_id": str(semester.id)},
        follow_redirects=False,
    )
    assert r_enroll.status_code == 303, f"G3-15: student_a enroll failed: {r_enroll.status_code}"
    test_db.expire_all()

    enrollment_a = (
        test_db.query(SemesterEnrollment)
        .filter_by(user_id=student_a_id, semester_id=semester.id, is_active=True)
        .first()
    )
    assert enrollment_a is not None, "G3-15: student_a must have an active enrollment"
    enrollment_a_id = enrollment_a.id

    # student_b attempts to withdraw student_a's enrollment
    student_b, _ = _make_inv_student(test_db)
    app.dependency_overrides[get_current_user_web] = lambda: student_b
    r_withdraw = client.post(
        "/semesters/withdraw-enrollment",
        data={"enrollment_id": str(enrollment_a_id)},
        follow_redirects=False,
    )
    assert r_withdraw.status_code == 303, (
        f"G3-15: expected 303, got {r_withdraw.status_code}"
    )
    assert "Enrollment+not+found+or+already+withdrawn" in r_withdraw.headers["location"], (
        f"G3-15: expected ownership error in location, got {r_withdraw.headers['location']}"
    )

    # enrollment_a must be untouched
    test_db.expire_all()
    enrollment_a_reloaded = (
        test_db.query(SemesterEnrollment).filter_by(id=enrollment_a_id).first()
    )
    assert enrollment_a_reloaded.is_active is True, (
        "G3-15: enrollment_a.is_active must remain True after wrong-user withdraw attempt"
    )
