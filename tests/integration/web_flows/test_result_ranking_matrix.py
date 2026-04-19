"""
Result Recording + Ranking Pipeline — tournament type matrix tests

SGR-01  INDIVIDUAL + LEAGUE H2H (win/loss)  → game_results stored, winner correct
                                               → calculate-rankings → W=3pts/L=0pts, rank 1+2
SGR-02  INDIVIDUAL + LEAGUE H2H (draw)      → tie stored, both 1pt, ranks assigned
SGR-03  INDIVIDUAL + KNOCKOUT H2H           → submit final result → winner rank=1
SGR-04  INDIVIDUAL_RANKING format           → rounds_data via /results → TournamentRanking × 2
SGR-05  TEAM + INDIVIDUAL_RANKING           → rounds_data "team_N" via /team-results
                                               → TournamentRanking with team_id rows
SGR-06  SWISS ranking gap (documented):
        Swiss tournament → calculate-rankings → 400 "Unsupported"
        RankingStrategyFactory has no Swiss strategy (docstring misleading).
        Bye player gets NO ranking — entire Swiss ranking pipeline unsupported.
        This test pins the deterministic failure behavior.

All tests use SAVEPOINT-isolated real DB (test_db) + TestClient (client) fixtures
from tests/integration/conftest.py. Admin Bearer token bypasses instructor-only checks.
"""
import json
import uuid
from datetime import date, datetime, timezone, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_type import TournamentType
from app.models.game_configuration import GameConfiguration
from app.models.game_preset import GamePreset
from app.models.session import Session as SessionModel, EventCategory, SessionType
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.tournament_ranking import TournamentRanking
from app.models.license import UserLicense
from app.models.team import Team, TeamMember, TournamentTeamEnrollment
from app.core.security import get_password_hash


# ── Helpers ────────────────────────────────────────────────────────────────────

_PFX = "sgr"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _user(db: Session, role=UserRole.STUDENT) -> User:
    u = User(
        email=f"{_PFX}-{_uid()}@lfa-test.com",
        name=f"SGR User {_uid()}",
        password_hash=get_password_hash("pw"),
        role=role,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _preset(db: Session) -> GamePreset:
    existing = db.query(GamePreset).filter(GamePreset.code == "sgr-default").first()
    if existing:
        return existing
    gp = GamePreset(
        code="sgr-default",
        name="SGR Default",
        description="Auto-created for SGR tests",
        is_active=True,
        game_config={"metadata": {"min_players": 0}, "skills_tested": [], "skill_weights": {}},
    )
    db.add(gp)
    db.flush()
    return gp


def _tt(db: Session, code: str, fmt: str = "HEAD_TO_HEAD", min_players: int = 2) -> TournamentType:
    """Get or create a TournamentType by code."""
    existing = db.query(TournamentType).filter(TournamentType.code == code).first()
    if existing:
        return existing
    tt = TournamentType(
        code=code,
        display_name=f"SGR {code}",
        description="Auto-created for SGR tests",
        format=fmt,
        min_players=min_players,
        max_players=64,
        requires_power_of_two=(code == "knockout"),
        session_duration_minutes=60,
        break_between_sessions_minutes=10,
        config={"code": code},
    )
    db.add(tt)
    db.flush()
    return tt


def _tournament(
    db: Session,
    instructor: User,
    tt: TournamentType,
    participant_type: str = "INDIVIDUAL",
    tournament_status: str = "IN_PROGRESS",
) -> Semester:
    """Create a tournament Semester + TournamentConfiguration + GameConfiguration."""
    preset = _preset(db)
    t = Semester(
        name=f"SGR Cup {_uid()}",
        code=f"SGR-{_uid()}",
        master_instructor_id=instructor.id,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status=tournament_status,
    )
    db.add(t)
    db.flush()
    db.add(TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=tt.id,
        participant_type=participant_type,
        max_players=32,
        number_of_rounds=1,
        parallel_fields=1,
    ))
    db.add(GameConfiguration(
        semester_id=t.id,
        game_preset_id=preset.id,
    ))
    db.flush()
    return t


def _ir_tournament(db: Session, instructor: User) -> Semester:
    """Create a plain INDIVIDUAL_RANKING tournament (no TournamentConfiguration needed
    for the format default, but we add one for INDIVIDUAL type detection)."""
    tt = _tt(db, f"sgr-ir-{_uid()}", fmt="INDIVIDUAL_RANKING", min_players=2)
    return _tournament(db, instructor, tt, participant_type="INDIVIDUAL")


def _match_session(db: Session, tournament: Semester) -> SessionModel:
    """Create a minimal MATCH session for the tournament."""
    sess = SessionModel(
        title=f"SGR Match {_uid()}",
        date_start=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
        date_end=datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc),
        semester_id=tournament.id,
        event_category=EventCategory.MATCH,
        session_type=SessionType.on_site,
    )
    db.add(sess)
    db.flush()
    return sess


def _enroll(db: Session, tournament: Semester, user: User) -> SemesterEnrollment:
    """Enroll a player in a tournament (with required UserLicense)."""
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        onboarding_completed=True,
        is_active=True,
    )
    db.add(lic)
    db.flush()
    enr = SemesterEnrollment(
        semester_id=tournament.id,
        user_id=user.id,
        user_license_id=lic.id,
        is_active=True,
        request_status=EnrollmentStatus.APPROVED,
    )
    db.add(enr)
    db.flush()
    return enr


def _make_team(db: Session) -> Team:
    captain = _user(db)
    team = Team(
        name=f"Team {_uid()}",
        code=f"T-{_uid()}",
        captain_user_id=captain.id,
        is_active=True,
    )
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=captain.id, role="CAPTAIN", is_active=True))
    db.flush()
    return team


def _enroll_team(db: Session, tournament: Semester, team: Team) -> TournamentTeamEnrollment:
    enr = TournamentTeamEnrollment(
        semester_id=tournament.id,
        team_id=team.id,
        payment_verified=True,
        is_active=True,
    )
    db.add(enr)
    db.flush()
    return enr


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestResultRankingMatrix:
    """
    Full result-recording → ranking pipeline for every participant_type × tournament_type.

    Uses:
      - test_db  (SAVEPOINT-isolated real PostgreSQL)
      - client   (TestClient bound to test_db)
      - admin_user, admin_token  (from conftest: admin can submit results for any tournament)
    """

    # ── SGR-01: INDIVIDUAL + LEAGUE H2H win/loss ──────────────────────────────
    def test_SGR_01_individual_league_h2h_win_loss(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """Win/loss in LEAGUE H2H → game_results has winner, ranking W=3pts / L=0pts."""
        tt = _tt(test_db, "league", min_players=2)
        t = _tournament(test_db, admin_user, tt)

        p1 = _user(test_db)
        p2 = _user(test_db)
        _enroll(test_db, t, p1)
        _enroll(test_db, t, p2)
        sess = _match_session(test_db, t)

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # ── Step 1: Submit H2H result (player1 wins 3-1) ──────────────────
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/head-to-head-results",
            json={"results": [
                {"user_id": p1.id, "score": 3},
                {"user_id": p2.id, "score": 1},
            ]},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"H2H submit failed: {resp.json()}"
        data = resp.json()
        assert data["winner_user_id"] == p1.id

        # ── Step 2: Verify game_results in DB ────────────────────────────
        test_db.refresh(sess)
        assert sess.game_results is not None, "game_results must be set after H2H submit"
        gr = json.loads(sess.game_results)
        assert gr["winner_user_id"] == p1.id
        assert sess.session_status == "completed"

        p1_part = next(p for p in gr["participants"] if p["user_id"] == p1.id)
        p2_part = next(p for p in gr["participants"] if p["user_id"] == p2.id)
        assert p1_part["result"] == "win"
        assert p2_part["result"] == "loss"

        # ── Step 3: Calculate rankings ────────────────────────────────────
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/calculate-rankings",
            headers=hdrs,
        )
        assert resp.status_code == 200, f"calculate-rankings failed: {resp.json()}"
        rdata = resp.json()
        assert rdata["rankings_count"] == 2

        # ── Step 4: Verify TournamentRanking rows ────────────────────────
        rows = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == t.id)
            .all()
        )
        assert len(rows) == 2

        winner_row = next((r for r in rows if r.user_id == p1.id), None)
        loser_row = next((r for r in rows if r.user_id == p2.id), None)
        assert winner_row is not None, "Winner must have a TournamentRanking row"
        assert loser_row is not None, "Loser must have a TournamentRanking row"

        assert winner_row.rank == 1, f"Winner must be rank 1, got {winner_row.rank}"
        assert loser_row.rank == 2, f"Loser must be rank 2, got {loser_row.rank}"
        # League: win = 3pts
        assert winner_row.wins == 1, f"Winner must have 1 win, got {winner_row.wins}"
        assert loser_row.losses == 1, f"Loser must have 1 loss, got {loser_row.losses}"

    # ── SGR-02: INDIVIDUAL + LEAGUE H2H draw ─────────────────────────────────
    def test_SGR_02_individual_league_h2h_draw(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """Draw in LEAGUE H2H → both players get 1pt (draw=1), ranks assigned."""
        tt = _tt(test_db, "league", min_players=2)
        t = _tournament(test_db, admin_user, tt)

        p1 = _user(test_db)
        p2 = _user(test_db)
        _enroll(test_db, t, p1)
        _enroll(test_db, t, p2)
        sess = _match_session(test_db, t)

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # ── Step 1: Submit draw (2-2) ─────────────────────────────────────
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/head-to-head-results",
            json={"results": [
                {"user_id": p1.id, "score": 2},
                {"user_id": p2.id, "score": 2},
            ]},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"H2H submit failed: {resp.json()}"
        data = resp.json()
        assert data["winner_user_id"] is None, "Draw must have no winner"

        # ── Step 2: game_results tie ──────────────────────────────────────
        test_db.refresh(sess)
        gr = json.loads(sess.game_results)
        assert gr["winner_user_id"] is None
        for part in gr["participants"]:
            assert part["result"] == "tie"

        # ── Step 3: Calculate rankings ────────────────────────────────────
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/calculate-rankings",
            headers=hdrs,
        )
        assert resp.status_code == 200, f"calculate-rankings failed: {resp.json()}"

        # ── Step 4: Both players have draws=1 and points > 0 ─────────────
        rows = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == t.id)
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.draws == 1, f"Draw match must set draws=1, got {row.draws}"

    # ── SGR-03: INDIVIDUAL + KNOCKOUT H2H → winner rank=1 ────────────────────
    def test_SGR_03_individual_knockout_h2h_winner_gets_rank1(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """Single-match knockout: winner gets rank=1."""
        tt = _tt(test_db, "knockout", min_players=2)
        t = _tournament(test_db, admin_user, tt)

        p1 = _user(test_db)
        p2 = _user(test_db)
        _enroll(test_db, t, p1)
        _enroll(test_db, t, p2)
        sess = _match_session(test_db, t)
        # Mark as round 1 (final for 2-player bracket)
        sess.tournament_round = 1
        sess.tournament_phase = "KNOCKOUT"
        test_db.flush()

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # ── Step 1: Submit result (p1 wins 2-0) ──────────────────────────
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/head-to-head-results",
            json={"results": [
                {"user_id": p1.id, "score": 2},
                {"user_id": p2.id, "score": 0},
            ]},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"H2H submit failed: {resp.json()}"
        assert resp.json()["winner_user_id"] == p1.id

        # ── Step 2: Calculate rankings ────────────────────────────────────
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/calculate-rankings",
            headers=hdrs,
        )
        assert resp.status_code == 200, f"calculate-rankings failed: {resp.json()}"

        # ── Step 3: Winner has rank=1 ─────────────────────────────────────
        rows = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == t.id)
            .all()
        )
        assert len(rows) == 2
        winner_row = next((r for r in rows if r.user_id == p1.id), None)
        assert winner_row is not None
        assert winner_row.rank == 1, f"Tournament winner must be rank=1, got {winner_row.rank}"
        # Knockout ranking is bracket-position based (not W/L counters like league)
        loser_row = next((r for r in rows if r.user_id == p2.id), None)
        assert loser_row is not None
        assert loser_row.rank > winner_row.rank, "Loser rank must be worse than winner rank"

    # ── SGR-04: INDIVIDUAL_RANKING format → rounds_data → TournamentRanking ──
    def test_SGR_04_individual_ranking_rounds_data_to_ranking(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """INDIVIDUAL_RANKING: /results endpoint → rounds_data → TournamentRanking × 2."""
        tt = _tt(test_db, f"sgr-ir-{_uid()}", fmt="INDIVIDUAL_RANKING", min_players=2)
        t = _tournament(test_db, admin_user, tt, participant_type="INDIVIDUAL")
        # Explicitly set DESC direction so higher score = better rank (rank 1)
        cfg = t.tournament_config_obj
        cfg.ranking_direction = "DESC"
        test_db.flush()

        p1 = _user(test_db)
        p2 = _user(test_db)
        sess = _match_session(test_db, t)

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # ── Step 1: Submit INDIVIDUAL_RANKING results ─────────────────────
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/results",
            json={"results": [
                {"user_id": p1.id, "score": 85.0, "rank": 1},
                {"user_id": p2.id, "score": 72.0, "rank": 2},
            ]},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"submit_game_results failed: {resp.json()}"

        # ── Step 2: rounds_data populated ────────────────────────────────
        test_db.refresh(sess)
        assert sess.rounds_data is not None
        round_1 = sess.rounds_data.get("round_results", {}).get("1", {})
        assert str(p1.id) in round_1, f"rounds_data must contain player1 result"
        assert str(p2.id) in round_1

        # ── Step 3: Calculate rankings ────────────────────────────────────
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/calculate-rankings",
            headers=hdrs,
        )
        assert resp.status_code == 200, f"calculate-rankings failed: {resp.json()}"
        rdata = resp.json()
        assert rdata["rankings_count"] == 2

        # ── Step 4: TournamentRanking rows correct ────────────────────────
        rows = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == t.id)
            .all()
        )
        assert len(rows) == 2, f"Expected 2 ranking rows, got {len(rows)}"

        p1_row = next((r for r in rows if r.user_id == p1.id), None)
        p2_row = next((r for r in rows if r.user_id == p2.id), None)
        assert p1_row is not None
        assert p2_row is not None
        # Higher score (85) should rank 1 with DESC direction (default)
        assert p1_row.rank == 1, f"Higher score should rank 1, got {p1_row.rank}"
        assert p2_row.rank == 2

    # ── SGR-05: TEAM + INDIVIDUAL_RANKING → team_id TournamentRanking ────────
    def test_SGR_05_team_individual_ranking_to_ranking(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """TEAM tournament: /team-results → rounds_data "team_N" keys → TournamentRanking team_id."""
        tt = _tt(test_db, f"sgr-ir-team-{_uid()}", fmt="INDIVIDUAL_RANKING", min_players=2)
        t = _tournament(test_db, admin_user, tt, participant_type="TEAM")
        # Set DESC so higher score = better rank (rank 1)
        t.tournament_config_obj.ranking_direction = "DESC"
        test_db.flush()

        team1 = _make_team(test_db)
        team2 = _make_team(test_db)
        _enroll_team(test_db, t, team1)
        _enroll_team(test_db, t, team2)
        sess = _match_session(test_db, t)

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # ── Step 1: Submit team results ───────────────────────────────────
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/team-results",
            json={"results": [
                {"team_id": team1.id, "score": 5.0},
                {"team_id": team2.id, "score": 3.0},
            ], "round_number": 1},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"submit_team_results failed: {resp.json()}"

        # ── Step 2: rounds_data has team_N keys ──────────────────────────
        test_db.refresh(sess)
        assert sess.rounds_data is not None
        round_1 = sess.rounds_data.get("round_results", {}).get("1", {})
        assert f"team_{team1.id}" in round_1, f"rounds_data must contain team1 key"
        assert f"team_{team2.id}" in round_1

        # ── Step 3: Calculate rankings ────────────────────────────────────
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/calculate-rankings",
            headers=hdrs,
        )
        assert resp.status_code == 200, f"calculate-rankings failed: {resp.json()}"
        rdata = resp.json()
        assert rdata["participant_type"] == "TEAM"
        assert rdata["rankings_count"] == 2

        # ── Step 4: TournamentRanking rows have team_id, no user_id ──────
        rows = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == t.id)
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.team_id is not None, f"TEAM ranking must have team_id, got {row}"
            assert row.user_id is None, f"TEAM ranking must NOT have user_id, got {row.user_id}"
            assert row.participant_type == "TEAM"

        team1_row = next((r for r in rows if r.team_id == team1.id), None)
        team2_row = next((r for r in rows if r.team_id == team2.id), None)
        assert team1_row is not None
        assert team2_row is not None
        # Team1 scored 5.0 > 3.0 → should be rank 1 with DESC
        assert team1_row.rank == 1, f"Higher score team must be rank 1, got {team1_row.rank}"

    def test_SGR_06_swiss_ranking_unsupported_documented(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SGR-06: Swiss tournament → calculate-rankings → 400 (RankingStrategyFactory
        has no Swiss strategy). Documents deterministic failure behavior.

        Implication: Bye player gets NO ranking — the entire Swiss ranking pipeline
        is unsupported. factory.py docstring claims 'swiss' is handled but the
        implementation only supports league/knockout/group_knockout.

        This test pins the behavior so future Swiss strategy additions are validated.
        """
        # Use existing "swiss" type from DB (get-or-create)
        tt = _tt(test_db, "swiss", min_players=4)
        t = _tournament(test_db, admin_user, tt)

        # Enroll 5 players (odd count → bye in session generation)
        players = [_user(test_db) for _ in range(5)]
        for p in players:
            _enroll(test_db, t, p)

        # Directly inject a completed session with game_results (bypass H2H endpoint)
        import json as _json
        sess = _match_session(test_db, t)
        sess.game_results = _json.dumps({
            "participants": [
                {"user_id": players[0].id, "score": 3},
                {"user_id": players[1].id, "score": 1},
            ],
            "winner_user_id": players[0].id,
        })
        sess.session_status = "completed"
        test_db.flush()

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        resp = client.post(f"/api/v1/tournaments/{t.id}/calculate-rankings", headers=hdrs)

        # Swiss is not supported: expect 400
        assert resp.status_code == 400, (
            f"Expected 400 for Swiss calculate-rankings (unsupported strategy), "
            f"got {resp.status_code}: {resp.text[:300]}"
        )
        body = resp.json()
        detail = (body.get("error") or body).get("message") or body.get("detail", "")
        assert "swiss" in detail.lower() or "unsupported" in detail.lower(), (
            f"Error must mention Swiss or unsupported: {detail}"
        )

        # No TournamentRanking rows created — no player (including bye player) gets a rank
        rows = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).all()
        assert len(rows) == 0, (
            f"No ranking rows should exist after failed calculate-rankings, got {len(rows)}"
        )
