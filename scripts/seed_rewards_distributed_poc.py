#!/usr/bin/env python3
"""
Seed PoC: REWARDS_DISTRIBUTED via pure API/service lifecycle flow.

Diagnostics-first: every lifecycle transition is logged with status-before,
target-status, HTTP code, and guard detail on failure.

Credential policy (from scripts/bootstrap_clean.py):
  admin@lfa.com          → admin123
  instructor@lfa.com     → instructor123
  lfa-adult-*.@lfa.com   → Bootstrap#123   (36 bootstrap players, 12 adult)

distribute-rewards-v2 (rewards_v2.py:92): sets tournament_status = "REWARDS_DISTRIBUTED"
directly inside the endpoint — no separate lifecycle PATCH needed.

Hidden dependencies resolved before implementation:
  1. Instructor LFA_COACH level=5 → age_group must be AMATEUR (requires 5, not PRO=7)
  2. Session check-in requires session.instructor_id == current_user.id
     → session generator sets instructor_id = master_instructor_id (fallback)
  3. Session completion: PATCH /sessions/{sid}/head-to-head-results sets session_status="completed"
     AND triggers KnockoutProgressionService for round advancement
  4. Tournament sessions endpoint does not expose session_status; use result_submitted proxy

Prerequisites: PYTHONPATH=. python scripts/bootstrap_clean.py
Run:          PYTHONPATH=. python scripts/seed_rewards_distributed_poc.py
"""
import os
import sys
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(level=logging.WARNING)  # suppress SQLAlchemy noise

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session as OrmSession  # noqa: E402

from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402

# ── Credentials (authoritative source: bootstrap_clean.py) ───────────────────
_ADMIN_EMAIL = "admin@lfa.com"
_ADMIN_PASS  = "admin123"
_INSTR_EMAIL = "instructor@lfa.com"
_INSTR_PASS  = "instructor123"
_PLAYER_PASS = "Bootstrap#123"

# ── ANSI colours ─────────────────────────────────────────────────────────────
_G = "\033[92m"   # green
_R = "\033[91m"   # red
_C = "\033[96m"   # cyan
_Y = "\033[93m"   # yellow
_GR = "\033[90m"  # grey
_B = "\033[1m"    # bold
_X = "\033[0m"    # reset


def _ok(msg):    print(f"{_G}  ✅ {msg}{_X}")
def _warn(msg):  print(f"{_Y}  ⚠  {msg}{_X}")
def _info(msg):  print(f"{_C}  ℹ  {msg}{_X}")
def _debug(msg): print(f"{_GR}     {msg}{_X}")


def _fail(msg):
    print(f"{_R}  ❌ {msg}{_X}")
    sys.exit(1)


# ── Auth ──────────────────────────────────────────────────────────────────────
def _login(client: TestClient, email: str, password: str) -> str:
    """POST /api/v1/auth/login with JSON body; returns JWT access_token."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    if resp.status_code != 200:
        _fail(f"Login failed for {email} (HTTP {resp.status_code}): {resp.text}")
    token = resp.json().get("access_token")
    if not token:
        _fail(f"No access_token in login response for {email}")
    return token


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Orchestration logging ─────────────────────────────────────────────────────
def _log(step: str, from_s: str | None, to_s: str, code: int, detail: str | None = None):
    ok = code in (200, 201)
    icon = "✅" if ok else "❌"
    colour = _G if ok else _R
    arrow = f"{from_s} → {to_s}" if from_s else f"→ {to_s}"
    print(f"{colour}  {icon} [{step}] {arrow}  HTTP {code}{_X}")
    if detail:
        print(f"{_GR}     GUARD: {detail}{_X}")


def _assert(resp, step: str, expected=(200, 201)) -> dict:
    if resp.status_code not in expected:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        _log(step, None, "FAIL", resp.status_code, str(detail))
        _fail(f"[{step}] Expected {expected}, got HTTP {resp.status_code}: {detail}")
    return resp.json()


# ── Fixture resolution ────────────────────────────────────────────────────────
def _preflight_check() -> None:
    """Verify bootstrap fixtures exist before making any API calls."""
    from app.models.user import User as UserModel
    from app.models.license import UserLicense

    db = SessionLocal()
    try:
        instr = db.query(UserModel).filter(UserModel.email == _INSTR_EMAIL).first()
        if not instr:
            _fail(f"Instructor {_INSTR_EMAIL} not in DB — run: PYTHONPATH=. python scripts/bootstrap_clean.py")

        coach_lic = (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id == instr.id,
                UserLicense.specialization_type == "LFA_COACH",
                UserLicense.is_active == True,  # noqa: E712
            )
            .first()
        )
        if not coach_lic:
            _fail(
                f"Instructor {_INSTR_EMAIL} has no active LFA_COACH license — "
                "run: PYTHONPATH=. python scripts/bootstrap_clean.py"
            )
        _debug(f"Preflight: LFA_COACH level={coach_lic.current_level} ✓")
    finally:
        db.close()


def _resolve_instructor(client: TestClient, admin_tok: str) -> int:
    resp = client.get("/api/v1/users/?role=instructor&size=100", headers=_h(admin_tok))
    data = _assert(resp, "resolve-instructor")
    for u in data.get("users", []):
        if u.get("email") == _INSTR_EMAIL:
            return u["id"]
    _fail(f"Instructor {_INSTR_EMAIL} not in DB — run bootstrap_clean.py")


def _resolve_campus(client: TestClient, admin_tok: str) -> int:
    resp = client.get("/api/v1/admin/campuses", headers=_h(admin_tok))
    data = _assert(resp, "resolve-campus")
    items = data if isinstance(data, list) else data.get("campuses", [])
    active = [c for c in items if c.get("is_active", True)]
    if not active:
        _fail("No active campus — run bootstrap_clean.py")
    return active[0]["id"]


def _resolve_players(client: TestClient, count: int = 4) -> list[dict]:
    """
    Return `count` adult bootstrap players with id + fresh JWT token.

    Uses direct DB query (not users API) because the users list endpoint raises
    Pydantic validation errors on .test-TLD emails that other seed scripts created.
    Player resolution is fixture setup, not lifecycle flow.
    """
    from app.models.user import User as UserModel, UserRole

    db = SessionLocal()
    try:
        adults = (
            db.query(UserModel)
            .filter(
                UserModel.role == UserRole.STUDENT,
                UserModel.email.like("lfa-adult-%@lfa.com"),
                UserModel.is_active == True,  # noqa: E712
            )
            .limit(count)
            .all()
        )
    finally:
        db.close()

    if len(adults) < count:
        _fail(
            f"Need {count} adult seed players, found {len(adults)} — run bootstrap_clean.py"
        )

    result = []
    for u in adults:
        tok = _login(client, u.email, _PLAYER_PASS)
        result.append({"id": u.id, "email": u.email, "token": tok})
    return result


# ── Lifecycle helpers ─────────────────────────────────────────────────────────
def _transition(
    client: TestClient, admin_tok: str, tid: int,
    from_s: str, to_s: str, reason: str = "PoC lifecycle step",
) -> dict:
    resp = client.patch(
        f"/api/v1/tournaments/{tid}/status",
        headers=_h(admin_tok),
        json={"new_status": to_s, "reason": reason},
    )
    detail = None
    if resp.status_code not in (200, 201):
        try:
            detail = resp.json().get("detail")
        except Exception:
            detail = resp.text
    _log(f"PATCH status → {to_s}", from_s, to_s, resp.status_code, detail)
    if resp.status_code not in (200, 201):
        _fail(f"Transition {from_s} → {to_s} failed: {detail}")
    return resp.json()


# ── Session management ────────────────────────────────────────────────────────
def _get_tournament_sessions(client: TestClient, tok: str, tid: int) -> list[dict]:
    """GET /api/v1/tournaments/{tid}/sessions — returns a plain list."""
    resp = client.get(f"/api/v1/tournaments/{tid}/sessions", headers=_h(tok))
    data = _assert(resp, f"GET /tournaments/{tid}/sessions")
    return data if isinstance(data, list) else data.get("sessions", [])


def _complete_session(
    client: TestClient,
    instr_tok: str,
    tid: int,
    session: dict,
) -> bool:
    """
    Check-in + HEAD_TO_HEAD result submission for a single session.
    Returns True if processed, False if participants not yet assigned (TBD round).
    """
    sid = session["id"]
    participants = session.get("participant_user_ids") or []
    if len(participants) < 2:
        _debug(f"Session {sid}: participants TBD (knockout progression pending), skipping")
        return False

    # ── Check-in (scheduled → in_progress) ───────────────────────────────────
    ci_resp = client.post(
        f"/api/v1/sessions/{sid}/check-in",
        headers=_h(instr_tok),
        json={},
    )
    if ci_resp.status_code in (200, 201):
        _debug(f"Session {sid}: checked in (→ in_progress)")
    else:
        # 400 = already in_progress (acceptable)
        _debug(f"Session {sid}: check-in HTTP {ci_resp.status_code} (may be in_progress already)")

    # ── Submit HEAD_TO_HEAD result (participants[0] wins 2–0) ─────────────────
    result_resp = client.patch(
        f"/api/v1/sessions/{sid}/head-to-head-results",
        headers=_h(instr_tok),
        json={
            "results": [
                {"user_id": participants[0], "score": 2},
                {"user_id": participants[1], "score": 0},
            ],
            "notes": "PoC deterministic result: participant[0] wins 2-0",
        },
    )

    detail = None
    if result_resp.status_code not in (200, 201):
        try:
            detail = result_resp.json().get("detail")
        except Exception:
            detail = result_resp.text

    _log(
        f"Session {sid} HEAD_TO_HEAD result",
        "in_progress", "completed",
        result_resp.status_code,
        detail,
    )
    if result_resp.status_code not in (200, 201):
        _fail(f"Session {sid} result submission failed: {detail}")

    progression = result_resp.json().get("knockout_progression")
    if progression:
        _debug(f"Session {sid}: knockout progression → {progression.get('message', progression)}")

    return True


def _complete_all_sessions(
    client: TestClient,
    instr_tok: str,
    tid: int,
) -> int:
    """
    Round-by-round session completion loop.
    Re-queries after each round to pick up knockout progression (next-round participants).
    Returns total sessions completed.
    """
    completed = 0
    for round_num in range(1, 11):  # safety cap: 10 rounds max
        sessions = _get_tournament_sessions(client, instr_tok, tid)
        pending = [s for s in sessions if not s.get("result_submitted")]
        if not pending:
            _info(f"All sessions completed after {round_num - 1} round pass(es)")
            break

        _info(f"Round pass {round_num}: {len(pending)} pending session(s)")
        made_progress = False
        for sess in pending:
            if _complete_session(client, instr_tok, tid, sess):
                completed += 1
                made_progress = True

        if not made_progress:
            _fail(
                f"Round pass {round_num}: no progress — {len(pending)} pending sessions "
                "all have empty participant_user_ids. Possible session generator issue."
            )
    return completed


# ── Verification ──────────────────────────────────────────────────────────────
def _verify_api(
    client: TestClient,
    admin_tok: str,
    instr_tok: str,
    tid: int,
    enrolled_count: int,
) -> None:
    print(f"\n{_B}═══ API-LEVEL VERIFICATION ═══════════════════════════════{_X}")

    # 1. Tournament detail
    resp = client.get(f"/api/v1/tournaments/{tid}", headers=_h(admin_tok))
    t = _assert(resp, "GET /tournaments/{tid}")
    t_status = t.get("tournament_status") or t.get("status")
    assert t_status == "REWARDS_DISTRIBUTED", \
        f"Tournament status: expected REWARDS_DISTRIBUTED, got {t_status}"
    _ok(f"GET /tournaments/{tid} → tournament_status = {t_status}")

    # 2. Rankings
    resp = client.get(f"/api/v1/tournaments/{tid}/rankings", headers=_h(admin_tok))
    rankings_body = _assert(resp, "GET /tournaments/{tid}/rankings")
    rank_list = rankings_body.get("rankings", [])
    assert len(rank_list) >= enrolled_count, \
        f"Rankings: expected ≥{enrolled_count}, got {len(rank_list)}"
    _ok(f"GET /tournaments/{tid}/rankings → {len(rank_list)} entries")

    # 3. Sessions — all completed
    sessions = _get_tournament_sessions(client, instr_tok, tid)
    match_sessions = [s for s in sessions if s.get("is_tournament_game")]
    completed = sum(1 for s in match_sessions if s.get("result_submitted"))
    assert completed == len(match_sessions) and len(match_sessions) > 0, \
        f"Sessions: expected all {len(match_sessions)} completed, got {completed}"
    _ok(f"GET /tournaments/{tid}/sessions → {completed}/{len(match_sessions)} MATCH sessions completed")

    # 4. Reward summary for top-ranked player (best-effort, non-blocking)
    if rank_list:
        uid = rank_list[0].get("user_id")
        if uid:
            resp = client.get(
                f"/api/v1/tournaments/{tid}/rewards/{uid}",
                headers=_h(admin_tok),
            )
            if resp.status_code == 200:
                _ok(f"GET /tournaments/{tid}/rewards/{uid} → reward summary OK")
            else:
                _warn(f"GET /tournaments/{tid}/rewards/{uid} → HTTP {resp.status_code} (non-blocking)")


def _verify_db(db: OrmSession, tid: int, enrolled_count: int) -> None:
    print(f"\n{_B}═══ DB-LEVEL VERIFICATION ════════════════════════════════{_X}")

    from app.models.semester import Semester
    from app.models.session import Session as SessionModel, EventCategory
    from app.models.tournament_ranking import TournamentRanking

    t = db.query(Semester).filter(Semester.id == tid).first()
    if t is None:
        _fail(f"Tournament {tid} not found in DB")

    # Guard 1: tournament_status
    assert t.tournament_status == "REWARDS_DISTRIBUTED", \
        f"DB tournament_status: expected REWARDS_DISTRIBUTED, got {t.tournament_status}"
    _ok(f"DB: tournament_status = {t.tournament_status}")

    # Guard 2: reward_policy_snapshot not NULL (SNAPSHOT_MISSING guard passed)
    assert t.reward_policy_snapshot is not None, \
        "DB: reward_policy_snapshot is NULL — SNAPSHOT_MISSING guard would block"
    _ok("DB: reward_policy_snapshot IS NOT NULL")

    # Guard 3: no incomplete auto-generated MATCH sessions (SESSIONS_INCOMPLETE guard passed)
    incomplete = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == tid,
            SessionModel.auto_generated == True,  # noqa: E712
            SessionModel.event_category == EventCategory.MATCH,
            SessionModel.session_status != "completed",
        )
        .count()
    )
    assert incomplete == 0, \
        f"DB: {incomplete} auto-generated MATCH session(s) not completed — SESSIONS_INCOMPLETE"
    _ok("DB: all auto-generated MATCH sessions have session_status='completed'")

    # Guard 4: TournamentRanking coverage (RANKINGS_INCOMPLETE guard passed)
    ranking_count = (
        db.query(TournamentRanking)
        .filter(TournamentRanking.tournament_id == tid)
        .count()
    )
    assert ranking_count >= enrolled_count, \
        f"DB: TournamentRanking count {ranking_count} < enrolled {enrolled_count}"
    _ok(f"DB: TournamentRanking count = {ranking_count} (≥ enrolled {enrolled_count})")

    # Guard 5: TournamentParticipation records (PARTICIPATION_RECORDS_MISSING guard passed)
    try:
        from app.models.tournament_achievement import TournamentParticipation
        part_count = (
            db.query(TournamentParticipation)
            .filter(TournamentParticipation.semester_id == tid)
            .count()
        )
        assert part_count > 0, \
            f"DB: TournamentParticipation count = 0 — PARTICIPATION_RECORDS_MISSING"
        _ok(f"DB: TournamentParticipation count = {part_count}")
    except ImportError:
        _warn("TournamentParticipation not importable — guard 5 skipped")


# ── Main orchestration ────────────────────────────────────────────────────────
def run() -> None:
    print(f"\n{_B}{'═' * 60}")
    print("  LFA — REWARDS_DISTRIBUTED Seed PoC")
    print("  Credential policy: scripts/bootstrap_clean.py")
    print(f"{'═' * 60}{_X}\n")

    _preflight_check()
    client = TestClient(app, raise_server_exceptions=True)

    # ── [0] Auth ──────────────────────────────────────────────────────────────
    print(f"{_B}[0] Authentication{_X}")
    admin_tok = _login(client, _ADMIN_EMAIL, _ADMIN_PASS)
    _ok(f"{_ADMIN_EMAIL}")
    instr_tok = _login(client, _INSTR_EMAIL, _INSTR_PASS)
    _ok(f"{_INSTR_EMAIL}")

    # ── [1] Fixture resolution ────────────────────────────────────────────────
    print(f"\n{_B}[1] Fixture Resolution{_X}")
    instr_id  = _resolve_instructor(client, admin_tok)
    _ok(f"instructor_id = {instr_id}")
    campus_id = _resolve_campus(client, admin_tok)
    _ok(f"campus_id = {campus_id}")
    players   = _resolve_players(client, count=4)
    _ok(f"players = {[p['email'] for p in players]}")

    # ── [2] Create tournament ─────────────────────────────────────────────────
    print(f"\n{_B}[2] Create Tournament — SEEKING_INSTRUCTOR{_X}")
    resp = client.post(
        "/api/v1/tournaments/ops/run-scenario",
        headers=_h(admin_tok),
        json={
            "scenario": "smoke_test",
            "player_count": 0,
            "max_players": 16,
            "tournament_format": "HEAD_TO_HEAD",
            "tournament_type_code": "knockout",
            "auto_generate_sessions": False,
            "simulation_mode": "manual",
            "age_group": "AMATEUR",       # AMATEUR requires level 5; bootstrap instructor = level 5
            "enrollment_cost": 0,         # zero cost → no credit fixtures needed
            "initial_tournament_status": "SEEKING_INSTRUCTOR",
            "dry_run": False,
            "confirmed": False,
            "campus_ids": [campus_id],
        },
    )
    data = _assert(resp, "ops/run-scenario")
    tid = data["tournament_id"]
    _log("ops/run-scenario", None, "SEEKING_INSTRUCTOR", resp.status_code)
    _ok(f"Tournament ID = {tid}, status = SEEKING_INSTRUCTOR, sessions = {data.get('session_count', 0)}")

    # ── [3] Assign + accept instructor ────────────────────────────────────────
    print(f"\n{_B}[3] Instructor Assignment{_X}")
    resp = client.post(
        f"/api/v1/tournaments/{tid}/direct-assign-instructor",
        headers=_h(admin_tok),
        json={"instructor_id": instr_id, "assignment_message": "PoC assignment"},
    )
    assign_data = _assert(resp, "direct-assign-instructor")
    _log("direct-assign-instructor", "SEEKING_INSTRUCTOR", "SEEKING_INSTRUCTOR", resp.status_code)
    _ok(f"Assigned (assignment_id = {assign_data.get('assignment_id')})")

    resp = client.post(
        f"/api/v1/tournaments/{tid}/instructor-assignment/accept",
        headers=_h(instr_tok),
        json={},
    )
    accept_data = _assert(resp, "instructor-assignment/accept")
    _log(
        "instructor-assignment/accept",
        "SEEKING_INSTRUCTOR", "INSTRUCTOR_CONFIRMED",
        resp.status_code,
    )
    _ok(f"Instructor accepted (status = {accept_data.get('status')})")

    # ── [4] Enrollment ────────────────────────────────────────────────────────
    print(f"\n{_B}[4] Enrollment Phase{_X}")
    _transition(client, admin_tok, tid, "INSTRUCTOR_CONFIRMED", "ENROLLMENT_OPEN")

    for p in players:
        resp = client.post(
            f"/api/v1/tournaments/{tid}/enroll",
            headers=_h(p["token"]),
            json={},
        )
        enroll = _assert(resp, f"enroll {p['email']}")
        _debug(f"Enrolled {p['email']} (status = {enroll.get('status', 'ok')})")
    _ok(f"Enrolled {len(players)} players")

    _transition(client, admin_tok, tid, "ENROLLMENT_OPEN", "ENROLLMENT_CLOSED")

    # ── [5] Schedule config ───────────────────────────────────────────────────
    print(f"\n{_B}[5] Schedule Config (guard: SCHEDULE_CONFIG_MISSING before CHECK_IN_OPEN){_X}")
    resp = client.patch(
        f"/api/v1/tournaments/{tid}/schedule-config",
        headers=_h(admin_tok),
        json={"match_duration_minutes": 90, "break_duration_minutes": 15, "parallel_fields": 1},
    )
    _assert(resp, "PATCH schedule-config")
    _log("PATCH schedule-config", None, "configured", resp.status_code)
    _ok("Schedule config set")

    # ── [6] CHECK_IN_OPEN ─────────────────────────────────────────────────────
    print(f"\n{_B}[6] CHECK_IN_OPEN (guard: SCHEDULE_CONFIG_MISSING){_X}")
    _transition(client, admin_tok, tid, "ENROLLMENT_CLOSED", "CHECK_IN_OPEN")

    # ── [7] Reward config ─────────────────────────────────────────────────────
    print(f"\n{_B}[7] Reward Config (guard: REWARD_CONFIG_MISSING before IN_PROGRESS){_X}")
    resp = client.post(
        f"/api/v1/tournaments/{tid}/reward-config",
        headers=_h(admin_tok),
        json={
            "skill_mappings": [
                {"skill": "speed", "weight": 1.0, "category": "PHYSICAL", "enabled": True}
            ]
        },
    )
    _assert(resp, "POST reward-config")
    _log("POST reward-config", None, "configured", resp.status_code)
    _ok("Reward config set")

    # ── [8] IN_PROGRESS — auto-generates sessions ─────────────────────────────
    print(f"\n{_B}[8] IN_PROGRESS (guard: REWARD_CONFIG_MISSING → auto-generates sessions){_X}")
    _transition(client, admin_tok, tid, "CHECK_IN_OPEN", "IN_PROGRESS")
    sessions_preview = _get_tournament_sessions(client, instr_tok, tid)
    _ok(f"Sessions auto-generated: {len(sessions_preview)} total")

    # ── [9] Complete all sessions round-by-round ──────────────────────────────
    print(f"\n{_B}[9] Session Completion (round-by-round, deterministic){_X}")
    _info("participant[0] always wins 2-0; knockout progression auto-advances winners")
    session_count = _complete_all_sessions(client, instr_tok, tid)
    _ok(f"Completed {session_count} session(s)")

    # ── [10] Calculate rankings ───────────────────────────────────────────────
    print(f"\n{_B}[10] Calculate Rankings{_X}")
    resp = client.post(
        f"/api/v1/tournaments/{tid}/calculate-rankings",
        headers=_h(instr_tok),
        json={},
    )
    _assert(resp, "POST calculate-rankings")
    _log("POST calculate-rankings", "IN_PROGRESS", "IN_PROGRESS", resp.status_code)
    _ok("Rankings calculated")

    # ── [11] COMPLETED ────────────────────────────────────────────────────────
    print(f"\n{_B}[11] COMPLETED (guards: SESSIONS_INCOMPLETE, RANKINGS_INCOMPLETE){_X}")
    _transition(client, admin_tok, tid, "IN_PROGRESS", "COMPLETED")

    # ── [12] Distribute rewards → REWARDS_DISTRIBUTED (auto-transition) ───────
    print(f"\n{_B}[12] Distribute Rewards (rewards_v2.py:92 → auto-transition){_X}")
    resp = client.post(
        f"/api/v1/tournaments/{tid}/distribute-rewards-v2",
        headers=_h(admin_tok),
        json={"tournament_id": tid, "force_redistribution": False},
    )
    dist = _assert(resp, "POST distribute-rewards-v2")
    _log("POST distribute-rewards-v2", "COMPLETED", "REWARDS_DISTRIBUTED", resp.status_code)
    _ok(
        f"Rewards distributed: {dist.get('total_participants')} participants — "
        f"{dist.get('message')}"
    )

    # ── Verification ──────────────────────────────────────────────────────────
    _verify_api(client, admin_tok, instr_tok, tid, enrolled_count=len(players))

    db = SessionLocal()
    try:
        _verify_db(db, tid, enrolled_count=len(players))
    finally:
        db.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{_B}{_G}{'═' * 60}")
    print("  ✅  REWARDS_DISTRIBUTED SEED POC — PASS")
    print(f"  Tournament ID : {tid}")
    print(f"  Sessions      : {session_count} completed")
    print(f"  Players       : {len(players)} enrolled")
    print(f"{'═' * 60}{_X}\n")


if __name__ == "__main__":
    run()
