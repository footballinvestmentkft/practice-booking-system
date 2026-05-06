"""
Smoke tests for scripts/seed_promotion_events.py

Calls the script's importable entry point `run_scenarios(db, client, ...)`
directly — no inline logic copied from the script.

What is tested:
  1. Preflight detects a missing/broken 9_players config and exits early.
  2. Dry-run returns None for all scenario IDs (no DB writes).
  3. SC-01 creates a DRAFT tournament with the PROMO-SEED- prefix.
  4. SC-04 completes without sys.exit: session structure validates (13 sessions,
     correct phase breakdown, correct SF matchup labels).

Each test uses a real DB session with SAVEPOINT isolation — all writes are
rolled back at teardown so no cleanup step is needed.
"""

from __future__ import annotations

import os
import importlib.util
import pathlib
import sys
import types
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import (
    get_current_admin_or_instructor_user_hybrid,
    get_current_admin_user_hybrid,
    get_current_user_web,
)
from app.models.tournament_enums import TournamentPhase
from app.models.tournament_type import TournamentType
from app.models.semester import Semester, SemesterCategory
from app.models.session import Session as SessionModel
from app.models.user import User, UserRole
from app.core.security import get_password_hash

# ─── Load the script module at collection time ───────────────────────────────

_SCRIPT_PATH = pathlib.Path(__file__).parents[2] / "scripts" / "seed_promotion_events.py"

def _load_seed_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("seed_promotion_events", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_seed = _load_seed_module()
run_scenarios = _seed.run_scenarios
run_reset = _seed.run_reset
_preflight_group_knockout_9p = _seed._preflight_group_knockout_9p
_validate_sc04 = _seed._validate_sc04


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def admin_user(test_db: Session) -> User:
    user = test_db.query(User).filter(User.email == "admin@lfa.com").first()
    if not user:
        pytest.skip("admin@lfa.com not found — run bootstrap first")
    return user


@pytest.fixture()
def seed_client(test_db: Session, admin_user: User) -> TestClient:
    """TestClient with get_db and auth overrides wired to the SAVEPOINT session."""

    def _override_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user_web] = lambda: admin_user
    app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin_user
    app.dependency_overrides[get_current_admin_or_instructor_user_hybrid] = lambda: admin_user

    client = TestClient(app, follow_redirects=False)
    yield client

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user_web, None)
    app.dependency_overrides.pop(get_current_admin_user_hybrid, None)
    app.dependency_overrides.pop(get_current_admin_or_instructor_user_hybrid, None)


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestPreflight:
    def test_preflight_passes_with_valid_db(self, test_db: Session) -> None:
        """Preflight should succeed when DB has the 9_players policy."""
        tt = _preflight_group_knockout_9p(test_db)
        nine = tt.config["group_configuration"]["9_players"]
        assert nine["qualification_policy"] == "winners_plus_best_runner_up"
        assert int(nine["best_runner_up_count"]) == 1

    def test_preflight_fails_if_9_players_missing(
        self, test_db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Preflight must sys.exit when 9_players config is absent."""
        tt = test_db.query(TournamentType).filter(
            TournamentType.code == "group_knockout"
        ).first()
        if not tt:
            pytest.skip("group_knockout TournamentType not in DB")

        original_config = tt.config

        def _patched_query(*args, **kwargs):
            class _FakeTT:
                id = tt.id
                config = {
                    "group_configuration": {
                        # 9_players deliberately absent
                        "8_players": {"groups": 2, "qualifiers": 2},
                    },
                    "round_names": original_config.get("round_names", {}),
                }

            class _FakeQuery:
                def filter(self, *a, **kw):
                    return self

                def first(self):
                    return _FakeTT()

            return _FakeQuery()

        monkeypatch.setattr(test_db, "query", _patched_query)

        with pytest.raises(SystemExit):
            _preflight_group_knockout_9p(test_db)


class TestDryRun:
    def test_dry_run_writes_nothing(
        self, test_db: Session, seed_client: TestClient
    ) -> None:
        """Dry-run must return None for every scenario and create no DB rows."""
        from app.models.sponsor import Sponsor

        sponsor_before = (
            test_db.query(Sponsor)
            .filter(Sponsor.name == "SEED-SPONSOR")
            .count()
        )

        results = run_scenarios(
            test_db,
            seed_client,
            scenario_ids=["SC-01"],
            dry_run=True,
        )

        sponsor_after = (
            test_db.query(Sponsor)
            .filter(Sponsor.name == "SEED-SPONSOR")
            .count()
        )

        assert results["SC-01"] is None
        assert sponsor_after == sponsor_before


class TestSC01:
    def test_sc01_creates_draft_tournament(
        self, test_db: Session, seed_client: TestClient
    ) -> None:
        """SC-01: creates one PROMO-SEED-* tournament in DRAFT status."""
        results = run_scenarios(
            test_db,
            seed_client,
            scenario_ids=["SC-01"],
            dry_run=False,
        )

        tid = results.get("SC-01")
        assert tid is not None, "SC-01 must return a tournament id"

        test_db.expire_all()
        t = test_db.query(Semester).filter(Semester.id == tid).first()
        assert t is not None
        assert t.tournament_status == "DRAFT"
        assert t.semester_category == SemesterCategory.PROMOTION_EVENT
        assert t.name.startswith("PROMO-SEED-")
        assert t.organizer_sponsor_id is not None
        assert t.organizer_campaign_id is not None


class TestSC04:
    def test_sc04_session_structure_valid(
        self, test_db: Session, seed_client: TestClient
    ) -> None:
        """SC-04: 13 sessions with correct phase breakdown and SF matchup labels.

        This is the hard-failure gate: _validate_sc04 exits with sys.exit(1) if
        anything is wrong, so any failure here is a real structural regression.
        """
        results = run_scenarios(
            test_db,
            seed_client,
            scenario_ids=["SC-04"],
            dry_run=False,
        )

        tid = results.get("SC-04")
        assert tid is not None, "SC-04 must return a tournament id"

        test_db.expire_all()
        sessions = (
            test_db.query(SessionModel)
            .filter(SessionModel.semester_id == tid)
            .all()
        )

        # Re-run the hard validation via the script's own function
        # (redundant but makes failures immediately actionable in pytest output)
        _validate_sc04(test_db, tid, "smoke-test")

        total = len(sessions)
        assert total == 13, f"expected 13 sessions, got {total}"

        by_phase_and_type = {
            "group_stage": 0,
            "play_in": 0,
            "semi_finals": 0,
            "final": 0,
            "bronze": 0,
        }
        sf_matchups: list[str] = []

        for s in sessions:
            phase = s.tournament_phase
            gt = s.game_type or ""
            if phase in (TournamentPhase.GROUP_STAGE, TournamentPhase.GROUP_STAGE.value):
                by_phase_and_type["group_stage"] += 1
            elif gt == "Play-in Round":
                by_phase_and_type["play_in"] += 1
            elif gt == "Semi-finals":
                by_phase_and_type["semi_finals"] += 1
                sc = s.structure_config or {}
                matchup = sc.get("matchup", "")
                if matchup:
                    sf_matchups.append(matchup)
            elif gt == "Final":
                by_phase_and_type["final"] += 1
            elif gt == "3rd Place Match":
                by_phase_and_type["bronze"] += 1

        assert by_phase_and_type["group_stage"] == 9, by_phase_and_type
        assert by_phase_and_type["play_in"] == 0, by_phase_and_type
        assert by_phase_and_type["semi_finals"] == 2, by_phase_and_type
        assert by_phase_and_type["final"] == 1, by_phase_and_type
        assert by_phase_and_type["bronze"] == 1, by_phase_and_type

        assert frozenset(sf_matchups) == frozenset({
            "Group A winner vs Best runner-up",
            "Group B winner vs Group C winner",
        }), f"SF matchup labels wrong: {sf_matchups}"
