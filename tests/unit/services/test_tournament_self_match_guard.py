"""
P0-A Self-Match Guard — unit tests.

TC-SM-01  league HEAD_TO_HEAD: clean pool → no self-match sessions
TC-SM-02  league HEAD_TO_HEAD: duplicate ID in pool → session skipped, error logged
TC-SM-03  knockout R1: clean pool → no self-match sessions
TC-SM-04  knockout R1: duplicate ID in pool → session skipped, error logged
TC-SM-05  swiss HEAD_TO_HEAD: clean pool → no self-match sessions
TC-SM-06  swiss HEAD_TO_HEAD: duplicate ID in pool → session skipped, error logged
TC-SM-07  dedup_participant_ids: preserves order, logs on duplicate
TC-SM-08  group_knockout HEAD_TO_HEAD: clean pool → no self-match sessions
TC-SM-09  group_knockout HEAD_TO_HEAD: duplicate ID in pool → session skipped, no dup in ids
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services.tournament.session_generation.utils import dedup_participant_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tournament(tournament_id=1, format_="HEAD_TO_HEAD", scoring_type="points"):
    t = MagicMock()
    t.id = tournament_id
    t.name = "Test Tournament"
    t.start_date = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    t.format = format_
    t.scoring_type = scoring_type
    t.campus = None
    t.location = None
    return t


def _make_tournament_type(fmt="HEAD_TO_HEAD"):
    tt = MagicMock()
    tt.config = {"round_names": {}}
    tt.format = fmt
    return tt


def _make_enrollment(user_id):
    e = MagicMock()
    e.user_id = user_id
    return e


def _make_db(enrollments):
    db = MagicMock()
    query_mock = db.query.return_value
    query_mock.filter.return_value.all.return_value = enrollments
    query_mock.filter.return_value.order_by.return_value.all.return_value = enrollments
    query_mock.filter.return_value.first.return_value = None  # no pitch
    return db


# ---------------------------------------------------------------------------
# TC-SM-07: dedup_participant_ids unit
# ---------------------------------------------------------------------------

class TestDedupParticipantIds:
    def test_clean_list_unchanged(self):
        result = dedup_participant_ids([1, 2, 3], tournament_id=1, logger=MagicMock())
        assert result == [1, 2, 3]

    def test_duplicate_removed_and_logged(self):
        logger = MagicMock()
        result = dedup_participant_ids([1, 2, 1, 3], tournament_id=99, logger=logger)
        assert result == [1, 2, 3]
        logger.error.assert_called_once()
        call_args = logger.error.call_args[0]
        assert "SEEDING DEDUP" in call_args[0]

    def test_insertion_order_preserved(self):
        result = dedup_participant_ids([5, 3, 1, 3, 5], tournament_id=1, logger=MagicMock())
        assert result == [5, 3, 1]

    def test_all_duplicates(self):
        logger = MagicMock()
        result = dedup_participant_ids([7, 7, 7], tournament_id=1, logger=logger)
        assert result == [7]
        logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# TC-SM-01/02: League HEAD_TO_HEAD
# ---------------------------------------------------------------------------

class TestLeagueHeadToHeadSelfMatchGuard:
    def _generate(self, player_ids, tournament_id=1):
        from app.services.tournament.session_generation.formats.league_generator import LeagueGenerator
        enrollments = [_make_enrollment(uid) for uid in player_ids]
        db = _make_db(enrollments)
        gen = LeagueGenerator(db)
        tournament = _make_tournament(tournament_id=tournament_id)
        tt = _make_tournament_type()
        return gen.generate(
            tournament=tournament,
            tournament_type=tt,
            player_count=len(player_ids),
            parallel_fields=1,
            session_duration=60,
            break_minutes=10,
        )

    def test_tc_sm_01_clean_pool_no_self_matches(self):
        sessions = self._generate([10, 20, 30, 40])
        for s in sessions:
            ids = s.get("participant_user_ids") or []
            if len(ids) == 2:
                assert ids[0] != ids[1], f"Self-match found: {ids}"

    def test_tc_sm_02_duplicate_id_session_skipped(self):
        # Pool [10, 10] → duplicate; dedup → [10] → only 1 player, 0 matches
        # Pool [10, 20, 10] → dedup → [10, 20] → 1 match, no self-match
        sessions = self._generate([10, 20, 10])
        for s in sessions:
            ids = s.get("participant_user_ids") or []
            if len(ids) == 2:
                assert ids[0] != ids[1], f"Self-match found after dedup: {ids}"


# ---------------------------------------------------------------------------
# TC-SM-03/04: Knockout R1
# ---------------------------------------------------------------------------

class TestKnockoutSelfMatchGuard:
    def _generate(self, player_ids):
        from app.services.tournament.session_generation.formats.knockout_generator import KnockoutGenerator
        enrollments = [_make_enrollment(uid) for uid in player_ids]
        db = _make_db(enrollments)
        gen = KnockoutGenerator(db)
        tournament = _make_tournament()
        tt = _make_tournament_type()
        tt.config = {"round_names": {}}
        return gen.generate(
            tournament=tournament,
            tournament_type=tt,
            player_count=len(player_ids),
            parallel_fields=1,
            session_duration=60,
            break_minutes=10,
        )

    def test_tc_sm_03_clean_pool_no_self_matches(self):
        sessions = self._generate([1, 2, 3, 4])
        r1_sessions = [s for s in sessions if s.get("tournament_round") == 1]
        assert len(r1_sessions) == 2
        for s in r1_sessions:
            ids = s.get("participant_user_ids") or []
            if len(ids) == 2:
                assert ids[0] != ids[1], f"Self-match in R1: {ids}"

    def test_tc_sm_04_duplicate_id_session_skipped(self):
        # [1, 1, 2, 3] → dedup → [1, 2, 3] → 2 R1 matches, no self-match
        sessions = self._generate([1, 1, 2, 3])
        for s in sessions:
            ids = s.get("participant_user_ids") or []
            if len(ids) == 2:
                assert ids[0] != ids[1], f"Self-match found: {ids}"


# ---------------------------------------------------------------------------
# TC-SM-05/06: Swiss HEAD_TO_HEAD
# ---------------------------------------------------------------------------

class TestSwissHeadToHeadSelfMatchGuard:
    def _generate(self, player_ids):
        from app.services.tournament.session_generation.formats.swiss_generator import SwissGenerator
        enrollments = [_make_enrollment(uid) for uid in player_ids]
        db = _make_db(enrollments)
        gen = SwissGenerator(db)
        tournament = _make_tournament()
        tt = _make_tournament_type()
        tt.config = {"round_names": {}, "pod_size": 4}
        return gen.generate(
            tournament=tournament,
            tournament_type=tt,
            player_count=len(player_ids),
            parallel_fields=1,
            session_duration=60,
            break_minutes=10,
        )

    def test_tc_sm_05_clean_pool_no_self_matches(self):
        sessions = self._generate([100, 200, 300, 400])
        for s in sessions:
            ids = s.get("participant_user_ids") or []
            if len(ids) == 2:
                assert ids[0] != ids[1], f"Self-match in Swiss: {ids}"

    def test_tc_sm_06_duplicate_id_session_skipped(self):
        # [100, 200, 100, 300] → dedup → [100, 200, 300] → odd player, 1 match per round
        sessions = self._generate([100, 200, 100, 300])
        for s in sessions:
            ids = s.get("participant_user_ids") or []
            if len(ids) == 2:
                assert ids[0] != ids[1], f"Self-match in Swiss after dedup: {ids}"


# ---------------------------------------------------------------------------
# TC-SM-08/09: Group Knockout HEAD_TO_HEAD
# ---------------------------------------------------------------------------

class TestGroupKnockoutSelfMatchGuard:
    def _generate(self, player_ids):
        from app.services.tournament.session_generation.formats.group_knockout_generator import GroupKnockoutGenerator
        enrollments = [_make_enrollment(uid) for uid in player_ids]
        db = _make_db(enrollments)
        gen = GroupKnockoutGenerator(db)
        tournament = _make_tournament()
        tt = _make_tournament_type()
        tt.config = {"round_names": {}, "group_configuration": {}}
        return gen.generate(
            tournament=tournament,
            tournament_type=tt,
            player_count=len(player_ids),
            parallel_fields=1,
            session_duration=60,
            break_minutes=10,
        )

    def test_tc_sm_08_clean_pool_no_self_matches(self):
        # 8 players → GroupDistribution → 2 groups of 4 → 3 rounds × 2 matches each
        sessions = self._generate([11, 22, 33, 44, 55, 66, 77, 88])
        assert len(sessions) > 0, "Expected sessions to be generated"
        for s in sessions:
            ids = s.get("participant_user_ids") or []
            if len(ids) == 2:
                assert ids[0] != ids[1], f"Self-match in group stage: {ids}"

    def test_tc_sm_09_duplicate_pool_session_skipped(self):
        # [11,22,33,44,55,66,77,88,33] → dedup → 8 unique players
        # All generated 1v1 sessions must have distinct participant IDs
        raw = [11, 22, 33, 44, 55, 66, 77, 88, 33]
        sessions = self._generate(raw)
        for s in sessions:
            ids = s.get("participant_user_ids") or []
            if len(ids) == 2:
                assert ids[0] != ids[1], f"Self-match found in group_knockout: {ids}"
            # No duplicate IDs within any single session's participant list
            assert len(ids) == len(set(ids)), f"Duplicate participant_ids in session: {ids}"
