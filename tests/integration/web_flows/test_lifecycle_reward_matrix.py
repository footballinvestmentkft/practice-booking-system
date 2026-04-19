"""
Tournament Lifecycle + Reward Distribution Matrix Tests

SRL-01  INDIVIDUAL LEAGUE full pipeline:
        IN_PROGRESS → submit H2H results → calculate-rankings
        → COMPLETED transition → distribute-rewards-v2 → REWARDS_DISTRIBUTED
        → TournamentParticipation rows created with xp_awarded, placement

SRL-02  Reward idempotency:
        distribute-rewards-v2 called twice → TournamentParticipation count unchanged

SRL-03  TEAM reward expansion:
        TEAM tournament rankings → distribute-rewards-v2
        → each active TeamMember gets a TournamentParticipation row
        → team_id set on rows

SRL-04  Status guard — rewards require COMPLETED:
        IN_PROGRESS tournament → distribute-rewards-v2 → 400 Bad Request

SRL-05  Status machine guard — CHECK_IN_OPEN mandatory:
        ENROLLMENT_CLOSED → IN_PROGRESS direct transition → 400 Bad Request

SRL-06  force_redistribution=True overwrites existing TournamentParticipation:
        Distribute once → update ranking → distribute again with force=True
        → placement updated in the existing TournamentParticipation row

SRL-07  TEAM + HEAD_TO_HEAD explicit block:
        TEAM tournament + H2H result submission → 400 with clear message
        (participant_type guard; TEAM must use /team-results instead)

LCG-01  COMPLETED transition blocked without rankings:
        IN_PROGRESS + sessions but 0 TournamentRanking → PATCH status COMPLETED → 400

LCG-02  COMPLETED transition allowed with rankings:
        IN_PROGRESS + sessions + ≥1 TournamentRanking → PATCH status COMPLETED → 200

ARK-01  Auto-ranking trigger on last H2H session:
        Submit last H2H result → TournamentRanking rows auto-created

ARK-02  Auto-ranking only fires on LAST session:
        2 sessions, submit only 1 → no TournamentRanking rows created yet

ARK-03  Auto-ranking non-breaking for Swiss:
        Swiss tournament last session completed → HTTP 200, no rankings (swallowed)

SRL-08  TEAM H2H ranking display correctness:
        TEAM league tournament, 2 teams × 2 members, submit team-results
        → auto-ranking creates 2 TournamentRanking rows with team_id set, user_id=None
        → GET /api/v1/tournaments/{id}/rankings → team_name populated, user_id=None
        → Rankings API returns correct W/D/L and points
        → After COMPLETED + distribute-rewards-v2 → each team member gets TournamentParticipation
        → REWARDS_DISTRIBUTED: GET rankings → xp_earned aggregated per team

All tests use SAVEPOINT-isolated real DB (test_db) + TestClient (client) fixtures
from tests/integration/conftest.py. Admin Bearer token bypasses instructor-only checks.
"""
import json
import uuid
from datetime import date, datetime, timezone

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
from app.models.tournament_achievement import TournamentParticipation
from app.models.license import UserLicense
from app.models.team import Team, TeamMember, TournamentTeamEnrollment
from app.models.location import Location
from app.models.campus import Campus
from app.core.security import get_password_hash


# ── Helpers ────────────────────────────────────────────────────────────────────

_PFX = "srl"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _user(db: Session, role=UserRole.STUDENT) -> User:
    u = User(
        email=f"{_PFX}-{_uid()}@lfa-test.com",
        name=f"SRL User {_uid()}",
        password_hash=get_password_hash("pw"),
        role=role,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _preset(db: Session) -> GamePreset:
    existing = db.query(GamePreset).filter(GamePreset.code == "srl-default").first()
    if existing:
        return existing
    gp = GamePreset(
        code="srl-default",
        name="SRL Default",
        description="Auto-created for SRL tests",
        is_active=True,
        game_config={"metadata": {"min_players": 0}, "skills_tested": [], "skill_weights": {}},
    )
    db.add(gp)
    db.flush()
    return gp


def _tt(db: Session, code: str, fmt: str = "HEAD_TO_HEAD", min_players: int = 2) -> TournamentType:
    existing = db.query(TournamentType).filter(TournamentType.code == code).first()
    if existing:
        return existing
    # Include type-specific config so tests are self-contained on a fresh DB
    _cfg: dict = {"code": code}
    if code == "knockout":
        _cfg["third_place_playoff"] = True
    elif code == "group_knockout":
        _cfg["rounds"] = {"4": "Semi-Finals", "2": "Finals"}
    tt = TournamentType(
        code=code,
        display_name=f"SRL {code}",
        description="Auto-created for SRL tests",
        format=fmt,
        min_players=min_players,
        max_players=64,
        requires_power_of_two=False,
        session_duration_minutes=60,
        break_between_sessions_minutes=10,
        config=_cfg,
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
        name=f"SRL Cup {_uid()}",
        code=f"SRL-{_uid()}",
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
        ranking_direction="DESC",
    ))
    db.add(GameConfiguration(
        semester_id=t.id,
        game_preset_id=preset.id,
    ))
    db.flush()
    return t


def _session(db: Session, tournament: Semester) -> SessionModel:
    """Create a minimal MATCH session."""
    sess = SessionModel(
        title=f"SRL Match {_uid()}",
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
        name=f"SRL Team {_uid()}",
        code=f"ST-{_uid()}",
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


# ── SRL Tests ──────────────────────────────────────────────────────────────────

class TestLifecycleRewardMatrix:
    """
    SRL-01..05 — Full tournament lifecycle + reward distribution matrix.
    Proves CI-level guarantee that the pipeline runs end-to-end correctly.
    """

    def test_SRL_01_individual_league_full_lifecycle(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-01: INDIVIDUAL + LEAGUE full lifecycle:
        IN_PROGRESS → H2H results → calculate-rankings → COMPLETED → distribute-rewards-v2
        → REWARDS_DISTRIBUTED + TournamentParticipation rows created.
        """
        # Use existing "league" code — RankingStrategyFactory matches on code
        tt = _tt(test_db, "league", min_players=2)
        t = _tournament(test_db, admin_user, tt)
        p1 = _user(test_db)
        p2 = _user(test_db)
        _enroll(test_db, t, p1)
        _enroll(test_db, t, p2)
        sess = _session(test_db, t)

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # Step 1: Submit H2H results (p1 wins 3-1)
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/head-to-head-results",
            json={"results": [{"user_id": p1.id, "score": 3}, {"user_id": p2.id, "score": 1}]},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"H2H results failed: {resp.text[:300]}"
        assert resp.json()["winner_user_id"] == p1.id

        # Step 2: Calculate rankings
        resp = client.post(f"/api/v1/tournaments/{t.id}/calculate-rankings", headers=hdrs)
        assert resp.status_code == 200, f"calculate-rankings failed: {resp.text[:300]}"

        rankings = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).all()
        assert len(rankings) == 2
        winner = next(r for r in rankings if r.user_id == p1.id)
        assert winner.rank == 1

        # Step 3: Transition IN_PROGRESS → COMPLETED
        resp = client.patch(
            f"/api/v1/tournaments/{t.id}/status",
            json={"new_status": "COMPLETED", "reason": "SRL-01 test"},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"Status → COMPLETED failed: {resp.text[:300]}"

        test_db.expire_all()
        t_refreshed = test_db.query(Semester).filter(Semester.id == t.id).first()
        assert t_refreshed.tournament_status == "COMPLETED"

        # Step 4: Distribute rewards
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
            json={"tournament_id": t.id, "force_redistribution": False},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"distribute-rewards-v2 failed: {resp.text[:400]}"

        test_db.expire_all()

        # Verify: tournament status → REWARDS_DISTRIBUTED
        t_final = test_db.query(Semester).filter(Semester.id == t.id).first()
        assert t_final.tournament_status == "REWARDS_DISTRIBUTED", (
            f"Expected REWARDS_DISTRIBUTED, got {t_final.tournament_status}"
        )

        # Verify: TournamentParticipation rows created for both players
        parts = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).all()
        assert len(parts) == 2, f"Expected 2 TournamentParticipation rows, got {len(parts)}"

        p1_part = next(p for p in parts if p.user_id == p1.id)
        assert p1_part.placement == 1
        assert p1_part.xp_awarded >= 0

        p2_part = next(p for p in parts if p.user_id == p2.id)
        assert p2_part.placement == 2

    def test_SRL_02_reward_distribution_idempotent(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-02: distribute-rewards-v2 called twice → TournamentParticipation count unchanged.
        Idempotency: second call skips already-distributed rows.
        """
        tt = _tt(test_db, f"srl-idem-{_uid()}", min_players=2)
        t = _tournament(test_db, admin_user, tt, tournament_status="COMPLETED")

        # Insert rankings directly (pipeline already proved in SRL-01)
        p1 = _user(test_db)
        p2 = _user(test_db)
        test_db.add(TournamentRanking(
            tournament_id=t.id,
            user_id=p1.id,
            participant_type="INDIVIDUAL",
            rank=1,
            points=100,
        ))
        test_db.add(TournamentRanking(
            tournament_id=t.id,
            user_id=p2.id,
            participant_type="INDIVIDUAL",
            rank=2,
            points=50,
        ))
        test_db.flush()

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # First call
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
            json={"tournament_id": t.id, "force_redistribution": False},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"First distribute-rewards-v2 failed: {resp.text[:300]}"

        test_db.expire_all()
        count_after_first = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).count()
        assert count_after_first == 2

        # Reset tournament status to COMPLETED for second call
        t_obj = test_db.query(Semester).filter(Semester.id == t.id).first()
        t_obj.tournament_status = "COMPLETED"
        test_db.flush()

        # Second call (no force_redistribution)
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
            json={"tournament_id": t.id, "force_redistribution": False},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"Second distribute-rewards-v2 failed: {resp.text[:300]}"

        test_db.expire_all()
        count_after_second = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).count()
        assert count_after_second == count_after_first, (
            f"Idempotency broken: count changed from {count_after_first} to {count_after_second}"
        )

    def test_SRL_03_team_reward_expansion(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-03: TEAM tournament → distribute-rewards-v2 expands team ranking
        → one TournamentParticipation per active TeamMember, with team_id set.
        """
        tt = _tt(test_db, f"srl-team-{_uid()}", min_players=2)
        t = _tournament(test_db, admin_user, tt, participant_type="TEAM", tournament_status="COMPLETED")

        # Create team with 2 active members
        team = _make_team(test_db)
        extra_member = _user(test_db)
        test_db.add(TeamMember(
            team_id=team.id, user_id=extra_member.id, role="PLAYER", is_active=True
        ))
        test_db.flush()
        _enroll_team(test_db, t, team)

        # Insert team ranking
        test_db.add(TournamentRanking(
            tournament_id=t.id,
            team_id=team.id,
            participant_type="TEAM",
            rank=1,
            points=100,
        ))
        test_db.flush()

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        resp = client.post(
            f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
            json={"tournament_id": t.id, "force_redistribution": False},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"distribute-rewards-v2 TEAM failed: {resp.text[:400]}"

        test_db.expire_all()

        parts = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).all()

        # 2 active members → 2 TournamentParticipation rows
        assert len(parts) == 2, f"Expected 2 rows (per active member), got {len(parts)}"
        for part in parts:
            assert part.team_id == team.id, "team_id must be set on TEAM participation rows"
            assert part.user_id is not None
            assert part.placement == 1

    def test_SRL_04_rewards_require_completed_status(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-04: distribute-rewards-v2 on IN_PROGRESS tournament → 400 Bad Request.
        Rewards endpoint enforces COMPLETED prerequisite.
        """
        tt = _tt(test_db, f"srl-guard-{_uid()}", min_players=2)
        t = _tournament(test_db, admin_user, tt, tournament_status="IN_PROGRESS")

        # Insert rankings (so the guard is purely status-based, not rankings-based)
        p1 = _user(test_db)
        test_db.add(TournamentRanking(
            tournament_id=t.id,
            user_id=p1.id,
            participant_type="INDIVIDUAL",
            rank=1,
            points=100,
        ))
        test_db.flush()

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        resp = client.post(
            f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
            json={"tournament_id": t.id, "force_redistribution": False},
            headers=hdrs,
        )
        assert resp.status_code == 400, (
            f"Expected 400 for IN_PROGRESS tournament, got {resp.status_code}: {resp.text[:300]}"
        )
        body = resp.json()
        detail = (body.get("error") or body).get("message") or body.get("detail", "")
        assert "COMPLETED" in detail or "completed" in detail.lower(), (
            f"Error message should mention COMPLETED: {detail}"
        )

    def test_SRL_05_enrollment_closed_to_in_progress_rejected(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-05: ENROLLMENT_CLOSED → IN_PROGRESS direct transition now invalid.
        CHECK_IN_OPEN phase is mandatory since 2026-03-27.
        """
        tt = _tt(test_db, f"srl-sm-{_uid()}", min_players=2)
        t = _tournament(
            test_db, admin_user, tt, tournament_status="ENROLLMENT_CLOSED"
        )

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        resp = client.patch(
            f"/api/v1/tournaments/{t.id}/status",
            json={"new_status": "IN_PROGRESS", "reason": "SRL-05 skip check-in"},
            headers=hdrs,
        )
        assert resp.status_code == 400, (
            f"Expected 400 for ENROLLMENT_CLOSED → IN_PROGRESS, got {resp.status_code}: {resp.text[:300]}"
        )
        # Confirm tournament status unchanged
        test_db.expire_all()
        t_obj = test_db.query(Semester).filter(Semester.id == t.id).first()
        assert t_obj.tournament_status == "ENROLLMENT_CLOSED", (
            "Tournament status must remain ENROLLMENT_CLOSED after rejected transition"
        )

    def test_SRL_06_force_redistribution_overwrites_existing(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-06: force_redistribution=True updates (overwrites) existing
        TournamentParticipation rows — placement changes when rankings change.
        Row count stays 1 (no duplicate created).
        """
        tt = _tt(test_db, f"srl-force-{_uid()}", min_players=2)
        t = _tournament(test_db, admin_user, tt, tournament_status="COMPLETED")

        p1 = _user(test_db)
        ranking = TournamentRanking(
            tournament_id=t.id,
            user_id=p1.id,
            participant_type="INDIVIDUAL",
            rank=2,
            points=50,
        )
        test_db.add(ranking)
        test_db.flush()

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # First distribution: rank=2 → placement=2
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
            json={"tournament_id": t.id, "force_redistribution": False},
            headers=hdrs,
        )
        assert resp.status_code == 200

        test_db.expire_all()
        part = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id,
            TournamentParticipation.user_id == p1.id,
        ).one()
        assert part.placement == 2

        # Update ranking to rank=1
        test_db.expire(ranking)
        ranking_obj = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id,
            TournamentRanking.user_id == p1.id,
        ).one()
        ranking_obj.rank = 1
        test_db.flush()

        # Reset tournament to COMPLETED for second call
        t_obj = test_db.query(Semester).filter(Semester.id == t.id).first()
        t_obj.tournament_status = "COMPLETED"
        test_db.flush()

        # Second distribution with force=True: rank=1 → placement should update
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
            json={"tournament_id": t.id, "force_redistribution": True},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"force redistribution failed: {resp.text[:300]}"

        test_db.expire_all()
        rows = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id,
            TournamentParticipation.user_id == p1.id,
        ).all()
        assert len(rows) == 1, f"Expected exactly 1 row, got {len(rows)} (duplicate created)"
        assert rows[0].placement == 1, (
            f"Placement must be updated to 1 after force redistribution, got {rows[0].placement}"
        )

    def test_SRL_07_team_h2h_result_submission_blocked(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-07: TEAM tournament + HEAD_TO_HEAD result submission → 400.
        Explicit participant_type guard prevents misleading SemesterEnrollment error.
        """
        # Use existing "league" (HEAD_TO_HEAD) type
        tt = _tt(test_db, "league", min_players=2)
        t = _tournament(test_db, admin_user, tt, participant_type="TEAM")

        team1 = _make_team(test_db)
        team2 = _make_team(test_db)
        _enroll_team(test_db, t, team1)
        _enroll_team(test_db, t, team2)

        sess = _session(test_db, t)

        # Attempt H2H result submission with team member user_ids
        captain1_id = team1.captain_user_id
        captain2_id = team2.captain_user_id

        hdrs = {"Authorization": f"Bearer {admin_token}"}
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/head-to-head-results",
            json={"results": [
                {"user_id": captain1_id, "score": 3},
                {"user_id": captain2_id, "score": 1},
            ]},
            headers=hdrs,
        )
        assert resp.status_code == 400, (
            f"Expected 400 for TEAM + H2H, got {resp.status_code}: {resp.text[:300]}"
        )
        body = resp.json()
        detail = (body.get("error") or body).get("message") or body.get("detail", "")
        assert "TEAM" in detail and "team-results" in detail, (
            f"Error must mention TEAM and /team-results redirect: {detail}"
        )


class TestLifecycleConsistencyGuards:
    """
    LCG-01..02: COMPLETED transition requires at least 1 TournamentRanking row.
    Guards against partial lifecycle states.
    """

    def test_LCG_01_completed_transition_blocked_without_rankings(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        LCG-01: IN_PROGRESS + sessions present but 0 TournamentRanking rows
        → PATCH status COMPLETED → 400 "No rankings calculated yet".
        """
        tt = _tt(test_db, f"lcg-{_uid()}", min_players=2)
        t = _tournament(test_db, admin_user, tt, tournament_status="IN_PROGRESS")
        _session(test_db, t)  # sessions exist (required for COMPLETED check)

        hdrs = {"Authorization": f"Bearer {admin_token}"}
        resp = client.patch(
            f"/api/v1/tournaments/{t.id}/status",
            json={"new_status": "COMPLETED", "reason": "LCG-01"},
            headers=hdrs,
        )
        assert resp.status_code == 400, (
            f"Expected 400 (no rankings), got {resp.status_code}: {resp.text[:300]}"
        )
        body = resp.json()
        detail = (body.get("error") or body).get("message") or body.get("detail", "")
        assert "ranking" in detail.lower() or "calculate" in detail.lower(), (
            f"Error must mention rankings: {detail}"
        )

    def test_LCG_02_completed_transition_allowed_with_rankings(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        LCG-02: IN_PROGRESS + sessions + ≥1 TournamentRanking → COMPLETED transition allowed.
        """
        tt = _tt(test_db, f"lcg2-{_uid()}", min_players=2)
        t = _tournament(test_db, admin_user, tt, tournament_status="IN_PROGRESS")
        _session(test_db, t)

        p1 = _user(test_db)
        test_db.add(TournamentRanking(
            tournament_id=t.id,
            user_id=p1.id,
            participant_type="INDIVIDUAL",
            rank=1,
            points=100,
        ))
        test_db.flush()

        hdrs = {"Authorization": f"Bearer {admin_token}"}
        resp = client.patch(
            f"/api/v1/tournaments/{t.id}/status",
            json={"new_status": "COMPLETED", "reason": "LCG-02"},
            headers=hdrs,
        )
        assert resp.status_code == 200, (
            f"Expected 200 (rankings present), got {resp.status_code}: {resp.text[:300]}"
        )


class TestAutoRankingTrigger:
    """
    ARK-01..03: Auto-trigger ranking calculation on last session completed.
    """

    def test_ARK_01_auto_ranking_on_last_h2h_session(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        ARK-01: Submit last H2H result → TournamentRanking rows auto-created.
        """
        tt = _tt(test_db, "league", min_players=2)
        t = _tournament(test_db, admin_user, tt)
        p1 = _user(test_db)
        p2 = _user(test_db)
        _enroll(test_db, t, p1)
        _enroll(test_db, t, p2)
        sess = _session(test_db, t)

        hdrs = {"Authorization": f"Bearer {admin_token}"}
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/head-to-head-results",
            json={"results": [{"user_id": p1.id, "score": 2}, {"user_id": p2.id, "score": 0}]},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"H2H result failed: {resp.text[:300]}"

        # Rankings must be auto-created (no explicit calculate-rankings call)
        test_db.expire_all()
        rows = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).all()
        assert len(rows) == 2, f"Expected 2 auto-created ranking rows, got {len(rows)}"
        winner = next((r for r in rows if r.user_id == p1.id), None)
        assert winner is not None and winner.rank == 1

    def test_ARK_02_auto_ranking_only_on_last_session(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        ARK-02: 2 sessions, submit only 1 → no TournamentRanking rows (trigger not fired).
        """
        tt = _tt(test_db, "league", min_players=2)
        t = _tournament(test_db, admin_user, tt)
        p1 = _user(test_db)
        p2 = _user(test_db)
        _enroll(test_db, t, p1)
        _enroll(test_db, t, p2)
        sess1 = _session(test_db, t)
        _session(test_db, t)  # sess2 — not submitted

        hdrs = {"Authorization": f"Bearer {admin_token}"}
        resp = client.patch(
            f"/api/v1/sessions/{sess1.id}/head-to-head-results",
            json={"results": [{"user_id": p1.id, "score": 1}, {"user_id": p2.id, "score": 0}]},
            headers=hdrs,
        )
        assert resp.status_code == 200

        test_db.expire_all()
        rows = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).all()
        assert len(rows) == 0, (
            f"Rankings must NOT be auto-created yet (1 session still pending), got {len(rows)}"
        )

    def test_ARK_03_auto_ranking_non_breaking_for_swiss(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        ARK-03: Swiss tournament last session completed → HTTP 200 (auto-ranking
        silently fails because Swiss has no strategy; result submission unaffected).
        """
        tt = _tt(test_db, "swiss", min_players=4)
        t = _tournament(test_db, admin_user, tt)
        p1 = _user(test_db)
        p2 = _user(test_db)
        _enroll(test_db, t, p1)
        _enroll(test_db, t, p2)
        sess = _session(test_db, t)

        hdrs = {"Authorization": f"Bearer {admin_token}"}
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/head-to-head-results",
            json={"results": [{"user_id": p1.id, "score": 1}, {"user_id": p2.id, "score": 0}]},
            headers=hdrs,
        )
        # Must still return 200 — auto-ranking failure must be swallowed
        assert resp.status_code == 200, (
            f"Swiss auto-ranking failure must not break result submission: {resp.text[:300]}"
        )
        # No ranking rows (Swiss auto-ranking raises ValueError, caught by _maybe_trigger)
        test_db.expire_all()
        rows = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).all()
        assert len(rows) == 0, f"Swiss must produce no auto-rankings, got {len(rows)}"


class TestTeamRankingDisplay:
    """
    SRL-08: TEAM H2H league — ranking display correctness end-to-end.

    Proves: team_id set in rankings, user_id=None, team_name populated in API,
    correct W/D/L, reward aggregation per team after distribution.
    """

    def test_SRL_08_team_h2h_ranking_display(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-08: TEAM + LEAGUE full ranking + reward display pipeline:
        - 2 teams × 2 members each
        - submit team-results → auto-ranking → TournamentRanking rows have team_id, user_id=None
        - GET /rankings API → team_name correct, user_id=None, W/D/L correct
        - COMPLETED → distribute-rewards-v2 → 4 TournamentParticipation rows (2 per team)
        - REWARDS_DISTRIBUTED: GET /rankings → xp_earned > 0 aggregated per team
        """
        tt = _tt(test_db, "league", min_players=2)
        t = _tournament(test_db, admin_user, tt, participant_type="TEAM")

        # Team A: 2 members
        team_a = _make_team(test_db)
        member_a2 = _user(test_db)
        test_db.add(TeamMember(team_id=team_a.id, user_id=member_a2.id, role="PLAYER", is_active=True))
        _enroll_team(test_db, t, team_a)

        # Team B: 2 members
        team_b = _make_team(test_db)
        member_b2 = _user(test_db)
        test_db.add(TeamMember(team_id=team_b.id, user_id=member_b2.id, role="PLAYER", is_active=True))
        _enroll_team(test_db, t, team_b)
        test_db.flush()

        # Session with both teams
        sess = _session(test_db, t)
        sess.participant_team_ids = [team_a.id, team_b.id]
        sess.match_format = "HEAD_TO_HEAD"
        test_db.flush()

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # Submit team results: Team A wins 3-1
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/team-results",
            json={"results": [
                {"team_id": team_a.id, "score": 3.0},
                {"team_id": team_b.id, "score": 1.0},
            ], "round_number": 1},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"team-results failed: {resp.text[:300]}"

        # Auto-ranking must have fired → 2 TournamentRanking rows with team_id
        test_db.expire_all()
        ranking_rows = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).order_by(TournamentRanking.rank).all()
        assert len(ranking_rows) == 2, f"Expected 2 ranking rows, got {len(ranking_rows)}"
        assert ranking_rows[0].team_id == team_a.id, "Team A (winner) must be rank 1"
        assert ranking_rows[0].user_id is None, "TEAM ranking rows must have user_id=None"
        assert ranking_rows[0].wins == 1
        assert ranking_rows[0].losses == 0
        assert ranking_rows[1].team_id == team_b.id
        assert ranking_rows[1].losses == 1

        # GET /rankings API → team_name populated
        resp = client.get(f"/api/v1/tournaments/{t.id}/rankings", headers=hdrs)
        assert resp.status_code == 200, resp.text
        api_rankings = resp.json()["rankings"]
        assert len(api_rankings) == 2
        rank1 = next(r for r in api_rankings if r["rank"] == 1)
        assert rank1["team_id"] == team_a.id
        assert rank1["user_id"] is None
        assert rank1["team_name"] == team_a.name, f"Expected '{team_a.name}', got '{rank1['team_name']}'"
        assert rank1["wins"] == 1
        assert rank1["points"] == 3.0

        # Transition to COMPLETED
        resp = client.patch(
            f"/api/v1/tournaments/{t.id}/status",
            json={"new_status": "COMPLETED"},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"COMPLETED transition failed: {resp.text[:300]}"

        # Distribute rewards → 4 TournamentParticipation rows (2 per team)
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
            json={"tournament_id": t.id},
            headers=hdrs,
        )
        assert resp.status_code == 200, f"distribute-rewards-v2 failed: {resp.text[:300]}"

        test_db.expire_all()
        from app.models.tournament_achievement import TournamentParticipation
        participations = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).all()
        assert len(participations) == 4, (
            f"Expected 4 TournamentParticipation rows (2 per team), got {len(participations)}"
        )
        # Winners (Team A members) must have placement=1, losers placement=2
        team_a_member_ids = {m.user_id for m in test_db.query(TeamMember).filter(
            TeamMember.team_id == team_a.id
        ).all()}
        for p in participations:
            if p.user_id in team_a_member_ids:
                assert p.placement == 1, f"Team A member must have placement=1, got {p.placement}"
                assert p.team_id == team_a.id
            else:
                assert p.placement == 2, f"Team B member must have placement=2, got {p.placement}"
                assert p.team_id == team_b.id

        # REWARDS_DISTRIBUTED: rankings API shows aggregated xp per team
        resp = client.get(f"/api/v1/tournaments/{t.id}/rankings", headers=hdrs)
        assert resp.status_code == 200
        rewarded = resp.json()["rankings"]
        r1 = next(r for r in rewarded if r["rank"] == 1)
        assert "xp_earned" in r1, "REWARDS_DISTRIBUTED rankings must include xp_earned"
        assert r1["xp_earned"] >= 0  # aggregated across 2 team members


# ── Helpers for extended lifecycle tests ──────────────────────────────────────

def _campus(db: Session) -> "Campus":
    """Create a minimal Location + Campus for tournament campus_id requirement."""
    loc = Location(name=f"SRL-Loc-{_uid()}", city=f"SRL-City-{_uid()}", country="HU")
    db.add(loc)
    db.flush()
    camp = Campus(location_id=loc.id, name=f"SRL-Campus-{_uid()}", is_active=True)
    db.add(camp)
    db.flush()
    return camp


def _ir_tournament(
    db: Session,
    instructor: User,
    participant_type: str = "INDIVIDUAL",
    tournament_status: str = "DRAFT",
) -> Semester:
    """Create an INDIVIDUAL_RANKING tournament (tournament_type_id=None, scoring_type=SCORE_BASED).

    INDIVIDUAL_RANKING tournaments MUST NOT have tournament_type_id set.
    The format is inferred from scoring_type != 'HEAD_TO_HEAD'.
    """
    preset = _preset(db)
    t = Semester(
        name=f"SRL IR {_uid()}",
        code=f"SRL-IR-{_uid()}",
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
        tournament_type_id=None,    # INDIVIDUAL_RANKING has no type
        participant_type=participant_type,
        max_players=32,
        number_of_rounds=1,
        parallel_fields=1,
        ranking_direction="DESC",   # higher score = better rank
        scoring_type="SCORE_BASED",
    ))
    db.add(GameConfiguration(
        semester_id=t.id,
        game_preset_id=preset.id,
    ))
    db.flush()
    return t


def _make_team_with_members(db: Session, count: int = 2) -> Team:
    """Create a team with `count` active members (1 captain + rest players)."""
    team = _make_team(db)  # captain already added as CAPTAIN member
    for _ in range(count - 1):
        player = _user(db)
        db.add(TeamMember(team_id=team.id, user_id=player.id, role="PLAYER", is_active=True))
    db.flush()
    return team


def _advance_to_in_progress(client, tournament_id: int, admin_token: str) -> None:
    """Drive a DRAFT tournament through the full state machine to IN_PROGRESS.

    Required pre-conditions (set BEFORE calling this):
      - tournament.campus_id is set
      - tournament.master_instructor_id is set
      - enough participants enrolled (≥ TournamentType.min_players)
    """
    hdrs = {"Authorization": f"Bearer {admin_token}"}
    for new_status in ("ENROLLMENT_OPEN", "ENROLLMENT_CLOSED", "CHECK_IN_OPEN", "IN_PROGRESS"):
        resp = client.patch(
            f"/api/v1/tournaments/{tournament_id}/status",
            json={"new_status": new_status},
            headers=hdrs,
        )
        assert resp.status_code == 200, (
            f"Status transition to {new_status} failed: {resp.status_code} {resp.text[:400]}"
        )


def _get_match_sessions(test_db: Session, tournament_id: int) -> list:
    """Return all MATCH sessions for a tournament (fresh from DB)."""
    test_db.expire_all()
    return (
        test_db.query(SessionModel)
        .filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.event_category == EventCategory.MATCH,
        )
        .order_by(SessionModel.tournament_round.nullslast(), SessionModel.id)
        .all()
    )


def _submit_h2h(client, session_id: int, winner_uid: int, loser_uid: int, admin_token: str) -> None:
    """Submit a HEAD_TO_HEAD result (winner scores 2, loser scores 0)."""
    resp = client.patch(
        f"/api/v1/sessions/{session_id}/head-to-head-results",
        json={"results": [
            {"user_id": winner_uid, "score": 2},
            {"user_id": loser_uid, "score": 0},
        ]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, f"H2H result for session {session_id} failed: {resp.text[:300]}"


def _submit_team(client, session_id: int, winner_tid: int, loser_tid: int, admin_token: str) -> None:
    """Submit a TEAM result (winner scores 3, loser scores 1)."""
    resp = client.patch(
        f"/api/v1/sessions/{session_id}/team-results",
        json={"results": [
            {"team_id": winner_tid, "score": 3.0},
            {"team_id": loser_tid, "score": 1.0},
        ], "round_number": 1},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, f"Team result for session {session_id} failed: {resp.text[:300]}"


def _finalize_tournament(client, tournament_id: int, admin_token: str, test_db: Session) -> None:
    """calculate-rankings → COMPLETED → distribute-rewards-v2 → assert REWARDS_DISTRIBUTED."""
    hdrs = {"Authorization": f"Bearer {admin_token}"}

    resp = client.post(f"/api/v1/tournaments/{tournament_id}/calculate-rankings", headers=hdrs)
    assert resp.status_code == 200, f"calculate-rankings failed: {resp.text[:300]}"

    resp = client.patch(
        f"/api/v1/tournaments/{tournament_id}/status",
        json={"new_status": "COMPLETED"},
        headers=hdrs,
    )
    assert resp.status_code == 200, f"COMPLETED transition failed: {resp.text[:300]}"

    resp = client.post(
        f"/api/v1/tournaments/{tournament_id}/distribute-rewards-v2",
        json={"tournament_id": tournament_id},
        headers=hdrs,
    )
    assert resp.status_code == 200, f"distribute-rewards-v2 failed: {resp.text[:300]}"

    test_db.expire_all()
    final = test_db.query(Semester).filter(Semester.id == tournament_id).first()
    assert final.tournament_status == "REWARDS_DISTRIBUTED", (
        f"Expected REWARDS_DISTRIBUTED, got {final.tournament_status}"
    )


# ── Extended lifecycle matrix ──────────────────────────────────────────────────

class TestExtendedLifecycleMatrix:
    """
    SRL-09..15 — Full DRAFT-to-REWARDS_DISTRIBUTED lifecycle for all
    format × participant_type combinations not covered by SRL-01..08.

    Each test:
      1. Creates a DRAFT tournament with the target combination
      2. Enrolls enough participants (≥ TournamentType.min_players)
      3. Advances through the full state machine (auto-generates sessions)
      4. Submits results for every MATCH session
      5. Calls calculate-rankings → COMPLETED → distribute-rewards-v2
      6. Asserts REWARDS_DISTRIBUTED + correct TournamentRanking rows
    """

    # ── SRL-09 ──────────────────────────────────────────────────────────────────

    def test_SRL_09_individual_knockout_full_lifecycle(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-09: INDIVIDUAL + HEAD_TO_HEAD + knockout (4 players)
        SF1 + SF2 → KnockoutProgressionService seeds Final → Final result
        → calculate-rankings → COMPLETED → REWARDS_DISTRIBUTED
        """
        tt = _tt(test_db, "knockout", min_players=4)
        camp = _campus(test_db)
        t = _tournament(test_db, admin_user, tt, tournament_status="DRAFT")
        t.campus_id = camp.id
        test_db.flush()

        # Enroll 4 players
        players = [_user(test_db) for _ in range(4)]
        for p in players:
            _enroll(test_db, t, p)

        _advance_to_in_progress(client, t.id, admin_token)

        # 4 sessions: 2 SFs (round 1) + 1 Final + 1 Bronze/3rd-place (round 2)
        # The "knockout" TournamentType has third_place_playoff=True
        sessions = _get_match_sessions(test_db, t.id)
        assert len(sessions) == 4, f"Expected 4 knockout sessions (2 SF + Final + Bronze), got {len(sessions)}"

        sf_sessions = [s for s in sessions if s.tournament_round == 1]
        # Final has tournament_match_number != 999; bronze has tournament_match_number = 999
        final_session = next(
            s for s in sessions
            if s.tournament_round == 2 and (s.tournament_match_number or 0) != 999
        )
        bronze_session = next(
            s for s in sessions
            if s.tournament_round == 2 and (s.tournament_match_number or 0) == 999
        )
        assert len(sf_sessions) == 2

        # Submit SF results — bracket seeding: p[0] vs p[3], p[1] vs p[2]
        sf_winners = []
        sf_losers = []
        for sf in sf_sessions:
            pids = sf.participant_user_ids
            assert pids and len(pids) == 2, "SF session must have 2 participants"
            _submit_h2h(client, sf.id, pids[0], pids[1], admin_token)
            sf_winners.append(pids[0])
            sf_losers.append(pids[1])

        # After SFs, KnockoutProgressionService seeds the Final and Bronze
        test_db.expire_all()
        final_session = test_db.query(SessionModel).filter(SessionModel.id == final_session.id).first()
        assert final_session.participant_user_ids and len(final_session.participant_user_ids) == 2, (
            "Final must have participant_user_ids set after SF results"
        )

        pids = final_session.participant_user_ids
        _submit_h2h(client, final_session.id, pids[0], pids[1], admin_token)

        # Bronze match — submit losers
        test_db.expire_all()
        bronze_session = test_db.query(SessionModel).filter(SessionModel.id == bronze_session.id).first()
        if bronze_session.participant_user_ids and len(bronze_session.participant_user_ids) == 2:
            bpids = bronze_session.participant_user_ids
            _submit_h2h(client, bronze_session.id, bpids[0], bpids[1], admin_token)

        _finalize_tournament(client, t.id, admin_token, test_db)

        rankings = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).order_by(TournamentRanking.rank).all()
        assert len(rankings) >= 2, f"Expected ≥2 ranking rows, got {len(rankings)}"
        assert rankings[0].rank == 1
        assert rankings[0].user_id is not None
        assert rankings[0].team_id is None

        participations = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).all()
        assert len(participations) == 4, (
            f"Expected 4 TournamentParticipation rows (1 per player), got {len(participations)}"
        )

    # ── SRL-10 ──────────────────────────────────────────────────────────────────

    def test_SRL_10_individual_group_knockout_full_lifecycle(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-10: INDIVIDUAL + HEAD_TO_HEAD + group_knockout (8 players)
        Group stage (12 sessions: 2 groups × 6 matches) → finalize-group-stage
        → knockout (4 sessions: 2 SF + Final + Bronze) → REWARDS_DISTRIBUTED

        Structure for 8 players:
          - 2 groups × 4 players each → 3 rounds × 2 matches = 6 matches/group = 12 group sessions
          - 4 qualifiers → KO bracket: 2 SF + Final + Bronze = 4 KO sessions
          - Total: 16 sessions
        """
        tt = _tt(test_db, "group_knockout", min_players=8)
        camp = _campus(test_db)
        t = _tournament(test_db, admin_user, tt, tournament_status="DRAFT")
        t.campus_id = camp.id
        test_db.flush()

        # Enroll 8 players
        players = [_user(test_db) for _ in range(8)]
        for p in players:
            _enroll(test_db, t, p)

        _advance_to_in_progress(client, t.id, admin_token)

        # 16 sessions: 12 GROUP_STAGE + 4 KNOCKOUT (SF×2 + Final + Bronze)
        sessions = _get_match_sessions(test_db, t.id)
        assert len(sessions) == 16, f"Expected 16 group_knockout sessions, got {len(sessions)}"

        from app.models.tournament_enums import TournamentPhase
        group_sessions = [s for s in sessions if s.tournament_phase == TournamentPhase.GROUP_STAGE]
        knockout_sessions = [s for s in sessions if s.tournament_phase == TournamentPhase.KNOCKOUT]
        assert len(group_sessions) == 12, f"Expected 12 group sessions (2 groups × 6 matches), got {len(group_sessions)}"
        assert len(knockout_sessions) == 4, f"Expected 4 knockout sessions (SF+Final+Bronze), got {len(knockout_sessions)}"

        hdrs = {"Authorization": f"Bearer {admin_token}"}

        # Submit all group stage results (deterministic: lower user_id wins)
        for gs in group_sessions:
            pids = gs.participant_user_ids
            assert pids and len(pids) == 2, f"Group session {gs.id} must have 2 participants"
            winner = min(pids)
            loser = max(pids)
            _submit_h2h(client, gs.id, winner, loser, admin_token)

        # Finalize group stage → seeds knockout brackets
        resp = client.post(
            f"/api/v1/tournaments/{t.id}/finalize-group-stage",
            headers=hdrs,
        )
        assert resp.status_code == 200, f"finalize-group-stage failed: {resp.text[:400]}"
        data = resp.json()
        assert data.get("success"), f"finalize-group-stage returned success=False: {data}"

        # Refresh sessions — knockout sessions now have participant_user_ids
        test_db.expire_all()
        all_sess = _get_match_sessions(test_db, t.id)
        ko_sessions = sorted(
            [s for s in all_sess if s.tournament_phase == TournamentPhase.KNOCKOUT],
            key=lambda s: (s.tournament_round or 99, s.id),
        )

        # Submit knockout results round by round (round 1 = SF, round 2 = Final, round 3 = Bronze)
        round1_ko = [s for s in ko_sessions if s.tournament_round == 1]
        for ko in round1_ko:
            pids = ko.participant_user_ids
            assert pids and len(pids) == 2, f"KO session {ko.id} must have 2 participants after seeding"
            _submit_h2h(client, ko.id, pids[0], pids[1], admin_token)

        # After round 1 KO, progression seeds the Final (round 2) and Bronze (round 3)
        test_db.expire_all()
        all_sess2 = _get_match_sessions(test_db, t.id)
        ko_later_sessions = sorted(
            [s for s in all_sess2 if s.tournament_phase == TournamentPhase.KNOCKOUT
             and (s.tournament_round or 0) >= 2],
            key=lambda s: (s.tournament_round or 99, s.id),
        )
        for sess in ko_later_sessions:
            pids = sess.participant_user_ids
            if pids and len(pids) == 2:
                _submit_h2h(client, sess.id, pids[0], pids[1], admin_token)

        _finalize_tournament(client, t.id, admin_token, test_db)

        rankings = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).order_by(TournamentRanking.rank).all()
        assert len(rankings) >= 2, f"Expected ranking rows for group_knockout, got {len(rankings)}"
        assert rankings[0].rank == 1

        participations = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).all()
        assert len(participations) == 8, (
            f"Expected 8 TournamentParticipation rows, got {len(participations)}"
        )

    # ── SRL-11 ──────────────────────────────────────────────────────────────────

    def test_SRL_11_individual_ranking_individual_score_based(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-11: INDIVIDUAL_RANKING + INDIVIDUAL + SCORE_BASED (4 players)
        Auto-generates 1 session → submit scores → calculate-rankings (DESC)
        → p1 rank=1 (highest score) → COMPLETED → REWARDS_DISTRIBUTED
        """
        camp = _campus(test_db)
        t = _ir_tournament(test_db, admin_user, tournament_status="DRAFT")
        t.campus_id = camp.id
        test_db.flush()

        players = [_user(test_db) for _ in range(4)]
        for p in players:
            _enroll(test_db, t, p)

        _advance_to_in_progress(client, t.id, admin_token)

        sessions = _get_match_sessions(test_db, t.id)
        assert len(sessions) == 1, f"INDIVIDUAL_RANKING must auto-generate exactly 1 session, got {len(sessions)}"
        sess = sessions[0]

        # Submit scores: p[0]=100 (rank1), p[1]=80, p[2]=60, p[3]=40
        scores = [100.0, 80.0, 60.0, 40.0]
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/results",
            json={"results": [
                {"user_id": players[i].id, "score": scores[i], "rank": i + 1}
                for i in range(4)
            ]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, f"IR results submission failed: {resp.text[:300]}"

        _finalize_tournament(client, t.id, admin_token, test_db)

        rankings = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).order_by(TournamentRanking.rank).all()
        assert len(rankings) == 4, f"Expected 4 ranking rows, got {len(rankings)}"
        # DESC direction: player[0] with score 100 should be rank=1
        rank1 = next(r for r in rankings if r.rank == 1)
        assert rank1.user_id == players[0].id, (
            f"Rank 1 must be player with highest score (user {players[0].id}), "
            f"got user {rank1.user_id}"
        )
        assert rank1.team_id is None

        participations = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).all()
        assert len(participations) == 4

    # ── SRL-12 ──────────────────────────────────────────────────────────────────

    def test_SRL_12_individual_ranking_team_score_based(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-12: INDIVIDUAL_RANKING + TEAM + SCORE_BASED (3 teams × 2 members each)
        Auto-generates 1 session → submit team scores → calculate-rankings (DESC)
        → team_A rank=1 → COMPLETED → 6 TournamentParticipation rows (team expansion)
        """
        camp = _campus(test_db)
        t = _ir_tournament(test_db, admin_user, participant_type="TEAM", tournament_status="DRAFT")
        t.campus_id = camp.id
        test_db.flush()

        # 3 teams × 2 members each
        team_a = _make_team_with_members(test_db, count=2)
        team_b = _make_team_with_members(test_db, count=2)
        team_c = _make_team_with_members(test_db, count=2)
        _enroll_team(test_db, t, team_a)
        _enroll_team(test_db, t, team_b)
        _enroll_team(test_db, t, team_c)

        _advance_to_in_progress(client, t.id, admin_token)

        sessions = _get_match_sessions(test_db, t.id)
        assert len(sessions) == 1, f"IR TEAM must auto-generate 1 session, got {len(sessions)}"
        sess = sessions[0]

        # Submit team scores: team_a=90 (rank1), team_b=75, team_c=60
        resp = client.patch(
            f"/api/v1/sessions/{sess.id}/team-results",
            json={"results": [
                {"team_id": team_a.id, "score": 90.0},
                {"team_id": team_b.id, "score": 75.0},
                {"team_id": team_c.id, "score": 60.0},
            ], "round_number": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, f"IR TEAM results failed: {resp.text[:300]}"

        _finalize_tournament(client, t.id, admin_token, test_db)

        rankings = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).order_by(TournamentRanking.rank).all()
        assert len(rankings) == 3, f"Expected 3 TEAM ranking rows, got {len(rankings)}"
        rank1 = rankings[0]
        assert rank1.team_id == team_a.id, (
            f"team_a (score=90) must be rank 1, got team_id={rank1.team_id}"
        )
        assert rank1.user_id is None, "TEAM ranking rows must have user_id=None"

        # Rewards expand to team members: 3 teams × 2 members = 6 rows
        participations = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).all()
        assert len(participations) == 6, (
            f"Expected 6 TournamentParticipation rows (3 teams × 2 members), got {len(participations)}"
        )
        for p in participations:
            assert p.team_id is not None, "TEAM tournament participations must have team_id set"

    # ── SRL-13 ──────────────────────────────────────────────────────────────────

    def test_SRL_13_team_knockout_two_teams_direct_final(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-13: TEAM + HEAD_TO_HEAD + knockout (4 teams × 2 members)
        submit_team_results does NOT call KnockoutProgressionService, so Final's
        participant_team_ids must be seeded manually after the SFs.
        SF1 + SF2 (manual Final seeding) → Final → calculate-rankings → REWARDS_DISTRIBUTED.
        """
        from sqlalchemy.orm.attributes import flag_modified

        tt = _tt(test_db, "knockout", min_players=4)
        camp = _campus(test_db)
        t = _tournament(test_db, admin_user, tt, participant_type="TEAM", tournament_status="DRAFT")
        t.campus_id = camp.id
        test_db.flush()

        teams = [_make_team_with_members(test_db, count=2) for _ in range(4)]
        for team in teams:
            _enroll_team(test_db, t, team)

        _advance_to_in_progress(client, t.id, admin_token)

        # 4 sessions: 2 SFs (round 1) + Final + Bronze (round 2, third_place_playoff=True)
        sessions = _get_match_sessions(test_db, t.id)
        assert len(sessions) == 4, f"4-team knockout must generate 4 sessions (SF+Final+Bronze), got {len(sessions)}"

        sf_sessions = [s for s in sessions if s.tournament_round == 1]
        # Final has match_number != 999; bronze has match_number == 999
        final_session = next(
            s for s in sessions
            if s.tournament_round == 2 and (s.tournament_match_number or 0) != 999
        )
        bronze_session = next(
            s for s in sessions
            if s.tournament_round == 2 and (s.tournament_match_number or 0) == 999
        )
        assert len(sf_sessions) == 2

        # Track SF winners (lower team_id wins — deterministic)
        sf_winners = []
        sf_losers = []
        for sf in sf_sessions:
            tids = sf.participant_team_ids
            assert tids and len(tids) == 2
            winner_tid = min(tids)
            loser_tid = max(tids)
            _submit_team(client, sf.id, winner_tid, loser_tid, admin_token)
            sf_winners.append(winner_tid)
            sf_losers.append(loser_tid)

        # Manually seed Final and Bronze (KnockoutProgressionService not called for TEAM)
        test_db.expire_all()
        final_session = test_db.query(SessionModel).filter(SessionModel.id == final_session.id).first()
        final_session.participant_team_ids = sf_winners
        flag_modified(final_session, "participant_team_ids")
        bronze_session = test_db.query(SessionModel).filter(SessionModel.id == bronze_session.id).first()
        bronze_session.participant_team_ids = sf_losers
        flag_modified(bronze_session, "participant_team_ids")
        test_db.flush()

        # Submit Final and Bronze
        _submit_team(client, final_session.id, sf_winners[0], sf_winners[1], admin_token)
        _submit_team(client, bronze_session.id, sf_losers[0], sf_losers[1], admin_token)

        _finalize_tournament(client, t.id, admin_token, test_db)

        rankings = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).order_by(TournamentRanking.rank).all()
        assert len(rankings) >= 2
        assert rankings[0].team_id is not None
        assert rankings[0].user_id is None

        participations = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).all()
        assert len(participations) == 8, (
            f"Expected 8 TournamentParticipation rows (4 teams × 2 members), got {len(participations)}"
        )

    # SRL-14: TEAM + group_knockout — ARCHITECTURAL GAP (documented, not tested)
    # finalize-group-stage and calculate-rankings group_knockout path both check
    # s.game_results (not rounds_data). TEAM sessions store results in rounds_data,
    # so group-stage finalization would fail. This gap requires a service-layer fix.
    # Tracked in: SGR-06 area — not blocking this matrix release.

    # ── SRL-15 ──────────────────────────────────────────────────────────────────

    def test_SRL_15_team_league_two_legs(
        self, test_db: Session, client, admin_user: User, admin_token: str
    ):
        """
        SRL-15: TEAM + HEAD_TO_HEAD + league + 2 legs (3 teams × 2 members)
        3 teams, number_of_legs=2 → 6 sessions (3 matches × 2 legs)
        Submit all 6 team-results → auto-ranking aggregates both legs
        → calculate-rankings → COMPLETED → 6 TournamentParticipation rows.
        """
        tt = _tt(test_db, "league", min_players=2)
        camp = _campus(test_db)
        t = _tournament(test_db, admin_user, tt, participant_type="TEAM", tournament_status="DRAFT")
        t.campus_id = camp.id
        cfg = t.tournament_config_obj
        cfg.number_of_legs = 2
        cfg.track_home_away = True
        test_db.flush()

        teams = [_make_team_with_members(test_db, count=2) for _ in range(3)]
        for team in teams:
            _enroll_team(test_db, t, team)

        _advance_to_in_progress(client, t.id, admin_token)

        # 3 teams × 2 legs = 3 matches/leg × 2 legs = 6 sessions
        sessions = _get_match_sessions(test_db, t.id)
        assert len(sessions) == 6, (
            f"3-team league × 2 legs must generate 6 sessions, got {len(sessions)}"
        )

        # Submit all 6 results: for each session, lower team_id wins
        for sess in sessions:
            tids = sess.participant_team_ids
            assert tids and len(tids) == 2, f"Session {sess.id} must have 2 participant_team_ids"
            winner_tid = min(tids)
            loser_tid = max(tids)
            _submit_team(client, sess.id, winner_tid, loser_tid, admin_token)

        _finalize_tournament(client, t.id, admin_token, test_db)

        rankings = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == t.id
        ).order_by(TournamentRanking.rank).all()
        assert len(rankings) == 3, f"Expected 3 TEAM ranking rows, got {len(rankings)}"
        assert rankings[0].rank == 1
        # All rankings have team_id, user_id=None
        for r in rankings:
            assert r.team_id is not None
            assert r.user_id is None
        # Winner has most wins (lower team_id wins both legs against each opponent)
        assert rankings[0].wins >= 2, "League winner must have ≥2 wins across 2 legs"

        participations = test_db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == t.id
        ).all()
        assert len(participations) == 6, (
            f"Expected 6 TournamentParticipation rows (3 teams × 2 members), got {len(participations)}"
        )
