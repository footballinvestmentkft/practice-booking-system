"""Unit tests for tournament edit page helper functions.

Tests:
  - _session_sort_key: ordering GROUP before KNOCKOUT, group-alphabetical,
    round-within-group, KO game-type priority (SF → Final → Bronze)
  - _matchup_label: concrete participants take priority, ⏳ fallback from
    structure_config["matchup"] when no participants assigned
  - _admin_tournament_url: correct URL format (seed script helper)
"""
from __future__ import annotations

import importlib.util
import pathlib
import types

import pytest

from app.api.web_routes.tournaments.edit import _session_sort_key, _matchup_label

# ── Seed script import ────────────────────────────────────────────────────────

_SCRIPT_PATH = pathlib.Path(__file__).parents[2] / "scripts" / "seed_promotion_events.py"


def _load_seed_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("seed_promotion_events", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_seed = _load_seed_module()
_admin_tournament_url = _seed._admin_tournament_url


# ── Session mock factory ──────────────────────────────────────────────────────

def _sess(
    *,
    tournament_phase=None,
    group_identifier=None,
    tournament_round=None,
    tournament_match_number=None,
    game_type=None,
    participant_team_ids=None,
    participant_user_ids=None,
    match_format=None,
    structure_config=None,
):
    """Build a lightweight session-like namespace for helper unit tests."""
    return types.SimpleNamespace(
        tournament_phase=tournament_phase,
        group_identifier=group_identifier,
        tournament_round=tournament_round,
        tournament_match_number=tournament_match_number,
        game_type=game_type,
        participant_team_ids=participant_team_ids,
        participant_user_ids=participant_user_ids,
        match_format=match_format,
        structure_config=structure_config,
    )


# ── _session_sort_key ─────────────────────────────────────────────────────────

class TestSessionSortKey:
    def test_group_stage_before_knockout(self):
        gs = _sess(tournament_phase="GROUP_STAGE", group_identifier="A", tournament_round=1)
        ko = _sess(tournament_phase="KNOCKOUT", game_type="Semi-finals", tournament_round=1)
        assert _session_sort_key(gs) < _session_sort_key(ko)

    def test_group_phase_enum_value_accepted(self):
        """tournament_phase may be an enum with .value attribute."""
        class _FakeEnum:
            value = "GROUP_STAGE"
        gs = _sess(tournament_phase=_FakeEnum(), group_identifier="A", tournament_round=1)
        ko = _sess(tournament_phase="KNOCKOUT", game_type="Final")
        assert _session_sort_key(gs) < _session_sort_key(ko)

    def test_group_alphabetical_A_before_B_before_C(self):
        a = _sess(tournament_phase="GROUP_STAGE", group_identifier="A", tournament_round=1, tournament_match_number=1)
        b = _sess(tournament_phase="GROUP_STAGE", group_identifier="B", tournament_round=1, tournament_match_number=1)
        c = _sess(tournament_phase="GROUP_STAGE", group_identifier="C", tournament_round=1, tournament_match_number=1)
        assert _session_sort_key(a) < _session_sort_key(b) < _session_sort_key(c)

    def test_round_order_within_group(self):
        r1 = _sess(tournament_phase="GROUP_STAGE", group_identifier="A", tournament_round=1, tournament_match_number=1)
        r2 = _sess(tournament_phase="GROUP_STAGE", group_identifier="A", tournament_round=2, tournament_match_number=1)
        r3 = _sess(tournament_phase="GROUP_STAGE", group_identifier="A", tournament_round=3, tournament_match_number=1)
        assert _session_sort_key(r1) < _session_sort_key(r2) < _session_sort_key(r3)

    def test_match_number_order_within_round(self):
        m1 = _sess(tournament_phase="GROUP_STAGE", group_identifier="B", tournament_round=1, tournament_match_number=1)
        m2 = _sess(tournament_phase="GROUP_STAGE", group_identifier="B", tournament_round=1, tournament_match_number=2)
        assert _session_sort_key(m1) < _session_sort_key(m2)

    def test_ko_semi_before_final_before_bronze(self):
        sf = _sess(tournament_phase="KNOCKOUT", game_type="Semi-finals", tournament_round=1, tournament_match_number=1)
        fi = _sess(tournament_phase="KNOCKOUT", game_type="Final", tournament_round=2, tournament_match_number=1)
        br = _sess(tournament_phase="KNOCKOUT", game_type="3rd Place Match", tournament_round=3, tournament_match_number=1)
        assert _session_sort_key(sf) < _session_sort_key(fi) < _session_sort_key(br)

    def test_ko_game_type_priority_over_round(self):
        """If round numbers are wrong, game_type still produces the correct order."""
        # Bronze has tournament_round=1 (wrong), but game_type=3rd Place Match → sorts last
        sf = _sess(tournament_phase="KNOCKOUT", game_type="Semi-finals", tournament_round=2)
        br = _sess(tournament_phase="KNOCKOUT", game_type="3rd Place Match", tournament_round=1)
        fi = _sess(tournament_phase="KNOCKOUT", game_type="Final", tournament_round=3)
        ordered = sorted([br, fi, sf], key=_session_sort_key)
        assert [s.game_type for s in ordered] == ["Semi-finals", "Final", "3rd Place Match"]

    def test_full_sc04_order(self):
        """Simulate the 13-session SC-04 tournament and verify end-to-end sort."""
        sessions = [
            # Group sessions (interleaved as date_start would give them)
            _sess(tournament_phase="GROUP_STAGE", group_identifier="A", tournament_round=1, tournament_match_number=2, game_type="Group A - Round 1"),
            _sess(tournament_phase="GROUP_STAGE", group_identifier="B", tournament_round=1, tournament_match_number=2, game_type="Group B - Round 1"),
            _sess(tournament_phase="GROUP_STAGE", group_identifier="C", tournament_round=1, tournament_match_number=2, game_type="Group C - Round 1"),
            _sess(tournament_phase="GROUP_STAGE", group_identifier="A", tournament_round=2, tournament_match_number=1, game_type="Group A - Round 2"),
            _sess(tournament_phase="GROUP_STAGE", group_identifier="B", tournament_round=2, tournament_match_number=1, game_type="Group B - Round 2"),
            _sess(tournament_phase="GROUP_STAGE", group_identifier="C", tournament_round=2, tournament_match_number=1, game_type="Group C - Round 2"),
            _sess(tournament_phase="GROUP_STAGE", group_identifier="A", tournament_round=3, tournament_match_number=1, game_type="Group A - Round 3"),
            _sess(tournament_phase="GROUP_STAGE", group_identifier="B", tournament_round=3, tournament_match_number=1, game_type="Group B - Round 3"),
            _sess(tournament_phase="GROUP_STAGE", group_identifier="C", tournament_round=3, tournament_match_number=1, game_type="Group C - Round 3"),
            # Knockout
            _sess(tournament_phase="KNOCKOUT", game_type="Semi-finals", tournament_round=1, tournament_match_number=1),
            _sess(tournament_phase="KNOCKOUT", game_type="Semi-finals", tournament_round=1, tournament_match_number=2),
            _sess(tournament_phase="KNOCKOUT", game_type="Final", tournament_round=2, tournament_match_number=1),
            _sess(tournament_phase="KNOCKOUT", game_type="3rd Place Match", tournament_round=3, tournament_match_number=1),
        ]
        import random
        random.shuffle(sessions)
        ordered = sorted(sessions, key=_session_sort_key)

        expected = [
            "Group A - Round 1", "Group A - Round 2", "Group A - Round 3",
            "Group B - Round 1", "Group B - Round 2", "Group B - Round 3",
            "Group C - Round 1", "Group C - Round 2", "Group C - Round 3",
            "Semi-finals", "Semi-finals",
            "Final",
            "3rd Place Match",
        ]
        assert [s.game_type for s in ordered] == expected


# ── _matchup_label ────────────────────────────────────────────────────────────

class TestMatchupLabel:
    def test_concrete_team_participants_priority(self):
        s = _sess(participant_team_ids=[10, 20])
        result = _matchup_label(s, {10: "Red FC", 20: "Blue FC"}, {})
        assert result == "Red FC vs Blue FC"

    def test_concrete_user_head_to_head_priority(self):
        u1 = types.SimpleNamespace(name="Alice")
        u2 = types.SimpleNamespace(name="Bob")
        s = _sess(participant_user_ids=[1, 2], match_format="HEAD_TO_HEAD")
        result = _matchup_label(s, {}, {1: u1, 2: u2})
        assert result == "Alice vs Bob"

    def test_concrete_participants_override_structure_config(self):
        """If participant_user_ids is populated, structure_config matchup is ignored."""
        u1 = types.SimpleNamespace(name="Alice")
        u2 = types.SimpleNamespace(name="Bob")
        s = _sess(
            participant_user_ids=[1, 2],
            match_format="HEAD_TO_HEAD",
            structure_config={"matchup": "Group A winner vs Best runner-up"},
        )
        result = _matchup_label(s, {}, {1: u1, 2: u2})
        assert result == "Alice vs Bob"
        assert "Group A winner" not in result

    def test_pending_fallback_from_structure_config(self):
        """No participants assigned → ⏳ prefix + structure_config matchup label."""
        s = _sess(
            participant_user_ids=None,
            participant_team_ids=None,
            structure_config={"matchup": "Group A winner vs Best runner-up"},
        )
        result = _matchup_label(s, {}, {})
        assert result == "⏳ Group A winner vs Best runner-up"

    def test_pending_fallback_sf2(self):
        s = _sess(structure_config={"matchup": "Group B winner vs Group C winner"})
        assert _matchup_label(s, {}, {}) == "⏳ Group B winner vs Group C winner"

    def test_pending_fallback_final(self):
        s = _sess(structure_config={"matchup": "SF1 winner vs SF2 winner"})
        assert _matchup_label(s, {}, {}) == "⏳ SF1 winner vs SF2 winner"

    def test_no_fallback_when_no_matchup_in_structure_config(self):
        """structure_config exists but has no 'matchup' key → None."""
        s = _sess(structure_config={"round_name": "3rd Place Match"})
        assert _matchup_label(s, {}, {}) is None

    def test_returns_none_when_nothing_available(self):
        s = _sess()
        assert _matchup_label(s, {}, {}) is None


# ── _admin_tournament_url ─────────────────────────────────────────────────────

class TestAdminTournamentUrl:
    def test_format_is_edit_page(self):
        assert _admin_tournament_url(22857) == "/admin/tournaments/22857/edit"

    def test_no_promotion_events_path(self):
        url = _admin_tournament_url(1)
        assert "/admin/promotion-events/" not in url
        assert url.startswith("/admin/tournaments/")
        assert url.endswith("/edit")

    def test_various_ids(self):
        for tid in [1, 100, 99999]:
            url = _admin_tournament_url(tid)
            assert str(tid) in url
            assert url == f"/admin/tournaments/{tid}/edit"
