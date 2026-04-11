"""
Comprehensive Demo Seed
=======================
Creates one tournament for every format × lifecycle-state combination to
demonstrate the full ranking_type / standings_state logic on a clean database.

5 tournament types × 8 lifecycle states = 40 events

Formats
-------
  A  H2H League     (TEAM,       WDL_BASED,    4 demo teams)
  B  Knockout       (TEAM,       WDL_BASED,    4 demo teams)
  C  Group Knockout (INDIVIDUAL, WDL_BASED,   16 demo players)
  D  Swiss          (INDIVIDUAL, SCORING_ONLY, 16 demo players)
  E  IR (Ind. Rank) (INDIVIDUAL, SCORING_ONLY, 16 demo players)

Lifecycle states per format
---------------------------
  DRAFT  ENROLLMENT_OPEN  ENROLLMENT_CLOSED  CHECK_IN_OPEN
  IN_PROGRESS  COMPLETED  REWARDS_DISTRIBUTED  CANCELLED

Club created: LFA Demo Club (LFA-DEMO)
Teams:        Demo U12  Demo U15  Demo U18  Demo Adult  (4 players each)

Idempotent: deletes all existing "Demo:" named tournaments before re-seeding.

Prerequisites
-------------
    PYTHONPATH=. python scripts/bootstrap_clean.py   # run once first

Usage
-----
    DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lfa_intern_system" \\
        SECRET_KEY="..." PYTHONPATH=. python scripts/seed_comprehensive_demo.py
"""
import os
import sys
import uuid
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system")
os.environ.setdefault("SECRET_KEY", "e2e-test-secret-key-minimum-32-chars-needed")

from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.main import app
from app.database import SessionLocal
from app.core.security import get_password_hash
from app.models.campus import Campus
from app.models.club import Club
from app.models.game_configuration import GameConfiguration
from app.models.game_preset import GamePreset
from app.models.license import UserLicense
from app.models.quiz import (
    Quiz, QuizQuestion, QuizAnswerOption, QuizAttempt, QuizUserAnswer,
    SessionQuiz, QuizCategory, QuizDifficulty, QuestionType,
)
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.semester_enrollment import EnrollmentStatus, SemesterEnrollment
from app.models.session import Session as SessionModel
from app.models.team import Team, TeamMember, TournamentTeamEnrollment
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_ranking import TournamentRanking
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.tournament_type import TournamentType
from app.models.user import User, UserRole
from app.skills_config import get_all_skill_keys
from app.dependencies import (
    get_current_admin_or_instructor_user_hybrid,
    get_current_admin_user_hybrid,
    get_current_user_web,
)

# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def ok(msg):   print(f"  ✅  {msg}")
def info(msg): print(f"       {msg}")
def err(msg):  print(f"  ❌  {msg}")
def section(title): print(f"\n{'='*64}\n  {title}\n{'='*64}")


# ─────────────────────────────────────────────────────────────────────────────
# Demo club definition
# ─────────────────────────────────────────────────────────────────────────────

_DEMO_CLUB_CODE = "LFA-DEMO"
_DEMO_CLUB_NAME = "LFA Demo Club"

_DEMO_TEAMS = [
    {"name": "Demo U12", "age_group_label": "U12", "skill_base": 55.0,
     "dob": date(2013, 6, 1)},
    {"name": "Demo U15", "age_group_label": "U15", "skill_base": 62.0,
     "dob": date(2010, 6, 1)},
    {"name": "Demo U18", "age_group_label": "U18", "skill_base": 68.0,
     "dob": date(2007, 6, 1)},
    {"name": "Demo Adult", "age_group_label": "ADULT", "skill_base": 73.0,
     "dob": date(1995, 6, 1)},
]

# 4 extra minimal teams for Format H (group_knockout × TEAM needs 8 teams)
_GK_EXTRA_TEAMS = [
    {"name": "Demo GK-1", "age_group_label": "U12"},
    {"name": "Demo GK-2", "age_group_label": "U15"},
    {"name": "Demo GK-3", "age_group_label": "U18"},
    {"name": "Demo GK-4", "age_group_label": "ADULT"},
]

# 4 players per team — unique names per age group
_DEMO_PLAYERS: dict[str, list[tuple[str, str]]] = {
    "Demo U12":    [("Aaron", "Adams"), ("Billy", "Baker"), ("Charlie", "Cole"), ("David", "Dean")],
    "Demo U15":    [("Eddie", "Evans"), ("Frank", "Ford"), ("George", "Grant"), ("Henry", "Hall")],
    "Demo U18":    [("Isaac", "Irving"), ("Jack", "Jones"), ("Kevin", "King"), ("Leo", "Lee")],
    "Demo Adult":  [("Mike", "Marsh"), ("Neil", "Nash"), ("Oscar", "Owen"), ("Peter", "Price")],
}

# ─────────────────────────────────────────────────────────────────────────────
# Reward config shared across all demo tournaments
# ─────────────────────────────────────────────────────────────────────────────

_REWARD_CONFIG = {
    "skill_mappings": [
        {"skill": "ball_control",  "weight": 1.2, "category": "TECHNICAL", "enabled": True},
        {"skill": "passing",       "weight": 1.0, "category": "TECHNICAL", "enabled": True},
        {"skill": "finishing",     "weight": 1.2, "category": "TECHNICAL", "enabled": True},
        {"skill": "dribbling",     "weight": 1.0, "category": "TECHNICAL", "enabled": True},
        {"skill": "sprint_speed",  "weight": 1.1, "category": "PHYSICAL",  "enabled": True},
        {"skill": "stamina",       "weight": 1.0, "category": "PHYSICAL",  "enabled": True},
        {"skill": "composure",     "weight": 1.0, "category": "MENTAL",    "enabled": True},
        {"skill": "reactions",     "weight": 1.1, "category": "MENTAL",    "enabled": True},
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

preset = db.query(GamePreset).filter(GamePreset.code == "outfield_default").first() or \
         db.query(GamePreset).first()
if not preset:
    print("❌  No GamePreset found — run bootstrap_clean.py first")
    sys.exit(1)

# TournamentType lookups
tt_league   = db.query(TournamentType).filter(TournamentType.code == "league").first()
tt_knockout = db.query(TournamentType).filter(TournamentType.code == "knockout").first()
tt_gk       = db.query(TournamentType).filter(TournamentType.code == "group_knockout").first()
tt_swiss    = db.query(TournamentType).filter(TournamentType.code == "swiss").first()
if not all([tt_league, tt_knockout, tt_gk, tt_swiss]):
    print("❌  TournamentType rows missing — run bootstrap_clean.py first")
    sys.exit(1)

app.dependency_overrides[get_current_user_web] = lambda: admin
app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
app.dependency_overrides[get_current_admin_or_instructor_user_hybrid] = lambda: admin

client = TestClient(app, follow_redirects=False)


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup: delete all existing "Demo:" prefixed tournaments
# ─────────────────────────────────────────────────────────────────────────────

section("Cleanup — removing existing Demo: tournaments")

_existing_ids = [
    row[0] for row in db.execute(
        _sql("SELECT id FROM semesters WHERE name LIKE 'Demo: %' OR name LIKE 'MC Demo: %'")
    ).fetchall()
]
if _existing_ids:
    print(f"  🧹  Found {len(_existing_ids)} existing Demo tournament(s) — deleting...")
    id_list = ", ".join(str(i) for i in _existing_ids)
    for tbl in [
        "tournament_reward_configs", "tournament_skill_mappings", "tournament_configurations",
        "game_configurations", "semester_enrollments", "tournament_team_enrollments",
        "tournament_player_checkins", "tournament_rankings", "tournament_participations",
        "tournament_reward_distributions", "tournament_instructor_slots", "sessions",
    ]:
        try:
            db.execute(_sql(f"DELETE FROM {tbl} WHERE semester_id IN ({id_list})"))
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
    ok(f"Deleted {len(_existing_ids)} tournament(s)")
else:
    ok("No existing Demo tournaments found")

# Cleanup orphaned Format L quizzes (quiz rows are not tied to a semester)
_vq_quiz_ids = [
    row[0] for row in db.execute(
        _sql("SELECT id FROM quizzes WHERE title LIKE 'Virtual Quiz L — %'")
    ).fetchall()
]
if _vq_quiz_ids:
    qid_list = ", ".join(str(i) for i in _vq_quiz_ids)
    db.execute(_sql(
        f"DELETE FROM quiz_user_answers WHERE attempt_id IN "
        f"(SELECT id FROM quiz_attempts WHERE quiz_id IN ({qid_list}))"
    ))
    db.execute(_sql(f"DELETE FROM quiz_attempts WHERE quiz_id IN ({qid_list})"))
    db.execute(_sql(
        f"DELETE FROM quiz_answer_options WHERE question_id IN "
        f"(SELECT id FROM quiz_questions WHERE quiz_id IN ({qid_list}))"
    ))
    db.execute(_sql(f"DELETE FROM quiz_questions WHERE quiz_id IN ({qid_list})"))
    db.execute(_sql(f"DELETE FROM quizzes WHERE id IN ({qid_list})"))
    db.commit()
    ok(f"Cleaned up {len(_vq_quiz_ids)} orphaned Format L quiz/quizzes")


# ─────────────────────────────────────────────────────────────────────────────
# Create Demo Club + 4 teams × 4 players
# ─────────────────────────────────────────────────────────────────────────────

section("Demo Club — LFA Demo Club (4 teams × 4 players)")

all_skill_keys = get_all_skill_keys()
now = datetime.now()

demo_club = db.query(Club).filter(Club.code == _DEMO_CLUB_CODE).first()
if demo_club:
    ok(f"Club '{_DEMO_CLUB_NAME}' already exists (id={demo_club.id})")
else:
    demo_club = Club(
        name=_DEMO_CLUB_NAME,
        code=_DEMO_CLUB_CODE,
        city="Budapest",
        country="HU",
        contact_email="demo@lfa.com",
        is_active=True,
    )
    db.add(demo_club)
    db.flush()
    ok(f"Club '{_DEMO_CLUB_NAME}' created (id={demo_club.id})")

demo_teams: list[Team] = []
all_demo_players: list[User] = []

for tdef in _DEMO_TEAMS:
    team = db.query(Team).filter(
        Team.club_id == demo_club.id,
        Team.name == tdef["name"],
    ).first()
    if not team:
        team = Team(
            name=tdef["name"],
            club_id=demo_club.id,
            age_group_label=tdef["age_group_label"],
            is_active=True,
        )
        db.add(team)
        db.flush()
        ok(f"  Team '{tdef['name']}' created (id={team.id})")
    else:
        ok(f"  Team '{tdef['name']}' already exists (id={team.id})")

    demo_teams.append(team)
    football_skills = {k: tdef["skill_base"] for k in all_skill_keys}
    age_slug = tdef["age_group_label"].lower()

    for idx, (first, last) in enumerate(_DEMO_PLAYERS[tdef["name"]]):
        email = f"demo-{age_slug}-{first.lower()}.{last.lower()}@lfa.com"
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                name=f"{first} {last}",
                first_name=first,
                last_name=last,
                nickname=first,
                email=email,
                password_hash=get_password_hash("Demo#1234"),
                role=UserRole.STUDENT,
                is_active=True,
                onboarding_completed=True,
                credit_balance=500,
                date_of_birth=tdef["dob"],
                nationality="British",
                gender="Male",
                phone=f"+44 7700 8{idx:05d}",
                street_address="2 Demo Lane",
                city="London",
                postal_code="EC2A 2BB",
                country="United Kingdom",
            )
            db.add(user)
            db.flush()

            lic = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=now,
                payment_verified=True,
                payment_verified_at=now,
                onboarding_completed=True,
                onboarding_completed_at=now,
                is_active=True,
                football_skills=football_skills,
                motivation_scores={
                    "position": "MIDFIELDER",
                    "goals": "improve_skills",
                    "motivation": "",
                    "average_skill_level": tdef["skill_base"],
                    "onboarding_completed_at": now.isoformat(),
                },
                average_motivation_score=tdef["skill_base"],
            )
            db.add(lic)
            db.flush()

        member = db.query(TeamMember).filter(
            TeamMember.team_id == team.id,
            TeamMember.user_id == user.id,
        ).first()
        if not member:
            db.add(TeamMember(team_id=team.id, user_id=user.id, role="PLAYER", is_active=True))
            db.flush()

        all_demo_players.append(user)

db.commit()
db.expire_all()

# Reload team objects + all players (in case they already existed)
demo_teams = [
    db.query(Team).filter(Team.club_id == demo_club.id, Team.name == tdef["name"]).first()
    for tdef in _DEMO_TEAMS
]
all_demo_players = (
    db.query(User)
    .join(TeamMember, TeamMember.user_id == User.id)
    .filter(TeamMember.team_id.in_([t.id for t in demo_teams]))
    .all()
)
print(f"  → Demo teams: {[t.name for t in demo_teams]}")
print(f"  → Demo players: {len(all_demo_players)} total")

# ── Invitation codes + CreditTransactions for demo players ────────────────────
section("Invitation Codes — Demo Players")

from app.models.invitation_code import InvitationCode  # noqa: E402
from app.models.credit_transaction import CreditTransaction, TransactionType as _TxType  # noqa: E402
from sqlalchemy import func as _func  # noqa: E402

_inv_created = _inv_skipped = 0
_tx_created  = _tx_skipped  = 0

for _user in all_demo_players:
    # Historical used code: INV-DEMO-{user_id}
    _code_str = f"INV-DEMO-{_user.id}"
    _exists = db.query(InvitationCode).filter(InvitationCode.code == _code_str).first()
    if not _exists:
        db.add(InvitationCode(
            code=_code_str,
            invited_name=_user.name,
            bonus_credits=_user.credit_balance,
            is_used=True,
            used_by_user_id=_user.id,
            used_at=now,
            created_by_admin_id=admin.id,
            notes="Historical demo code — auto-generated by seed_comprehensive_demo.py",
        ))
        _inv_created += 1
    else:
        _inv_skipped += 1

    # CreditTransaction audit trail
    _ikey = f"demo-credit-{_user.id}"
    _tx_exists = db.query(CreditTransaction).filter(
        CreditTransaction.idempotency_key == _ikey
    ).first()
    if not _tx_exists:
        db.add(CreditTransaction(
            user_id=_user.id,
            transaction_type=_TxType.ADMIN_ADJUSTMENT.value,
            amount=_user.credit_balance,
            balance_after=_user.credit_balance,
            description="Demo seed initial credit allocation",
            idempotency_key=_ikey,
            performed_by_user_id=admin.id,
        ))
        _tx_created += 1
    else:
        _tx_skipped += 1

db.commit()
ok(f"Invitation codes (used): {_inv_created} new, {_inv_skipped} already existed")
ok(f"CreditTransactions: {_tx_created} new, {_tx_skipped} already existed")

# Validation: every demo player has a code + credit sum matches balance
_inv_issues: list[str] = []
for _user in all_demo_players:
    _code = db.query(InvitationCode).filter(
        InvitationCode.used_by_user_id == _user.id
    ).first()
    if not _code:
        _inv_issues.append(f"    no invitation code for user {_user.id} ({_user.email})")
    _tx_sum = db.query(_func.sum(CreditTransaction.amount)).filter(
        CreditTransaction.user_id == _user.id
    ).scalar() or 0
    if _tx_sum != _user.credit_balance:
        _inv_issues.append(
            f"    credit mismatch user {_user.id}: "
            f"balance={_user.credit_balance}, tx_sum={_tx_sum}"
        )
if _inv_issues:
    err("Validation issues:")
    for _iss in _inv_issues:
        err(_iss)
else:
    ok(f"Validation passed: {len(all_demo_players)} demo players — codes + transactions consistent")

# Extra teams for Format H (group_knockout × TEAM needs 8 teams)
gk_extra_teams: list[Team] = []
for gdef in _GK_EXTRA_TEAMS:
    team = db.query(Team).filter(
        Team.club_id == demo_club.id,
        Team.name == gdef["name"],
    ).first()
    if not team:
        team = Team(
            name=gdef["name"],
            club_id=demo_club.id,
            age_group_label=gdef["age_group_label"],
            is_active=True,
        )
        db.add(team)
        db.flush()
    gk_extra_teams.append(team)
db.commit()
db.expire_all()
gk_extra_teams = [
    db.query(Team).filter(Team.club_id == demo_club.id, Team.name == gdef["name"]).first()
    for gdef in _GK_EXTRA_TEAMS
]
gk_all_teams = demo_teams + gk_extra_teams  # 8 teams total for GK × TEAM
print(f"  → GK extra teams: {[t.name for t in gk_extra_teams]}")


# ─────────────────────────────────────────────────────────────────────────────
# Shared tournament factory + lifecycle helpers
# ─────────────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:6]


def create_tournament(
    name: str,
    tt_id: int | None,
    participant_type: str,
    scoring_type: str = "SCORE_BASED",
    ranking_direction: str = "DESC",
) -> Semester:
    """Create a DRAFT tournament with all required related rows."""
    t = Semester(
        name=name,
        code=f"DEMO-{_uid()}",
        master_instructor_id=instructor.id,
        campus_id=campus.id,
        location_id=campus.location_id,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="DRAFT",
    )
    db.add(t)
    db.flush()
    db.add(TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=tt_id,
        participant_type=participant_type,
        max_players=64,
        number_of_rounds=1,
        parallel_fields=1,
        ranking_direction=ranking_direction,
        scoring_type=scoring_type,
    ))
    db.add(GameConfiguration(semester_id=t.id, game_preset_id=preset.id))
    db.add(TournamentRewardConfig(
        semester_id=t.id,
        reward_policy_name="Demo Default",
        reward_config=_REWARD_CONFIG,
    ))
    db.commit()
    db.expire_all()
    ok(f"Created '{t.name}'  id={t.id}")
    return t


def enroll_teams(tid: int, teams: list[Team]) -> list[Team]:
    """Enroll demo teams (TEAM participant_type)."""
    db.expire_all()
    enrolled = []
    for team in teams:
        existing = db.query(TournamentTeamEnrollment).filter(
            TournamentTeamEnrollment.semester_id == tid,
            TournamentTeamEnrollment.team_id == team.id,
        ).first()
        if not existing:
            db.add(TournamentTeamEnrollment(
                semester_id=tid,
                team_id=team.id,
                is_active=True,
                payment_verified=True,
            ))
        enrolled.append(team)
    db.commit()
    info(f"Enrolled {len(enrolled)} teams")
    return enrolled


def enroll_individual_players(tid: int, players: list[User]) -> list[User]:
    """Enroll demo players individually."""
    db.expire_all()
    enrolled = []
    for u in players:
        lic = db.query(UserLicense).filter(
            UserLicense.user_id == u.id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            UserLicense.is_active == True,
        ).first()
        if not lic:
            continue
        existing = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == tid,
            SemesterEnrollment.user_id == u.id,
        ).first()
        if not existing:
            db.add(SemesterEnrollment(
                semester_id=tid,
                user_id=u.id,
                user_license_id=lic.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED,
            ))
        enrolled.append(u)
    db.commit()
    info(f"Enrolled {len(enrolled)} individual players")
    return enrolled


def transition(tid: int, new_status: str) -> bool:
    r = client.patch(
        f"/api/v1/tournaments/{tid}/status",
        json={"new_status": new_status, "reason": "demo-seed"},
    )
    if r.status_code != 200:
        err(f"Transition {tid} → {new_status} failed: {r.status_code} {r.text[:200]}")
        return False
    db.expire_all()
    ok(f"→ {new_status}")
    return True


def _reach(tid: int, target: str, teams: list[Team] | None = None,
           players: list[User] | None = None) -> bool:
    """Advance from DRAFT to the given target state, enrolling as needed."""
    chain = [
        ("ENROLLMENT_OPEN",   lambda: teams and enroll_teams(tid, teams)
                                      or players and enroll_individual_players(tid, players)),
        ("ENROLLMENT_CLOSED", None),
        ("CHECK_IN_OPEN",     None),
        ("IN_PROGRESS",       None),
    ]
    for status, prep in chain:
        if prep:
            prep()
        if not transition(tid, status):
            return False
        if status == target:
            db.expire_all()
            n = db.query(SessionModel).filter(SessionModel.semester_id == tid).count()
            if n:
                info(f"Sessions generated: {n}")
            return True
    return True


def submit_team_results(tid: int) -> int:
    """Submit 1-0 H2H results for Round-1 sessions (participant_team_ids already set)."""
    db.expire_all()
    sessions = db.query(SessionModel).filter(SessionModel.semester_id == tid).all()
    submitted = 0
    for sess in sessions:
        pids = list(sess.participant_team_ids or [])
        if len(pids) < 2:
            continue
        r = client.patch(
            f"/api/v1/sessions/{sess.id}/team-results",
            json={
                "results": [
                    {"team_id": pids[0], "score": 2},
                    {"team_id": pids[1], "score": 0},
                ],
                "round_number": 1,
            },
        )
        if r.status_code in (200, 201):
            submitted += 1
        else:
            err(f"Team result session {sess.id}: {r.status_code} {r.text[:120]}")
    info(f"Team results submitted: {submitted} session(s)")
    return submitted


def submit_knockout_team_results(tid: int, teams: list[Team]) -> int:
    """Submit knockout results: Round 1 via API, then manually seed Round 2."""
    db.expire_all()
    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == tid)
        .order_by(SessionModel.id)
        .all()
    )

    # Round 1: sessions that already have participant_team_ids
    r1 = [s for s in sessions if s.participant_team_ids and len(s.participant_team_ids) >= 2]
    winners: list[int] = []
    for sess in r1:
        pids = list(sess.participant_team_ids)
        winner_id, loser_id = pids[0], pids[1]
        winners.append(winner_id)
        r = client.patch(
            f"/api/v1/sessions/{sess.id}/team-results",
            json={
                "results": [
                    {"team_id": winner_id, "score": 2},
                    {"team_id": loser_id,  "score": 0},
                ],
                "round_number": 1,
            },
        )
        if r.status_code not in (200, 201):
            err(f"Knockout R1 session {sess.id}: {r.status_code} {r.text[:120]}")

    # Round 2+: fill participant_team_ids with winners and submit
    r2_plus = [s for s in sessions if not s.participant_team_ids or len(s.participant_team_ids) < 2]
    submitted = len(r1)
    for i, sess in enumerate(r2_plus):
        w_pair = winners[i * 2: i * 2 + 2] if len(winners) >= i * 2 + 2 else winners[-2:]
        if len(w_pair) < 2:
            break
        sess.participant_team_ids = w_pair
        db.commit()
        db.expire_all()
        r = client.patch(
            f"/api/v1/sessions/{sess.id}/team-results",
            json={
                "results": [
                    {"team_id": w_pair[0], "score": 1},
                    {"team_id": w_pair[1], "score": 0},
                ],
                "round_number": 1,
            },
        )
        if r.status_code in (200, 201):
            submitted += 1
        else:
            err(f"Knockout R2 session {sess.id}: {r.status_code} {r.text[:120]}")
        winners = [w_pair[0]]   # winner advances

    info(f"Knockout results submitted: {submitted} session(s) "
         f"(R1={len(r1)}, R2+={len(r2_plus)})")
    return submitted


def write_gk_game_results(tid: int) -> int:
    """Write game_results directly to GROUP_STAGE sessions for Group Knockout."""
    import json as _json
    db.expire_all()
    sessions = db.query(SessionModel).filter(
        SessionModel.semester_id == tid,
        SessionModel.tournament_phase == "GROUP_STAGE",
    ).all()
    written = 0
    for sess in sessions:
        pids = list(sess.participant_user_ids or [])
        if len(pids) < 2:
            continue
        data = {
            "match_format": "HEAD_TO_HEAD",
            "participants": [
                {"user_id": pids[0], "score": 3.0, "result": "win"},
                {"user_id": pids[1], "score": 0.0, "result": "loss"},
            ],
        }
        sess.game_results = _json.dumps(data)
        sess.session_status = "completed"
        written += 1
    db.commit()
    info(f"GK group results written: {written} session(s)")
    return written


def seed_gk_knockout(tid: int) -> bool:
    """
    After group stage results are written:
    1. Call /finalize-group-stage → seeds R1 knockout participant_user_ids
    2. Write game_results for R1 knockout (winner = first participant)
    3. Advance winners to R2+ and repeat until final
    """
    import json as _json

    # Step 1: finalize group stage (computes standings, seeds R1 knockout participants)
    r = client.post(f"/api/v1/tournaments/{tid}/finalize-group-stage")
    if r.status_code != 200:
        err(f"finalize-group-stage failed: HTTP {r.status_code}")
        return False
    result = r.json()
    if not result.get("success"):
        err(f"finalize-group-stage: {result.get('message')}")
        return False
    ok(f"Group stage finalized: {result.get('knockout_sessions_updated', '?')} knockout sessions seeded")

    # Step 2 & 3: write knockout results round by round
    db.expire_all()
    knockout_sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == tid,
            SessionModel.tournament_phase == "KNOCKOUT",
        )
        .order_by(SessionModel.tournament_round.asc(), SessionModel.id.asc())
        .all()
    )
    if not knockout_sessions:
        err("No knockout sessions found")
        return False

    # Group sessions by round number
    rounds: dict[int, list] = {}
    for s in knockout_sessions:
        rounds.setdefault(s.tournament_round or 0, []).append(s)

    round_winners: dict[int, list[int]] = {}  # round_num → [winner_user_ids]
    round_losers:  dict[int, list[int]] = {}  # round_num → [loser_user_ids]

    for rnd in sorted(rounds.keys()):
        sessions_in_round = rounds[rnd]

        # R2+: populate participant_user_ids
        # Normal rounds: use previous round winners
        # Bronze match: use SF losers (round rnd-2) when prev winners insufficient
        if rnd > 1:
            prev_winners = round_winners.get(rnd - 1, [])
            needed = 2 * len(sessions_in_round)
            if len(prev_winners) < needed:
                # Bronze/3rd-place match — use semifinal losers (two rounds back)
                prev = round_losers.get(rnd - 2, [])
            else:
                prev = prev_winners
            for i, sess in enumerate(sessions_in_round):
                lo, hi = i * 2, i * 2 + 2
                if hi <= len(prev):
                    sess.participant_user_ids = prev[lo:hi]
            db.commit()
            db.expire_all()

        # Write game_results: first participant wins each match
        round_winners[rnd] = []
        round_losers[rnd] = []
        for sess in sessions_in_round:
            pids = list(sess.participant_user_ids or [])
            if len(pids) < 2:
                info(f"  Skipping session {sess.id} (R{rnd}): no participants")
                continue
            sess.game_results = _json.dumps({
                "match_format": "HEAD_TO_HEAD",
                "participants": [
                    {"user_id": pids[0], "score": 2.0, "result": "win"},
                    {"user_id": pids[1], "score": 0.0, "result": "loss"},
                ],
            })
            sess.session_status = "completed"
            round_winners[rnd].append(pids[0])
            round_losers[rnd].append(pids[1])
        db.commit()
        db.expire_all()

    total = sum(len(v) for v in rounds.values())
    ok(f"GK knockout seeded: {total} session(s) across {len(rounds)} round(s)")
    return True


def submit_individual_results(tid: int, players: list[User]) -> int:
    """Submit individual score results for all sessions (Swiss / IR)."""
    db.expire_all()
    sessions = db.query(SessionModel).filter(SessionModel.semester_id == tid).all()
    submitted = 0
    for sess in sessions:
        results = [
            {"user_id": p.id, "score": float(100 - i * 5), "rank": i + 1}
            for i, p in enumerate(players)
        ]
        r = client.patch(
            f"/api/v1/sessions/{sess.id}/results",
            json={"results": results},
        )
        if r.status_code in (200, 201):
            submitted += 1
        else:
            err(f"Individual result session {sess.id}: {r.status_code} {r.text[:120]}")
    info(f"Individual results submitted: {submitted} session(s)")
    return submitted


def submit_h2h_individual_results(tid: int) -> int:
    """Submit HEAD_TO_HEAD results for all sessions (Swiss / league × IND).

    Uses PATCH /sessions/{id}/head-to-head-results with the 2 participants
    from each session's participant_user_ids. Score 3-1 so player-0 wins.
    """
    db.expire_all()
    sessions = db.query(SessionModel).filter(SessionModel.semester_id == tid).all()
    submitted = 0
    for sess in sessions:
        pids = list(sess.participant_user_ids or [])
        if len(pids) < 2:
            err(f"H2H session {sess.id} has <2 participants — skipping")
            continue
        r = client.patch(
            f"/api/v1/sessions/{sess.id}/head-to-head-results",
            json={"results": [
                {"user_id": pids[0], "score": 3},
                {"user_id": pids[1], "score": 1},
            ]},
        )
        if r.status_code in (200, 201):
            submitted += 1
        else:
            err(f"H2H result session {sess.id}: {r.status_code} {r.text[:120]}")
    info(f"H2H individual results submitted: {submitted}/{len(sessions)} session(s)")
    return submitted


def submit_h2h_results_by_round(tid: int) -> int:
    """Submit H2H individual results round-by-round for knockout × IND.

    Between rounds, db.expire_all() allows KPS-seeded participant_user_ids to
    become visible before the next round's sessions are queried.
    """
    total = 0
    for rn in range(1, 6):  # safety: up to 5 knockout rounds
        db.expire_all()
        round_sessions = (
            db.query(SessionModel)
            .filter(
                SessionModel.semester_id == tid,
                SessionModel.tournament_round == rn,
            )
            .all()
        )
        if not round_sessions:
            break
        submitted = 0
        for sess in round_sessions:
            pids = list(sess.participant_user_ids or [])
            if len(pids) < 2:
                continue
            r = client.patch(
                f"/api/v1/sessions/{sess.id}/head-to-head-results",
                json={"results": [
                    {"user_id": pids[0], "score": 3},
                    {"user_id": pids[1], "score": 1},
                ]},
            )
            if r.status_code in (200, 201):
                submitted += 1
                total += 1
            else:
                err(f"KO IND R{rn} session {sess.id}: {r.status_code} {r.text[:120]}")
        info(f"KO IND R{rn}: {submitted}/{len(round_sessions)} sessions submitted")
    return total


def submit_gk_team_results_api(tid: int) -> bool:
    """Submit GK × TEAM results end-to-end via API only.

    1. GROUP_STAGE: submit all sessions with participant_team_ids
    2. POST /finalize-group-stage → seeds knockout participant_team_ids
    3. KNOCKOUT: submit round-by-round (KPS TEAM auto-seeds next round)
    """
    # --- GROUP_STAGE ---
    db.expire_all()
    gs_sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == tid,
            SessionModel.tournament_phase == "GROUP_STAGE",
        )
        .all()
    )
    gs_submitted = 0
    for sess in gs_sessions:
        pids = list(sess.participant_team_ids or [])
        if len(pids) < 2:
            continue
        r = client.patch(
            f"/api/v1/sessions/{sess.id}/team-results",
            json={
                "results": [
                    {"team_id": pids[0], "score": 2},
                    {"team_id": pids[1], "score": 0},
                ],
                "round_number": 1,
            },
        )
        if r.status_code in (200, 201):
            gs_submitted += 1
        else:
            err(f"GK GS session {sess.id}: {r.status_code} {r.text[:120]}")
    info(f"GK group stage: {gs_submitted}/{len(gs_sessions)} sessions submitted")

    # --- FINALIZE GROUP STAGE ---
    r = client.post(f"/api/v1/tournaments/{tid}/finalize-group-stage")
    if r.status_code != 200:
        err(f"finalize-group-stage failed: {r.status_code} {r.text[:120]}")
        return False
    res = r.json()
    if not res.get("success"):
        err(f"finalize-group-stage: {res.get('message')}")
        return False
    ok(f"Group stage finalized: {res.get('knockout_sessions_updated', '?')} KO sessions seeded")

    # --- KNOCKOUT (round-by-round, KPS TEAM auto-seeds next round) ---
    for rn in range(1, 6):
        db.expire_all()
        ko_sessions = (
            db.query(SessionModel)
            .filter(
                SessionModel.semester_id == tid,
                SessionModel.tournament_phase == "KNOCKOUT",
                SessionModel.tournament_round == rn,
            )
            .all()
        )
        if not ko_sessions:
            break
        round_submitted = 0
        for sess in ko_sessions:
            pids = list(sess.participant_team_ids or [])
            if len(pids) < 2:
                continue
            r = client.patch(
                f"/api/v1/sessions/{sess.id}/team-results",
                json={
                    "results": [
                        {"team_id": pids[0], "score": 2},
                        {"team_id": pids[1], "score": 0},
                    ],
                    "round_number": 1,
                },
            )
            if r.status_code in (200, 201):
                round_submitted += 1
            else:
                err(f"GK KO R{rn} session {sess.id}: {r.status_code} {r.text[:120]}")
        info(f"GK KO R{rn}: {round_submitted}/{len(ko_sessions)} sessions submitted")

    return True


def seed_individual_rankings(tid: int, players: list[User]) -> int:
    """Directly write TournamentRanking rows (used for Swiss — no API strategy)."""
    db.expire_all()
    db.query(TournamentRanking).filter(TournamentRanking.tournament_id == tid).delete()
    db.commit()
    for i, p in enumerate(players):
        db.add(TournamentRanking(
            tournament_id=tid,
            user_id=p.id,
            participant_type="INDIVIDUAL",
            rank=i + 1,
            points=float(max(0, 100 - i * 5)),
        ))
    db.commit()
    ok(f"Swiss rankings seeded: {len(players)} player(s)")
    return len(players)


def seed_team_rankings(tid: int, teams: list[Team]) -> int:
    """Directly write TournamentRanking rows for TEAM format (fallback)."""
    db.expire_all()
    db.query(TournamentRanking).filter(TournamentRanking.tournament_id == tid).delete()
    db.commit()
    for i, team in enumerate(teams):
        db.add(TournamentRanking(
            tournament_id=tid,
            team_id=team.id,
            participant_type="TEAM",
            rank=i + 1,
            points=float(max(0, 10 - i * 3)),
            wins=max(0, 3 - i),
            losses=i,
            draws=0,
        ))
    db.commit()
    ok(f"Team rankings seeded directly: {len(teams)} team(s)")
    return len(teams)


def calculate_rankings(tid: int) -> bool:
    r = client.post(f"/api/v1/tournaments/{tid}/calculate-rankings", json={})
    if r.status_code == 200:
        ok("Rankings calculated via API")
        return True
    err(f"Rankings API failed ({r.status_code}): {r.text[:150]}")
    return False


def distribute_rewards(tid: int) -> bool:
    r = client.post(
        f"/api/v1/tournaments/{tid}/distribute-rewards-v2",
        json={"tournament_id": tid, "force_redistribution": False},
    )
    if r.status_code == 200:
        db.expire_all()
        final = db.query(Semester).filter(Semester.id == tid).first()
        ok(f"Rewards distributed → status: {final.tournament_status}")
        return True
    err(f"Rewards failed: {r.status_code} {r.text[:150]}")
    return False


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT A — H2H LEAGUE (TEAM, WDL_BASED)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT A — H2H League (TEAM, WDL_BASED) — 8 states")

def _league(label: str) -> Semester:
    return create_tournament(
        f"Demo: H2H League — {label}",
        tt_id=tt_league.id,
        participant_type="TEAM",
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
    )

print("\n[A1/8] DRAFT")
_league("Draft")

print("\n[A2/8] ENROLLMENT_OPEN")
t = _league("Enrollment Open")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[A3/8] ENROLLMENT_CLOSED")
t = _league("Enrollment Closed")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[A4/8] CHECK_IN_OPEN")
t = _league("Check-In Open")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[A5/8] IN_PROGRESS")
t = _league("In Progress")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results submitted — standings_state=NONE")

print("\n[A6/8] COMPLETED")
t = _league("Completed")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_team_results(t.id)
    if not calculate_rankings(t.id):
        seed_team_rankings(t.id, demo_teams)
    transition(t.id, "COMPLETED")

print("\n[A7/8] REWARDS_DISTRIBUTED")
t = _league("Rewards Distributed")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_team_results(t.id)
    if not calculate_rankings(t.id):
        seed_team_rankings(t.id, demo_teams)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[A8/8] CANCELLED")
t = _league("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT B — KNOCKOUT (TEAM, WDL_BASED)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT B — Knockout (TEAM, WDL_BASED) — 8 states")

def _knockout(label: str) -> Semester:
    return create_tournament(
        f"Demo: Knockout — {label}",
        tt_id=tt_knockout.id,
        participant_type="TEAM",
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
    )

print("\n[B1/8] DRAFT")
_knockout("Draft")

print("\n[B2/8] ENROLLMENT_OPEN")
t = _knockout("Enrollment Open")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[B3/8] ENROLLMENT_CLOSED")
t = _knockout("Enrollment Closed")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[B4/8] CHECK_IN_OPEN")
t = _knockout("Check-In Open")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[B5/8] IN_PROGRESS")
t = _knockout("In Progress")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[B6/8] COMPLETED")
t = _knockout("Completed")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_knockout_team_results(t.id, demo_teams)
    if not calculate_rankings(t.id):
        seed_team_rankings(t.id, demo_teams)
    transition(t.id, "COMPLETED")

print("\n[B7/8] REWARDS_DISTRIBUTED")
t = _knockout("Rewards Distributed")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_knockout_team_results(t.id, demo_teams)
    if not calculate_rankings(t.id):
        seed_team_rankings(t.id, demo_teams)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[B8/8] CANCELLED")
t = _knockout("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT C — GROUP KNOCKOUT (INDIVIDUAL, WDL_BASED)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT C — Group Knockout (INDIVIDUAL, WDL_BASED) — 8 states")

def _gk(label: str) -> Semester:
    return create_tournament(
        f"Demo: Group Knockout — {label}",
        tt_id=tt_gk.id,
        participant_type="INDIVIDUAL",
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
    )

print("\n[C1/8] DRAFT")
_gk("Draft")

print("\n[C2/8] ENROLLMENT_OPEN")
t = _gk("Enrollment Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[C3/8] ENROLLMENT_CLOSED")
t = _gk("Enrollment Closed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[C4/8] CHECK_IN_OPEN")
t = _gk("Check-In Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[C5/8] IN_PROGRESS")
t = _gk("In Progress")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[C6/8] COMPLETED")
t = _gk("Completed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    write_gk_game_results(t.id)
    seed_gk_knockout(t.id)
    if not calculate_rankings(t.id):
        seed_individual_rankings(t.id, all_demo_players)
    transition(t.id, "COMPLETED")

print("\n[C7/8] REWARDS_DISTRIBUTED")
t = _gk("Rewards Distributed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    write_gk_game_results(t.id)
    seed_gk_knockout(t.id)
    if not calculate_rankings(t.id):
        seed_individual_rankings(t.id, all_demo_players)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[C8/8] CANCELLED")
t = _gk("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT D — SWISS (INDIVIDUAL, SCORING_ONLY)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT D — Swiss (INDIVIDUAL, SCORING_ONLY) — 8 states")

def _swiss(label: str) -> Semester:
    return create_tournament(
        f"Demo: Swiss — {label}",
        tt_id=tt_swiss.id,
        participant_type="INDIVIDUAL",
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
    )

print("\n[D1/8] DRAFT")
_swiss("Draft")

print("\n[D2/8] ENROLLMENT_OPEN")
t = _swiss("Enrollment Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[D3/8] ENROLLMENT_CLOSED")
t = _swiss("Enrollment Closed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[D4/8] CHECK_IN_OPEN")
t = _swiss("Check-In Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[D5/8] IN_PROGRESS")
t = _swiss("In Progress")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[D6/8] COMPLETED")
t = _swiss("Completed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_h2h_individual_results(t.id)
    calculate_rankings(t.id)
    transition(t.id, "COMPLETED")

print("\n[D7/8] REWARDS_DISTRIBUTED")
t = _swiss("Rewards Distributed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_h2h_individual_results(t.id)
    calculate_rankings(t.id)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[D8/8] CANCELLED")
t = _swiss("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT E — INDIVIDUAL RANKING (INDIVIDUAL, SCORING_ONLY)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT E — Individual Ranking (INDIVIDUAL, SCORING_ONLY) — 8 states")

def _ir(label: str) -> Semester:
    # tournament_type_id=None → format resolved as INDIVIDUAL_RANKING
    return create_tournament(
        f"Demo: IR — {label}",
        tt_id=None,
        participant_type="INDIVIDUAL",
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
    )

print("\n[E1/8] DRAFT")
_ir("Draft")

print("\n[E2/8] ENROLLMENT_OPEN")
t = _ir("Enrollment Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[E3/8] ENROLLMENT_CLOSED")
t = _ir("Enrollment Closed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[E4/8] CHECK_IN_OPEN")
t = _ir("Check-In Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[E5/8] IN_PROGRESS")
t = _ir("In Progress")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[E6/8] COMPLETED")
t = _ir("Completed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_individual_results(t.id, all_demo_players)
    calculate_rankings(t.id)
    transition(t.id, "COMPLETED")

print("\n[E7/8] REWARDS_DISTRIBUTED")
t = _ir("Rewards Distributed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_individual_results(t.id, all_demo_players)
    calculate_rankings(t.id)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[E8/8] CANCELLED")
t = _ir("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT F — H2H LEAGUE (INDIVIDUAL, WDL_BASED) — KPS not triggered (league)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT F — H2H League (INDIVIDUAL, WDL_BASED) — 8 states")

def _league_ind(label: str) -> Semester:
    return create_tournament(
        f"Demo: H2H League IND — {label}",
        tt_id=tt_league.id,
        participant_type="INDIVIDUAL",
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
    )

print("\n[F1/8] DRAFT")
_league_ind("Draft")

print("\n[F2/8] ENROLLMENT_OPEN")
t = _league_ind("Enrollment Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[F3/8] ENROLLMENT_CLOSED")
t = _league_ind("Enrollment Closed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[F4/8] CHECK_IN_OPEN")
t = _league_ind("Check-In Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[F5/8] IN_PROGRESS")
t = _league_ind("In Progress")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[F6/8] COMPLETED")
t = _league_ind("Completed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_h2h_individual_results(t.id)
    calculate_rankings(t.id)
    transition(t.id, "COMPLETED")

print("\n[F7/8] REWARDS_DISTRIBUTED")
t = _league_ind("Rewards Distributed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_h2h_individual_results(t.id)
    calculate_rankings(t.id)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[F8/8] CANCELLED")
t = _league_ind("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT G — KNOCKOUT (INDIVIDUAL, WDL_BASED) — KPS IND auto-seeds Final+Bronze
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT G — Knockout (INDIVIDUAL, WDL_BASED, KPS IND) — 8 states")

def _knockout_ind(label: str) -> Semester:
    return create_tournament(
        f"Demo: Knockout IND — {label}",
        tt_id=tt_knockout.id,
        participant_type="INDIVIDUAL",
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
    )

print("\n[G1/8] DRAFT")
_knockout_ind("Draft")

print("\n[G2/8] ENROLLMENT_OPEN")
t = _knockout_ind("Enrollment Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[G3/8] ENROLLMENT_CLOSED")
t = _knockout_ind("Enrollment Closed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[G4/8] CHECK_IN_OPEN")
t = _knockout_ind("Check-In Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[G5/8] IN_PROGRESS")
t = _knockout_ind("In Progress")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[G6/8] COMPLETED")
t = _knockout_ind("Completed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_h2h_results_by_round(t.id)
    calculate_rankings(t.id)
    transition(t.id, "COMPLETED")

print("\n[G7/8] REWARDS_DISTRIBUTED")
t = _knockout_ind("Rewards Distributed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_h2h_results_by_round(t.id)
    calculate_rankings(t.id)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[G8/8] CANCELLED")
t = _knockout_ind("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT H — GROUP KNOCKOUT (TEAM, WDL_BASED) — KPS TEAM (EC-03-AT)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT H — Group Knockout (TEAM, WDL_BASED, KPS TEAM) — 8 states")

def _gk_team(label: str) -> Semester:
    return create_tournament(
        f"Demo: Group Knockout TEAM — {label}",
        tt_id=tt_gk.id,
        participant_type="TEAM",
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
    )

print("\n[H1/8] DRAFT")
_gk_team("Draft")

print("\n[H2/8] ENROLLMENT_OPEN")
t = _gk_team("Enrollment Open")
enroll_teams(t.id, gk_all_teams)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[H3/8] ENROLLMENT_CLOSED")
t = _gk_team("Enrollment Closed")
enroll_teams(t.id, gk_all_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[H4/8] CHECK_IN_OPEN")
t = _gk_team("Check-In Open")
enroll_teams(t.id, gk_all_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[H5/8] IN_PROGRESS")
t = _gk_team("In Progress")
enroll_teams(t.id, gk_all_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[H6/8] COMPLETED")
t = _gk_team("Completed")
enroll_teams(t.id, gk_all_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_gk_team_results_api(t.id)
    calculate_rankings(t.id)
    transition(t.id, "COMPLETED")

print("\n[H7/8] REWARDS_DISTRIBUTED")
t = _gk_team("Rewards Distributed")
enroll_teams(t.id, gk_all_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_gk_team_results_api(t.id)
    calculate_rankings(t.id)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[H8/8] CANCELLED")
t = _gk_team("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT I — INDIVIDUAL RANKING (TEAM, SCORE_BASED)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT I — Individual Ranking (TEAM, SCORE_BASED) — 8 states")

def _ir_team(label: str) -> Semester:
    return create_tournament(
        f"Demo: IR TEAM — {label}",
        tt_id=None,
        participant_type="TEAM",
        scoring_type="SCORE_BASED",
        ranking_direction="DESC",
    )

print("\n[I1/8] DRAFT")
_ir_team("Draft")

print("\n[I2/8] ENROLLMENT_OPEN")
t = _ir_team("Enrollment Open")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[I3/8] ENROLLMENT_CLOSED")
t = _ir_team("Enrollment Closed")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[I4/8] CHECK_IN_OPEN")
t = _ir_team("Check-In Open")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[I5/8] IN_PROGRESS")
t = _ir_team("In Progress")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[I6/8] COMPLETED")
t = _ir_team("Completed")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_team_results(t.id)
    calculate_rankings(t.id)
    transition(t.id, "COMPLETED")

print("\n[I7/8] REWARDS_DISTRIBUTED")
t = _ir_team("Rewards Distributed")
enroll_teams(t.id, demo_teams)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_team_results(t.id)
    calculate_rankings(t.id)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[I8/8] CANCELLED")
t = _ir_team("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT J — INDIVIDUAL RANKING (INDIVIDUAL, TIME_BASED)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT J — Individual Ranking (INDIVIDUAL, TIME_BASED) — 8 states")

def _ir_time(label: str) -> Semester:
    return create_tournament(
        f"Demo: IR Time — {label}",
        tt_id=None,
        participant_type="INDIVIDUAL",
        scoring_type="TIME_BASED",
        ranking_direction="ASC",
    )

print("\n[J1/8] DRAFT")
_ir_time("Draft")

print("\n[J2/8] ENROLLMENT_OPEN")
t = _ir_time("Enrollment Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[J3/8] ENROLLMENT_CLOSED")
t = _ir_time("Enrollment Closed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[J4/8] CHECK_IN_OPEN")
t = _ir_time("Check-In Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[J5/8] IN_PROGRESS")
t = _ir_time("In Progress")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[J6/8] COMPLETED")
t = _ir_time("Completed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_individual_results(t.id, all_demo_players)
    calculate_rankings(t.id)
    transition(t.id, "COMPLETED")

print("\n[J7/8] REWARDS_DISTRIBUTED")
t = _ir_time("Rewards Distributed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_individual_results(t.id, all_demo_players)
    calculate_rankings(t.id)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[J8/8] CANCELLED")
t = _ir_time("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT K — INDIVIDUAL RANKING (INDIVIDUAL, PLACEMENT)
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT K — Individual Ranking (INDIVIDUAL, PLACEMENT) — 8 states")

def _ir_placement(label: str) -> Semester:
    return create_tournament(
        f"Demo: IR Placement — {label}",
        tt_id=None,
        participant_type="INDIVIDUAL",
        scoring_type="PLACEMENT",
        ranking_direction="ASC",
    )

print("\n[K1/8] DRAFT")
_ir_placement("Draft")

print("\n[K2/8] ENROLLMENT_OPEN")
t = _ir_placement("Enrollment Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")

print("\n[K3/8] ENROLLMENT_CLOSED")
t = _ir_placement("Enrollment Closed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")

print("\n[K4/8] CHECK_IN_OPEN")
t = _ir_placement("Check-In Open")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
db.expire_all()
info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")

print("\n[K5/8] IN_PROGRESS")
t = _ir_placement("In Progress")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    info("No results — standings_state=NONE")

print("\n[K6/8] COMPLETED")
t = _ir_placement("Completed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_individual_results(t.id, all_demo_players)
    calculate_rankings(t.id)
    transition(t.id, "COMPLETED")

print("\n[K7/8] REWARDS_DISTRIBUTED")
t = _ir_placement("Rewards Distributed")
enroll_individual_players(t.id, all_demo_players)
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
if transition(t.id, "IN_PROGRESS"):
    db.expire_all()
    info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
    submit_individual_results(t.id, all_demo_players)
    calculate_rankings(t.id)
    if transition(t.id, "COMPLETED"):
        distribute_rewards(t.id)

print("\n[K8/8] CANCELLED")
t = _ir_placement("Cancelled")
transition(t.id, "CANCELLED")


# ═════════════════════════════════════════════════════════════════════════════
# FORMAT L — VIRTUAL TOURNAMENT × INDIVIDUAL × QUIZ-BASED RANKING
# ═════════════════════════════════════════════════════════════════════════════
#
# Demonstrates the full virtual+quiz student flow:
#   enroll → virtual session → take quiz → score → ranking
#
# Only 2 lifecycle states are meaningful for virtual quiz tournaments:
#   L1: IN_PROGRESS (session active, quiz open, 2 attempts seeded)
#   L2: REWARDS_DISTRIBUTED (completed, quiz ranking applied)
#
# ═════════════════════════════════════════════════════════════════════════════

section("FORMAT L — Virtual Quiz Tournament (INDIVIDUAL, quiz-based ranking) — 2 states")

_L_QUESTIONS = [
    {
        "text": "What is the offside rule in football?",
        "explanation": "A player is offside if they are nearer to the opponent's goal than the ball when the ball is played.",
        "options": [
            ("A player is offside if nearer to opponent's goal than the ball when played", True),
            ("A player is offside if they touch the ball last", False),
            ("Offside only applies in the penalty area", False),
        ],
    },
    {
        "text": "How long is a standard football match?",
        "explanation": "A standard match consists of two 45-minute halves = 90 minutes total.",
        "options": [
            ("90 minutes (two 45-minute halves)", True),
            ("80 minutes (two 40-minute halves)", False),
            ("60 minutes (one continuous half)", False),
        ],
    },
    {
        "text": "How many players are on a football team during a match?",
        "explanation": "Each team fields 11 players including the goalkeeper.",
        "options": [
            ("11 players including goalkeeper", True),
            ("10 outfield players only", False),
            ("12 players including one reserve", False),
        ],
    },
]


def _create_virtual_quiz_tournament(label: str) -> tuple:
    """Create a virtual tournament with a quiz linked to one session.
    Returns (semester, session, quiz).
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    name = f"Demo: Virtual Quiz L — {label}"
    t = Semester(
        name=name,
        code=f"DEMO-VQL-{_uid()}",
        master_instructor_id=instructor.id,
        campus_id=campus.id,
        location_id=campus.location_id,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="DRAFT",
    )
    db.add(t)
    db.flush()
    db.add(TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=None,
        participant_type="INDIVIDUAL",
        max_players=64,
        number_of_rounds=1,
        session_type_config="virtual",
        sessions_generated=True,
        sessions_generated_at=_dt.now(_tz.utc),
    ))
    db.add(GameConfiguration(semester_id=t.id, game_preset_id=preset.id))
    db.add(TournamentRewardConfig(
        semester_id=t.id,
        reward_policy_name="Demo Default",
        reward_config=_REWARD_CONFIG,
    ))
    db.flush()

    # Virtual session: started 2 hours ago so quiz is accessible
    now = _dt.now(_tz.utc).replace(tzinfo=None)
    sess = SessionModel(
        title=f"{name} — Session",
        semester_id=t.id,
        session_type="virtual",
        meeting_link="https://meet.example.com/demo-virtual-quiz-l",
        date_start=now - _td(hours=2),
        date_end=now + _td(hours=22),
        capacity=50,
        base_xp=50,
    )
    db.add(sess)
    db.flush()

    # Quiz: 3 questions, 60% passing threshold
    quiz = Quiz(
        title=f"Virtual Quiz L — {label}",
        description="Football knowledge quiz for virtual tournament demo.",
        category=QuizCategory.LESSON,
        difficulty=QuizDifficulty.EASY,
        passing_score=0.6,
        time_limit_minutes=15,
        is_active=True,
    )
    db.add(quiz)
    db.flush()

    correct_option_ids: list[int] = []
    for i, q_spec in enumerate(_L_QUESTIONS):
        q = QuizQuestion(
            quiz_id=quiz.id,
            question_text=q_spec["text"],
            question_type=QuestionType.MULTIPLE_CHOICE,
            points=1.0,
            order_index=i + 1,
            explanation=q_spec["explanation"],
        )
        db.add(q)
        db.flush()
        correct_id = None
        for j, (opt_text, is_correct) in enumerate(q_spec["options"]):
            ao = QuizAnswerOption(
                question_id=q.id,
                option_text=opt_text,
                is_correct=is_correct,
                order_index=j + 1,
            )
            db.add(ao)
            db.flush()
            if is_correct:
                correct_id = ao.id
        correct_option_ids.append(correct_id)

    db.add(SessionQuiz(
        session_id=sess.id,
        quiz_id=quiz.id,
        is_required=True,
        max_attempts=3,
    ))
    db.commit()
    db.expire_all()
    ok(f"Created '{t.name}'  id={t.id}  session={sess.id}  quiz={quiz.id}")
    return t, sess, quiz, correct_option_ids


def _seed_quiz_attempts(quiz_id: int, session_id: int, players: list[User],
                        correct_option_ids: list[int]) -> None:
    """Seed quiz attempts: player[0] → 100% pass, player[1] → 33% fail."""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now = _dt.now(_tz.utc)

    for idx, player in enumerate(players[:2]):
        if idx == 0:
            # Player 0: all 3 correct → 100%
            selected_ids = correct_option_ids[:]
        else:
            # Player 1: only first correct → 33%
            selected_ids = [correct_option_ids[0], None, None]

        correct_count = sum(1 for sid in selected_ids if sid is not None)
        score = correct_count / len(selected_ids)
        passed = score >= 0.6

        attempt = QuizAttempt(
            user_id=player.id,
            quiz_id=quiz_id,
            score=score,
            passed=passed,
            correct_answers=correct_count,
            total_questions=len(selected_ids),
            xp_awarded=50 if passed else 0,
            started_at=now - _td(minutes=20),
            completed_at=now - _td(minutes=10),
            time_spent_minutes=10,
        )
        db.add(attempt)
        db.flush()

        questions = db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.order_index).all()
        for q_idx, question in enumerate(questions):
            sel_id = selected_ids[q_idx] if q_idx < len(selected_ids) else None
            if sel_id:
                db.add(QuizUserAnswer(
                    attempt_id=attempt.id,
                    question_id=question.id,
                    selected_option_id=sel_id,
                ))
        db.commit()
        result = "PASSED ✅" if passed else "FAILED ❌"
        info(f"Attempt: {player.email}  score={score:.0%}  {result}")


# L1: IN_PROGRESS — session active, quiz open, 2 attempts seeded
print("\n[L1/2] IN_PROGRESS (virtual, quiz active, 2 attempts seeded)")
t, sess, quiz, correct_ids = _create_virtual_quiz_tournament("In Progress")
enrolled = enroll_individual_players(t.id, all_demo_players[:4])
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
transition(t.id, "IN_PROGRESS")
if enrolled:
    _seed_quiz_attempts(quiz.id, sess.id, enrolled, correct_ids)

# L2: REWARDS_DISTRIBUTED — quiz ranking applied
print("\n[L2/2] REWARDS_DISTRIBUTED (quiz completed, ranking seeded)")
t, sess, quiz, correct_ids = _create_virtual_quiz_tournament("Rewards Distributed")
enrolled = enroll_individual_players(t.id, all_demo_players[:4])
transition(t.id, "ENROLLMENT_OPEN")
transition(t.id, "ENROLLMENT_CLOSED")
transition(t.id, "CHECK_IN_OPEN")
transition(t.id, "IN_PROGRESS")
if enrolled:
    _seed_quiz_attempts(quiz.id, sess.id, enrolled, correct_ids)
    # Seed rankings directly based on quiz scores
    db.expire_all()
    from app.models.tournament_ranking import TournamentRanking as TR
    existing_ranks = db.query(TR).filter(TR.tournament_id == t.id).count()
    if existing_ranks == 0 and len(enrolled) >= 2:
        # player[0] = rank 1 (100%), player[1] = rank 2 (33%), others = rank 3+
        for rank_pos, player in enumerate(enrolled, start=1):
            db.add(TR(
                tournament_id=t.id,
                user_id=player.id,
                participant_type="INDIVIDUAL",
                rank=rank_pos,
                points=100.0 if rank_pos == 1 else (33.0 if rank_pos == 2 else 0.0),
            ))
        db.commit()
    transition(t.id, "COMPLETED")
    distribute_rewards(t.id)


# ═════════════════════════════════════════════════════════════════════════════
# MULTI-CAMPUS TOURNAMENTS (MC-1, MC-2, MC-3)
# ═════════════════════════════════════════════════════════════════════════════

section("Multi-Campus infrastructure — loading bootstrap data")

from app.models.pitch import Pitch as PitchModel
from app.models.tournament_instructor_slot import TournamentInstructorSlot

# Load all 6 campuses in creation order (C0 = Főváros, C1..C5 = multi-city)
_mc_all_campuses = db.query(Campus).filter(Campus.is_active == True).order_by(Campus.id.asc()).limit(6).all()
if len(_mc_all_campuses) < 2:
    print("⚠️  Less than 2 campuses found — run bootstrap_clean.py with Step 8 first. Skipping MC tournaments.")
else:
    _PITCHES_BY_CAMPUS: dict[int, list] = {}
    for _c in _mc_all_campuses:
        _PITCHES_BY_CAMPUS[_c.id] = db.query(PitchModel).filter(
            PitchModel.campus_id == _c.id
        ).order_by(PitchModel.pitch_number.asc()).all()

    _mc_all_instructors = [
        db.query(User).filter(User.email == "instructor@lfa.com").first(),  # I0 = MASTER
        *[db.query(User).filter(User.email == f"lfa-instr-{n}@lfa.com").first()
          for n in range(1, 5)],  # I1..I4 = FIELD
    ]
    _mc_all_instructors = [u for u in _mc_all_instructors if u]

    # Load 32 bootstrap players (U15 + U18 + Adult)
    _boot_club = db.query(Club).filter(Club.code == "LFA-BOOT").first()
    if _boot_club:
        _boot_players: list[User] = (
            db.query(User)
            .join(TeamMember, TeamMember.user_id == User.id)
            .join(Team, Team.id == TeamMember.team_id)
            .filter(Team.club_id == _boot_club.id, TeamMember.is_active == True)
            .order_by(User.id.asc())
            .limit(32)
            .all()
        )
    else:
        _boot_players = []
    ok(f"MC infra: {len(_mc_all_campuses)} campuses, {len(_mc_all_instructors)} instructors, {len(_boot_players)} boot players")

    def _enrich_sessions_with_venue(tid: int, gk_phase_split: bool = False) -> None:
        """Deterministic campus/pitch/instructor assignment per session."""
        db.expire_all()
        sessions = (
            db.query(SessionModel)
            .filter(SessionModel.semester_id == tid)
            .order_by(SessionModel.tournament_phase.asc().nulls_last(), SessionModel.id.asc())
            .all()
        )
        N = len(_mc_all_campuses)
        N_I = len(_mc_all_instructors)
        if N == 0 or N_I == 0:
            return

        if gk_phase_split:
            group_s = [s for s in sessions if s.tournament_phase == "GROUP_STAGE"]
            ko_s    = [s for s in sessions if s.tournament_phase == "KNOCKOUT"]
            pairs = [(s, i % 3)     for i, s in enumerate(group_s)] + \
                    [(s, 3 + i % 3) for i, s in enumerate(ko_s)]
        else:
            pairs = [(s, i % N) for i, s in enumerate(sessions)]

        for i, (sess, c_idx) in enumerate(pairs):
            c_idx = min(c_idx, N - 1)
            camp   = _mc_all_campuses[c_idx]
            pitches = _PITCHES_BY_CAMPUS.get(camp.id, [])
            pitch  = pitches[(i // N) % len(pitches)] if pitches else None
            instr  = _mc_all_instructors[i % N_I]
            sess.campus_id     = camp.id
            sess.pitch_id      = pitch.id if pitch else None
            sess.instructor_id = instr.id
        db.commit()
        info(f"  Venue enriched: {len(sessions)} sessions across {N} campuses")

    def _create_instructor_slots(tid: int) -> None:
        """1 MASTER slot (I0) + FIELD slots per unique pitch (I1..I4, no duplicates)."""
        db.expire_all()
        # Remove stale slots first (idempotent)
        db.query(TournamentInstructorSlot).filter(
            TournamentInstructorSlot.semester_id == tid
        ).delete()
        db.commit()

        used_pitch_ids = sorted({
            s.pitch_id for s in db.query(SessionModel).filter(SessionModel.semester_id == tid).all()
            if s.pitch_id
        })
        if not _mc_all_instructors:
            return
        db.add(TournamentInstructorSlot(
            semester_id=tid, instructor_id=_mc_all_instructors[0].id,
            role="MASTER", pitch_id=None, assigned_by=admin.id, status="CONFIRMED",
        ))
        field_instrs = _mc_all_instructors[1:] if len(_mc_all_instructors) > 1 else _mc_all_instructors
        used_instr_ids: set[int] = {_mc_all_instructors[0].id}
        field_count = 0
        for j, pid in enumerate(used_pitch_ids):
            instr = field_instrs[j % len(field_instrs)]
            if instr.id in used_instr_ids:
                continue  # skip — unique constraint (1 slot per instructor per tournament)
            used_instr_ids.add(instr.id)
            db.add(TournamentInstructorSlot(
                semester_id=tid, instructor_id=instr.id,
                role="FIELD", pitch_id=pid, assigned_by=admin.id, status="CONFIRMED",
            ))
            field_count += 1
        db.commit()
        info(f"  Instructor slots: 1 MASTER + {field_count} FIELD")

    def _assert_mc_ac(tid: int, require_multi_campus: bool = True) -> None:
        """Self-validating AC check after each MC tournament seed."""
        db.expire_all()
        sessions = db.query(SessionModel).filter(SessionModel.semester_id == tid).all()
        done = [s for s in sessions if s.game_results or s.rounds_data]
        campus_set = {s.campus_id for s in sessions if s.campus_id}
        assert all(s.campus_id and s.pitch_id and s.instructor_id for s in done), \
            f"AC-01 FAIL t={tid}: not all done sessions have venue"
        if require_multi_campus:
            assert len(campus_set) >= 2, f"AC-02 FAIL t={tid}: only {len(campus_set)} campus"
        ok(f"✅ AC-01{'AC-02' if require_multi_campus else ''} verified for tid={tid}  (campuses={len(campus_set)}, sessions={len(sessions)})")

    # ── MC-1: Group Knockout, 16 players (bootstrap U15 + U18), GK phase-split
    if len(_boot_players) >= 16:
        section("MC Demo: Group Knockout — Multi-Campus (16 players, phase-split)")
        t = create_tournament(
            "MC Demo: Group Knockout 2026",
            tt_id=tt_gk.id,
            participant_type="INDIVIDUAL",
            scoring_type="SCORE_BASED",
            ranking_direction="DESC",
        )
        _mc1_players = _boot_players[:16]
        enroll_individual_players(t.id, _mc1_players)
        transition(t.id, "ENROLLMENT_OPEN")
        transition(t.id, "ENROLLMENT_CLOSED")
        transition(t.id, "CHECK_IN_OPEN")
        if transition(t.id, "IN_PROGRESS"):
            db.expire_all()
            info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
            write_gk_game_results(t.id)
            seed_gk_knockout(t.id)
            _enrich_sessions_with_venue(t.id, gk_phase_split=True)
            _create_instructor_slots(t.id)
            if not calculate_rankings(t.id):
                seed_individual_rankings(t.id, _mc1_players)
            transition(t.id, "COMPLETED")
            distribute_rewards(t.id)
            _assert_mc_ac(t.id)
    else:
        print("⚠️  Not enough boot players for MC-1 (need 16)")

    # ── MC-2: H2H League, 12 players (bootstrap), 6-campus round-robin
    if len(_boot_players) >= 12:
        import json as _json_mc2

        def _write_h2h_all_sessions(tid: int) -> int:
            """Write game_results to ALL sessions with >=2 participant_user_ids."""
            db.expire_all()
            sessions = db.query(SessionModel).filter(SessionModel.semester_id == tid).all()
            written = 0
            for sess in sessions:
                pids = list(getattr(sess, 'participant_user_ids', None) or [])
                if len(pids) < 2:
                    continue
                sess.game_results = _json_mc2.dumps({
                    "match_format": "HEAD_TO_HEAD",
                    "participants": [
                        {"user_id": pids[0], "score": 3.0, "result": "win"},
                        {"user_id": pids[1], "score": 0.0, "result": "loss"},
                    ],
                })
                sess.session_status = "completed"
                written += 1
            db.commit()
            info(f"H2H game_results written: {written} sessions")
            return written

        section("MC Demo: H2H League — Multi-Campus (12 players)")
        t = create_tournament(
            "MC Demo: H2H League 2026",
            tt_id=tt_league.id,
            participant_type="INDIVIDUAL",
            scoring_type="SCORE_BASED",
            ranking_direction="DESC",
        )
        _mc2_players = _boot_players[:12]
        enroll_individual_players(t.id, _mc2_players)
        transition(t.id, "ENROLLMENT_OPEN")
        transition(t.id, "ENROLLMENT_CLOSED")
        transition(t.id, "CHECK_IN_OPEN")
        if transition(t.id, "IN_PROGRESS"):
            db.expire_all()
            info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
            _write_h2h_all_sessions(t.id)
            _enrich_sessions_with_venue(t.id)
            _create_instructor_slots(t.id)
            if not calculate_rankings(t.id):
                seed_individual_rankings(t.id, _mc2_players)
            transition(t.id, "COMPLETED")
            distribute_rewards(t.id)
            _assert_mc_ac(t.id)
    else:
        print("⚠️  Not enough boot players for MC-2 (need 12)")

    # ── MC-3: IR, 12 players, 6 cups across 6 campuses
    if len(_boot_players) >= 12:
        section("MC Demo: IR — Multi-Campus (12 players, 6 cups)")
        t = create_tournament(
            "MC Demo: IR 2026",
            tt_id=None,
            participant_type="INDIVIDUAL",
            scoring_type="SCORE_BASED",
            ranking_direction="DESC",
        )
        _mc3_players = _boot_players[:12]
        enroll_individual_players(t.id, _mc3_players)
        transition(t.id, "ENROLLMENT_OPEN")
        transition(t.id, "ENROLLMENT_CLOSED")
        transition(t.id, "CHECK_IN_OPEN")
        if transition(t.id, "IN_PROGRESS"):
            db.expire_all()
            info(f"Sessions: {db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()}")
            submit_individual_results(t.id, _mc3_players)
            _enrich_sessions_with_venue(t.id)
            _create_instructor_slots(t.id)
            calculate_rankings(t.id)
            transition(t.id, "COMPLETED")
            distribute_rewards(t.id)
            _assert_mc_ac(t.id, require_multi_campus=False)  # AC-06: venue shown, multi-campus not required for IR
    else:
        print("⚠️  Not enough boot players for MC-3 (need 12)")


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

db.expire_all()
section("FINAL SUMMARY — 88 Demo Events (A-K × 8 states + 3 MC)")

_FORMATS = [
    ("A",    "Demo: H2H League —",               "TEAM"),
    ("B",    "Demo: Knockout —",                 "TEAM"),
    ("C",    "Demo: Group Knockout —",           "INDIVIDUAL"),
    ("D",    "Demo: Swiss —",                   "INDIVIDUAL"),
    ("E",    "Demo: IR —",                      "INDIVIDUAL"),
    ("F",    "Demo: H2H League IND —",          "INDIVIDUAL"),
    ("G",    "Demo: Knockout IND —",            "INDIVIDUAL"),
    ("H",    "Demo: Group Knockout TEAM —",     "TEAM"),
    ("I",    "Demo: IR TEAM —",                 "TEAM"),
    ("J",    "Demo: IR Time —",                 "INDIVIDUAL"),
    ("K",    "Demo: IR Placement —",            "INDIVIDUAL"),
    ("L",    "Demo: Virtual Quiz L —",          "INDIVIDUAL"),
    ("MC-1", "MC Demo: Group Knockout 2026",    "INDIVIDUAL"),
    ("MC-2", "MC Demo: H2H League 2026",        "INDIVIDUAL"),
    ("MC-3", "MC Demo: IR 2026",                "INDIVIDUAL"),
]

total = 0
for fmt_id, prefix, pt in _FORMATS:
    rows = (
        db.query(Semester)
        .filter(Semester.name.like(f"{prefix}%"))
        .order_by(Semester.id)
        .all()
    )
    print(f"\n  [{fmt_id}] {prefix.strip()}")
    for row in rows:
        sessions = db.query(SessionModel).filter(SessionModel.semester_id == row.id).count()
        rankings = db.query(TournamentRanking).filter(TournamentRanking.tournament_id == row.id).count()
        print(
            f"    id={row.id:4d}  {row.tournament_status:22s}"
            f"  sessions={sessions:3d}  rankings={rankings:3d}  {row.name}"
        )
        total += 1

_inv_total  = db.query(InvitationCode).count()
_inv_used   = db.query(InvitationCode).filter(InvitationCode.is_used.is_(True)).count()
_inv_unused = _inv_total - _inv_used
_inv_cred   = db.query(_func.sum(InvitationCode.bonus_credits)).filter(
    InvitationCode.is_used.is_(True)
).scalar() or 0

print(f"\n  Total events seeded: {total}")
print(f"  Demo Club:  {demo_club.name}  (id={demo_club.id})")
print(f"  Demo Teams: {[t.name for t in demo_teams]}")
print(f"  Demo Players: {len(all_demo_players)} total (4 per team)")
print(f"  InvitationCodes: {_inv_total} total "
      f"({_inv_used} used / {_inv_unused} unused), "
      f"{_inv_cred:,} credits mapped")
print()
print("  View events : http://localhost:8000/admin/promotion-events")
print("  Public page : http://localhost:8000/events/<id>")
print("="*64)

db.close()
