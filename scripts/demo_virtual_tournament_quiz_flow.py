#!/usr/bin/env python3
"""
demo_virtual_tournament_quiz_flow.py

Bizonyítja, hogy a virtual tournament quiz flow valós user flow-ban is
működik: clean DB → seed → HTTP login → quiz take → submit → compare scores.

Futtatás:
    PYTHONPATH=. python scripts/demo_virtual_tournament_quiz_flow.py

Előfeltétel: a FastAPI szerver NEM fut (a script maga indítja el).
Ha már fut valami 8000-en, a script figyelmeztet.

Output:
  - Seed összefoglaló (tournament id, session id, quiz id, enrolled students)
  - URL-ek
  - Mindkét student quiz attempt eredménye (score, helyes/összes, pass/fail)
  - Side-by-side összehasonlítás
"""

import sys
import os
import time
import subprocess
import signal
import requests
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── DB imports ────────────────────────────────────────────────────────────────
from app.database import SessionLocal
from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.session import Session as SessionModel, SessionType
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.tournament_configuration import TournamentConfiguration
from app.models.game_configuration import GameConfiguration
from app.models.game_preset import GamePreset
from app.models.license import UserLicense
from app.models.quiz import (
    Quiz, QuizQuestion, QuizAnswerOption, QuizAttempt,
    SessionQuiz, QuizCategory, QuizDifficulty, QuestionType,
)
from app.core.security import get_password_hash

TZ = ZoneInfo("Europe/Budapest")
BASE_URL = "http://localhost:8000"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


# ══════════════════════════════════════════════════════════════════════════════
# Part 1 — DB Setup
# ══════════════════════════════════════════════════════════════════════════════

_TOURN_CODE   = "DEMO-VIRTUAL-QUIZ-2026"
_TOURN_NAME   = "Demo Virtual Tournament 2026"
_MEETING_LINK = "https://meet.example.com/demo-virtual-quiz"
_SESSION_TITLE = "Demo Virtual Session"

_STUDENT_1 = {
    "email": "rdias@manchestercity.com",
    "name": "Ruben Dias",
    "password": "TestPlayer2026",
    "dob": "1998-03-14",
}
_STUDENT_2 = {
    "email": "fresh.e2e@lfa.com",
    "name": "Fresh E2E Student",
    "password": "FreshE2E2026",
    "dob": "2000-05-20",
}

_QUIZ_TITLE = "Virtual Tactics Quiz"
_QUESTIONS = [
    {
        "text": "A virtual tournament quiz milyen session_type-on érhető el?",
        "options": [
            ("virtual",      True),
            ("on_site",      False),
            ("csak szombati", False),
        ],
        "explanation": "A quiz a virtual és hybrid session-öknél érhető el SemesterEnrollment alapján.",
    },
    {
        "text": "Mi a meeting_link mező célja a TournamentConfiguration-ban?",
        "options": [
            ("Meeting URL virtual/hybrid session-höz", True),
            ("Admin bejelentkező URL",                 False),
            ("Képfeltöltési végpont",                  False),
        ],
        "explanation": "A meeting_link a generátorokon keresztül propagálódik az összes virtual session-be.",
    },
    {
        "text": "Melyik route ellenőrzi a SemesterEnrollment-et quiz submission előtt?",
        "options": [
            ("/quizzes/{id}/submit fallback ág", True),
            ("Csak /sessions/{id}",              False),
            ("Egyik sem",                        False),
        ],
        "explanation": "A quiz submit route Booking hiányában SemesterEnrollment fallbacket használ.",
    },
]


def _get_or_create_user(db, spec, credit_balance=1000):
    from datetime import date as date_type
    u = db.query(User).filter(User.email == spec["email"]).first()
    if not u:
        dob_parts = spec["dob"].split("-")
        dob = date_type(int(dob_parts[0]), int(dob_parts[1]), int(dob_parts[2]))
        u = User(
            name=spec["name"],
            email=spec["email"],
            password_hash=get_password_hash(spec["password"]),
            role=UserRole.STUDENT,
            is_active=True,
            date_of_birth=dob,
            credit_balance=credit_balance,
        )
        db.add(u)
        db.flush()
        print(f"  created student {spec['email']}")
    else:
        u.credit_balance = credit_balance
        u.is_active = True
        print(f"  updated student {spec['email']}")
    return u


def _get_or_create_license(db, user):
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if not lic:
        from datetime import datetime, timezone as tz_
        lic = UserLicense(
            user_id=user.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            onboarding_completed=True,
            started_at=datetime.now(tz_.utc),
        )
        db.add(lic)
        db.flush()
    else:
        lic.onboarding_completed = True
    return lic


def _cleanup(db):
    """Idempotent cleanup of previous demo run (correct FK cascade order)."""
    from app.models.quiz import QuizUserAnswer, QuizAnswerOption as QAO

    old_quiz = db.query(Quiz).filter(Quiz.title == _QUIZ_TITLE).first()
    old = db.query(Semester).filter(Semester.code == _TOURN_CODE).first()

    if old_quiz:
        # 1. delete user answers for all attempts of this quiz
        attempt_ids = [a.id for a in db.query(QuizAttempt).filter(QuizAttempt.quiz_id == old_quiz.id).all()]
        if attempt_ids:
            db.query(QuizUserAnswer).filter(QuizUserAnswer.attempt_id.in_(attempt_ids)).delete(synchronize_session="fetch")
        db.query(QuizAttempt).filter(QuizAttempt.quiz_id == old_quiz.id).delete()
        db.query(SessionQuiz).filter(SessionQuiz.quiz_id == old_quiz.id).delete()
        for q in db.query(QuizQuestion).filter(QuizQuestion.quiz_id == old_quiz.id).all():
            db.query(QAO).filter(QAO.question_id == q.id).delete()
        db.query(QuizQuestion).filter(QuizQuestion.quiz_id == old_quiz.id).delete()
        db.delete(old_quiz)

    if old:
        db.query(SessionModel).filter(SessionModel.semester_id == old.id).delete()
        db.query(SemesterEnrollment).filter(SemesterEnrollment.semester_id == old.id).delete()
        db.query(TournamentConfiguration).filter(TournamentConfiguration.semester_id == old.id).delete()
        db.delete(old)

    if old or old_quiz:
        db.commit()
        print("  cleaned up previous demo data")


def seed_db():
    """Create virtual tournament + quiz + 2 enrolled students. Returns (tourn_id, session_id, quiz_id)."""
    db = SessionLocal()
    try:
        print(f"\n{BOLD}▶ DB Seed{RESET}")
        _cleanup(db)

        now = datetime.now(TZ)

        # ── Tournament ────────────────────────────────────────────────────────
        tourn = Semester(
            code=_TOURN_CODE,
            name=_TOURN_NAME,
            status=SemesterStatus.ONGOING,
            semester_category=SemesterCategory.TOURNAMENT,
            start_date=now.date(),
            end_date=(now + timedelta(days=30)).date(),
            focus_description="E2E demo: virtual tournament quiz flow",
        )
        db.add(tourn)
        db.flush()

        cfg = TournamentConfiguration(
            semester_id=tourn.id,
            session_type_config="virtual",
            meeting_link=_MEETING_LINK,
            sessions_generated=True,
            sessions_generated_at=now,
        )
        db.add(cfg)
        db.flush()

        # ── GameConfiguration (required: defines skill rules) ─────────────────
        preset = db.query(GamePreset).filter(GamePreset.code == "outfield_default").first()
        if preset:
            db.add(GameConfiguration(semester_id=tourn.id, game_preset_id=preset.id))
            db.flush()

        # ── Session (started 2 hours ago → quiz accessible) ───────────────────
        session_start = now - timedelta(hours=2)
        sess = SessionModel(
            title=_SESSION_TITLE,
            semester_id=tourn.id,
            session_type=SessionType.virtual,
            meeting_link=_MEETING_LINK,
            date_start=session_start,
            date_end=session_start + timedelta(hours=1),
            capacity=50,
            base_xp=50,
        )
        db.add(sess)
        db.flush()

        # ── Quiz (3 questions, passing_score=0.6 → 60%) ───────────────────────
        quiz = Quiz(
            title=_QUIZ_TITLE,
            description="A virtual tournament quiz flow bizonyítása.",
            category=QuizCategory.LESSON,
            difficulty=QuizDifficulty.EASY,
            passing_score=0.6,      # 60% threshold (2 out of 3)
            time_limit_minutes=15,
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
                explanation=q_spec["explanation"],
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

        db.flush()

        # ── SessionQuiz link ──────────────────────────────────────────────────
        db.add(SessionQuiz(
            session_id=sess.id,
            quiz_id=quiz.id,
            is_required=True,
            max_attempts=3,
        ))

        # ── Students + licenses + enrollments ─────────────────────────────────
        s1 = _get_or_create_user(db, _STUDENT_1, credit_balance=1000)
        lic1 = _get_or_create_license(db, s1)
        db.add(SemesterEnrollment(
            user_id=s1.id, semester_id=tourn.id,
            user_license_id=lic1.id,
            request_status=EnrollmentStatus.APPROVED,
            is_active=True,
            enrolled_at=now,
        ))

        s2 = _get_or_create_user(db, _STUDENT_2, credit_balance=500)
        lic2 = _get_or_create_license(db, s2)
        db.add(SemesterEnrollment(
            user_id=s2.id, semester_id=tourn.id,
            user_license_id=lic2.id,
            request_status=EnrollmentStatus.APPROVED,
            is_active=True,
            enrolled_at=now,
        ))

        db.commit()

        print(f"  tournament id={tourn.id}  code={_TOURN_CODE}")
        print(f"  session    id={sess.id}   started {session_start.strftime('%H:%M')} (2h ago)")
        print(f"  quiz       id={quiz.id}   '{_QUIZ_TITLE}' (3 q, pass≥60%)")
        print(f"  student 1  id={s1.id}   {_STUDENT_1['email']}")
        print(f"  student 2  id={s2.id}   {_STUDENT_2['email']}")

        return tourn.id, sess.id, quiz.id

    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Part 2 — Server lifecycle
# ══════════════════════════════════════════════════════════════════════════════

_server_proc = None


def start_server():
    global _server_proc
    # Check if something is already on 8000
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=2)
        if r.status_code == 200:
            print(f"\n{YELLOW}  server already running on :8000 — using it{RESET}")
            return
    except Exception:
        pass

    print(f"\n{BOLD}▶ Starting FastAPI server{RESET}")
    _server_proc = subprocess.Popen(
        ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    # Wait up to 30s for server ready
    for _ in range(30):
        time.sleep(1)
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=2)
            if r.status_code == 200:
                print(f"  server ready ✅")
                return
        except Exception:
            pass

    raise RuntimeError("Server failed to start within 30s")


def stop_server():
    global _server_proc
    if _server_proc:
        _server_proc.send_signal(signal.SIGTERM)
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
        _server_proc = None


# ══════════════════════════════════════════════════════════════════════════════
# Part 3 — HTTP helpers
# ══════════════════════════════════════════════════════════════════════════════

def _parse_quiz_form(html):
    """
    Parse quiz take page HTML via regex.
    Returns (attempt_id_str, questions_dict) where questions_dict is
    OrderedDict: radio_name → [value1, value2, ...] in DOM order.
    """
    # attempt_id hidden input
    m = re.search(r'name=["\']attempt_id["\'][^>]+value=["\'](\d+)["\']', html)
    if not m:
        m = re.search(r'value=["\'](\d+)["\'][^>]+name=["\']attempt_id["\']', html)
    attempt_id = m.group(1) if m else None

    # All radio input tags (handle any attribute order)
    radio_tags = re.findall(r'<input\b[^>]+type=["\']radio["\'][^>]*/?>', html, re.I)
    questions: dict[str, list[str]] = {}
    for tag_str in radio_tags:
        name_m  = re.search(r'\bname=["\']([^"\']+)["\']', tag_str)
        value_m = re.search(r'\bvalue=["\']([^"\']+)["\']', tag_str)
        if name_m and value_m:
            n, v = name_m.group(1), value_m.group(1)
            questions.setdefault(n, []).append(v)

    return attempt_id, questions


def http_login(email, password):
    """Login via POST /login, return authenticated requests.Session."""
    s = requests.Session()
    # Allow redirect following but capture the cookie
    resp = s.post(
        f"{BASE_URL}/login",
        data={"email": email, "password": password, "next": ""},
        allow_redirects=True,
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed for {email}: HTTP {resp.status_code}")
    if "access_token" not in s.cookies:
        raise RuntimeError(f"No access_token cookie after login for {email}")
    return s


def check_public_event_page(tourn_id):
    """GET /events/{id} and check for 💻 Online badge (no auth needed)."""
    r = requests.get(f"{BASE_URL}/events/{tourn_id}", timeout=10)
    assert r.status_code == 200, f"/events/{tourn_id} returned {r.status_code}"
    assert "Internal Server Error" not in r.text, "/events returned 500"
    online_badge = "💻 Online" in r.text
    return online_badge


def check_session_list(sess_http, session_id):
    """GET /sessions and verify the virtual session card has Join Meeting button."""
    r = sess_http.get(f"{BASE_URL}/sessions", timeout=10)
    assert r.status_code == 200
    has_session = _SESSION_TITLE in r.text
    has_join = "Join Meeting" in r.text
    return has_session, has_join


def take_and_submit_quiz(sess_http, quiz_id, session_id, correct_indices):
    """
    Take quiz and submit.
    correct_indices: list of per-question option index to select (0=first=correct).
    Returns (score_pct, correct, total, passed, attempt_id).
    """
    # 1) GET take page
    r = sess_http.get(
        f"{BASE_URL}/quizzes/{quiz_id}/take?session_id={session_id}",
        timeout=10,
    )
    assert r.status_code == 200, f"quiz take page returned {r.status_code}: {r.text[:200]}"

    # 2) Parse attempt_id + radio options via regex
    attempt_id, q_options = _parse_quiz_form(r.text)
    assert attempt_id, "attempt_id hidden input not found in quiz take page"
    assert len(q_options) == 3, f"Expected 3 questions, found {len(q_options)}: {list(q_options.keys())}"

    # 3) Build answer payload
    data = {
        "attempt_id": attempt_id,
        "time_spent": "3",
        "session_id": str(session_id),
    }
    for i, (q_name, opts) in enumerate(q_options.items()):
        chosen_idx = correct_indices[i]
        data[q_name] = opts[chosen_idx]

    # 5) POST submit — include CSRF token from cookie as header
    csrf_token = sess_http.cookies.get("csrf_token", "")
    r2 = sess_http.post(
        f"{BASE_URL}/quizzes/{quiz_id}/submit",
        data=data,
        headers={"X-CSRF-Token": csrf_token},
        timeout=10,
        allow_redirects=True,
    )
    if r2.status_code != 200:
        print(f"  DEBUG submit body: {r2.text[:800]}")
    assert r2.status_code == 200, f"quiz submit returned {r2.status_code}"
    assert "Internal Server Error" not in r2.text

    # 6) Parse result
    score_m  = re.search(r'stat-value[^>]*>([\d.]+)%', r2.text)
    correct_m = re.search(r'Correct Answers.*?stat-value[^>]*>(\d+)\s*/\s*(\d+)', r2.text, re.S)
    passed   = "result-title passed" in r2.text

    score_pct = float(score_m.group(1)) if score_m else -1.0
    correct   = int(correct_m.group(1)) if correct_m else -1
    total     = int(correct_m.group(2)) if correct_m else -1

    return score_pct, correct, total, passed, int(attempt_id)


def check_review_page(sess_http, attempt_id):
    """GET /quizzes/attempts/{id}/review → returns HTTP 200."""
    r = sess_http.get(f"{BASE_URL}/quizzes/attempts/{attempt_id}/review", timeout=10)
    return r.status_code == 200 and "Internal Server Error" not in r.text


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'='*64}")
    print(f"{BOLD}Virtual Tournament Quiz Flow — End-to-End Demo{RESET}")
    print(f"{'='*64}")

    # ── 1. Seed ──────────────────────────────────────────────────────────────
    tourn_id, session_id, quiz_id = seed_db()

    # ── 2. Server ─────────────────────────────────────────────────────────────
    start_server()

    try:
        # ── 3. Public event page ─────────────────────────────────────────────
        print(f"\n{BOLD}▶ C1 — Public event page{RESET}")
        url_event = f"{BASE_URL}/events/{tourn_id}"
        online_badge = check_public_event_page(tourn_id)
        status = f"{GREEN}✅ 💻 Online badge visible{RESET}" if online_badge else f"{RED}❌ badge MISSING{RESET}"
        print(f"  GET {url_event}")
        print(f"  {status}")
        assert online_badge, "💻 Online badge missing from public event page!"

        # ── 4. Student 1 — all correct (3/3 = 100%) ──────────────────────────
        print(f"\n{BOLD}▶ C2 — Student 1 quiz (3/3 correct → 100%){RESET}")
        print(f"  login  {_STUDENT_1['email']}")
        s1_http = http_login(_STUDENT_1["email"], _STUDENT_1["password"])
        print(f"  {GREEN}✅ login success{RESET}")

        # Check sessions list
        has_session, has_join = check_session_list(s1_http, session_id)
        print(f"  GET {BASE_URL}/sessions")
        sess_ok = f"{GREEN}✅{RESET}" if has_session else f"{RED}❌{RESET}"
        join_ok = f"{GREEN}✅ Join Meeting visible{RESET}" if has_join else f"{RED}❌ Join Meeting missing{RESET}"
        print(f"  session visible: {sess_ok}   {join_ok}")

        # Take + submit (all 3 correct: index 0 for each)
        url_quiz = f"{BASE_URL}/quizzes/{quiz_id}/take?session_id={session_id}"
        print(f"  GET {url_quiz}")
        score1, correct1, total1, passed1, attempt1_id = take_and_submit_quiz(
            s1_http, quiz_id, session_id, correct_indices=[0, 0, 0]  # all correct
        )
        url_submit = f"{BASE_URL}/quizzes/{quiz_id}/submit"
        result1 = f"{GREEN}✅ PASSED{RESET}" if passed1 else f"{RED}❌ FAILED{RESET}"
        print(f"  POST {url_submit}")
        print(f"  Score: {score1:.1f}%  ({correct1}/{total1} correct)  → {result1}")

        # Review page
        url_review1 = f"{BASE_URL}/quizzes/attempts/{attempt1_id}/review"
        review1_ok = check_review_page(s1_http, attempt1_id)
        r1_status = f"{GREEN}✅ review accessible{RESET}" if review1_ok else f"{RED}❌ review failed{RESET}"
        print(f"  GET {url_review1}")
        print(f"  {r1_status}")

        # ── 5. Student 2 — 1/3 correct (33.3% → FAIL) ────────────────────────
        print(f"\n{BOLD}▶ C3 — Student 2 quiz (1/3 correct → 33.3%, FAIL){RESET}")
        print(f"  login  {_STUDENT_2['email']}")
        s2_http = http_login(_STUDENT_2["email"], _STUDENT_2["password"])
        print(f"  {GREEN}✅ login success{RESET}")

        print(f"  GET {url_quiz}")
        # Only Q1 correct (index 0), Q2 and Q3 wrong (index 1)
        score2, correct2, total2, passed2, attempt2_id = take_and_submit_quiz(
            s2_http, quiz_id, session_id, correct_indices=[0, 1, 1]  # Q1 correct, Q2+Q3 wrong
        )
        print(f"  POST {url_submit}")
        result2 = f"{GREEN}✅ PASSED{RESET}" if passed2 else f"{RED}❌ FAILED{RESET}" + f" {YELLOW}(Retry Quiz available){RESET}"
        print(f"  Score: {score2:.1f}%  ({correct2}/{total2} correct)  → {result2}")

        url_review2 = f"{BASE_URL}/quizzes/attempts/{attempt2_id}/review"
        review2_ok = check_review_page(s2_http, attempt2_id)
        r2_status = f"{GREEN}✅ review accessible{RESET}" if review2_ok else f"{RED}❌ review failed{RESET}"
        print(f"  GET {url_review2}")
        print(f"  {r2_status}")

        # ── 6. Summary ────────────────────────────────────────────────────────
        print(f"\n{'='*64}")
        print(f"{BOLD}Summary — Virtual Tournament Quiz Results{RESET}")
        print(f"{'='*64}")
        print(f"  Tournament:  {_TOURN_NAME}  (id={tourn_id})")
        print(f"  Session:     {_SESSION_TITLE}       (id={session_id})")
        print(f"  Quiz:        {_QUIZ_TITLE}  (id={quiz_id})")
        print(f"  Meeting:     {_MEETING_LINK}")
        print()
        print(f"  {'Student':<30} {'Score':>7}  {'Correct':>7}  {'Result':>8}")
        print(f"  {'-'*57}")

        def _result_str(passed):
            return "PASSED ✅" if passed else "FAILED ❌"

        print(f"  {_STUDENT_1['email']:<30} {score1:>6.1f}%  {correct1:>3}/{total1:<3}   {_result_str(passed1)}")
        print(f"  {_STUDENT_2['email']:<30} {score2:>6.1f}%  {correct2:>3}/{total2:<3}   {_result_str(passed2)}")
        print()
        print(f"  Passing threshold: 60%  (≥2/3 correct)")
        print()
        print(f"  URLs:")
        print(f"    Public event:  {url_event}")
        print(f"    Quiz take:     {url_quiz}")
        print(f"    S1 review:     {url_review1}")
        print(f"    S2 review:     {url_review2}")
        print()
        print(f"  Credentials:")
        print(f"    Student 1:  {_STUDENT_1['email']} / {_STUDENT_1['password']}")
        print(f"    Student 2:  {_STUDENT_2['email']} / {_STUDENT_2['password']}")
        print()

        # Final assertion — all checks must pass
        all_ok = (
            online_badge
            and has_session
            and has_join
            and passed1
            and not passed2
            and review1_ok
            and review2_ok
        )
        if all_ok:
            print(f"{GREEN}{BOLD}✅ All checks passed — virtual tournament quiz flow működik valós user flow-ban is.{RESET}")
        else:
            print(f"{RED}{BOLD}❌ Some checks FAILED — see above for details.{RESET}")
            sys.exit(1)

    finally:
        stop_server()


if __name__ == "__main__":
    main()
