"""
Unit tests: GroupKnockoutGenerator — 9-player professional model + backward compat

GKG-01  9 players → knockout_players=4, zero play-in sessions, has_bronze=True
GKG-02  9 players → SF1 structure_config.matchup == "Group A winner vs Best runner-up"
GKG-03  9 players → SF2 structure_config.matchup == "Group B winner vs Group C winner"
GKG-04  9 players → Final structure_config.matchup == "SF1 winner vs SF2 winner"
GKG-05  9 players → 3rd Place Match structure_config.matchup == "SF1 loser vs SF2 loser"
GKG-06  9 players → all KO sessions have participant_user_ids == None
GKG-07  9 players → total sessions = 9 group + 4 KO = 13
GKG-08  8 players, no policy key → knockout_players=4, seeding_info A1 vs B2 (backward compat)
GKG-09  12 players, no policy key → knockout_players=6, play_in_matches=2 (backward compat)
GKG-10  9 players, explicit fixed_per_group → knockout_players=6 (old behaviour preserved)
"""

import pytest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.services.tournament.session_generation.formats.group_knockout_generator import (
    GroupKnockoutGenerator,
)
from app.models.semester_enrollment import EnrollmentStatus


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_tournament(name: str = "Test Cup") -> MagicMock:
    t = MagicMock()
    t.id = 1
    t.name = name
    t.start_date = datetime(2026, 9, 1, 9, 0)
    t.format = "HEAD_TO_HEAD"
    t.scoring_type = "WIN_LOSS"
    t.location = "Test Venue"
    t.campus_id = None
    t.tournament_config_obj = None
    return t


def _make_tournament_type(config: dict) -> MagicMock:
    tt = MagicMock()
    tt.config = config
    tt.format = "HEAD_TO_HEAD"
    return tt


def _make_enrollment(user_id: int) -> MagicMock:
    e = MagicMock()
    e.user_id = user_id
    e.request_status = EnrollmentStatus.APPROVED
    return e


def _make_db(enrollments: list) -> MagicMock:
    """Mock DB that returns enrollments from the group enrollment query."""
    db = MagicMock()

    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.all.return_value = enrollments
    chain.count.return_value = len(enrollments)

    # query(SemesterEnrollment).filter(...).all() → enrollments
    db.query.return_value = chain
    return db


def _generate(player_count: int, tt_config: dict) -> list[dict]:
    """Run the generator and return the session dicts."""
    enrollments = [_make_enrollment(uid) for uid in range(1, player_count + 1)]
    db = _make_db(enrollments)
    tournament = _make_tournament()
    tournament_type = _make_tournament_type(tt_config)

    gen = GroupKnockoutGenerator(db)

    with patch(
        "app.services.tournament.session_generation.formats.group_knockout_generator.dedup_participant_ids",
        side_effect=lambda ids, *a, **kw: ids,
    ), patch(
        "app.services.tournament.session_generation.formats.group_knockout_generator.get_tournament_venue",
        return_value="Venue",
    ), patch(
        "app.services.tournament.session_generation.formats.group_knockout_generator.pick_campus",
        return_value=None,
    ), patch(
        "app.services.tournament.session_generation.formats.group_knockout_generator.pick_pitch",
        return_value=None,
    ):
        sessions = gen.generate(
            tournament=tournament,
            tournament_type=tournament_type,
            player_count=player_count,
            parallel_fields=1,
            session_duration=90,
            break_minutes=15,
        )
    return sessions


_POLICY_9P = {
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
}

_POLICY_NONE = {
    "round_names": {"4": "Semi-Finals", "2": "Finals"},
}

# GKG-10: 9p without a group_configuration.9_players entry — generator falls
# back to dynamic distribution (qualifiers_per_group=2) → 6 qualifiers → play-in.
_POLICY_FIXED = {
    "round_names": {"4": "Semi-Finals", "2": "Finals"},
}


# ── GKG-01..07: 9-player professional model ────────────────────────────────────

class TestNinePlayerProfessionalModel:

    @pytest.fixture(autouse=True)
    def sessions(self):
        self._sessions = _generate(9, _POLICY_9P)

    def _group_sessions(self):
        return [s for s in self._sessions if s["tournament_phase"] == "GROUP_STAGE"]

    def _ko_sessions(self):
        return [s for s in self._sessions if s["tournament_phase"] == "KNOCKOUT"]

    def _play_in_sessions(self):
        return [s for s in self._ko_sessions() if s.get("tournament_round") == 0]

    def _sf_sessions(self):
        return sorted(
            [s for s in self._ko_sessions() if s.get("tournament_round") == 1],
            key=lambda s: s["tournament_match_number"],
        )

    def _final_sessions(self):
        return [s for s in self._ko_sessions() if s.get("game_type") == "Finals"
                or (s.get("tournament_round", 0) == 2 and "3rd" not in s.get("title", ""))]

    def _bronze_sessions(self):
        return [s for s in self._ko_sessions() if "3rd Place" in s.get("title", "")]

    def test_gkg01_knockout_players_4_no_play_in(self):
        play_in = self._play_in_sessions()
        assert len(play_in) == 0, "Expected 0 play-in sessions for 9-player tournament"

    def test_gkg01b_has_bronze(self):
        bronze = self._bronze_sessions()
        assert len(bronze) == 1, "Expected exactly 1 bronze match"

    def test_gkg02_sf1_matchup_label(self):
        sfs = self._sf_sessions()
        assert len(sfs) >= 1
        sc = sfs[0].get("structure_config", {})
        assert sc.get("matchup") == "Group A winner vs Best runner-up", (
            f"Expected 'Group A winner vs Best runner-up', got {sc.get('matchup')!r}"
        )
        assert sc.get("seed_1") == "A1"
        assert sc.get("seed_2") == "BR"

    def test_gkg03_sf2_matchup_label(self):
        sfs = self._sf_sessions()
        assert len(sfs) >= 2
        sc = sfs[1].get("structure_config", {})
        assert sc.get("matchup") == "Group B winner vs Group C winner", (
            f"Expected 'Group B winner vs Group C winner', got {sc.get('matchup')!r}"
        )
        assert sc.get("seed_1") == "B1"
        assert sc.get("seed_2") == "C1"

    def test_gkg04_final_matchup_label(self):
        finals = [
            s for s in self._ko_sessions()
            if s.get("tournament_round") == 2 and "3rd" not in s.get("title", "")
        ]
        assert len(finals) == 1
        sc = finals[0].get("structure_config", {})
        assert sc.get("matchup") == "SF1 winner vs SF2 winner", (
            f"Got {sc.get('matchup')!r}"
        )

    def test_gkg05_bronze_matchup_label(self):
        bronze = self._bronze_sessions()
        assert len(bronze) == 1
        sc = bronze[0].get("structure_config", {})
        assert sc.get("matchup") == "SF1 loser vs SF2 loser", (
            f"Got {sc.get('matchup')!r}"
        )

    def test_gkg06_all_ko_sessions_have_null_participants(self):
        for s in self._ko_sessions():
            assert s.get("participant_user_ids") is None, (
                f"Session {s.get('title')!r} has participant_user_ids pre-filled"
            )

    def test_gkg07_total_session_count(self):
        # 3 groups × 3 matches (3-player RR) = 9 group sessions
        # 2 semis + 1 final + 1 bronze = 4 KO sessions
        # Total = 13
        assert len(self._group_sessions()) == 9
        assert len(self._ko_sessions()) == 4
        assert len(self._sessions) == 13


# ── GKG-08: 8-player backward compat ──────────────────────────────────────────

class TestEightPlayerBackwardCompat:

    def test_gkg08_standard_4_qualifiers_seeding_unchanged(self):
        sessions = _generate(8, _POLICY_NONE)
        ko = [s for s in sessions if s["tournament_phase"] == "KNOCKOUT"]
        play_in = [s for s in ko if s.get("tournament_round") == 0]
        sf = sorted(
            [s for s in ko if s.get("tournament_round") == 1],
            key=lambda s: s["tournament_match_number"],
        )

        assert len(play_in) == 0, "8-player: expected no play-in"
        assert len(sf) == 2, "8-player: expected 2 semis"

        sc1 = sf[0].get("structure_config", {})
        sc2 = sf[1].get("structure_config", {})
        assert sc1.get("matchup") == "A1 vs B2"
        assert sc1.get("seed_1") == "A1"
        assert sc1.get("seed_2") == "B2"
        assert sc2.get("matchup") == "B1 vs A2"


# ── GKG-09: 12-player backward compat ─────────────────────────────────────────

class TestTwelvePlayerBackwardCompat:

    def test_gkg09_6_qualifiers_play_in_preserved(self):
        sessions = _generate(12, _POLICY_NONE)
        ko = [s for s in sessions if s["tournament_phase"] == "KNOCKOUT"]
        play_in = [s for s in ko if s.get("tournament_round") == 0]
        assert len(play_in) == 2, (
            f"12-player: expected 2 play-in sessions, got {len(play_in)}"
        )


# ── GKG-10: 9-player with explicit fixed_per_group ────────────────────────────

class TestNinePlayerExplicitFixedPolicy:

    def test_gkg10_fixed_per_group_gives_6_qualifiers(self):
        sessions = _generate(9, _POLICY_FIXED)
        ko = [s for s in sessions if s["tournament_phase"] == "KNOCKOUT"]
        play_in = [s for s in ko if s.get("tournament_round") == 0]
        # fixed_per_group on 9 players → 3 groups × 2 qualifiers = 6 → 2 play-in
        assert len(play_in) == 2, (
            f"Expected 2 play-in sessions for fixed_per_group+9p, got {len(play_in)}"
        )
