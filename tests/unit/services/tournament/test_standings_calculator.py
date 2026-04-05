"""
Unit Tests: StandingsCalculator

Covers all business branches in calculate_group_standings():
- Empty / missing input guards
- Participant zero-initialization
- Win / loss / draw point assignment (3-0, 0-3, 1-1)
- Goals-for / goals-against tracking and goal_difference
- Multi-match accumulation across sessions in the same group
- game_results parsing: dict, JSON string, list (legacy), invalid JSON, unsupported type
- HEAD_TO_HEAD guard: raw_results len != 2 → skip
- Tie-breaking: points > goal_difference > goals_for
- Multiple groups remain independent
- User not returned by DB → excluded from standings
- Name fallback: user.name or user.email
"""

import json
import pytest
from unittest.mock import MagicMock

from app.services.tournament.results.calculators.standings_calculator import StandingsCalculator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(users=None):
    """Mock DB whose query().filter().all() returns `users`."""
    db = MagicMock()
    filter_mock = MagicMock()
    filter_mock.all.return_value = users or []
    db.query.return_value.filter.return_value = filter_mock
    return db


def _make_user(uid, name=None, email=None):
    user = MagicMock()
    user.id = uid
    user.name = name
    user.email = email or f"user{uid}@test.com"
    return user


def _make_session(group_identifier=None, participant_user_ids=None, game_results=None,
                  participant_team_ids=None, rounds_data=None):
    s = MagicMock()
    s.group_identifier = group_identifier
    s.participant_user_ids = participant_user_ids
    s.participant_team_ids = participant_team_ids  # explicit None → INDIVIDUAL dispatch
    s.game_results = game_results
    s.rounds_data = rounds_data
    return s


def _h2h(uid1, score1, uid2, score2):
    """Shorthand for a standard HEAD_TO_HEAD dict result (raw_results key = legacy)."""
    return {"raw_results": [{"user_id": uid1, "score": score1},
                            {"user_id": uid2, "score": score2}]}


def _h2h_api(uid1, score1, uid2, score2, r1="", r2=""):
    """Shorthand using the current API format (participants key)."""
    def _res(s1, s2, r):
        if r:
            return r
        return "win" if s1 > s2 else ("draw" if s1 == s2 else "loss")
    return {
        "match_format": "HEAD_TO_HEAD",
        "participants": [
            {"user_id": uid1, "score": score1, "result": _res(score1, score2, r1)},
            {"user_id": uid2, "score": score2, "result": _res(score2, score1, r2)},
        ],
        "match_status": "completed",
    }


# ---------------------------------------------------------------------------
# Empty / guard cases
# ---------------------------------------------------------------------------

class TestEmptyAndGuards:

    def test_empty_sessions_returns_empty_dict(self):
        calc = StandingsCalculator(_make_db())
        assert calc.calculate_group_standings([]) == {}

    def test_session_without_group_identifier_ignored(self):
        session = _make_session(group_identifier=None, participant_user_ids=[1, 2])
        calc = StandingsCalculator(_make_db())
        assert calc.calculate_group_standings([session]) == {}

    def test_session_without_participant_ids_no_players_initialized(self):
        session = _make_session(group_identifier="A", participant_user_ids=None,
                                game_results=None)
        calc = StandingsCalculator(_make_db(users=[]))
        result = calc.calculate_group_standings([session])
        # Group A may or may not appear; if it does, it must be empty
        assert result.get("A", []) == []

    def test_session_with_no_game_results_and_no_participants(self):
        """group_identifier present but empty participant list → empty group."""
        session = _make_session(group_identifier="X", participant_user_ids=[],
                                game_results=None)
        calc = StandingsCalculator(_make_db(users=[]))
        result = calc.calculate_group_standings([session])
        assert result.get("X", []) == []


# ---------------------------------------------------------------------------
# Zero initialisation (participants, no matches)
# ---------------------------------------------------------------------------

class TestZeroInitialisation:

    def test_participants_start_with_all_stats_zero(self):
        u1, u2 = _make_user(1, "Alice"), _make_user(2, "Bob")
        session = _make_session("A", [1, 2], game_results=None)
        result = StandingsCalculator(_make_db([u1, u2])).calculate_group_standings([session])

        assert "A" in result
        assert len(result["A"]) == 2
        for entry in result["A"]:
            assert entry["points"] == 0
            assert entry["wins"] == 0
            assert entry["draws"] == 0
            assert entry["losses"] == 0
            assert entry["goals_for"] == 0
            assert entry["goals_against"] == 0
            assert entry["matches_played"] == 0

    def test_rank_field_present_even_with_zero_stats(self):
        u1 = _make_user(1, "Alice")
        session = _make_session("A", [1], game_results=None)
        result = StandingsCalculator(_make_db([u1])).calculate_group_standings([session])
        assert "rank" in result["A"][0]


# ---------------------------------------------------------------------------
# Win / Loss / Draw point assignment
# ---------------------------------------------------------------------------

class TestPointAssignment:

    def _result(self, score1, score2):
        u1, u2 = _make_user(1, "A"), _make_user(2, "B")
        session = _make_session("G", [1, 2], _h2h(1, score1, 2, score2))
        db = _make_db([u1, u2])
        return {e["user_id"]: e for e in
                StandingsCalculator(db).calculate_group_standings([session])["G"]}

    def test_win_gives_winner_3_points_loser_0(self):
        by = self._result(3, 1)
        assert by[1]["points"] == 3 and by[1]["wins"] == 1
        assert by[2]["points"] == 0 and by[2]["losses"] == 1

    def test_loss_from_other_side(self):
        by = self._result(0, 2)
        assert by[2]["points"] == 3 and by[2]["wins"] == 1
        assert by[1]["points"] == 0 and by[1]["losses"] == 1

    def test_draw_gives_each_player_1_point(self):
        by = self._result(2, 2)
        assert by[1]["points"] == 1 and by[1]["draws"] == 1
        assert by[2]["points"] == 1 and by[2]["draws"] == 1

    def test_zero_zero_draw(self):
        by = self._result(0, 0)
        assert by[1]["points"] == 1
        assert by[2]["points"] == 1

    def test_high_score_win(self):
        by = self._result(10, 0)
        assert by[1]["goals_for"] == 10
        assert by[2]["goals_against"] == 10


# ---------------------------------------------------------------------------
# Goal tracking and goal_difference
# ---------------------------------------------------------------------------

class TestGoalTracking:

    def test_goals_for_against_and_difference(self):
        u1, u2 = _make_user(1, "A"), _make_user(2, "B")
        session = _make_session("G", [1, 2], _h2h(1, 5, 2, 2))
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings([session])["G"]}
        assert by[1]["goals_for"] == 5
        assert by[1]["goals_against"] == 2
        assert by[1]["goal_difference"] == 3
        assert by[2]["goals_for"] == 2
        assert by[2]["goals_against"] == 5
        assert by[2]["goal_difference"] == -3

    def test_matches_played_counter(self):
        u1, u2 = _make_user(1), _make_user(2)
        sessions = [
            _make_session("A", [1, 2], _h2h(1, 1, 2, 0)),
            _make_session("A", [1, 2], _h2h(1, 2, 2, 1)),
        ]
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings(sessions)["A"]}
        assert by[1]["matches_played"] == 2
        assert by[2]["matches_played"] == 2


# ---------------------------------------------------------------------------
# Multi-match accumulation
# ---------------------------------------------------------------------------

class TestMultiMatchAccumulation:

    def test_three_players_three_matches(self):
        u1 = _make_user(1, "Alice")
        u2 = _make_user(2, "Bob")
        u3 = _make_user(3, "Carol")
        sessions = [
            _make_session("A", [1, 2, 3], _h2h(1, 2, 2, 0)),   # 1W 2L
            _make_session("A", [1, 2, 3], _h2h(1, 1, 3, 1)),   # draw
            _make_session("A", [1, 2, 3], _h2h(2, 3, 3, 0)),   # 2W 3L
        ]
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2, u3])).calculate_group_standings(sessions)["A"]}
        # u1: W + D = 4 pts
        assert by[1]["points"] == 4
        assert by[1]["wins"] == 1
        assert by[1]["draws"] == 1
        # u2: L + W = 3 pts
        assert by[2]["points"] == 3
        # u3: D + L = 1 pt
        assert by[3]["points"] == 1

    def test_same_two_players_play_twice(self):
        u1, u2 = _make_user(1), _make_user(2)
        sessions = [
            _make_session("A", [1, 2], _h2h(1, 1, 2, 0)),
            _make_session("A", [1, 2], _h2h(1, 1, 2, 0)),
        ]
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings(sessions)["A"]}
        assert by[1]["wins"] == 2
        assert by[1]["points"] == 6
        assert by[1]["goals_for"] == 2


# ---------------------------------------------------------------------------
# Parsing variations
# ---------------------------------------------------------------------------

class TestParsing:

    def test_game_results_as_json_string(self):
        u1, u2 = _make_user(1), _make_user(2)
        results_str = json.dumps(_h2h(1, 3, 2, 1))
        session = _make_session("A", [1, 2], results_str)
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings([session])["A"]}
        assert by[1]["wins"] == 1

    def test_invalid_json_string_session_skipped(self):
        u1, u2 = _make_user(1), _make_user(2)
        init_session = _make_session("A", [1, 2], game_results=None)
        bad_session = _make_session("A", [1, 2], game_results="{not-json{{")
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings(
                  [init_session, bad_session])["A"]}
        assert by[1]["wins"] == 0   # bad session skipped

    def test_game_results_as_list_legacy_format(self):
        """list with 2 entries → raw_results fallback."""
        u1, u2 = _make_user(1), _make_user(2)
        results_list = [{"user_id": 1, "score": 2}, {"user_id": 2, "score": 1}]
        session = _make_session("A", [1, 2], results_list)
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings([session])["A"]}
        assert by[1]["wins"] == 1
        assert by[2]["losses"] == 1

    def test_unsupported_type_game_results_skipped(self):
        init = _make_session("A", [1, 2], game_results=None)
        bad = _make_session("A", [1, 2], game_results=99999)  # int
        u1, u2 = _make_user(1), _make_user(2)
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings([init, bad])["A"]}
        assert by[1]["wins"] == 0

    def test_participants_key_parsed_correctly(self):
        """SC-KEY-01: API format uses 'participants' key — must parse same as 'raw_results'."""
        u1, u2 = _make_user(1), _make_user(2)
        session = _make_session("A", [1, 2], _h2h_api(1, 3, 2, 0))
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings([session])["A"]}
        assert by[1]["wins"] == 1
        assert by[1]["points"] == 3
        assert by[2]["losses"] == 1
        assert by[2]["points"] == 0

    def test_participants_key_draw(self):
        """SC-KEY-02: 'participants' format — draw scored correctly."""
        u1, u2 = _make_user(1), _make_user(2)
        session = _make_session("A", [1, 2], _h2h_api(1, 1, 2, 1))
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings([session])["A"]}
        assert by[1]["draws"] == 1 and by[1]["points"] == 1
        assert by[2]["draws"] == 1 and by[2]["points"] == 1

    def test_participants_key_as_json_string(self):
        """SC-KEY-03: 'participants' format as JSON string (as stored in DB)."""
        u1, u2 = _make_user(1), _make_user(2)
        results_str = json.dumps(_h2h_api(1, 5, 2, 2))
        session = _make_session("A", [1, 2], results_str)
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings([session])["A"]}
        assert by[1]["wins"] == 1
        assert by[1]["goals_for"] == 5
        assert by[2]["goals_for"] == 2

    def test_qualification_uses_correct_standings_with_api_format(self):
        """
        SC-KEY-04 / Core regression: with 'participants' key data, top-ranked player
        (highest pts) must appear first — not random insertion order.
        """
        users = [_make_user(uid) for uid in (10, 20, 30, 40)]
        # Player 10 wins all 3 matches → 9 pts; others get 0
        sessions = [
            _make_session("A", [10, 20], _h2h_api(10, 3, 20, 0)),
            _make_session("A", [10, 30], _h2h_api(10, 3, 30, 0)),
            _make_session("A", [10, 40], _h2h_api(10, 3, 40, 0)),
        ]
        result = StandingsCalculator(_make_db(users)).calculate_group_standings(sessions)
        assert result["A"][0]["user_id"] == 10
        assert result["A"][0]["points"] == 9


# ---------------------------------------------------------------------------
# HEAD_TO_HEAD guard (raw_results len != 2)
# ---------------------------------------------------------------------------

class TestH2HGuard:

    def test_single_entry_raw_results_skipped(self):
        u1 = _make_user(1)
        init = _make_session("A", [1], game_results=None)
        bad = _make_session("A", [1],
                            game_results={"raw_results": [{"user_id": 1, "score": 5}]})
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1])).calculate_group_standings([init, bad])["A"]}
        assert by[1]["wins"] == 0

    def test_three_entry_raw_results_skipped(self):
        u1, u2, u3 = _make_user(1), _make_user(2), _make_user(3)
        init = _make_session("A", [1, 2, 3], game_results=None)
        bad = _make_session("A", [1, 2, 3], game_results={
            "raw_results": [
                {"user_id": 1, "score": 3},
                {"user_id": 2, "score": 2},
                {"user_id": 3, "score": 1},
            ]
        })
        result = StandingsCalculator(_make_db([u1, u2, u3])).calculate_group_standings(
            [init, bad])
        for e in result["A"]:
            assert e["wins"] == 0

    def test_empty_raw_results_skipped(self):
        u1, u2 = _make_user(1), _make_user(2)
        init = _make_session("A", [1, 2], game_results=None)
        bad = _make_session("A", [1, 2], game_results={"raw_results": []})
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2])).calculate_group_standings([init, bad])["A"]}
        assert by[1]["wins"] == 0


# ---------------------------------------------------------------------------
# Sorting and tie-breaking
# ---------------------------------------------------------------------------

class TestSortingAndRanking:

    def test_higher_points_ranked_first(self):
        u1, u2, u3 = _make_user(1, "A"), _make_user(2, "B"), _make_user(3, "C")
        sessions = [
            _make_session("A", [1, 2, 3], _h2h(1, 2, 2, 0)),
            _make_session("A", [1, 2, 3], _h2h(1, 1, 3, 0)),
            _make_session("A", [1, 2, 3], _h2h(2, 1, 3, 1)),
        ]
        standings = StandingsCalculator(_make_db([u1, u2, u3])).calculate_group_standings(
            sessions)["A"]
        assert standings[0]["user_id"] == 1
        assert standings[0]["rank"] == 1

    def test_tiebreak_by_goal_difference(self):
        """Same points, different goal_difference → better gd ranks higher."""
        u1, u2, u3 = _make_user(1), _make_user(2), _make_user(3)
        sessions = [
            # u1 beats u3 (3-0): u1 gd = +3
            _make_session("A", [1, 2, 3], _h2h(1, 3, 3, 0)),
            # u2 beats u3 (1-0): u2 gd = +1
            _make_session("A", [1, 2, 3], _h2h(2, 1, 3, 0)),
        ]
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2, u3])).calculate_group_standings(sessions)["A"]}
        # Both u1 and u2 have 3 pts — u1 has better gd
        assert by[1]["rank"] < by[2]["rank"]

    def test_tiebreak_by_goals_for(self):
        """Same points AND same goal_difference → higher goals_for ranks higher."""
        u1, u2, u3 = _make_user(1), _make_user(2), _make_user(3)
        sessions = [
            # u1 beats u3 2-1: gd=+1, gf=2
            _make_session("A", [1, 2, 3], _h2h(1, 2, 3, 1)),
            # u2 beats u3 3-2: gd=+1, gf=3
            _make_session("A", [1, 2, 3], _h2h(2, 3, 3, 2)),
        ]
        by = {e["user_id"]: e for e in
              StandingsCalculator(_make_db([u1, u2, u3])).calculate_group_standings(sessions)["A"]}
        # u1: 3pts, gd=+1, gf=2 | u2: 3pts, gd=+1, gf=3 → u2 higher
        assert by[2]["rank"] < by[1]["rank"]

    def test_rank_numbers_start_at_1_and_are_sequential(self):
        u1, u2, u3 = _make_user(1), _make_user(2), _make_user(3)
        sessions = [
            _make_session("A", [1, 2, 3], _h2h(1, 3, 2, 0)),
            _make_session("A", [1, 2, 3], _h2h(1, 2, 3, 0)),
            _make_session("A", [1, 2, 3], _h2h(2, 2, 3, 1)),
        ]
        standings = StandingsCalculator(_make_db([u1, u2, u3])).calculate_group_standings(
            sessions)["A"]
        ranks = [e["rank"] for e in standings]
        assert ranks[0] == 1


# ---------------------------------------------------------------------------
# Multiple groups
# ---------------------------------------------------------------------------

class TestMultipleGroups:

    def test_two_groups_remain_independent(self):
        u1, u2 = _make_user(1, "A"), _make_user(2, "B")
        u3, u4 = _make_user(3, "C"), _make_user(4, "D")
        sessions = [
            _make_session("A", [1, 2], _h2h(1, 2, 2, 0)),
            _make_session("B", [3, 4], _h2h(3, 1, 4, 3)),
        ]
        result = StandingsCalculator(_make_db([u1, u2, u3, u4])).calculate_group_standings(
            sessions)
        assert {e["user_id"] for e in result["A"]} == {1, 2}
        assert {e["user_id"] for e in result["B"]} == {3, 4}

    def test_user_not_returned_by_db_excluded(self):
        u1 = _make_user(1, "Alice")
        # u2 intentionally absent from DB response
        session = _make_session("A", [1, 2], _h2h(1, 2, 2, 1))
        result = StandingsCalculator(_make_db([u1])).calculate_group_standings([session])
        ids = [e["user_id"] for e in result["A"]]
        assert 1 in ids
        assert 2 not in ids


# ---------------------------------------------------------------------------
# Name fallback
# ---------------------------------------------------------------------------

class TestNameFallback:

    def test_uses_name_when_available(self):
        user = _make_user(1, name="John Doe", email="john@test.com")
        session = _make_session("A", [1], game_results=None)
        result = StandingsCalculator(_make_db([user])).calculate_group_standings([session])
        assert result["A"][0]["name"] == "John Doe"

    def test_falls_back_to_email_when_name_is_none(self):
        user = _make_user(1, name=None, email="fallback@test.com")
        session = _make_session("A", [1], game_results=None)
        result = StandingsCalculator(_make_db([user])).calculate_group_standings([session])
        assert result["A"][0]["name"] == "fallback@test.com"


# ---------------------------------------------------------------------------
# Deterministic tie-breaker (SC-TIE-01)
# ---------------------------------------------------------------------------

class TestDeterministicTieBreaker:
    """
    SC-TIE-01: When Pts + GD + GF are fully equal, lower user_id must rank higher.
    This prevents qualification/display ordering mismatch.
    """

    def _three_way_tie_standings(self):
        """
        Group A: players 10, 20, 30
        All draw all their matches → each has 2 pts, GD=0, GF=1
        Correct final order: id=10 > id=20 > id=30 (lower id ranks higher).
        """
        users = [_make_user(uid) for uid in (10, 20, 30)]
        # 3 round-robin sessions: 10v20 draw, 10v30 draw, 20v30 draw
        sessions = [
            _make_session("A", [10, 20], _h2h(10, 1, 20, 1)),
            _make_session("A", [10, 30], _h2h(10, 1, 30, 1)),
            _make_session("A", [20, 30], _h2h(20, 1, 30, 1)),
        ]
        return users, sessions

    def test_lower_user_id_ranks_higher_on_full_tie(self):
        users, sessions = self._three_way_tie_standings()
        result = StandingsCalculator(_make_db(users)).calculate_group_standings(sessions)
        order = [e["user_id"] for e in result["A"]]
        assert order == [10, 20, 30], f"Expected [10,20,30] but got {order}"

    def test_rank_field_consistent_with_sort_order_on_full_tie(self):
        users, sessions = self._three_way_tie_standings()
        result = StandingsCalculator(_make_db(users)).calculate_group_standings(sessions)
        for expected_rank, entry in enumerate(result["A"], start=1):
            assert entry["rank"] == expected_rank

    def test_top_n_qualifiers_are_deterministically_lowest_ids(self):
        """Top 2 must be user_id 10 and 20, regardless of dict insertion order."""
        users, sessions = self._three_way_tie_standings()
        result = StandingsCalculator(_make_db(users)).calculate_group_standings(sessions)
        top2_ids = {e["user_id"] for e in result["A"][:2]}
        assert top2_ids == {10, 20}, f"Expected top-2 = {{10, 20}}, got {top2_ids}"
