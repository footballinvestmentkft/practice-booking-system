"""
Unit tests — HeadToHeadGroupKnockoutRankingStrategy combined stats (Step A P0).

Verifies that knockout match stats (wins/losses/goals) are accumulated on top of
group stage stats so that the final `tournament_rankings` row reflects the full
tournament picture.  Points remain group-stage-only.

Tournament fixture (8 players, 2 groups of 4, SF + Final + Bronze):

  Group A: u1, u2, u3, u4  — u1 wins group, u2 runner-up
  Group B: u5, u6, u7, u8  — u5 wins group, u6 runner-up

  SF1: u1 vs u6 → u1 wins 2-1
  SF2: u5 vs u2 → u5 wins 3-0

  Final:  u1 vs u5 → u5 wins 2-1  (u5=champion, u1=runner-up)
  Bronze: u2 vs u6 → u2 wins 1-0  (u2=3rd, u6=4th)

  u3/u4 (Group A losers), u7/u8 (Group B losers) — no knockout stage.

Expected combined stats for selected players:
  u5 (champion): wins = group_wins + SF win + Final win
  u1 (rank 2):   losses = group_losses + Final loss; wins include SF win
  u2 (rank 3):   wins include Bronze win; losses include SF loss
  u6 (rank 4):   losses include SF loss + Bronze loss
  Points = group stage only (no knockout contribution).
  goal_difference = combined (group + KO).
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace
from app.services.tournament.ranking.strategies.head_to_head_group_knockout import (
    HeadToHeadGroupKnockoutRankingStrategy,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _gr(p1_id: int, p1_score: int, p2_id: int, p2_score: int) -> dict:
    """Build a game_results dict for a HEAD_TO_HEAD match."""
    if p1_score > p2_score:
        r1, r2 = "win", "loss"
    elif p1_score < p2_score:
        r1, r2 = "loss", "win"
    else:
        r1, r2 = "draw", "draw"
    return {
        "match_format": "HEAD_TO_HEAD",
        "participants": [
            {"user_id": p1_id, "score": p1_score, "result": r1},
            {"user_id": p2_id, "score": p2_score, "result": r2},
        ],
    }


def _group_session(
    p1_id: int, p1_score: int, p2_id: int, p2_score: int, group: str, rnd: int
) -> SimpleNamespace:
    from app.models.tournament_enums import TournamentPhase
    return SimpleNamespace(
        tournament_phase=TournamentPhase.GROUP_STAGE.value,
        group_identifier=group,
        tournament_round=rnd,
        title=None,
        game_results=_gr(p1_id, p1_score, p2_id, p2_score),
    )


def _ko_session(
    p1_id: int, p1_score: int, p2_id: int, p2_score: int, rnd: int, title: str = ""
) -> SimpleNamespace:
    from app.models.tournament_enums import TournamentPhase
    return SimpleNamespace(
        tournament_phase=TournamentPhase.KNOCKOUT.value,
        group_identifier=None,
        tournament_round=rnd,
        title=title,
        game_results=_gr(p1_id, p1_score, p2_id, p2_score),
    )


# ── shared fixture ─────────────────────────────────────────────────────────────

# user ids
U1, U2, U3, U4 = 1, 2, 3, 4   # Group A
U5, U6, U7, U8 = 5, 6, 7, 8   # Group B


def _build_sessions():
    """Return (group_sessions, knockout_sessions, all_sessions)."""

    # Group A round-robin (u1 goes 3W, u2 goes 2W1L)
    # u1 beats u2 2-1, u3 1-0, u4 3-0
    # u2 beats u3 2-0, u4 1-0
    # u3 beats u4 1-0
    group_a = [
        _group_session(U1, 2, U2, 1, "A", 1),
        _group_session(U1, 1, U3, 0, "A", 1),
        _group_session(U1, 3, U4, 0, "A", 2),
        _group_session(U2, 2, U3, 0, "A", 2),
        _group_session(U2, 1, U4, 0, "A", 3),
        _group_session(U3, 1, U4, 0, "A", 3),
    ]

    # Group B round-robin (u5 goes 3W, u6 goes 2W1L)
    # u5 beats u6 2-0, u7 3-1, u8 2-0
    # u6 beats u7 1-0, u8 2-0
    # u7 beats u8 1-0
    group_b = [
        _group_session(U5, 2, U6, 0, "B", 1),
        _group_session(U5, 3, U7, 1, "B", 1),
        _group_session(U5, 2, U8, 0, "B", 2),
        _group_session(U6, 1, U7, 0, "B", 2),
        _group_session(U6, 2, U8, 0, "B", 3),
        _group_session(U7, 1, U8, 0, "B", 3),
    ]

    # Knockout
    sf1    = _ko_session(U1, 2, U6, 1, rnd=2, title="Semi-finals")
    sf2    = _ko_session(U5, 3, U2, 0, rnd=2, title="Semi-finals")
    final  = _ko_session(U5, 2, U1, 1, rnd=3, title="Final")
    bronze = _ko_session(U2, 1, U6, 0, rnd=3, title="3rd Place Match")

    return group_a + group_b, [sf1, sf2, final, bronze]


def _rankings_by_uid(sessions_all) -> dict:
    strategy = HeadToHeadGroupKnockoutRankingStrategy()
    group_s = [s for s in sessions_all if "GROUP" in str(s.tournament_phase)]
    ko_s    = [s for s in sessions_all if "KNOCKOUT" in str(s.tournament_phase)]
    results = strategy.calculate_rankings(group_s + ko_s, db_session=None)
    return {r["user_id"]: r for r in results}


@pytest.fixture(scope="module")
def rankings():
    group_s, ko_s = _build_sessions()
    return _rankings_by_uid(group_s + ko_s)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestGroupKnockoutCombinedStats:

    def test_champion_rank_is_1(self, rankings):
        assert rankings[U5]["rank"] == 1

    def test_runner_up_rank_is_2(self, rankings):
        assert rankings[U1]["rank"] == 2

    def test_bronze_winner_rank_is_3(self, rankings):
        assert rankings[U2]["rank"] == 3

    def test_bronze_loser_rank_is_4(self, rankings):
        assert rankings[U6]["rank"] == 4

    def test_group_only_losers_ranked_below_4(self, rankings):
        for uid in (U3, U4, U7, U8):
            assert rankings[uid]["rank"] > 4

    # ── wins combine group + knockout ─────────────────────────────────────────

    def test_champion_wins_include_ko(self, rankings):
        # u5: 3 group wins + SF win + Final win = 5
        assert rankings[U5]["wins"] == 5

    def test_runner_up_wins_include_sf_win(self, rankings):
        # u1: 3 group wins + SF win (lost Final) = 4
        assert rankings[U1]["wins"] == 4

    def test_bronze_winner_wins_include_bronze(self, rankings):
        # u2: 2 group wins + Bronze win (lost SF) = 3
        assert rankings[U2]["wins"] == 3

    # ── losses combine group + knockout ───────────────────────────────────────

    def test_runner_up_losses_include_final(self, rankings):
        # u1: 1 group loss (to nobody — u1 won all in group A: 0) + Final loss = 1
        assert rankings[U1]["losses"] == 1

    def test_bronze_loser_losses_include_sf_and_bronze(self, rankings):
        # u6: 1 group loss (to u5) + SF loss + Bronze loss = 3
        assert rankings[U6]["losses"] == 3

    def test_sf_loser_who_won_bronze_loss_is_sf_only(self, rankings):
        # u2: 1 group loss (to u1) + SF loss = 2; bronze WIN not a loss
        assert rankings[U2]["losses"] == 2

    # ── points = group stage only ──────────────────────────────────────────────

    def test_champion_points_are_group_only(self, rankings):
        # u5: 3W × 3pts = 9
        assert rankings[U5]["points"] == 9

    def test_runner_up_points_are_group_only(self, rankings):
        # u1: 3W × 3pts = 9
        assert rankings[U1]["points"] == 9

    def test_bronze_winner_points_are_group_only(self, rankings):
        # u2: 2W × 3pts + 0 = 6; bronze win does NOT add 3
        assert rankings[U2]["points"] == 6

    # ── goal_difference is combined ───────────────────────────────────────────

    def test_champion_goal_difference_combined(self, rankings):
        # u5 group GF=7, GA=1 (+6); KO: SF 3-0, Final 2-1 → GF=5, GA=1 (+4); total +10
        r = rankings[U5]
        assert r["goals_for"] == 12
        assert r["goals_against"] == 2
        assert r["goal_difference"] == r["goals_for"] - r["goals_against"]

    def test_runner_up_goal_difference_combined(self, rankings):
        # u1 group: 2-1, 1-0, 3-0 → GF=6, GA=1; KO: SF 2-1 (win), Final 1-2 (loss) → GF=3, GA=3
        r = rankings[U1]
        assert r["goals_for"] == 9
        assert r["goals_against"] == 4
        assert r["goal_difference"] == 5

    def test_goal_difference_formula_consistent(self, rankings):
        for uid, r in rankings.items():
            assert r["goal_difference"] == r["goals_for"] - r["goals_against"], (
                f"user {uid}: gd={r['goal_difference']} but gf-ga="
                f"{r['goals_for']}-{r['goals_against']}"
            )

    # ── _calculate_knockout_stats helper ──────────────────────────────────────

    def test_knockout_stats_helper_champion(self):
        _, ko_s = _build_sessions()
        strategy = HeadToHeadGroupKnockoutRankingStrategy()
        ks = strategy._calculate_knockout_stats(ko_s)
        # u5: SF win 3-0, Final win 2-1 → wins=2, losses=0, gf=5, ga=1
        assert ks[U5]["wins"] == 2
        assert ks[U5]["losses"] == 0
        assert ks[U5]["goals_for"] == 5
        assert ks[U5]["goals_against"] == 1

    def test_knockout_stats_helper_runner_up(self):
        _, ko_s = _build_sessions()
        strategy = HeadToHeadGroupKnockoutRankingStrategy()
        ks = strategy._calculate_knockout_stats(ko_s)
        # u1: SF win 2-1, Final loss 1-2 → wins=1, losses=1, gf=3, ga=3
        assert ks[U1]["wins"] == 1
        assert ks[U1]["losses"] == 1
        assert ks[U1]["goals_for"] == 3
        assert ks[U1]["goals_against"] == 3

    def test_knockout_stats_helper_bronze_winner(self):
        _, ko_s = _build_sessions()
        strategy = HeadToHeadGroupKnockoutRankingStrategy()
        ks = strategy._calculate_knockout_stats(ko_s)
        # u2: SF loss 0-3, Bronze win 1-0 → wins=1, losses=1, gf=1, ga=3
        assert ks[U2]["wins"] == 1
        assert ks[U2]["losses"] == 1
        assert ks[U2]["goals_for"] == 1
        assert ks[U2]["goals_against"] == 3

    def test_knockout_stats_excludes_group_only_players(self):
        _, ko_s = _build_sessions()
        strategy = HeadToHeadGroupKnockoutRankingStrategy()
        ks = strategy._calculate_knockout_stats(ko_s)
        for uid in (U3, U4, U7, U8):
            assert uid not in ks
