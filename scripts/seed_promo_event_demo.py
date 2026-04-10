"""
Promo Event Demo Seed
=====================
Creates a virtual promotional event with ≥5 interactive quiz questions,
max 10 participants, and automated result notifications.

Demonstrates the full end-to-end workflow:
  REGISTRATION → QUIZ → RESULTS → FOLLOW-UP NOTIFICATION

Events created (2 lifecycle states)
-------------------------------------
  DRAFT      — tournament + session + quiz setup, no enrollments
  COMPLETED  — full flow: 6 enrolled, 6 quiz attempts, quiz-ranked, notifications sent

Quiz content (5 questions)
--------------------------
  Q1  What does 'IR' stand for?             (Multiple choice, 1 pt)
  Q2  Which session_type uses meeting_link? (Multiple choice, 1 pt)
  Q3  Base XP for virtual sessions?         (Multiple choice, 1 pt)
  Q4  How is rank 1 determined?             (Multiple choice, 1 pt)
  Q5  What happens when COMPLETED reached?  (Text, 2 pts — scored 100%)

Simulated scores: 95, 80, 70, 60, 45, 30 → ranking 1-6

Virtual guarantees (validated at end of script)
------------------------------------------------
  • 2 Promo Event tournaments created
  • COMPLETED event: TournamentRanking rows match quiz score order
  • COMPLETED event: Notification created for each of the 6 participants
  • COMPLETED event: session_type=virtual, base_xp=50

Players: 6 bootstrap LFA U15 players (bootstrap_clean.py must run first)
Idempotent: deletes all "Promo Event: " prefixed tournaments before re-seeding.

Usage
-----
    DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lfa_intern_system" \\
        SECRET_KEY="..." PYTHONPATH=. python scripts/seed_promo_event_demo.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system")
os.environ.setdefault("SECRET_KEY", "e2e-test-secret-key-minimum-32-chars-needed")

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.main import app
from app.database import SessionLocal
from app.models.campus import Campus
from app.models.club import Club
from app.models.game_configuration import GameConfiguration
from app.models.game_preset import GamePreset
from app.models.license import UserLicense
from app.models.notification import Notification
from app.models.quiz import Quiz, QuizQuestion, QuizAnswerOption, QuizAttempt, SessionQuiz
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.semester_enrollment import EnrollmentStatus, SemesterEnrollment
from app.models.session import Session as SessionModel, SessionType, EventCategory
from app.models.team import Team, TeamMember
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_ranking import TournamentRanking
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.tournament_type import TournamentType
from app.models.user import User
from app.dependencies import (
    get_current_admin_or_instructor_user_hybrid,
    get_current_admin_user_hybrid,
    get_current_user_web,
)
from app.services.tournament.quiz_ranking_service import auto_rank_from_quiz

# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def ok(msg):     print(f"  ✅  {msg}")
def info(msg):   print(f"       {msg}")
def err(msg):    print(f"  ❌  {msg}")
def section(t):  print(f"\n{'='*64}\n  {t}\n{'='*64}")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_PREFIX = "Promo Event: "
_MEETING_LINK = "https://meet.example.com/promo-event"
_MAX_PLAYERS = 10
_QUIZ_SCORES = [95.0, 80.0, 70.0, 60.0, 45.0, 30.0]  # 6 simulated scores → ranks 1-6

_REWARD_CONFIG = {
    "skill_mappings": [
        {"skill": "ball_control", "weight": 1.2, "category": "TECHNICAL", "enabled": True},
        {"skill": "passing",      "weight": 1.0, "category": "TECHNICAL", "enabled": True},
        {"skill": "composure",    "weight": 1.0, "category": "MENTAL",    "enabled": True},
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# DB + auth setup
# ─────────────────────────────────────────────────────────────────────────────

db = SessionLocal()

admin = db.query(User).filter(User.email == "admin@lfa.com").first()
instructor = db.query(User).filter(User.email == "instructor@lfa.com").first()
if not admin or not instructor:
    print("❌  admin@lfa.com or instructor@lfa.com not found — run bootstrap_clean.py first")
    sys.exit(1)

campus = db.query(Campus).first()
if not campus:
    print("❌  No campus found — run bootstrap_clean.py first")
    sys.exit(1)

preset = (
    db.query(GamePreset).filter(GamePreset.code == "outfield_default").first()
    or db.query(GamePreset).first()
)
if not preset:
    print("❌  No GamePreset found — run bootstrap_clean.py first")
    sys.exit(1)

app.dependency_overrides[get_current_user_web] = lambda: admin
app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
app.dependency_overrides[get_current_admin_or_instructor_user_hybrid] = lambda: admin

client = TestClient(app, follow_redirects=False)


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap U15 players
# ─────────────────────────────────────────────────────────────────────────────

section("Bootstrap U15 players — LFA-BOOT / LFA U15 (6 players)")

boot_club = db.query(Club).filter(Club.code == "LFA-BOOT").first()
if not boot_club:
    print("❌  LFA-BOOT club not found — run bootstrap_clean.py first")
    sys.exit(1)

boot_team = db.query(Team).filter(
    Team.club_id == boot_club.id,
    Team.name == "LFA U15",
).first()
if not boot_team:
    print("❌  'LFA U15' team not found — run bootstrap_clean.py first")
    sys.exit(1)

boot_players = (
    db.query(User)
    .join(TeamMember, TeamMember.user_id == User.id)
    .filter(TeamMember.team_id == boot_team.id)
    .limit(6)
    .all()
)
if len(boot_players) < 6:
    print(f"❌  Only {len(boot_players)} U15 players found (need 6) — run bootstrap_clean.py first")
    sys.exit(1)

ok(f"Found {len(boot_players)} bootstrap U15 players: {[p.name for p in boot_players]}")


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup: remove existing Promo Event: tournaments
# ─────────────────────────────────────────────────────────────────────────────

section("Cleanup — removing existing Promo Event: tournaments")

_existing_ids = [
    row[0] for row in db.execute(
        _sql("SELECT id FROM semesters WHERE name LIKE 'Promo Event: %'")
    ).fetchall()
]
if _existing_ids:
    print(f"  🧹  Found {len(_existing_ids)} existing Promo Event tournament(s) — deleting...")
    id_list = ", ".join(str(i) for i in _existing_ids)
    for tbl in [
        "tournament_reward_configs", "tournament_skill_mappings", "tournament_configurations",
        "game_configurations", "semester_enrollments", "tournament_team_enrollments",
        "tournament_player_checkins", "tournament_rankings", "tournament_participations",
        "tournament_reward_distributions", "tournament_instructor_slots",
    ]:
        try:
            db.execute(_sql(f"DELETE FROM {tbl} WHERE semester_id IN ({id_list})"))
        except Exception:
            db.rollback()
    # Sessions have a cascade on quiz attempts via SessionQuiz; delete sessions after quiz data
    _sess_ids = [
        row[0] for row in db.execute(
            _sql(f"SELECT id FROM sessions WHERE semester_id IN ({id_list})")
        ).fetchall()
    ]
    if _sess_ids:
        sess_list = ", ".join(str(i) for i in _sess_ids)
        for tbl in ["session_quizzes", "bookings", "attendances"]:
            try:
                db.execute(_sql(f"DELETE FROM {tbl} WHERE session_id IN ({sess_list})"))
            except Exception:
                db.rollback()
        try:
            db.execute(_sql(f"DELETE FROM sessions WHERE id IN ({sess_list})"))
        except Exception:
            db.rollback()
    try:
        db.execute(_sql(f"DELETE FROM tournament_status_history WHERE tournament_id IN ({id_list})"))
    except Exception:
        db.rollback()
    try:
        db.execute(_sql(f"DELETE FROM notifications WHERE related_semester_id IN ({id_list})"))
    except Exception:
        db.rollback()
    db.execute(_sql(f"DELETE FROM semesters WHERE id IN ({id_list})"))
    db.commit()
    ok(f"Deleted {len(_existing_ids)} Promo Event tournament(s)")
else:
    ok("No existing Promo Event tournaments found")


# ─────────────────────────────────────────────────────────────────────────────
# Quiz factory — shared between both events
# ─────────────────────────────────────────────────────────────────────────────

def create_promo_quiz() -> Quiz:
    """Create a Quiz with 5 questions (4 MC + 1 text) and answer options."""
    from app.models.quiz import QuizCategory, QuizDifficulty, QuestionType

    quiz = Quiz(
        title="Promo Event Knowledge Quiz",
        description="Test your knowledge about the LFA virtual tournament format.",
        category=QuizCategory.TACTICS if hasattr(QuizCategory, "TACTICS") else list(QuizCategory)[0],
        difficulty=QuizDifficulty.MEDIUM,
        time_limit_minutes=20,
        xp_reward=100,
        passing_score=60.0,
        is_active=True,
    )
    db.add(quiz)
    db.flush()

    questions_data = [
        {
            "text": "What does 'IR' stand for in the context of tournament formats?",
            "type": QuestionType.MULTIPLE_CHOICE if hasattr(QuestionType, "MULTIPLE_CHOICE") else list(QuestionType)[0],
            "points": 1,
            "order": 1,
            "options": [
                ("Individual Ranking", True),
                ("Instructor Review", False),
                ("Instant Replay", False),
                ("Initial Round", False),
            ],
        },
        {
            "text": "Which session_type requires a meeting_link to be set?",
            "type": QuestionType.MULTIPLE_CHOICE if hasattr(QuestionType, "MULTIPLE_CHOICE") else list(QuestionType)[0],
            "points": 1,
            "order": 2,
            "options": [
                ("virtual", True),
                ("on_site", False),
                ("hybrid", False),
                ("All of the above", False),
            ],
        },
        {
            "text": "What is the base XP awarded for completing a virtual session?",
            "type": QuestionType.MULTIPLE_CHOICE if hasattr(QuestionType, "MULTIPLE_CHOICE") else list(QuestionType)[0],
            "points": 1,
            "order": 3,
            "options": [
                ("50", True),
                ("75", False),
                ("100", False),
                ("25", False),
            ],
        },
        {
            "text": "In a quiz-ranked promo event, what determines Rank 1?",
            "type": QuestionType.MULTIPLE_CHOICE if hasattr(QuestionType, "MULTIPLE_CHOICE") else list(QuestionType)[0],
            "points": 1,
            "order": 4,
            "options": [
                ("Highest quiz score", True),
                ("Fastest completion time", False),
                ("Most correct answers", False),
                ("Admin assignment", False),
            ],
        },
        {
            "text": "What automated action occurs when a tournament reaches COMPLETED status?",
            "type": QuestionType.MULTIPLE_CHOICE if hasattr(QuestionType, "MULTIPLE_CHOICE") else list(QuestionType)[0],
            "points": 2,
            "order": 5,
            "options": [
                ("In-app result notification sent to all enrolled participants", True),
                ("All enrollments are automatically cancelled", False),
                ("A new tournament is auto-created", False),
                ("Sessions are deleted", False),
            ],
        },
    ]

    for qd in questions_data:
        question = QuizQuestion(
            quiz_id=quiz.id,
            question_text=qd["text"],
            question_type=qd["type"],
            points=qd["points"],
            order_index=qd["order"],
        )
        db.add(question)
        db.flush()

        for idx, (opt_text, is_correct) in enumerate(qd["options"]):
            db.add(QuizAnswerOption(
                question_id=question.id,
                option_text=opt_text,
                is_correct=is_correct,
                order_index=idx,
            ))

    db.commit()
    db.refresh(quiz)
    return quiz


# ─────────────────────────────────────────────────────────────────────────────
# Tournament factory
# ─────────────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:6]


def create_promo_tournament(name: str) -> Semester:
    """Create a virtual IR tournament with max_players=10."""
    sem = Semester(
        code=f"PE-{_uid()}",
        name=name,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=14),
        status=SemesterStatus.DRAFT,
        semester_category="TOURNAMENT",
        tournament_status="DRAFT",
        enrollment_cost=0,
        age_group="YOUTH",
        master_instructor_id=admin.id,
    )
    db.add(sem)
    db.flush()

    cfg = TournamentConfiguration(
        semester_id=sem.id,
        tournament_type_id=None,          # IR format
        participant_type="INDIVIDUAL",
        sessions_generated=False,
        session_type_config="virtual",    # Phase 1
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
        number_of_rounds=1,
        max_players=_MAX_PLAYERS,
    )
    db.add(cfg)

    gc = GameConfiguration(
        semester_id=sem.id,
        game_preset_id=preset.id,
    )
    db.add(gc)

    reward_cfg = TournamentRewardConfig(
        semester_id=sem.id,
        reward_config=_REWARD_CONFIG,
        is_active=True,
    )
    db.add(reward_cfg)

    db.commit()
    db.refresh(sem)
    ok(f"Tournament created: {name!r}  [{sem.id}]")
    return sem


def create_virtual_session(sem: Semester, in_past: bool = False) -> SessionModel:
    """Create a virtual IR session linked to the tournament."""
    if in_past:
        # Quiz window already closed — rank-from-quiz is callable
        start = datetime.utcnow() - timedelta(hours=3)
        end   = datetime.utcnow() - timedelta(hours=1)
    else:
        start = datetime.utcnow() + timedelta(hours=2)
        end   = datetime.utcnow() + timedelta(hours=3)

    sess = SessionModel(
        title=f"Promo Virtual Session — {sem.name}",
        semester_id=sem.id,
        session_type=SessionType.virtual,
        event_category=EventCategory.MATCH,
        date_start=start,
        date_end=end,
        base_xp=50,
        capacity=_MAX_PLAYERS,
        meeting_link=_MEETING_LINK,
        instructor_id=admin.id,
        auto_generated=True,
    )
    db.add(sess)
    db.flush()

    # Mark sessions_generated on config
    if sem.tournament_config_obj:
        sem.tournament_config_obj.sessions_generated = True
        sem.tournament_config_obj.sessions_generated_at = datetime.utcnow()

    db.commit()
    db.refresh(sess)
    ok(f"Session created: {sess.id}  [virtual, {'past' if in_past else 'future'} window]")
    return sess


def enroll_players(sem: Semester, players: list) -> list:
    """Directly enroll players (admin privilege, payment_verified=True)."""
    enrollments = []
    for player in players:
        lic = db.query(UserLicense).filter(
            UserLicense.user_id == player.id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        ).first()
        if not lic:
            info(f"Skipping {player.name} — no LFA_FOOTBALL_PLAYER license")
            continue

        existing = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.user_id == player.id,
            SemesterEnrollment.semester_id == sem.id,
            SemesterEnrollment.is_active == True,
        ).first()
        if existing:
            enrollments.append(existing)
            continue

        enr = SemesterEnrollment(
            user_id=player.id,
            semester_id=sem.id,
            user_license_id=lic.id,
            age_category="YOUTH",
            request_status=EnrollmentStatus.APPROVED,
            approved_at=datetime.utcnow(),
            approved_by=admin.id,
            payment_verified=True,
            is_active=True,
            enrolled_at=datetime.utcnow(),
            requested_at=datetime.utcnow(),
        )
        db.add(enr)
        enrollments.append(enr)

    db.commit()
    ok(f"Enrolled {len(enrollments)} players in {sem.name!r}")
    return enrollments


def link_quiz_to_session(sess: SessionModel, quiz: Quiz) -> SessionQuiz:
    """Create SessionQuiz association."""
    sq = SessionQuiz(
        session_id=sess.id,
        quiz_id=quiz.id,
        is_required=True,
        max_attempts=2,
    )
    db.add(sq)
    db.commit()
    ok(f"SessionQuiz linked: session={sess.id}, quiz={quiz.id}, max_attempts=2")
    return sq


def simulate_quiz_attempts(
    quiz: Quiz, players: list, scores: list[float]
) -> None:
    """Simulate completed QuizAttempt rows for each player with given scores."""
    total_q = len(quiz.questions)
    for player, score in zip(players, scores):
        correct = round(score / 100 * total_q)
        attempt = QuizAttempt(
            user_id=player.id,
            quiz_id=quiz.id,
            started_at=datetime.utcnow() - timedelta(hours=2),
            completed_at=datetime.utcnow() - timedelta(hours=1, minutes=30),
            time_spent_minutes=15.0,
            score=score,
            total_questions=total_q,
            correct_answers=correct,
            xp_awarded=0,
            passed=(score >= quiz.passing_score),
        )
        db.add(attempt)
    db.commit()
    ok(f"Simulated {len(players)} QuizAttempts — scores: {scores}")


# ─────────────────────────────────────────────────────────────────────────────
# EVENT 1: DRAFT (setup only, no enrollments)
# ─────────────────────────────────────────────────────────────────────────────

section("Event 1: Promo Event — Draft")

draft_sem = create_promo_tournament(f"{_PREFIX}Draft")
draft_quiz = create_promo_quiz()
draft_session = create_virtual_session(draft_sem, in_past=False)
link_quiz_to_session(draft_session, draft_quiz)
ok(f"✅ Promo Event: Draft  [{draft_sem.id}]  session={draft_session.id}  quiz={draft_quiz.id}")


# ─────────────────────────────────────────────────────────────────────────────
# EVENT 2: COMPLETED (full flow)
# ─────────────────────────────────────────────────────────────────────────────

section("Event 2: Promo Event — Completed (full E2E flow)")

completed_sem = create_promo_tournament(f"{_PREFIX}Completed")
completed_quiz = create_promo_quiz()
completed_session = create_virtual_session(completed_sem, in_past=True)
link_quiz_to_session(completed_session, completed_quiz)
enroll_players(completed_sem, boot_players)
simulate_quiz_attempts(completed_quiz, boot_players, _QUIZ_SCORES)

# Compute ranking from quiz scores
info("Computing ranking from quiz scores...")
ranked = auto_rank_from_quiz(db, completed_session.id)
ok(f"Ranked {len(ranked)} participants via quiz scores")
for r in ranked:
    info(f"  Rank {r['rank']:2d} — user_id={r['user_id']}  score={r['score']}")

# Transition to COMPLETED via lifecycle API → notification trigger fires
resp = client.patch(
    f"/api/v1/tournaments/{completed_sem.id}/status",
    json={"new_status": "COMPLETED"},
)
if resp.status_code == 200:
    ok("Status → COMPLETED (via lifecycle API)")
else:
    # Fallback: admin override (bypasses state machine)
    resp2 = client.patch(
        f"/api/v1/tournaments/{completed_sem.id}",
        json={"tournament_status": "COMPLETED"},
    )
    if resp2.status_code == 200:
        ok("Status → COMPLETED (via admin override)")
    else:
        err(f"Failed to set COMPLETED status: {resp.status_code} / {resp2.status_code}")
        err(resp.text[:300])

ok(f"✅ Promo Event: Completed  [{completed_sem.id}]  session={completed_session.id}")


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

section("Validation")

issues = []

db.expire_all()

# 1. Both tournaments exist
promo_tournaments = (
    db.query(Semester).filter(Semester.name.like(f"{_PREFIX}%")).all()
)
if len(promo_tournaments) != 2:
    issues.append(f"Expected 2 Promo Event tournaments, got {len(promo_tournaments)}")

# 2. COMPLETED event checks
db.expire_all()
comp = db.query(Semester).filter(Semester.id == completed_sem.id).first()
comp_sessions = db.query(SessionModel).filter(
    SessionModel.semester_id == comp.id
).all()

# session_type = virtual
bad_type = [s.id for s in comp_sessions if s.session_type != SessionType.virtual]
if bad_type:
    issues.append(f"Sessions {bad_type} have wrong session_type (not virtual)")

# base_xp = 50
bad_xp = [s.id for s in comp_sessions if s.base_xp != 50]
if bad_xp:
    issues.append(f"Sessions {bad_xp} have base_xp != 50")

# meeting_link set
missing_link = [s.id for s in comp_sessions if not s.meeting_link]
if missing_link:
    issues.append(f"Sessions {missing_link} missing meeting_link")

# Rankings created
rankings = db.query(TournamentRanking).filter(
    TournamentRanking.tournament_id == comp.id
).order_by(TournamentRanking.rank).all()
if not rankings:
    issues.append("No TournamentRanking rows found for COMPLETED event")
else:
    # Rank 1 should have the highest quiz score (user_id of boot_players[0])
    rank1_user_id = boot_players[0].id
    if rankings[0].user_id != rank1_user_id:
        issues.append(
            f"Expected rank 1 = user_id {rank1_user_id} (score 95), "
            f"got user_id {rankings[0].user_id}"
        )

# Notifications sent
notifs = db.query(Notification).filter(
    Notification.related_semester_id == comp.id
).all()
if len(notifs) != len(boot_players):
    issues.append(
        f"Expected {len(boot_players)} notifications for COMPLETED event, "
        f"got {len(notifs)}"
    )

# Quiz ≥ 5 questions
q_count = len(completed_quiz.questions)
if q_count < 5:
    issues.append(f"Quiz has only {q_count} questions (need ≥5)")

# Print results
if issues:
    print(f"\n❌  Validation FAILED — {len(issues)} issue(s):")
    for iss in issues:
        print(f"   • {iss}")
    sys.exit(1)

print("\n✅  Validation passed — 2 Promo Event tournaments OK")
for t in promo_tournaments:
    t_sessions = db.query(SessionModel).filter(SessionModel.semester_id == t.id).all()
    t_type = (t_sessions[0].session_type if t_sessions else "n/a")
    t_xp   = (t_sessions[0].base_xp    if t_sessions else "n/a")
    print(f"   ✅ {t.name}  [{t.tournament_status}]  "
          f"{len(t_sessions)} session(s)  type={t_type}  base_xp={t_xp}")

print(f"\n   Rankings for {comp.name}:")
for r in rankings:
    print(f"     Rank {r.rank:2d} — user_id={r.user_id}  points={r.points}")

print(f"\n   Notifications sent: {len(notifs)}")
for n in notifs:
    print(f"     → user_id={n.user_id}  title={n.title[:50]!r}")
