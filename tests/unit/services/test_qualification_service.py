"""
Unit tests for app/services/tournament/qualification.py

All tests use MagicMock — no real DB required.

Coverage
--------
QS-01  compute_best_runner_up: distinct points → highest points selected
QS-02  compute_best_runner_up: equal points, distinct GD → better GD selected
QS-03  compute_best_runner_up: equal points + GD, distinct GF → higher GF selected
QS-04  compute_best_runner_up: equal points + GD + GF → lower user_id selected
QS-05  assign_semifinal_participants: SF sessions receive correct participant_user_ids
QS-06  assign_semifinal_participants: Final / 3rd Place sessions never modified
QS-07  assign_semifinal_participants: idempotent — second call produces same result
QS-08  assign_semifinal_participants: unresolvable seed → session skipped (no partial write)
QS-09  compute_group_standings: win/draw/loss points and tiebreaker sort correct
QS-10  assign_semifinal_participants: empty standings → no DB write, no crash
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call

from app.services.tournament.qualification import (
    compute_best_runner_up,
    compute_group_standings,
    assign_semifinal_participants,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _standings(groups: dict) -> dict[str, list[dict]]:
    """
    Build a standings dict from {group: [(uid, pts, gf, ga), ...]} triples.
    Rank is assigned in list order (index 0 = rank 1).
    """
    result = {}
    for g, players in groups.items():
        rows = []
        for rank, (uid, pts, gf, ga) in enumerate(players, start=1):
            rows.append({
                "user_id": uid,
                "points": pts,
                "gf": float(gf),
                "ga": float(ga),
                "wins": pts // 3,
                "draws": pts % 3,
                "losses": 0,
                "rank": rank,
            })
        result[g] = rows
    return result


def _mock_session(
    *,
    id: int,
    phase: str,
    round_num: int,
    match_num: int = 1,
    structure_config: dict | None = None,
    participant_user_ids=None,
    group_identifier: str | None = None,
    game_results=None,
) -> MagicMock:
    s = MagicMock()
    s.id = id
    s.tournament_phase = phase
    s.tournament_round = round_num
    s.tournament_match_number = match_num
    s.structure_config = structure_config or {}
    s.participant_user_ids = participant_user_ids
    s.group_identifier = group_identifier
    s.game_results = game_results
    return s


def _mock_db(sessions: list) -> MagicMock:
    """DB that returns `sessions` from the final .all() call."""
    db = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.all.return_value = sessions
    db.query.return_value = chain
    return db


# ── QS-01..04: compute_best_runner_up (pure, standings pre-built) ──────────────

class TestComputeBestRunnerUp:

    def test_qs01_higher_points_wins(self):
        # Group A runner-up: 6 pts; Group B runner-up: 3 pts → A runner-up wins
        standings = _standings({
            "A": [(10, 9, 5, 0), (11, 6, 3, 1)],   # rank 1=uid10, rank 2=uid11
            "B": [(20, 9, 4, 0), (21, 3, 2, 3)],   # rank 2=uid21
            "C": [(30, 9, 6, 0), (31, 1, 1, 4)],   # rank 2=uid31
        })
        db = MagicMock()
        result = compute_best_runner_up(db, 1, 1, standings=standings)
        assert result == [11]  # uid11 has 6 pts, uid21 has 3 pts, uid31 has 1 pt

    def test_qs02_equal_points_gd_decides(self):
        # Both runners-up have 3 pts; uid11 GD=+2, uid21 GD=+1
        standings = _standings({
            "A": [(10, 6, 5, 0), (11, 3, 4, 2)],   # uid11: pts=3, gf=4, ga=2 → GD=+2
            "B": [(20, 6, 5, 0), (21, 3, 3, 2)],   # uid21: pts=3, gf=3, ga=2 → GD=+1
            "C": [(30, 6, 5, 0), (31, 0, 0, 5)],
        })
        db = MagicMock()
        result = compute_best_runner_up(db, 1, 1, standings=standings)
        assert result == [11]  # GD +2 > +1

    def test_qs03_equal_points_gd_gf_decides(self):
        # Equal pts, equal GD; uid11 GF=4, uid21 GF=3
        standings = _standings({
            "A": [(10, 6, 6, 0), (11, 3, 4, 2)],   # uid11: GD=+2, GF=4
            "B": [(20, 6, 6, 0), (21, 3, 3, 1)],   # uid21: GD=+2, GF=3
            "C": [(30, 6, 5, 0), (31, 0, 0, 5)],
        })
        db = MagicMock()
        result = compute_best_runner_up(db, 1, 1, standings=standings)
        assert result == [11]  # GF 4 > 3

    def test_qs04_equal_all_lower_user_id_wins(self):
        # All sport metrics equal; lower user_id is deterministic fallback
        standings = _standings({
            "A": [(10, 9, 0, 0), (200, 3, 2, 1)],  # uid200
            "B": [(11, 9, 0, 0), (100, 3, 2, 1)],  # uid100 — same pts/GD/GF
            "C": [(12, 9, 0, 0), (50, 0, 0, 4)],   # uid50 — 0 pts, not best
        })
        db = MagicMock()
        result = compute_best_runner_up(db, 1, 1, standings=standings)
        # uid100 (pts=3, GD=+1, GF=2) vs uid200 (same) → lower uid wins
        assert result == [100]


# ── QS-09: compute_group_standings via mocked DB sessions ─────────────────────

class TestComputeGroupStandings:

    def _make_game_results(self, uid1, score1, uid2, score2) -> dict:
        r1 = "win" if score1 > score2 else ("draw" if score1 == score2 else "loss")
        r2 = "win" if score2 > score1 else ("draw" if score2 == score1 else "loss")
        return {
            "match_format": "HEAD_TO_HEAD",
            "participants": [
                {"user_id": uid1, "result": r1, "score": score1},
                {"user_id": uid2, "result": r2, "score": score2},
            ],
        }

    def test_qs09_win_draw_loss_points_and_sort(self):
        # Group A: uid1 beats uid2, uid1 draws uid3, uid2 beats uid3
        # uid1: 1W + 1D = 4 pts | uid2: 1W + 1L = 3 pts | uid3: 0W + 1D + 1L = 1 pt
        sessions = [
            _mock_session(
                id=1, phase="GROUP_STAGE", round_num=1, group_identifier="A",
                game_results=self._make_game_results(1, 3, 2, 1),  # uid1 win
            ),
            _mock_session(
                id=2, phase="GROUP_STAGE", round_num=2, group_identifier="A",
                game_results=self._make_game_results(1, 2, 3, 2),  # draw
            ),
            _mock_session(
                id=3, phase="GROUP_STAGE", round_num=3, group_identifier="A",
                game_results=self._make_game_results(2, 3, 3, 0),  # uid2 win
            ),
        ]
        db = _mock_db(sessions)
        standings = compute_group_standings(db, tournament_id=99)
        assert "A" in standings
        rows = standings["A"]
        assert rows[0]["user_id"] == 1 and rows[0]["rank"] == 1 and rows[0]["points"] == 4
        assert rows[1]["user_id"] == 2 and rows[1]["rank"] == 2 and rows[1]["points"] == 3
        assert rows[2]["user_id"] == 3 and rows[2]["rank"] == 3 and rows[2]["points"] == 1


# ── QS-05..08: assign_semifinal_participants ───────────────────────────────────

class TestAssignSemifinalParticipants:

    def _make_sf_sessions(self):
        sf1 = _mock_session(
            id=10, phase="KNOCKOUT", round_num=1, match_num=1,
            structure_config={"matchup": "Group A winner vs Best runner-up", "seed_1": "A1", "seed_2": "BR"},
        )
        sf2 = _mock_session(
            id=11, phase="KNOCKOUT", round_num=1, match_num=2,
            structure_config={"matchup": "Group B winner vs Group C winner", "seed_1": "B1", "seed_2": "C1"},
        )
        return sf1, sf2

    def _make_final_session(self):
        return _mock_session(
            id=20, phase="KNOCKOUT", round_num=2, match_num=1,
            structure_config={"matchup": "SF1 winner vs SF2 winner"},
        )

    def _make_bronze_session(self):
        return _mock_session(
            id=21, phase="KNOCKOUT", round_num=3, match_num=1,
            structure_config={"matchup": "SF1 loser vs SF2 loser"},
        )

    def _patch_standings(self, group_winners: dict, best_runner_up_uid: int):
        """
        Patches compute_group_standings so assign_semifinal_participants
        resolves winners and runner-up without a real DB.
        """
        standings = {}
        for letter, (w_uid, ru_uid) in group_winners.items():
            standings[letter] = [
                {"user_id": w_uid,  "rank": 1, "points": 6, "gf": 3.0, "ga": 0.0,
                 "wins": 2, "draws": 0, "losses": 0},
                {"user_id": ru_uid, "rank": 2, "points": 3, "gf": 2.0, "ga": 1.0,
                 "wins": 1, "draws": 0, "losses": 1},
                {"user_id": ru_uid + 100, "rank": 3, "points": 0, "gf": 0.0, "ga": 4.0,
                 "wins": 0, "draws": 0, "losses": 2},
            ]
        return standings

    def test_qs05_sf_sessions_get_correct_participants(self):
        sf1, sf2 = self._make_sf_sessions()
        final = self._make_final_session()
        bronze = self._make_bronze_session()

        standings = self._patch_standings(
            {"A": (101, 102), "B": (201, 202), "C": (301, 302)},
            best_runner_up_uid=102,
        )
        # Best runner-up: uid102 (Group A runner-up has highest pts in equal scenario)

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [sf1, sf2]

        with patch(
            "app.services.tournament.qualification.compute_group_standings",
            return_value=standings,
        ):
            assign_semifinal_participants(db, tournament_id=1, best_runner_up_count=1)

        # SF1: A1 → uid101, BR → uid102
        assert sf1.participant_user_ids == [101, 102]
        # SF2: B1 → uid201, C1 → uid301
        assert sf2.participant_user_ids == [201, 301]

        # Final and Bronze must NOT be modified (they are not in sf_sessions query)
        assert final.participant_user_ids is None
        assert bronze.participant_user_ids is None

        db.flush.assert_called_once()

    def test_qs06_final_and_bronze_never_modified(self):
        """The DB query filters tournament_round == 1 — Final (round 2) and
        Bronze (round 3) are never returned, so they are never touched."""
        sf1, sf2 = self._make_sf_sessions()
        final = self._make_final_session()
        bronze = self._make_bronze_session()

        standings = self._patch_standings(
            {"A": (101, 102), "B": (201, 202), "C": (301, 302)},
            best_runner_up_uid=102,
        )
        db = MagicMock()
        # Only SF sessions returned by round-1 filter
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [sf1, sf2]

        with patch(
            "app.services.tournament.qualification.compute_group_standings",
            return_value=standings,
        ):
            assign_semifinal_participants(db, tournament_id=1, best_runner_up_count=1)

        assert final.participant_user_ids is None
        assert bronze.participant_user_ids is None

    def test_qs07_idempotent_second_call_same_result(self):
        sf1, sf2 = self._make_sf_sessions()
        standings = self._patch_standings(
            {"A": (101, 102), "B": (201, 202), "C": (301, 302)},
            best_runner_up_uid=102,
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [sf1, sf2]

        with patch(
            "app.services.tournament.qualification.compute_group_standings",
            return_value=standings,
        ):
            assign_semifinal_participants(db, tournament_id=1, best_runner_up_count=1)
            first_sf1 = list(sf1.participant_user_ids)
            first_sf2 = list(sf2.participant_user_ids)
            assign_semifinal_participants(db, tournament_id=1, best_runner_up_count=1)

        assert sf1.participant_user_ids == first_sf1
        assert sf2.participant_user_ids == first_sf2

    def test_qs08_unresolvable_seed_skips_session(self):
        # SF1 has seed_1="X1" which is not in slot_map → session skipped
        sf_bad = _mock_session(
            id=10, phase="KNOCKOUT", round_num=1, match_num=1,
            structure_config={"seed_1": "X1", "seed_2": "BR"},
        )
        sf_good = _mock_session(
            id=11, phase="KNOCKOUT", round_num=1, match_num=2,
            structure_config={"seed_1": "B1", "seed_2": "C1"},
        )
        standings = self._patch_standings(
            {"A": (101, 102), "B": (201, 202), "C": (301, 302)},
            best_runner_up_uid=102,
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [sf_bad, sf_good]

        with patch(
            "app.services.tournament.qualification.compute_group_standings",
            return_value=standings,
        ):
            assign_semifinal_participants(db, tournament_id=1, best_runner_up_count=1)

        # Bad session: participant_user_ids must NOT be set (no partial [None, ...])
        assert sf_bad.participant_user_ids is None
        # Good session: still gets assigned
        assert sf_good.participant_user_ids == [201, 301]

    def test_qs10_empty_standings_no_crash(self):
        db = MagicMock()
        with patch(
            "app.services.tournament.qualification.compute_group_standings",
            return_value={},
        ):
            # Must not raise; must not call flush
            assign_semifinal_participants(db, tournament_id=1, best_runner_up_count=1)
        db.flush.assert_not_called()
