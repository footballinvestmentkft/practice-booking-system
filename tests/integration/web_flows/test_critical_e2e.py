"""
Critical E2E Tests
==================
8 full-chain tests covering previously-identified coverage gaps:

  QRB — Quiz Retry Best Score      : fail → retry → pass (UI + DB)
  QEG — Quiz Enrollment Gate       : no booking → 403; with booking → 200
  SFJ — Student Full Journey       : browse → enroll → enrolled state visible
  SDE — Skill Delta E2E            : tournament → TournamentParticipation → skills page
  CDE — Credit Deduction E2E       : enroll in paid event → deduction → history visible
  QAL — Quiz Attempt Limit         : fail × max_attempts → "No More Attempts" UI state
  QIS — Quiz Interrupted Resume    : start → abandon → re-GET → same attempt resumed
  QPG — Quiz State Progression     : no attempt → fail → pass → session_details tracks state

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
from app.dependencies import get_current_user_web, get_current_user_optional
from app.core.security import get_password_hash
from app.models.user import User, UserRole, SpecializationType
from app.models.license import UserLicense
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
    SessionQuiz,
    QuizCategory,
    QuizDifficulty,
    QuestionType,
)
from app.models.credit_transaction import CreditTransaction
from app.models.tournament_achievement import TournamentParticipation


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
        code=f"E2E-{_uid()}",
        name=f"E2E Tournament {_uid()}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="ENROLLMENT_OPEN",
        enrollment_cost=enrollment_cost,
    )
    db.add(sem)
    db.flush()
    cfg = TournamentConfiguration(
        semester_id=sem.id,
        participant_type="INDIVIDUAL",
        session_type_config="on_site",
        max_players=100,
    )
    db.add(cfg)
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
    r1 = client.get("/events/tournaments")
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
    r3 = client.get("/events/tournaments")
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
