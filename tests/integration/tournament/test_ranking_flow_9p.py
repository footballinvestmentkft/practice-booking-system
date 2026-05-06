"""
Integration tests: 9-player group-knockout ranking flow

RK-01  POST /calculate-rankings on a 9-player tournament with all group
       results entered returns HTTP 200.
RK-02  After POST, both SF sessions have participant_user_ids set (non-null).
RK-03  After POST, Final and 3rd Place sessions still have participant_user_ids=None.
RK-04  SF1 participant_user_ids == [Group A winner, best runner-up (uid order).
RK-05  POST called twice (idempotency) → same participant_user_ids, HTTP 200 both times.

Fixture dependency tree (all function-scoped, SAVEPOINT-isolated):
  test_db
  ├── instructor_user   (from tests/integration/conftest.py)
  ├── _nine_player_tournament
  ├── _nine_players
  ├── _group_sessions   (9 GROUP_STAGE sessions, results populated)
  └── _ko_sessions      (4 KNOCKOUT sessions, participants=None at creation)
"""

import json
import uuid
import pytest
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user, get_current_admin_or_instructor_user_hybrid
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.session import Session as SessionModel, EventCategory
from app.models.tournament_type import TournamentType
from app.models.tournament_configuration import TournamentConfiguration
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.license import UserLicense
from app.models.tournament_enums import TournamentPhase
from app.models.user import User, UserRole
from app.core.security import get_password_hash


# ── Helpers ────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_h2h_result(uid1: int, score1: int, uid2: int, score2: int) -> dict:
    r1 = "win" if score1 > score2 else ("draw" if score1 == score2 else "loss")
    r2 = "win" if score2 > score1 else ("draw" if score2 == score1 else "loss")
    return {
        "match_format": "HEAD_TO_HEAD",
        "participants": [
            {"user_id": uid1, "result": r1, "score": score1},
            {"user_id": uid2, "result": r2, "score": score2},
        ],
    }


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def _gk_tournament_type(test_db: Session) -> TournamentType:
    """Group-knockout TournamentType with winners_plus_best_runner_up policy
    stored in group_configuration.9_players (production-equivalent structure)."""
    tt = TournamentType(
        code=f"group_knockout_{_uid()}",
        display_name="Group + KO (9p test)",
        description="Test fixture",
        format="HEAD_TO_HEAD",
        min_players=8,
        max_players=32,
        requires_power_of_two=False,
        session_duration_minutes=90,
        break_between_sessions_minutes=15,
        config={
            "round_names": {"4": "Semi-Finals", "2": "Finals"},
            "group_configuration": {
                "9_players": {
                    "groups": 3,
                    "players_per_group": 3,
                    "qualifiers": 1,
                    "qualification_policy": "winners_plus_best_runner_up",
                    "best_runner_up_count": 1,
                }
            },
        },
    )
    test_db.add(tt)
    test_db.commit()
    test_db.refresh(tt)
    return tt


@pytest.fixture()
def _nine_players(test_db: Session) -> list[User]:
    players = []
    for i in range(1, 10):
        u = User(
            email=f"gk9p+p{i}+{_uid()}@test.com",
            name=f"Player {i}",
            password_hash=get_password_hash("pw"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(u)
        test_db.flush()
        players.append(u)
    test_db.commit()
    for u in players:
        test_db.refresh(u)
    return players


@pytest.fixture()
def _nine_player_tournament(
    test_db: Session,
    instructor_user: User,
    _gk_tournament_type: TournamentType,
    _nine_players: list[User],
) -> Semester:
    sem = Semester(
        code=f"GK9P-{_uid()}",
        name="9-Player GK Cup",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.MINI_SEASON,
        enrollment_cost=0,
        master_instructor_id=instructor_user.id,
        tournament_status="IN_PROGRESS",
    )
    test_db.add(sem)
    test_db.flush()

    test_db.add(TournamentConfiguration(
        semester_id=sem.id,
        participant_type="INDIVIDUAL",
        sessions_generated=True,
        tournament_type_id=_gk_tournament_type.id,
    ))

    for u in _nine_players:
        lic = UserLicense(
            user_id=u.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            is_active=True,
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(lic)
        test_db.flush()
        test_db.add(SemesterEnrollment(
            semester_id=sem.id,
            user_id=u.id,
            user_license_id=lic.id,
            is_active=True,
            request_status=EnrollmentStatus.APPROVED,
        ))

    test_db.commit()
    test_db.refresh(sem)
    return sem


def _create_group_sessions(
    db: Session,
    sem: Semester,
    players: list[User],
) -> list[SessionModel]:
    """
    Create 9 GROUP_STAGE HEAD_TO_HEAD sessions (3 groups × 3 round-robin matches)
    with pre-populated game_results.

    Group assignments:
      A: players[0], players[1], players[2]
      B: players[3], players[4], players[5]
      C: players[6], players[7], players[8]

    Results designed so:
      Group A winner: players[0] (6 pts)  runner-up: players[1] (3 pts)
      Group B winner: players[3] (6 pts)  runner-up: players[4] (3 pts)
      Group C winner: players[6] (6 pts)  runner-up: players[7] (3 pts)

    Best runner-up: players[1] (GF=4), vs players[4] (GF=3), vs players[7] (GF=2)
    → players[1] is best runner-up.
    """
    sessions = []
    groups = {
        "A": players[0:3],
        "B": players[3:6],
        "C": players[6:9],
    }
    # Scores: p0 beats p1 (3-1), p0 beats p2 (2-0), p1 beats p2 (4-0)
    # → p0: 6pts, GF=5, GA=1 | p1: 3pts, GF=5, GA=3 | p2: 0pts, GF=0, GA=6
    # Adjust per group: Group B runner-up (p4) gets GF=3, Group C (p7) gets GF=2

    def group_results(pa, pb, pc, runner_up_gf_vs_last):
        return [
            (pa.id, 3, pb.id, 1),                      # winner beats runner-up
            (pa.id, 2, pc.id, 0),                      # winner beats last
            (pb.id, runner_up_gf_vs_last, pc.id, 0),   # runner-up beats last
        ]

    match_data = {
        "A": group_results(players[0], players[1], players[2], 4),
        "B": group_results(players[3], players[4], players[5], 3),
        "C": group_results(players[6], players[7], players[8], 2),
    }

    rn = 0
    for grp, matches in match_data.items():
        for mn, (u1, s1, u2, s2) in enumerate(matches, start=1):
            rn += 1
            s = SessionModel(
                title=f"GK Cup - Group {grp} - Round {mn} - Match 1",
                date_start=datetime(2026, 9, 1, 9, 0) + timedelta(minutes=rn * 105),
                date_end=datetime(2026, 9, 1, 9, 0) + timedelta(minutes=rn * 105 + 90),
                semester_id=sem.id,
                match_format="HEAD_TO_HEAD",
                auto_generated=True,
                credit_cost=0,
                event_category=EventCategory.MATCH,
                tournament_phase=TournamentPhase.GROUP_STAGE,
                tournament_round=mn,
                tournament_match_number=mn,
                group_identifier=grp,
                participant_user_ids=[u1, u2],
                game_results=json.dumps(_make_h2h_result(u1, s1, u2, s2)),
            )
            db.add(s)
            sessions.append(s)

    db.flush()
    return sessions


def _create_ko_sessions(
    db: Session,
    sem: Semester,
) -> dict[str, SessionModel]:
    """Create 4 KNOCKOUT sessions (SF1, SF2, Final, Bronze) with slot metadata."""
    base = datetime(2026, 9, 1, 15, 0)

    sf1 = SessionModel(
        title="GK Cup - Semi-Finals - Match 1",
        date_start=base,
        date_end=base + timedelta(minutes=90),
        semester_id=sem.id,
        match_format="HEAD_TO_HEAD",
        auto_generated=True,
        credit_cost=0,
        event_category=EventCategory.MATCH,
        tournament_phase=TournamentPhase.KNOCKOUT,
        tournament_round=1,
        tournament_match_number=1,
        structure_config={
            "matchup": "Group A winner vs Best runner-up",
            "seed_1": "A1",
            "seed_2": "BR",
            "round_name": "Semi-Finals",
        },
    )
    sf2 = SessionModel(
        title="GK Cup - Semi-Finals - Match 2",
        date_start=base + timedelta(minutes=105),
        date_end=base + timedelta(minutes=195),
        semester_id=sem.id,
        match_format="HEAD_TO_HEAD",
        auto_generated=True,
        credit_cost=0,
        event_category=EventCategory.MATCH,
        tournament_phase=TournamentPhase.KNOCKOUT,
        tournament_round=1,
        tournament_match_number=2,
        structure_config={
            "matchup": "Group B winner vs Group C winner",
            "seed_1": "B1",
            "seed_2": "C1",
            "round_name": "Semi-Finals",
        },
    )
    final = SessionModel(
        title="GK Cup - Finals - Match 1",
        date_start=base + timedelta(minutes=210),
        date_end=base + timedelta(minutes=300),
        semester_id=sem.id,
        match_format="HEAD_TO_HEAD",
        auto_generated=True,
        credit_cost=0,
        event_category=EventCategory.MATCH,
        tournament_phase=TournamentPhase.KNOCKOUT,
        tournament_round=2,
        tournament_match_number=1,
        structure_config={"matchup": "SF1 winner vs SF2 winner"},
    )
    bronze = SessionModel(
        title="GK Cup - 3rd Place Match",
        date_start=base + timedelta(minutes=315),
        date_end=base + timedelta(minutes=405),
        semester_id=sem.id,
        match_format="HEAD_TO_HEAD",
        auto_generated=True,
        credit_cost=0,
        event_category=EventCategory.MATCH,
        tournament_phase=TournamentPhase.KNOCKOUT,
        tournament_round=3,
        tournament_match_number=1,
        structure_config={"matchup": "SF1 loser vs SF2 loser"},
    )
    for s in (sf1, sf2, final, bronze):
        db.add(s)
    db.flush()
    return {"sf1": sf1, "sf2": sf2, "final": final, "bronze": bronze}


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestRankingFlow9Player:

    @pytest.fixture(autouse=True)
    def setup(
        self,
        test_db: Session,
        instructor_user: User,
        _nine_player_tournament: Semester,
        _nine_players: list[User],
    ):
        self.db = test_db
        self.sem = _nine_player_tournament
        self.players = _nine_players
        self.instructor = instructor_user

        _create_group_sessions(test_db, _nine_player_tournament, _nine_players)
        self.ko = _create_ko_sessions(test_db, _nine_player_tournament)
        test_db.commit()

        # Auth override
        def _override_db():
            yield test_db

        def _override_user():
            return instructor_user

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_current_admin_or_instructor_user_hybrid] = _override_user
        self.client = TestClient(app, raise_server_exceptions=True)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def _post_rankings(self):
        return self.client.post(
            f"/api/v1/tournaments/{self.sem.id}/calculate-rankings"
        )

    def test_rk01_calculate_rankings_returns_200(self):
        resp = self._post_rankings()
        assert resp.status_code == 200, resp.text

    def test_rk02_sf_sessions_get_participants_after_post(self):
        self._post_rankings()
        self.db.expire_all()

        sf1 = self.db.get(SessionModel, self.ko["sf1"].id)
        sf2 = self.db.get(SessionModel, self.ko["sf2"].id)
        assert sf1.participant_user_ids is not None
        assert len(sf1.participant_user_ids) == 2
        assert sf2.participant_user_ids is not None
        assert len(sf2.participant_user_ids) == 2

    def test_rk03_final_and_bronze_remain_null(self):
        self._post_rankings()
        self.db.expire_all()

        final = self.db.get(SessionModel, self.ko["final"].id)
        bronze = self.db.get(SessionModel, self.ko["bronze"].id)
        assert final.participant_user_ids is None
        assert bronze.participant_user_ids is None

    def test_rk04_sf1_participants_are_a_winner_and_best_runner_up(self):
        self._post_rankings()
        self.db.expire_all()

        # Group A winner = players[0], best runner-up = players[1] (GF=4 highest)
        expected_a_winner = self.players[0].id
        expected_br = self.players[1].id

        sf1 = self.db.get(SessionModel, self.ko["sf1"].id)
        assert expected_a_winner in sf1.participant_user_ids
        assert expected_br in sf1.participant_user_ids

    def test_rk05_idempotent_double_call(self):
        self._post_rankings()
        self.db.expire_all()
        sf1_first = list(self.db.get(SessionModel, self.ko["sf1"].id).participant_user_ids)

        resp2 = self._post_rankings()
        assert resp2.status_code == 200
        self.db.expire_all()
        sf1_second = list(self.db.get(SessionModel, self.ko["sf1"].id).participant_user_ids)

        assert sf1_first == sf1_second
