"""
Virtual Quiz State Demo Seed
============================
4 virtual tournaments in 4 distinct quiz/lifecycle states.
All use the same GamePreset (outfield_default) + shared Quiz.

States
------
  VQS-DONE        → REWARDS_DISTRIBUTED
                    quiz completed (player1 PASS, player2 FAIL), rankings, skill deltas
  VQS-ACTIVE      → IN_PROGRESS
                    session LIVE now, quiz kitölthető, no attempts yet
  VQS-INTERRUPTED → IN_PROGRESS
                    QuizAttempt started (player1), NOT completed (completed_at=NULL)
  VQS-FAILED      → IN_PROGRESS
                    QuizAttempt completed (player1), score=25% < 75% passing

Quiz
----
  Title:          "Virtual Quiz State Demo — Football Tactics"
  4 kérdés × 1 pont = 4 pont total
  passing_score=0.75 (75%) → 3/4 helyes kell a PASS-hoz
  max_attempts=3 / session

GamePreset
----------
  outfield_default (minden tournament) → skill delta validálható

Credentials
-----------
  admin:     admin@lfa.com       / Admin123!
  player 1:  vq-player1@lfa.com  / VQPlayer1Abc!
  player 2:  vq-player2@lfa.com  / VQPlayer2Abc!

Usage
-----
  DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lfa_intern_system" \\
    SECRET_KEY="..." PYTHONPATH=. python scripts/seed_virtual_quiz_states.py

Prerequisites
-------------
  bootstrap_clean.py must run first (admin, campus, outfield_default preset)
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system")
os.environ.setdefault("SECRET_KEY", "e2e-test-secret-key-minimum-32-chars-needed")

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.main import app
from app.database import SessionLocal
from app.models.campus import Campus
from app.models.game_configuration import GameConfiguration
from app.models.game_preset import GamePreset
from app.models.license import UserLicense
from app.models.quiz import (
    Quiz, QuizQuestion, QuizAnswerOption, QuizAttempt, QuizUserAnswer,
    SessionQuiz, QuizCategory, QuizDifficulty, QuestionType,
)
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.semester_enrollment import EnrollmentStatus, SemesterEnrollment
from app.models.session import Session as SessionModel, SessionType
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_ranking import TournamentRanking
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.dependencies import (
    get_current_admin_or_instructor_user_hybrid,
    get_current_admin_user_hybrid,
    get_current_user_web,
)

# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def ok(msg):    print(f"  ✅  {msg}")
def info(msg):  print(f"       {msg}")
def err(msg):   print(f"  ❌  {msg}")
def warn(msg):  print(f"  ⚠️   {msg}")
def section(t): print(f"\n{'='*64}\n  {t}\n{'='*64}")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_PREFIX       = "Virtual Quiz State Demo: "
_QUIZ_TITLE   = "Virtual Quiz State Demo — Football Tactics"
_MEETING_LINK = "https://meet.example.com/virtual-quiz-states"

_STUDENTS = [
    {
        "email":    "vq-player1@lfa.com",
        "password": "VQPlayer1Abc!",
        "name":     "VQ Player One",
        "dob":      date(2005, 3, 15),
    },
    {
        "email":    "vq-player2@lfa.com",
        "password": "VQPlayer2Abc!",
        "name":     "VQ Player Two",
        "dob":      date(2006, 7, 22),
    },
]

_REWARD_CONFIG = {
    "skill_mappings": [
        {"skill": "ball_control",  "weight": 1.2, "category": "TECHNICAL", "enabled": True},
        {"skill": "passing",       "weight": 1.0, "category": "TECHNICAL", "enabled": True},
        {"skill": "finishing",     "weight": 1.2, "category": "TECHNICAL", "enabled": True},
        {"skill": "sprint_speed",  "weight": 1.1, "category": "PHYSICAL",  "enabled": True},
        {"skill": "stamina",       "weight": 1.0, "category": "PHYSICAL",  "enabled": True},
        {"skill": "composure",     "weight": 1.0, "category": "MENTAL",    "enabled": True},
    ],
}

# 4 kérdés: correct option is always index 0 (first option)
_QUESTIONS = [
    {
        "text": "Melyik session_type teszi elérhetővé a virtual quiz-t?",
        "options": [
            ("virtual",    True),
            ("on_site",    False),
            ("on_site_only", False),
        ],
    },
    {
        "text": "Mi a base_xp értéke virtual session-nél?",
        "options": [
            ("50",  True),
            ("75",  False),
            ("100", False),
        ],
    },
    {
        "text": "Melyik model köti össze a quiz-t és a session-t?",
        "options": [
            ("SessionQuiz",  True),
            ("QuizSession",  False),
            ("SessionModel", False),
        ],
    },
    {
        "text": "Melyik HTTP method-dal nyújthatók be a quiz válaszok a web route-on?",
        "options": [
            ("POST",  True),
            ("PATCH", False),
            ("PUT",   False),
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# DB + auth setup
# ─────────────────────────────────────────────────────────────────────────────

db = SessionLocal()

admin = db.query(User).filter(User.email == "admin@lfa.com").first()
if not admin:
    print("❌  admin@lfa.com not found — run bootstrap_clean.py first")
    sys.exit(1)

campus = db.query(Campus).first()
if not campus:
    print("❌  No campus found — run bootstrap_clean.py first")
    sys.exit(1)

preset = db.query(GamePreset).filter(GamePreset.code == "outfield_default").first()
if not preset:
    print("❌  outfield_default preset not found — run bootstrap_clean.py first")
    sys.exit(1)

# Auth override for API calls (distribute-rewards-v2)
app.dependency_overrides[get_current_user_web] = lambda: admin
app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
app.dependency_overrides[get_current_admin_or_instructor_user_hybrid] = lambda: admin
client = TestClient(app, follow_redirects=False)


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────────────────────

def _cleanup():
    section("Cleanup — removing existing Virtual Quiz State Demo data")

    # 1. Quiz cleanup (cascade order: user_answers → attempts → session_quiz → options → questions → quiz)
    old_quiz = db.query(Quiz).filter(Quiz.title == _QUIZ_TITLE).first()
    if old_quiz:
        attempt_ids = [
            a.id for a in db.query(QuizAttempt).filter(QuizAttempt.quiz_id == old_quiz.id).all()
        ]
        if attempt_ids:
            db.query(QuizUserAnswer).filter(
                QuizUserAnswer.attempt_id.in_(attempt_ids)
            ).delete(synchronize_session="fetch")
        db.query(QuizAttempt).filter(QuizAttempt.quiz_id == old_quiz.id).delete()
        db.query(SessionQuiz).filter(SessionQuiz.quiz_id == old_quiz.id).delete()
        for q in db.query(QuizQuestion).filter(QuizQuestion.quiz_id == old_quiz.id).all():
            db.query(QuizAnswerOption).filter(QuizAnswerOption.question_id == q.id).delete()
        db.query(QuizQuestion).filter(QuizQuestion.quiz_id == old_quiz.id).delete()
        db.delete(old_quiz)
        db.commit()
        ok("Previous quiz + attempts deleted")

    # 2. Tournament cleanup
    rows = db.execute(
        _sql("SELECT id FROM semesters WHERE name LIKE :p"),
        {"p": _PREFIX + "%"},
    ).fetchall()
    existing_ids = [r[0] for r in rows]

    if existing_ids:
        id_list = ", ".join(str(i) for i in existing_ids)
        for tbl in [
            "tournament_reward_configs", "tournament_skill_mappings",
            "tournament_configurations", "game_configurations",
            "semester_enrollments", "tournament_rankings",
            "tournament_participations", "tournament_reward_distributions",
            "tournament_instructor_slots", "sessions",
        ]:
            try:
                db.execute(_sql(f"DELETE FROM {tbl} WHERE semester_id IN ({id_list})"))
            except Exception:
                db.rollback()
        for extra_tbl_sql in [
            f"DELETE FROM tournament_status_history WHERE tournament_id IN ({id_list})",
            f"DELETE FROM notifications WHERE related_semester_id IN ({id_list})",
        ]:
            try:
                db.execute(_sql(extra_tbl_sql))
            except Exception:
                db.rollback()
        db.execute(_sql(f"DELETE FROM semesters WHERE id IN ({id_list})"))
        db.commit()
        ok(f"Deleted {len(existing_ids)} previous Virtual Quiz State Demo tournament(s)")
    else:
        ok("No existing Virtual Quiz State Demo tournaments found")


# ─────────────────────────────────────────────────────────────────────────────
# User helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_or_create_user(spec):
    u = db.query(User).filter(User.email == spec["email"]).first()
    if not u:
        u = User(
            name=spec["name"],
            email=spec["email"],
            password_hash=get_password_hash(spec["password"]),
            role=UserRole.STUDENT,
            is_active=True,
            date_of_birth=spec["dob"],
            credit_balance=1000,
        )
        db.add(u)
        db.flush()
        info(f"Created {spec['email']}")
    else:
        u.password_hash = get_password_hash(spec["password"])
        u.credit_balance = 1000
        u.is_active = True
        info(f"Updated {spec['email']}")
    return u


def _get_or_create_license(user):
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if not lic:
        lic = UserLicense(
            user_id=user.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            onboarding_completed=True,
            started_at=datetime.now(timezone.utc),
        )
        db.add(lic)
        db.flush()
    else:
        lic.onboarding_completed = True
    return lic


# ─────────────────────────────────────────────────────────────────────────────
# Quiz builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_shared_quiz():
    """Build Quiz + 4 Questions + 12 Options. Returns Quiz ORM object."""
    quiz = Quiz(
        title=_QUIZ_TITLE,
        description="Virtual tournament quiz flow validálása — 4 state × 1 quiz.",
        category=QuizCategory.LESSON,
        difficulty=QuizDifficulty.MEDIUM,
        passing_score=0.75,    # 75% → 3/4 helyes kell
        time_limit_minutes=20,
        xp_reward=50,
        is_active=True,
    )
    db.add(quiz)
    db.flush()

    for i, q_spec in enumerate(_QUESTIONS):
        q = QuizQuestion(
            quiz_id=quiz.id,
            question_text=q_spec["text"],
            question_type=QuestionType.MULTIPLE_CHOICE,
            points=1.0,
            order_index=i + 1,
        )
        db.add(q)
        db.flush()
        for j, (opt_text, is_correct) in enumerate(q_spec["options"]):
            db.add(QuizAnswerOption(
                question_id=q.id,
                option_text=opt_text,
                is_correct=is_correct,
                order_index=j + 1,
            ))
    db.commit()
    db.expire_all()
    ok(f"Quiz: '{_QUIZ_TITLE}'  id={quiz.id}  passing=75%  4 questions  xp=50")
    return quiz


# ─────────────────────────────────────────────────────────────────────────────
# Shared tournament/session/enrollment helpers
# ─────────────────────────────────────────────────────────────────────────────

def _create_tournament(name, tournament_status):
    """Create a virtual IR tournament directly (no lifecycle API calls)."""
    now = datetime.now(timezone.utc)
    t = Semester(
        name=name,
        code=f"VQS-{uuid.uuid4().hex[:6].upper()}",
        master_instructor_id=admin.id,
        campus_id=campus.id,
        location_id=campus.location_id,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status=tournament_status,
    )
    db.add(t)
    db.flush()
    db.add(TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=None,        # IR (score-based individual ranking)
        participant_type="INDIVIDUAL",
        max_players=64,
        number_of_rounds=1,
        parallel_fields=1,
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
        session_type_config="virtual",
        sessions_generated=True,
        sessions_generated_at=now,
        meeting_link=_MEETING_LINK,
    ))
    db.add(GameConfiguration(semester_id=t.id, game_preset_id=preset.id))
    db.commit()
    db.expire_all()
    return t


def _create_session(tid, date_start, date_end):
    """Create a single virtual session directly in DB."""
    sess = SessionModel(
        title="Virtual Quiz State Demo Session",
        semester_id=tid,
        session_type=SessionType.virtual,
        meeting_link=_MEETING_LINK,
        date_start=date_start,
        date_end=date_end,
        capacity=50,
        base_xp=50,
    )
    db.add(sess)
    db.commit()
    db.expire_all()
    return sess


def _enroll(tid, players, licenses):
    now = datetime.now(timezone.utc)
    for u, lic in zip(players, licenses):
        existing = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == tid,
            SemesterEnrollment.user_id == u.id,
        ).first()
        if not existing:
            db.add(SemesterEnrollment(
                semester_id=tid,
                user_id=u.id,
                user_license_id=lic.id,
                request_status=EnrollmentStatus.APPROVED,
                is_active=True,
                enrolled_at=now,
            ))
    db.commit()


def _add_session_quiz(sess_id, quiz_id):
    existing = db.query(SessionQuiz).filter(
        SessionQuiz.session_id == sess_id,
        SessionQuiz.quiz_id == quiz_id,
    ).first()
    if not existing:
        db.add(SessionQuiz(
            session_id=sess_id,
            quiz_id=quiz_id,
            is_required=True,
            max_attempts=3,
        ))
        db.commit()


def _get_question_options(quiz):
    """
    Returns list of (question_id, correct_option_id, wrong_option_id) per question,
    ordered by question.order_index.
    """
    db.expire_all()
    result = []
    questions = sorted(
        db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz.id).all(),
        key=lambda x: x.order_index,
    )
    for q in questions:
        opts = sorted(
            db.query(QuizAnswerOption).filter(QuizAnswerOption.question_id == q.id).all(),
            key=lambda x: x.order_index,
        )
        correct_id = next(o.id for o in opts if o.is_correct)
        wrong_id   = next(o.id for o in opts if not o.is_correct)
        result.append((q.id, correct_id, wrong_id))
    return result


def _write_attempt(
    quiz, user, score_pct, correct_count, total,
    is_passed, xp, started_at, completed_at, answer_option_ids,
):
    """
    Write QuizAttempt + QuizUserAnswers directly to DB.

    score_pct        : float, 0–100 scale (e.g. 100.0 / 50.0 / 25.0)
    answer_option_ids: list[int | None] — selected option id per question;
                       None = question not answered (INTERRUPTED state)
    """
    attempt = QuizAttempt(
        user_id=user.id,
        quiz_id=quiz.id,
        started_at=started_at,
        completed_at=completed_at,
        score=score_pct,
        total_questions=total,
        correct_answers=correct_count,
        xp_awarded=xp,
        passed=is_passed,
    )
    db.add(attempt)
    db.flush()

    q_opts = _get_question_options(quiz)
    for i, (q_id, correct_id, _wrong_id) in enumerate(q_opts):
        if i >= len(answer_option_ids):
            break                           # unanswered questions skipped
        selected_id = answer_option_ids[i]
        if selected_id is None:
            continue                        # explicitly unanswered
        db.add(QuizUserAnswer(
            attempt_id=attempt.id,
            question_id=q_id,
            selected_option_id=selected_id,
            is_correct=(selected_id == correct_id),
            answered_at=started_at + timedelta(minutes=i + 1),
        ))
    db.commit()
    db.expire_all()
    return attempt


# ─────────────────────────────────────────────────────────────────────────────
# VQS-DONE — REWARDS_DISTRIBUTED
# ─────────────────────────────────────────────────────────────────────────────

def seed_done(quiz, players, licenses):
    section("VQS-DONE — REWARDS_DISTRIBUTED")
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    past_start = yesterday.replace(hour=14, minute=0, second=0, microsecond=0)
    past_end   = past_start + timedelta(hours=2)

    t = _create_tournament(f"{_PREFIX}Completed", "COMPLETED")
    db.add(TournamentRewardConfig(
        semester_id=t.id,
        reward_policy_name="Virtual Quiz State Demo Default",
        reward_config=_REWARD_CONFIG,
    ))
    db.commit()

    sess = _create_session(t.id, past_start, past_end)
    _enroll(t.id, players, licenses)
    _add_session_quiz(sess.id, quiz.id)
    ok(f"Tournament id={t.id}  session id={sess.id}  (dates: yesterday 14:00–16:00)")

    # Direct TournamentRanking: player1=rank1 (90pts), player2=rank2 (60pts)
    db.add(TournamentRanking(
        tournament_id=t.id,
        user_id=players[0].id,
        participant_type="INDIVIDUAL",
        rank=1,
        points=90.0,
        wins=0, losses=0, draws=0,
    ))
    db.add(TournamentRanking(
        tournament_id=t.id,
        user_id=players[1].id,
        participant_type="INDIVIDUAL",
        rank=2,
        points=60.0,
        wins=0, losses=0, draws=0,
    ))
    db.commit()
    ok("Rankings: player1 rank=1 (90pts) / player2 rank=2 (60pts)")

    # Distribute rewards via API → REWARDS_DISTRIBUTED + skill deltas
    r = client.post(
        f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
        json={"tournament_id": t.id, "force_redistribution": False},
    )
    if r.status_code == 200:
        db.expire_all()
        final = db.query(Semester).filter(Semester.id == t.id).first()
        ok(f"Rewards distributed → status={final.tournament_status}")
    else:
        warn(f"Rewards API returned {r.status_code}: {r.text[:200]}")
        warn("Manually setting REWARDS_DISTRIBUTED")
        db.execute(
            _sql("UPDATE semesters SET tournament_status='REWARDS_DISTRIBUTED' WHERE id=:id"),
            {"id": t.id},
        )
        db.commit()

    # QuizAttempts (direct DB write — post-tournament state)
    q_opts = _get_question_options(quiz)
    correct_ids = [c for _, c, _ in q_opts]
    wrong_ids   = [w for _, _, w in q_opts]

    # player1: 4/4 correct → score=100.0 → PASS
    _write_attempt(
        quiz, players[0],
        score_pct=100.0, correct_count=4, total=4,
        is_passed=True, xp=50,
        started_at=past_end - timedelta(minutes=60),
        completed_at=past_end - timedelta(minutes=55),
        answer_option_ids=correct_ids,                  # all correct
    )
    ok("QuizAttempt player1: 4/4 PASSED ✅  xp=50")

    # player2: Q1+Q2 correct, Q3+Q4 wrong → score=50.0 → FAIL (50 < 75)
    _write_attempt(
        quiz, players[1],
        score_pct=50.0, correct_count=2, total=4,
        is_passed=False, xp=0,
        started_at=past_end - timedelta(minutes=50),
        completed_at=past_end - timedelta(minutes=43),
        answer_option_ids=[
            correct_ids[0], correct_ids[1],   # Q1, Q2 helyes
            wrong_ids[2],   wrong_ids[3],     # Q3, Q4 helytelen
        ],
    )
    ok("QuizAttempt player2: 2/4 FAILED ❌  xp=0")

    return t.id, sess.id


# ─────────────────────────────────────────────────────────────────────────────
# VQS-ACTIVE — IN_PROGRESS, session LIVE, no attempts
# ─────────────────────────────────────────────────────────────────────────────

def seed_active(quiz, players, licenses):
    section("VQS-ACTIVE — IN_PROGRESS (session LIVE, no attempts)")
    now = datetime.now(timezone.utc)
    live_start = now - timedelta(minutes=30)
    live_end   = now + timedelta(hours=2)

    t = _create_tournament(f"{_PREFIX}Active", "IN_PROGRESS")
    sess = _create_session(t.id, live_start, live_end)
    _enroll(t.id, players, licenses)
    _add_session_quiz(sess.id, quiz.id)

    ok(f"Tournament id={t.id}  session id={sess.id}  LIVE: now−30min → now+2h")
    ok("No QuizAttempts — quiz kitölthető bármelyik player által")
    return t.id, sess.id


# ─────────────────────────────────────────────────────────────────────────────
# VQS-INTERRUPTED — IN_PROGRESS, attempt started but NOT completed
# ─────────────────────────────────────────────────────────────────────────────

def seed_interrupted(quiz, players, licenses):
    section("VQS-INTERRUPTED — IN_PROGRESS (QuizAttempt started, NOT completed)")
    now = datetime.now(timezone.utc)
    live_start = now - timedelta(minutes=30)
    live_end   = now + timedelta(hours=2)

    t = _create_tournament(f"{_PREFIX}Interrupted", "IN_PROGRESS")
    sess = _create_session(t.id, live_start, live_end)
    _enroll(t.id, players, licenses)
    _add_session_quiz(sess.id, quiz.id)

    # player1: started 10min ago, answered Q1+Q2 only, NOT completed
    q_opts = _get_question_options(quiz)
    correct_ids = [c for _, c, _ in q_opts]

    _write_attempt(
        quiz, players[0],
        score_pct=None,   # not yet scored
        correct_count=0,  # not yet counted
        total=4,
        is_passed=False,  # default
        xp=0,
        started_at=now - timedelta(minutes=10),
        completed_at=None,              # ← KEY: NOT completed
        answer_option_ids=[
            correct_ids[0],             # Q1 helyes
            correct_ids[1],             # Q2 helyes
        ],                              # Q3, Q4: unanswered (no QuizUserAnswer rows)
    )
    ok("QuizAttempt player1: started 10min ago, completed_at=NULL (Q1+Q2 answered, Q3+Q4 missing)")

    ok(f"Tournament id={t.id}  session id={sess.id}")
    return t.id, sess.id


# ─────────────────────────────────────────────────────────────────────────────
# VQS-FAILED — IN_PROGRESS, attempt completed, score < 75%
# ─────────────────────────────────────────────────────────────────────────────

def seed_failed(quiz, players, licenses):
    section("VQS-FAILED — IN_PROGRESS (QuizAttempt completed, 25% FAILED)")
    now = datetime.now(timezone.utc)
    live_start = now - timedelta(minutes=30)
    live_end   = now + timedelta(hours=2)

    t = _create_tournament(f"{_PREFIX}Failed", "IN_PROGRESS")
    sess = _create_session(t.id, live_start, live_end)
    _enroll(t.id, players, licenses)
    _add_session_quiz(sess.id, quiz.id)

    # player1: Q1 wrong, Q2 correct, Q3 wrong, Q4 wrong → 1/4 = 25% FAIL
    q_opts = _get_question_options(quiz)
    correct_ids = [c for _, c, _ in q_opts]
    wrong_ids   = [w for _, _, w in q_opts]

    completed_at = now - timedelta(minutes=5)
    _write_attempt(
        quiz, players[0],
        score_pct=25.0, correct_count=1, total=4,
        is_passed=False, xp=0,
        started_at=completed_at - timedelta(minutes=8),
        completed_at=completed_at,
        answer_option_ids=[
            wrong_ids[0],               # Q1 helytelen
            correct_ids[1],             # Q2 helyes
            wrong_ids[2],               # Q3 helytelen
            wrong_ids[3],               # Q4 helytelen
        ],
    )
    ok("QuizAttempt player1: 1/4 FAILED ❌ (25% < 75%)  — 2 attempt remaining (max=3)")

    ok(f"Tournament id={t.id}  session id={sess.id}")
    return t.id, sess.id


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*64}")
    print("  Virtual Quiz State Demo — Seed")
    print(f"{'='*64}")

    _cleanup()

    # ── Students ──────────────────────────────────────────────────────────────
    section("Dedicated Students")
    players  = [_get_or_create_user(s) for s in _STUDENTS]
    licenses = [_get_or_create_license(u) for u in players]
    db.commit()
    ok(f"Players: {players[0].email} (id={players[0].id}) / {players[1].email} (id={players[1].id})")

    # ── Shared quiz ───────────────────────────────────────────────────────────
    section("Shared Quiz")
    quiz = _build_shared_quiz()

    # ── 4 tournaments ─────────────────────────────────────────────────────────
    done_tid, done_sid     = seed_done(quiz, players, licenses)
    active_tid, active_sid = seed_active(quiz, players, licenses)
    int_tid, int_sid       = seed_interrupted(quiz, players, licenses)
    fail_tid, fail_sid     = seed_failed(quiz, players, licenses)

    # ── Summary ───────────────────────────────────────────────────────────────
    db.expire_all()
    done_status = (db.query(Semester).filter(Semester.id == done_tid).first() or type("x", (), {"tournament_status": "?"})()).tournament_status

    base = "http://localhost:8000"
    print(f"\n{'='*64}")
    print("  Virtual Quiz State Demo — Summary")
    print(f"{'='*64}\n")

    print(f"  Quiz:  \"{_QUIZ_TITLE}\"")
    print(f"         id={quiz.id}  passing=75%  4 kérdés  xp=50  max_attempts=3/session\n")

    print("  Credentials:")
    print(f"    admin:     admin@lfa.com           / Admin123!")
    print(f"    player 1:  {players[0].email:<28} / VQPlayer1Abc!")
    print(f"    player 2:  {players[1].email:<28} / VQPlayer2Abc!\n")

    print(f"  ┌─ [VQS-DONE] Completed ─────────────────────────────────────────")
    print(f"  │  tournament_id={done_tid}  status={done_status}")
    print(f"  │  session_id={done_sid}  dates: yesterday 14:00→16:00 (PAST)")
    print(f"  │  quiz_id={quiz.id}  SessionQuiz linked ✅")
    print(f"  │  rankings: player1 rank=1 (90pts) / player2 rank=2 (60pts)")
    print(f"  │  QuizAttempt player1: 4/4 PASSED ✅  xp=50")
    print(f"  │  QuizAttempt player2: 2/4 FAILED ❌  xp=0")
    print(f"  │  URL:  {base}/events/{done_tid}")
    print()

    print(f"  ┌─ [VQS-ACTIVE] Active ──────────────────────────────────────────")
    print(f"  │  tournament_id={active_tid}  status=IN_PROGRESS")
    print(f"  │  session_id={active_sid}  LIVE: now−30min → now+2h")
    print(f"  │  quiz_id={quiz.id}  SessionQuiz linked ✅")
    print(f"  │  QuizAttempts: NONE — quiz kitölthető most!")
    print(f"  │  URL:  {base}/events/{active_tid}")
    print(f"  │  Quiz: {base}/quizzes/{quiz.id}/take?session_id={active_sid}")
    print()

    print(f"  ┌─ [VQS-INTERRUPTED] Interrupted ────────────────────────────────")
    print(f"  │  tournament_id={int_tid}  status=IN_PROGRESS")
    print(f"  │  session_id={int_sid}  LIVE")
    print(f"  │  QuizAttempt player1: started 10min ago, NOT COMPLETED")
    print(f"  │    Q1+Q2 megválaszolva, Q3+Q4 hiányzik, completed_at=NULL")
    print(f"  │  URL:  {base}/events/{int_tid}")
    print(f"  │  Quiz: {base}/quizzes/{quiz.id}/take?session_id={int_sid}")
    print()

    print(f"  ┌─ [VQS-FAILED] Failed ──────────────────────────────────────────")
    print(f"  │  tournament_id={fail_tid}  status=IN_PROGRESS")
    print(f"  │  session_id={fail_sid}  LIVE")
    print(f"  │  QuizAttempt player1: 1/4 FAILED ❌ (25% < 75%)  2 attempt maradt")
    print(f"  │  URL:  {base}/events/{fail_tid}")
    print(f"  │  Quiz: {base}/quizzes/{quiz.id}/take?session_id={fail_sid}")
    print()

    print(f"✅  Virtual Quiz State Demo seed complete!\n")
    db.close()


if __name__ == "__main__":
    main()
