"""
Knockout Format Generator

Generates sessions for single elimination knockout tournaments.
"""
import math
import logging
from typing import List, Dict, Any
from datetime import timedelta

from app.models.semester import Semester
from app.models.tournament_type import TournamentType
from app.models.tournament_enums import TournamentPhase
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from .base_format_generator import BaseFormatGenerator
from ..utils import get_tournament_venue, pick_campus, pick_pitch, dedup_participant_ids


class KnockoutGenerator(BaseFormatGenerator):
    """
    Generates knockout (single elimination) tournament sessions

    ✅ HEAD_TO_HEAD: Each match is 1v1 (2 players)
    ✅ BRACKET PROGRESSION: Winners advance, losers eliminated
    ✅ SEEDING: Round 1 uses enrollment order, later rounds use match results

    Total matches = n - 1 (e.g., 8 players → 7 matches: 4 QF + 2 SF + 1 Final)
    """

    def generate(
        self,
        tournament: Semester,
        tournament_type: TournamentType,
        player_count: int,
        parallel_fields: int,
        session_duration: int,
        break_minutes: int,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Generate knockout tournament sessions
        """
        sessions = []
        logger = logging.getLogger(__name__)
        campus_ids = kwargs.get('campus_ids')
        team_ids = kwargs.get('team_ids')
        team_mode = kwargs.get('team_mode', False)
        total_rounds = math.ceil(math.log2(player_count))
        round_names = tournament_type.config.get('round_names', {})

        # Resolve seeding pool: teams for TEAM tournaments, players otherwise
        if team_mode and team_ids:
            player_ids = team_ids
        else:
            enrolled_players = self.db.query(SemesterEnrollment).filter(
                SemesterEnrollment.semester_id == tournament.id,
                SemesterEnrollment.is_active == True,
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            ).order_by(SemesterEnrollment.created_at).all()
            player_ids = dedup_participant_ids(
                [e.user_id for e in enrolled_players],
                tournament.id, logger, context="knockout.generate",
            )

        current_time = tournament.start_date

        # ✅ Generate bracket structure (all rounds)
        for round_num in range(1, total_rounds + 1):
            players_in_round = player_count // (2 ** (round_num - 1))
            matches_in_round = players_in_round // 2

            round_name = round_names.get(str(players_in_round), f"Round of {players_in_round}")

            for match_in_round in range(1, matches_in_round + 1):
                session_start = current_time
                session_end = session_start + timedelta(minutes=session_duration)

                # ✅ SEEDING LOGIC:
                # Round 1: Assign players using bracket seeding (1 vs n, 2 vs n-1, etc.)
                # Round 2+: participant_user_ids = None (determined by previous round results)
                if round_num == 1:
                    # ✅ BRACKET SEEDING: Standard single-elimination pairing
                    # Match 1: Seed 1 vs Seed N
                    # Match 2: Seed 2 vs Seed N-1
                    # etc.
                    seed1_index = (match_in_round - 1)
                    seed2_index = players_in_round - match_in_round

                    player1_id = player_ids[seed1_index] if seed1_index < len(player_ids) else None
                    player2_id = player_ids[seed2_index] if seed2_index < len(player_ids) else None

                    if player1_id is None or player2_id is None:
                        continue
                    if player1_id == player2_id:             # P0-A: self-match guard
                        logger.error(
                            "🚨 SELF-MATCH BLOCKED | tournament=%s | participant_id=%s",
                            tournament.id, player1_id,
                        )
                        continue

                    r1_ids = [player1_id, player2_id]
                else:
                    r1_ids = None

                session_data = {
                    'title': f'{tournament.name} - {round_name} - Match {match_in_round}',
                    'description': f'Head-to-head knockout match - {round_name}',
                    'date_start': session_start,
                    'date_end': session_end,
                    'game_type': round_name,
                    'tournament_phase': TournamentPhase.KNOCKOUT.value,
                    'tournament_round': round_num,
                    'tournament_match_number': match_in_round,
                    'location': get_tournament_venue(tournament),
                    'session_type': 'on_site',
                    'ranking_mode': 'HEAD_TO_HEAD',
                    'round_number': round_num,
                    'expected_participants': 2,
                    'participant_filter': None,
                    'group_identifier': None,
                    'match_format': tournament.format,
                    'scoring_type': tournament.scoring_type,
                    'structure_config': {
                        'round_name': round_name,
                        'round_number': round_num,
                        'match_number': match_in_round,
                        'total_matches_in_round': matches_in_round,
                    },
                    'campus_id': pick_campus(len(sessions), campus_ids),
                    'pitch_id': pick_pitch(len(sessions), pick_campus(len(sessions), campus_ids), parallel_fields, self.db),
                }
                if team_mode:
                    session_data['participant_team_ids'] = r1_ids
                    session_data['participant_user_ids'] = None
                else:
                    session_data['participant_user_ids'] = r1_ids
                sessions.append(session_data)

                # Schedule parallel fields
                if match_in_round % parallel_fields != 0:
                    continue
                else:
                    current_time += timedelta(minutes=session_duration + break_minutes)

            # Break between rounds
            current_time += timedelta(minutes=break_minutes * 2)

        # ✅ Add 3rd place playoff if configured
        if tournament_type.config.get('third_place_playoff'):
            sessions.append({
                'title': f'{tournament.name} - 3rd Place Playoff',
                'description': 'Bronze medal match - losers of semifinals',
                'date_start': current_time,
                'date_end': current_time + timedelta(minutes=session_duration),
                'game_type': '3rd Place Playoff',
                'tournament_phase': TournamentPhase.KNOCKOUT.value,
                'tournament_round': total_rounds,
                'tournament_match_number': 999,  # Special match number
                'location': get_tournament_venue(tournament),
                'session_type': 'on_site',
                # ✅ HEAD_TO_HEAD: 1v1 match
                'ranking_mode': 'HEAD_TO_HEAD',
                'round_number': total_rounds,
                'expected_participants': 2,  # ✅ FIX: 2 players (losers of semifinals)
                'participant_filter': None,
                'group_identifier': None,
                # ✅ MATCH STRUCTURE
                'match_format': tournament.format,
                'scoring_type': tournament.scoring_type,
                'structure_config': {
                    'round_name': '3rd Place Playoff',
                    'round_number': total_rounds,
                    'is_bronze_match': True
                },
                # ✅ Participants determined after semifinals
                'participant_user_ids': None,
                # ✅ Multi-campus: round-robin campus assignment
                'campus_id': pick_campus(len(sessions), campus_ids),
            })

        return sessions
