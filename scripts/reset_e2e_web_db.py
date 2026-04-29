#!/usr/bin/env python3
"""
Web E2E DB Reset — Sprint 55

Truncates E2E-specific tables and re-seeds baseline for Cypress web tests
(FastAPI Jinja2, localhost:8000).  Idempotent per scenario.

Usage:
    python scripts/reset_e2e_web_db.py --scenario baseline
    python scripts/reset_e2e_web_db.py --scenario student_no_dob
    python scripts/reset_e2e_web_db.py --scenario student_with_credits
    python scripts/reset_e2e_web_db.py --scenario session_ready
    python scripts/reset_e2e_web_db.py --scenario business_lifecycle
    python scripts/reset_e2e_web_db.py --scenario tournament_e2e
    python scripts/reset_e2e_web_db.py --scenario tournament_e2e_enrolled
    python scripts/reset_e2e_web_db.py --scenario semester_scheduling

Scenarios:
    baseline             admin + instructor + student (DOB set) + semester
    student_no_dob       baseline users but fresh student has no DOB
    student_with_credits baseline + student.credit_balance = 200
    session_ready        student_with_credits + 1 on_site + 1 hybrid session
    business_lifecycle   session_ready + 1 lifecycle session (started 90min ago) + student booking
    tournament_e2e           baseline + student LFA license (1000 cr) + ENROLLMENT_OPEN tournament
    tournament_e2e_enrolled  tournament_e2e + student already enrolled (900 cr, for instructor view tests)
    student_skill_history    baseline + student LFA license (29 skills) + 2 COMPLETED tournaments + TournamentParticipation (EMA timeline)
    student_1tournament      baseline + student LFA license (29 skills) + 1 COMPLETED tournament (single-entry EMA edge case)
    semester_scheduling      sched-admin@lfa.com + MINI_SEASON (SCHED-CYPRESS-01) + SemesterScheduleConfig (fresh state)
"""

import sys
import os
import argparse
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.semester_schedule_config import SemesterScheduleConfig
from app.models.location import Location, LocationType
from app.models.campus import Campus
from app.models.pitch import Pitch
from app.models.session import Session as SessionModel, SessionType
from app.models.booking import Booking, BookingStatus
from app.models.attendance import Attendance, AttendanceHistory
from app.models.quiz import (
    Quiz, QuizQuestion, QuizAnswerOption, QuizAttempt, QuizUserAnswer,
    QuizCategory, QuizDifficulty, QuestionType,
)
from app.models.credit_transaction import CreditTransaction
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.invitation_code import InvitationCode
from app.models.license import UserLicense
from app.models.tournament_achievement import TournamentParticipation
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.feedback import Feedback
from app.models.performance_review import StudentPerformanceReview, InstructorSessionReview
from app.models.notification import Notification
from app.models.project import ProjectEnrollmentQuiz
from app.core.security import get_password_hash

TZ = ZoneInfo("Europe/Budapest")

# ── Baseline credentials ───────────────────────────────────────────────────────

_BASELINE_USERS = [
    {
        "email":    "admin@lfa.com",
        "name":     "LFA Admin",
        "password": "admin123",
        "role":     UserRole.ADMIN,
        "dob":      date(1985, 6, 15),
    },
    {
        "email":    "grandmaster@lfa.com",
        "name":     "Grand Master",
        "password": "TestInstructor2026",
        "role":     UserRole.INSTRUCTOR,
        "dob":      date(1980, 3, 20),
    },
    {
        "email":    "rdias@manchestercity.com",
        "name":     "Ruben Dias",
        "password": "TestPlayer2026",
        "role":     UserRole.STUDENT,
        "dob":      date(1998, 5, 14),
    },
]

_FRESH_STUDENT = {
    "email":    "fresh.e2e@lfa.com",
    "name":     "Fresh E2E Student",
    "password": "FreshE2E2026",
    "role":     UserRole.STUDENT,
    "dob":      None,   # intentionally missing — age_verification flow
}

# Seeded with is_active=False in every scenario → used by AUTH-07
_INACTIVE_STUDENT = {
    "email":    "inactive.e2e@lfa.com",
    "name":     "Inactive E2E Student",
    "password": "InactiveE2E2026",
    "role":     UserRole.STUDENT,
    "dob":      date(2000, 1, 1),
}

_SEMESTER_CODE = "E2E-CI-2026"
_SEMESTER_NAME = "E2E CI Test Semester"

# Fixed invitation code for Cypress registration tests
_E2E_INV_CODE = "INV-E2E-TEST01"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _truncate_transactional_data(db) -> None:
    """Remove all transactional E2E data (bookings, attendance, quiz attempts, etc.)."""
    # Resolve E2E session IDs first so child-table deletes can be filtered
    _e2e_session_ids = [
        r[0] for r in db.query(SessionModel.id).filter(SessionModel.title.like("E2E%")).all()
    ]

    # Delete FK children of quiz_attempts before quiz_attempts
    db.query(ProjectEnrollmentQuiz).delete(synchronize_session=False)
    db.query(QuizUserAnswer).delete(synchronize_session=False)
    db.query(QuizAttempt).delete(synchronize_session=False)

    # Delete FK children of bookings before bookings
    if _e2e_session_ids:
        db.query(Notification).filter(
            Notification.related_session_id.in_(_e2e_session_ids)
        ).delete(synchronize_session=False)
    db.query(Notification).filter(
        Notification.related_booking_id.isnot(None)
    ).delete(synchronize_session=False)

    # Delete FK children of attendance before attendance (attendance_history)
    db.query(AttendanceHistory).delete(synchronize_session=False)
    db.query(Attendance).delete(synchronize_session=False)
    db.query(Booking).delete(synchronize_session=False)
    db.query(CreditTransaction).delete(synchronize_session=False)
    db.query(TournamentParticipation).delete(synchronize_session=False)
    db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id.in_(
            db.query(User.id).filter(User.email.in_(
                [u["email"] for u in _BASELINE_USERS]
                + [_FRESH_STUDENT["email"], _INACTIVE_STUDENT["email"]]
            ))
        )
    ).delete(synchronize_session=False)

    # Delete FK children of sessions before sessions
    if _e2e_session_ids:
        db.query(Feedback).filter(
            Feedback.session_id.in_(_e2e_session_ids)
        ).delete(synchronize_session=False)
        db.query(InstructorSessionReview).filter(
            InstructorSessionReview.session_id.in_(_e2e_session_ids)
        ).delete(synchronize_session=False)
        db.query(StudentPerformanceReview).filter(
            StudentPerformanceReview.session_id.in_(_e2e_session_ids)
        ).delete(synchronize_session=False)

    db.query(SessionModel).filter(
        SessionModel.title.like("E2E%")
    ).delete(synchronize_session=False)
    db.commit()


def _upsert_user(db, spec: dict, credit_balance: int = 0,
                 clear_dob: bool = False, is_active: bool = True,
                 onboarding_completed: bool = False) -> User:
    existing = db.query(User).filter(User.email == spec["email"]).first()
    dob = None if clear_dob else spec.get("dob")
    dob_dt = datetime(dob.year, dob.month, dob.day) if dob else None

    if existing:
        existing.password_hash = get_password_hash(spec["password"])
        existing.is_active = is_active
        existing.credit_balance = credit_balance
        existing.date_of_birth = dob_dt
        existing.onboarding_completed = onboarding_completed
        db.commit()
        return existing
    else:
        user = User(
            name=spec["name"],
            email=spec["email"],
            password_hash=get_password_hash(spec["password"]),
            role=spec["role"],
            is_active=is_active,
            credit_balance=credit_balance,
            date_of_birth=dob_dt,
            onboarding_completed=onboarding_completed,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _upsert_semester(db) -> Semester:
    today = date.today()
    sem = db.query(Semester).filter(Semester.code == _SEMESTER_CODE).first()
    if sem:
        sem.start_date = today - timedelta(days=180)
        sem.end_date   = today + timedelta(days=180)
        db.commit()
    else:
        sem = Semester(
            code=_SEMESTER_CODE,
            name=_SEMESTER_NAME,
            start_date=today - timedelta(days=180),
            end_date=today + timedelta(days=180),
        )
        db.add(sem)
        db.commit()
        db.refresh(sem)
    return sem


def _create_sessions(db, semester: Semester, instructor: User) -> list[SessionModel]:
    """Create 1 on_site + 1 hybrid session for instructor."""
    now = datetime.now(TZ).replace(tzinfo=None)
    sessions = []

    on_site = SessionModel(
        title="E2E On-Site Session",
        description="Auto-created for Cypress web E2E",
        date_start=now + timedelta(days=1),
        date_end=now + timedelta(days=1, hours=1),
        session_type=SessionType.on_site,
        capacity=20,
        location="E2E Test Field",
        semester_id=semester.id,
        instructor_id=instructor.id,
        session_status="scheduled",
        quiz_unlocked=False,
    )
    db.add(on_site)

    hybrid = SessionModel(
        title="E2E Hybrid Session",
        description="Auto-created for Cypress web E2E (hybrid with quiz)",
        date_start=now + timedelta(days=2),
        date_end=now + timedelta(days=2, hours=1),
        session_type=SessionType.hybrid,
        capacity=20,
        location="E2E Test Field",
        semester_id=semester.id,
        instructor_id=instructor.id,
        session_status="scheduled",
        quiz_unlocked=False,
    )
    db.add(hybrid)

    db.commit()
    db.refresh(on_site)
    db.refresh(hybrid)
    sessions = [on_site, hybrid]
    return sessions


# ── Scenario runners ──────────────────────────────────────────────────────────

def _upsert_e2e_invitation_code(db) -> InvitationCode:
    """Ensure the fixed E2E invitation code exists and is UNUSED."""
    code = db.query(InvitationCode).filter(InvitationCode.code == _E2E_INV_CODE).first()
    if code:
        code.is_used = False
        code.used_by_user_id = None
        code.used_at = None
        code.bonus_credits = 100
        db.commit()
    else:
        code = InvitationCode(
            code=_E2E_INV_CODE,
            invited_name="E2E Test Registrant",
            bonus_credits=100,
            is_used=False,
        )
        db.add(code)
        db.commit()
        db.refresh(code)
    return code


_E2E_QUIZ_TITLE = "E2E UI Quiz"


def _upsert_e2e_quiz(db) -> Quiz:
    """Ensure a quiz with 2 real questions exists for Cypress UI tests."""
    quiz = db.query(Quiz).filter(Quiz.title == _E2E_QUIZ_TITLE).first()
    if not quiz:
        quiz = Quiz(
            title=_E2E_QUIZ_TITLE,
            description="Cypress E2E UI test quiz — do not delete",
            category=QuizCategory.GENERAL,
            difficulty=QuizDifficulty.EASY,
            time_limit_minutes=10,
            xp_reward=10,
            passing_score=0.5,
            is_active=True,
        )
        db.add(quiz)
        db.flush()  # get quiz.id before adding children

        q1 = QuizQuestion(
            quiz_id=quiz.id,
            question_text="What colour is the sky on a clear day?",
            question_type=QuestionType.MULTIPLE_CHOICE,
            points=1,
            order_index=1,
        )
        db.add(q1)
        db.flush()
        db.add(QuizAnswerOption(question_id=q1.id, option_text="Blue",  is_correct=True,  order_index=1))
        db.add(QuizAnswerOption(question_id=q1.id, option_text="Green", is_correct=False, order_index=2))

        q2 = QuizQuestion(
            quiz_id=quiz.id,
            question_text="How many sides does a triangle have?",
            question_type=QuestionType.MULTIPLE_CHOICE,
            points=1,
            order_index=2,
        )
        db.add(q2)
        db.flush()
        db.add(QuizAnswerOption(question_id=q2.id, option_text="3", is_correct=True,  order_index=1))
        db.add(QuizAnswerOption(question_id=q2.id, option_text="4", is_correct=False, order_index=2))

        db.commit()
        db.refresh(quiz)
    return quiz


def scenario_baseline(db) -> list[str]:
    _truncate_transactional_data(db)
    lines = []
    for spec in _BASELINE_USERS:
        u = _upsert_user(db, spec, credit_balance=0)
        lines.append(f"  upserted user {spec['email']} ({spec['role'].value})")
    _upsert_user(db, _INACTIVE_STUDENT, credit_balance=0, is_active=False)
    lines.append(f"  upserted inactive user {_INACTIVE_STUDENT['email']}")
    _upsert_semester(db)
    lines.append(f"  upserted semester {_SEMESTER_CODE}")
    _upsert_e2e_invitation_code(db)
    lines.append(f"  upserted invitation code {_E2E_INV_CODE} (unused)")
    quiz = _upsert_e2e_quiz(db)
    lines.append(f"  upserted E2E UI quiz id={quiz.id} (2 questions)")
    return lines


def scenario_student_no_dob(db) -> list[str]:
    lines = scenario_baseline(db)
    u = _upsert_user(db, _FRESH_STUDENT, credit_balance=50, clear_dob=True)
    lines.append(f"  upserted fresh student {_FRESH_STUDENT['email']} (no DOB)")
    return lines


def scenario_student_with_credits(db) -> list[str]:
    _truncate_transactional_data(db)
    lines = []
    for spec in _BASELINE_USERS:
        credit = 200 if spec["role"] == UserRole.STUDENT else 0
        u = _upsert_user(db, spec, credit_balance=credit)
        lines.append(f"  upserted user {spec['email']} credit_balance={credit}")
    u = _upsert_user(db, _FRESH_STUDENT, credit_balance=200, clear_dob=True)
    lines.append(f"  upserted fresh student {_FRESH_STUDENT['email']} credit_balance=200")
    _upsert_semester(db)
    lines.append(f"  upserted semester {_SEMESTER_CODE}")
    return lines


def scenario_session_ready(db) -> list[str]:
    lines = scenario_student_with_credits(db)
    # Mark the E2E student as onboarding_completed so /sessions and /calendar are accessible
    student_spec = next(s for s in _BASELINE_USERS if s["role"] == UserRole.STUDENT)
    _upsert_user(db, student_spec, credit_balance=200, onboarding_completed=True)
    lines.append(f"  set onboarding_completed=True for {student_spec['email']}")
    semester = db.query(Semester).filter(Semester.code == _SEMESTER_CODE).first()
    instructor = db.query(User).filter(User.email == "grandmaster@lfa.com").first()
    sessions = _create_sessions(db, semester, instructor)
    for s in sessions:
        lines.append(f"  created session id={s.id} '{s.title}' ({s.session_type.value})")
    return lines


def _create_lifecycle_session(db, semester: Semester, instructor: User, student: User) -> SessionModel:
    """Create an on-site session that started 90 minutes ago with a student booking.

    date_start = now - 90min  → can_mark_attendance=True (past the 15-min window)
    actual_start_time = None   → 'Start Session' button visible for instructor
    Student has a CONFIRMED booking → enrolled_students list shows for attendance
    """
    now = datetime.now(TZ).replace(tzinfo=None)

    session = SessionModel(
        title="E2E Lifecycle Session",
        description="Business workflow lifecycle test — scheduled 90min ago, not started",
        date_start=now - timedelta(minutes=90),
        date_end=now + timedelta(minutes=30),
        session_type=SessionType.on_site,
        capacity=20,
        location="E2E Lifecycle Field",
        semester_id=semester.id,
        instructor_id=instructor.id,
        session_status="scheduled",
        quiz_unlocked=False,
    )
    db.add(session)
    db.flush()

    booking = Booking(
        user_id=student.id,
        session_id=session.id,
        status=BookingStatus.CONFIRMED,
    )
    db.add(booking)
    db.commit()
    db.refresh(session)
    return session


_TOURN_E2E_CODE = "TOURN-E2E-2026"


def _upsert_lfa_license(db, user: User) -> UserLicense:
    """Ensure a student has an active, onboarding-completed LFA_FOOTBALL_PLAYER license."""
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if lic:
        lic.is_active = True
        lic.onboarding_completed = True
        db.commit()
    else:
        lic = UserLicense(
            user_id=user.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(),
            payment_verified=True,
            payment_verified_at=datetime.now(),
            onboarding_completed=True,
            onboarding_completed_at=datetime.now(),
            is_active=True,
        )
        db.add(lic)
        db.commit()
        db.refresh(lic)
    return lic


def _upsert_tournament_e2e(db, instructor: User) -> Semester:
    """Create/update an ENROLLMENT_OPEN tournament for Cypress E2E tests."""
    today = date.today()
    tourn = db.query(Semester).filter(Semester.code == _TOURN_E2E_CODE).first()
    if tourn:
        tourn.name = "E2E Tournament 2026"
        tourn.tournament_status = "ENROLLMENT_OPEN"
        tourn.master_instructor_id = instructor.id
        tourn.start_date = today + timedelta(days=7)
        tourn.end_date = today + timedelta(days=14)
        tourn.enrollment_cost = 100
        tourn.specialization_type = "LFA_FOOTBALL_PLAYER"
        tourn.age_group = "AMATEUR"
        db.commit()
    else:
        tourn = Semester(
            code=_TOURN_E2E_CODE,
            name="E2E Tournament 2026",
            start_date=today + timedelta(days=7),
            end_date=today + timedelta(days=14),
            tournament_status="ENROLLMENT_OPEN",
            enrollment_cost=100,
            specialization_type="LFA_FOOTBALL_PLAYER",
            age_group="AMATEUR",
            master_instructor_id=instructor.id,
        )
        db.add(tourn)
        db.commit()
        db.refresh(tourn)
    return tourn


def scenario_tournament_e2e(db) -> list[str]:
    """Tournament lifecycle scenario: student can browse + enroll, instructor can manage.

    State:
        - Student rdias@manchestercity.com: 1000 credits, LFA license, onboarding done
        - Tournament TOURN-E2E-2026: ENROLLMENT_OPEN, cost=100, instructor=grandmaster
        - No enrollments yet (student enrolls during the Cypress test)
    """
    lines = scenario_baseline(db)

    student_spec = next(s for s in _BASELINE_USERS if s["role"] == UserRole.STUDENT)
    student = _upsert_user(db, student_spec, credit_balance=1000, onboarding_completed=True)
    lines.append(f"  set {student_spec['email']} credit_balance=1000 onboarding_completed=True")

    _upsert_lfa_license(db, student)
    lines.append(f"  upserted LFA_FOOTBALL_PLAYER license for {student_spec['email']}")

    instructor = db.query(User).filter(User.email == "grandmaster@lfa.com").first()
    tourn = _upsert_tournament_e2e(db, instructor)
    lines.append(
        f"  upserted tournament id={tourn.id} '{tourn.name}' "
        f"status={tourn.tournament_status} cost={tourn.enrollment_cost}"
    )
    return lines


def scenario_tournament_e2e_enrolled(db) -> list[str]:
    """Tournament instructor view scenario: student already enrolled in the tournament.

    State (extends tournament_e2e):
        - Student rdias@manchestercity.com: 900 credits (1000 - 100 entry fee), LFA license
        - Tournament TOURN-E2E-2026: ENROLLMENT_OPEN, 1 active enrollment (the student)
    Used by TOUR-I-02/TOUR-I-03 instructor tests that need a pre-enrolled participant.
    """
    lines = scenario_tournament_e2e(db)

    student_spec = next(s for s in _BASELINE_USERS if s["role"] == UserRole.STUDENT)
    student = db.query(User).filter(User.email == student_spec["email"]).first()
    instructor = db.query(User).filter(User.email == "grandmaster@lfa.com").first()
    tourn = db.query(Semester).filter(Semester.code == _TOURN_E2E_CODE).first()
    license_ = db.query(UserLicense).filter(
        UserLicense.user_id == student.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()

    # Remove any existing enrollment for this student + tournament (idempotent)
    db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == student.id,
        SemesterEnrollment.semester_id == tourn.id,
    ).delete(synchronize_session=False)
    db.commit()

    now = datetime.utcnow()
    cost = tourn.enrollment_cost or 100
    enrollment = SemesterEnrollment(
        user_id=student.id,
        semester_id=tourn.id,
        user_license_id=license_.id,
        age_category="AMATEUR",
        request_status=EnrollmentStatus.APPROVED,
        approved_at=now,
        approved_by=student.id,
        payment_verified=True,
        is_active=True,
        enrolled_at=now,
        requested_at=now,
    )
    db.add(enrollment)
    db.flush()

    # Deduct credits + add transaction (mirrors tournament_enroll route)
    student.credit_balance = 1000 - cost
    db.add(CreditTransaction(
        user_license_id=license_.id,
        transaction_type="TOURNAMENT_ENROLLMENT",
        amount=-cost,
        balance_after=student.credit_balance,
        description=f"Tournament enrollment: {tourn.name} ({tourn.code})",
        semester_id=tourn.id,
        enrollment_id=enrollment.id,
        idempotency_key=str(uuid.uuid4()),
    ))
    db.commit()
    lines.append(
        f"  pre-enrolled {student_spec['email']} in tournament (balance={student.credit_balance})"
    )
    return lines


def scenario_business_lifecycle(db) -> list[str]:
    lines = scenario_session_ready(db)
    semester = db.query(Semester).filter(Semester.code == _SEMESTER_CODE).first()
    instructor = db.query(User).filter(User.email == "grandmaster@lfa.com").first()
    student_spec = next(s for s in _BASELINE_USERS if s["role"] == UserRole.STUDENT)
    student = db.query(User).filter(User.email == student_spec["email"]).first()
    session = _create_lifecycle_session(db, semester, instructor, student)
    lines.append(
        f"  created lifecycle session id={session.id} '{session.title}' "
        f"date_start={session.date_start} (student id={student.id} booked)"
    )
    return lines


# ── student_skill_history scenario ────────────────────────────────────────────

_TOURN_HIST_CODE_1 = "TOURN-E2E-HIST-1"
_TOURN_HIST_CODE_2 = "TOURN-E2E-HIST-2"


def _upsert_hist_tournament(db, code: str, name: str, start_d: date, instructor: User) -> Semester:
    """Create or update a COMPLETED tournament with passing/ball_control/dribbling skill_mappings."""
    tourn = db.query(Semester).filter(Semester.code == code).first()
    if tourn:
        tourn.tournament_status = "COMPLETED"
        tourn.master_instructor_id = instructor.id
        db.flush()
    else:
        tourn = Semester(
            code=code,
            name=name,
            start_date=start_d,
            end_date=start_d + timedelta(days=7),
            tournament_status="COMPLETED",
            specialization_type="LFA_FOOTBALL_PLAYER",
            age_group="AMATEUR",
            master_instructor_id=instructor.id,
        )
        db.add(tourn)
        db.flush()

    _skill_mappings = [
        {"skill": "passing",     "enabled": True, "weight": 1.0},
        {"skill": "ball_control","enabled": True, "weight": 0.8},
        {"skill": "dribbling",   "enabled": True, "weight": 0.7},
    ]
    rc = db.query(TournamentRewardConfig).filter(
        TournamentRewardConfig.semester_id == tourn.id
    ).first()
    if rc:
        rc.reward_config = {"skill_mappings": _skill_mappings}
    else:
        rc = TournamentRewardConfig(
            semester_id=tourn.id,
            reward_policy_name="E2E Skill History",
            reward_config={"skill_mappings": _skill_mappings},
        )
        db.add(rc)
    db.flush()
    return tourn


def scenario_student_skill_history(db) -> list[str]:
    """Student skill history: completed tournaments with EMA timeline data.

    State:
        - Student rdias@manchestercity.com: 1000 credits, LFA license, onboarding done
        - LFA license football_skills: all 29 skills at 70.0 (flat format)
        - TOURN-E2E-HIST-1 (COMPLETED, 2 months ago): student 2nd/2, instructor 1st/2
        - TOURN-E2E-HIST-2 (COMPLETED, 1 month ago):  student 1st/2, instructor 2nd/2
        - Both tournaments test passing, ball_control, dribbling skills
        - Result: /skills/history shows 2-entry upward-trend EMA timeline for 'passing'
    """
    lines = scenario_baseline(db)

    student_spec = next(s for s in _BASELINE_USERS if s["role"] == UserRole.STUDENT)
    student = _upsert_user(db, student_spec, credit_balance=1000, onboarding_completed=True)
    lines.append(f"  set {student_spec['email']} credit_balance=1000 onboarding_completed=True")

    # LFA license with all 29 football_skills at 70.0 (flat format)
    from app.skills_config import get_all_skill_keys
    all_skills = {k: 70.0 for k in get_all_skill_keys()}
    lic = _upsert_lfa_license(db, student)
    lic.football_skills = all_skills
    db.commit()
    lines.append(f"  set football_skills on LFA license ({len(all_skills)} skills @ 70.0)")

    instructor = db.query(User).filter(User.email == "grandmaster@lfa.com").first()
    today = date.today()
    t1_date = today - timedelta(days=60)
    t2_date = today - timedelta(days=30)

    tourn1 = _upsert_hist_tournament(db, _TOURN_HIST_CODE_1, "E2E History Tournament 1", t1_date, instructor)
    tourn2 = _upsert_hist_tournament(db, _TOURN_HIST_CODE_2, "E2E History Tournament 2", t2_date, instructor)
    db.commit()
    lines.append(f"  upserted 2 COMPLETED tournaments (HIST-1 id={tourn1.id}, HIST-2 id={tourn2.id})")

    # Remove any stale participations for these tournaments (idempotent)
    for tourn in [tourn1, tourn2]:
        db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == tourn.id
        ).delete(synchronize_session=False)
    db.commit()

    # Tournament 1: student placed 2nd out of 2 → modest skill gain
    db.add(TournamentParticipation(
        user_id=student.id,
        semester_id=tourn1.id,
        placement=2,
        skill_points_awarded={"passing": 3.5, "ball_control": 2.8},
        xp_awarded=50,
        credits_awarded=0,
        achieved_at=datetime(t1_date.year, t1_date.month, t1_date.day, 12, 0, 0),
    ))
    db.add(TournamentParticipation(
        user_id=instructor.id,
        semester_id=tourn1.id,
        placement=1,
        skill_points_awarded={},
        xp_awarded=100,
        credits_awarded=0,
        achieved_at=datetime(t1_date.year, t1_date.month, t1_date.day, 12, 0, 0),
    ))

    # Tournament 2: student placed 1st out of 2 → stronger skill gain
    db.add(TournamentParticipation(
        user_id=student.id,
        semester_id=tourn2.id,
        placement=1,
        skill_points_awarded={"passing": 6.2, "ball_control": 5.0, "dribbling": 4.1},
        xp_awarded=100,
        credits_awarded=0,
        achieved_at=datetime(t2_date.year, t2_date.month, t2_date.day, 12, 0, 0),
    ))
    db.add(TournamentParticipation(
        user_id=instructor.id,
        semester_id=tourn2.id,
        placement=2,
        skill_points_awarded={},
        xp_awarded=50,
        credits_awarded=0,
        achieved_at=datetime(t2_date.year, t2_date.month, t2_date.day, 12, 0, 0),
    ))
    db.commit()
    lines.append("  created 4 TournamentParticipation records (student: 2nd→1st arc)")
    return lines


def scenario_student_1tournament(db) -> list[str]:
    """Single-tournament edge case: student has exactly 1 completed tournament.

    Used to verify EMA does not distort (NaN, infinity, out-of-range) when
    tournament_count=1. Student placed 2nd of 2 so skill dips below baseline —
    a meaningful (non-trivial) edge case.

    State:
        - Student rdias@manchestercity.com: 1000 credits, LFA license, 29 skills @ 70.0
        - TOURN-E2E-HIST-1 (COMPLETED): student 2nd/2, instructor 1st/2
        - Expected passing timeline: 1 entry, skill_value_after=64.0 (valid range [40–99])
    """
    lines = scenario_baseline(db)

    student_spec = next(s for s in _BASELINE_USERS if s["role"] == UserRole.STUDENT)
    student = _upsert_user(db, student_spec, credit_balance=1000, onboarding_completed=True)
    lines.append(f"  set {student_spec['email']} credit_balance=1000 onboarding_completed=True")

    from app.skills_config import get_all_skill_keys
    all_skills = {k: 70.0 for k in get_all_skill_keys()}
    lic = _upsert_lfa_license(db, student)
    lic.football_skills = all_skills
    db.commit()
    lines.append(f"  set football_skills on LFA license ({len(all_skills)} skills @ 70.0)")

    instructor = db.query(User).filter(User.email == "grandmaster@lfa.com").first()
    today = date.today()
    t1_date = today - timedelta(days=60)

    tourn1 = _upsert_hist_tournament(db, _TOURN_HIST_CODE_1, "E2E History Tournament 1", t1_date, instructor)
    db.commit()
    lines.append(f"  upserted 1 COMPLETED tournament (HIST-1 id={tourn1.id})")

    # Remove ALL participations for the student (idempotent — ensures exactly 1 tournament)
    db.query(TournamentParticipation).filter(
        TournamentParticipation.user_id == student.id
    ).delete(synchronize_session=False)
    # Also clean up T1 instructor participations for a clean count
    db.query(TournamentParticipation).filter(
        TournamentParticipation.semester_id == tourn1.id
    ).delete(synchronize_session=False)
    db.commit()

    db.add(TournamentParticipation(
        user_id=student.id,
        semester_id=tourn1.id,
        placement=2,
        skill_points_awarded={"passing": 3.5, "ball_control": 2.8},
        xp_awarded=50,
        credits_awarded=0,
        achieved_at=datetime(t1_date.year, t1_date.month, t1_date.day, 12, 0, 0),
    ))
    db.add(TournamentParticipation(
        user_id=instructor.id,
        semester_id=tourn1.id,
        placement=1,
        skill_points_awarded={},
        xp_awarded=100,
        credits_awarded=0,
        achieved_at=datetime(t1_date.year, t1_date.month, t1_date.day, 12, 0, 0),
    ))
    db.commit()
    lines.append("  created 2 TournamentParticipation records (student 2nd/2)")
    return lines


_SCHED_CODE = "SCHED-CYPRESS-01"
_SCHED_ADMIN_EMAIL = "sched-admin@lfa.com"


def scenario_semester_scheduling(db) -> list[str]:
    """
    Seed for Cypress SCHED-01..05 (admin semester scheduling UI).

    Creates:
      - sched-admin@lfa.com (ADMIN, SchedAdmin123!)
      - Location → Campus → Pitch (all deterministic, idempotent)
      - MINI_SEASON Semester (code=SCHED-CYPRESS-01, start=today+12w, end=today+24w)
      - SemesterScheduleConfig (Monday 17:00, 90 min, sessions_per_week=1)
      - Clears any auto_generated sessions for that semester (fresh state)
    """
    lines: list[str] = []

    # 1. Admin user
    admin_spec = {
        "email":    _SCHED_ADMIN_EMAIL,
        "name":     "Sched Admin",
        "password": "SchedAdmin123!",
        "role":     UserRole.ADMIN,
        "dob":      date(1985, 1, 1),
    }
    admin = _upsert_user(db, admin_spec, credit_balance=0)
    lines.append(f"  upserted admin: {admin.email} (id={admin.id})")

    # 2. Location (idempotent by name)
    loc = db.query(Location).filter(Location.name == "Sched Cypress Location").first()
    if not loc:
        loc = Location(
            name="Sched Cypress Location",
            city="Sched City",
            country="Hungary",
            location_type=LocationType.CENTER,
            is_active=True,
        )
        db.add(loc)
        db.flush()
    lines.append(f"  upserted location: id={loc.id}")

    # 3. Campus (idempotent by name + location)
    campus = db.query(Campus).filter(
        Campus.name == "Sched Campus",
        Campus.location_id == loc.id,
    ).first()
    if not campus:
        campus = Campus(
            name="Sched Campus",
            location_id=loc.id,
            is_active=True,
        )
        db.add(campus)
        db.flush()
    lines.append(f"  upserted campus: id={campus.id}")

    # 4. Pitch (idempotent by campus + pitch_number)
    pitch = db.query(Pitch).filter(
        Pitch.campus_id == campus.id,
        Pitch.pitch_number == 1,
    ).first()
    if not pitch:
        pitch = Pitch(
            campus_id=campus.id,
            pitch_number=1,
            name="Sched Pitch 1",
            capacity=20,
            is_active=True,
        )
        db.add(pitch)
        db.flush()
    lines.append(f"  upserted pitch: id={pitch.id}")

    # 5. MINI_SEASON Semester (idempotent by code)
    today = date.today()
    start_date = today + timedelta(weeks=12)
    end_date   = today + timedelta(weeks=24)

    semester = db.query(Semester).filter(Semester.code == _SCHED_CODE).first()
    if semester:
        semester.start_date       = start_date
        semester.end_date         = end_date
        semester.status           = SemesterStatus.ONGOING
        semester.semester_category = SemesterCategory.MINI_SEASON
        db.flush()
    else:
        semester = Semester(
            code=_SCHED_CODE,
            name="Mini Season Cypress",
            semester_category=SemesterCategory.MINI_SEASON,
            specialization_type="LFA_FOOTBALL_PLAYER",
            status=SemesterStatus.ONGOING,
            start_date=start_date,
            end_date=end_date,
            location_id=loc.id,
            campus_id=campus.id,
            enrollment_cost=2000,
            master_instructor_id=admin.id,
        )
        db.add(semester)
        db.flush()
        db.refresh(semester)
    lines.append(f"  upserted semester: {semester.code} (id={semester.id})")

    # 6. Clear auto-generated sessions (fresh state for generate/delete tests)
    deleted = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester.id,
            SessionModel.auto_generated == True,
        )
        .delete(synchronize_session=False)
    )
    if deleted:
        lines.append(f"  cleared {deleted} auto_generated sessions")

    # 7. Upsert SemesterScheduleConfig (reset sessions_generated=False)
    config = db.query(SemesterScheduleConfig).filter_by(semester_id=semester.id).first()
    if config:
        config.day_of_week        = 0          # Monday
        config.start_time         = __import__("datetime").time(17, 0)
        config.duration_minutes   = 90
        config.sessions_per_week  = 1
        config.campus_id          = campus.id
        config.pitch_id           = pitch.id
        config.sessions_generated = False
        config.sessions_generated_at = None
        config.sessions_count     = None
    else:
        from datetime import time as dt_time
        config = SemesterScheduleConfig(
            semester_id=semester.id,
            day_of_week=0,
            start_time=dt_time(17, 0),
            duration_minutes=90,
            sessions_per_week=1,
            campus_id=campus.id,
            pitch_id=pitch.id,
            sessions_generated=False,
        )
        db.add(config)
    db.commit()
    lines.append(f"  upserted SemesterScheduleConfig (sessions_generated=False)")
    lines.append(f"  semester_id={semester.id} ready for SCHED Cypress tests")
    return lines


# ── Entry point ───────────────────────────────────────────────────────────────

_SCENARIOS = {
    "baseline":                     scenario_baseline,
    "student_no_dob":               scenario_student_no_dob,
    "student_with_credits":         scenario_student_with_credits,
    "session_ready":                scenario_session_ready,
    "business_lifecycle":           scenario_business_lifecycle,
    "tournament_e2e":               scenario_tournament_e2e,
    "tournament_e2e_enrolled":      scenario_tournament_e2e_enrolled,
    "student_skill_history":        scenario_student_skill_history,
    "student_1tournament":          scenario_student_1tournament,
    "semester_scheduling":          scenario_semester_scheduling,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset E2E web test database")
    parser.add_argument("--scenario", choices=list(_SCENARIOS.keys()),
                        default="baseline", help="DB scenario to seed")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        print(f"reset_e2e_web_db — scenario: {args.scenario}")
        lines = _SCENARIOS[args.scenario](db)
        for line in lines:
            print(line)
        print(f"Done ({len(lines)} operations).")
    except Exception as exc:
        db.rollback()
        print(f"✗ reset_e2e_web_db failed: {exc}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
