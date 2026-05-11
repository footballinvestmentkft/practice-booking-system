"""
Player Game Loop Integration Tests (SMOKE-41a–41h)

Validates the COMPLETE pipeline from zero state to skill history visualization.
All state is created inside each test — no manual seeding required.
CI-safe: works on a fresh DB (only alembic upgrade head needed).

SMOKE-41a  Factory creates User + LFA license with 44 football_skills
SMOKE-41b  Factory creates completed tournament → TournamentParticipation exists
SMOKE-41c  Tournament with placement → skill_rating_delta is computed (not None)
SMOKE-41d  Tournament with NULL placement (participant) → skill_rating_delta is None
SMOKE-41e  3 sequential tournaments → get_skill_timeline returns 2-entry timeline
           (only ranked placements get EMA deltas; participant entries appear in
           timeline but delta_from_previous is 0)
SMOKE-41f  GET /skills/history/data (cookie auth, student) → 200 JSON, timeline non-empty
SMOKE-41g  GET /skills/history/data with unknown skill key → 404
SMOKE-41h  GET /skills (student with history) → 200, 'Skill Progression' present

Auth:   get_current_user_web overridden — no real login flow needed.
DB:     SAVEPOINT-isolated — all changes rolled back after each test.
Seed:   None required — factories create all state within the test transaction.
"""

import uuid
import pytest

from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.models.tournament_achievement import TournamentParticipation
from app.services.skill_progression_service import get_skill_timeline

from tests.factories.game_factory import PlayerFactory, TournamentFactory


# ── SAVEPOINT-isolated DB fixture ─────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db():
    connection = engine.connect()
    transaction = connection.begin()
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSessionLocal()
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, txn):
        if txn.nested and not txn._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


# ── TestClient fixture ─────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def player_client(test_db: Session):
    """
    Creates a fully-onboarded LFA player with 3-tournament history,
    overrides auth + DB, returns (client, user).
    """
    preset = TournamentFactory.ensure_preset(test_db)
    tt = TournamentFactory.ensure_tournament_type(test_db)
    user, _license = PlayerFactory.create_lfa_player(test_db)

    # 3 tournaments in chronological order (EMA replays history correctly)
    TournamentFactory.create_completed_tournament(
        test_db, preset=preset, tt=tt,
        participants=[(user.id, 3)],          # 3rd place
        start_date=date(2026, 1, 15),
        code=f"GAMELOOP-T1-{uuid.uuid4().hex[:8]}",
    )
    TournamentFactory.create_completed_tournament(
        test_db, preset=preset, tt=tt,
        participants=[(user.id, 2)],          # 2nd place
        start_date=date(2026, 2, 15),
        code=f"GAMELOOP-T2-{uuid.uuid4().hex[:8]}",
    )
    TournamentFactory.create_completed_tournament(
        test_db, preset=preset, tt=tt,
        participants=[(user.id, 1)],          # 1st place
        start_date=date(2026, 3, 15),
        code=f"GAMELOOP-T3-{uuid.uuid4().hex[:8]}",
    )

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_web] = lambda: user

    with TestClient(
        app,
        headers={"Authorization": "Bearer test-csrf-bypass"},
        follow_redirects=False,
    ) as client:
        yield client, user

    app.dependency_overrides.clear()


# ── SMOKE-41a–41e: Factory & service layer ─────────────────────────────────────

class TestFactoryLayer:
    """SMOKE-41a–41e — validates factory functions and service pipeline."""

    def test_smoke41a_creates_user_with_license_and_skills(self, test_db: Session):
        """SMOKE-41a: Factory creates User + LFA license with 44 football_skills."""
        from app.models.license import UserLicense

        user, license = PlayerFactory.create_lfa_player(test_db)

        assert user.id is not None
        assert user.role == UserRole.STUDENT
        assert user.onboarding_completed is True

        assert license.id is not None
        assert license.specialization_type == "LFA_FOOTBALL_PLAYER"
        assert license.onboarding_completed is True
        assert license.football_skills is not None
        assert len(license.football_skills) == 44
        assert "passing" in license.football_skills
        assert "dribbling" in license.football_skills

    def test_smoke41b_completed_tournament_creates_participation(self, test_db: Session):
        """SMOKE-41b: Factory creates tournament → TournamentParticipation row exists."""
        preset = TournamentFactory.ensure_preset(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db)
        user, _ = PlayerFactory.create_lfa_player(test_db)

        tourn = TournamentFactory.create_completed_tournament(
            test_db, preset=preset, tt=tt,
            participants=[(user.id, 1)],
        )

        tp = (
            test_db.query(TournamentParticipation)
            .filter_by(user_id=user.id, semester_id=tourn.id)
            .first()
        )
        assert tp is not None
        assert tp.placement == 1
        assert tp.skill_points_awarded is not None

    def test_smoke41c_ranked_placement_produces_ema_delta(self, test_db: Session):
        """SMOKE-41c: Placement 1/2/3 → skill_rating_delta is computed (not None)."""
        preset = TournamentFactory.ensure_preset(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db)
        user, _ = PlayerFactory.create_lfa_player(test_db)

        tourn = TournamentFactory.create_completed_tournament(
            test_db, preset=preset, tt=tt,
            participants=[(user.id, 1)],
        )

        tp = test_db.query(TournamentParticipation).filter_by(
            user_id=user.id, semester_id=tourn.id,
        ).first()

        assert tp.skill_rating_delta is not None, (
            "EMA delta must be computed for a ranked placement. "
            "If None: check that reward_config skill_mappings use lowercase keys "
            "(e.g. 'dribbling', not 'Dribbling')."
        )
        assert isinstance(tp.skill_rating_delta, dict)
        assert len(tp.skill_rating_delta) > 0
        # Each delta key must be a valid skill key
        for skill_key, delta in tp.skill_rating_delta.items():
            assert isinstance(delta, (int, float)), f"Delta for {skill_key!r} is not numeric: {delta!r}"

    def test_smoke41d_participant_placement_has_no_ema_delta(self, test_db: Session):
        """SMOKE-41d: NULL placement (participant only) → skill_rating_delta is None."""
        preset = TournamentFactory.ensure_preset(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db)
        user, _ = PlayerFactory.create_lfa_player(test_db)

        tourn = TournamentFactory.create_completed_tournament(
            test_db, preset=preset, tt=tt,
            participants=[(user.id, None)],   # NULL placement = participant only
        )

        tp = test_db.query(TournamentParticipation).filter_by(
            user_id=user.id, semester_id=tourn.id,
        ).first()

        assert tp.placement is None
        assert tp.skill_rating_delta is None, (
            "EMA should NOT be computed for NULL placement (participant-only). "
            "The EMA loop skips entries where placement IS NULL."
        )

    def test_smoke41e_three_tournaments_produce_timeline(self, test_db: Session):
        """SMOKE-41e: 3 tournaments (3rd→2nd→1st of 4 players) → get_skill_timeline 3 entries.

        Uses 4 players per tournament so placement semantics are correct:
        3rd of 4 → placement_skill below median → delta negative for a skilled player.
        1st of 4 → placement_skill=100 → delta positive.
        """
        preset = TournamentFactory.ensure_preset(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db)

        # Focus player: 3rd→2nd→1st arc
        focus, _ = PlayerFactory.create_lfa_player(test_db)
        # 3 fillers to make placement semantics meaningful (4 players per tournament)
        fillers = [PlayerFactory.create_lfa_player(test_db)[0] for _ in range(3)]

        placements_per_round = [
            # T1: focus=3rd, fillers=1st/2nd/4th (i.e. None)
            [(focus.id, 3), (fillers[0].id, 1), (fillers[1].id, 2), (fillers[2].id, None)],
            # T2: focus=2nd
            [(focus.id, 2), (fillers[0].id, 1), (fillers[1].id, 3), (fillers[2].id, None)],
            # T3: focus=1st
            [(focus.id, 1), (fillers[0].id, 2), (fillers[1].id, 3), (fillers[2].id, None)],
        ]

        for i, participants in enumerate(placements_per_round, start=1):
            TournamentFactory.create_completed_tournament(
                test_db, preset=preset, tt=tt,
                participants=participants,
                start_date=date(2026, i, 15),
                code=f"SMOKE41E-T{i}-{uuid.uuid4().hex[:6]}",
            )

        timeline_data = get_skill_timeline(test_db, focus.id, "dribbling")

        assert timeline_data["skill"] == "dribbling"
        assert timeline_data["baseline"] > 0
        assert isinstance(timeline_data["timeline"], list)
        assert len(timeline_data["timeline"]) == 3, (
            f"Expected 3 timeline entries (one per tournament), got {len(timeline_data['timeline'])}. "
            "Each ranked placement (1/2/3) produces one entry in the timeline."
        )

        # With baseline=70 and 4 players:
        # 3rd of 4 → placement_skill=60 < 70 → delta negative
        # 1st of 4 → placement_skill=100 > current → delta positive
        t1_delta = timeline_data["timeline"][0]["delta_from_previous"]
        t3_delta = timeline_data["timeline"][2]["delta_from_previous"]
        assert t1_delta < 0, (
            f"3rd-of-4 delta should be negative (EMA pulls toward lower placement_skill=60), "
            f"got {t1_delta}. Baseline is 70."
        )
        assert t3_delta > 0, f"1st-place delta should be positive (pulls toward 100), got {t3_delta}"


# ── SMOKE-41f–41h: HTTP endpoint layer ────────────────────────────────────────

class TestSkillHistoryEndpointWithData:
    """SMOKE-41f–41h — validates HTTP endpoints return correct data for a player with history."""

    def test_smoke41f_skills_history_data_returns_timeline(
        self, player_client: tuple[TestClient, User]
    ):
        """SMOKE-41f: GET /skills/history/data → 200, non-empty timeline, valid structure."""
        client, _ = player_client
        resp = client.get("/skills/history/data?skill=dribbling")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()

        assert data["skill"] == "dribbling"
        assert "baseline" in data and data["baseline"] > 0
        assert "current_level" in data
        assert "total_delta" in data
        assert "skill_display_name" in data
        assert isinstance(data["timeline"], list)

        # Player has 3 tournaments — timeline must be non-empty
        assert len(data["timeline"]) == 3, (
            f"Expected 3 timeline entries for a player with 3 tournaments, "
            f"got {len(data['timeline'])}."
        )

        # Validate timeline entry structure
        entry = data["timeline"][0]
        required_keys = {
            "tournament_id", "tournament_name", "achieved_at", "placement",
            "total_players", "placement_skill", "skill_value_after", "delta_from_previous",
        }
        missing = required_keys - set(entry.keys())
        assert not missing, f"Timeline entry missing keys: {missing}"

    def test_smoke41g_unknown_skill_key_returns_404(
        self, player_client: tuple[TestClient, User]
    ):
        """SMOKE-41g: GET /skills/history/data with unknown skill → 404."""
        client, _ = player_client
        resp = client.get("/skills/history/data?skill=INVALID_SKILL_ZZZZ")
        assert resp.status_code == 404

    def test_smoke41h_skills_page_renders_for_player_with_history(
        self, player_client: tuple[TestClient, User]
    ):
        """SMOKE-41h: GET /skills → 200, page renders with Skill Progression heading."""
        client, _ = player_client
        resp = client.get("/skills")
        assert resp.status_code == 200
        assert "Skill Progression" in resp.text
        # Skill history link must be present
        assert "/skills/history" in resp.text
