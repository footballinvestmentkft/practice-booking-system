"""
League Format Generator

Generates sessions for league (round-robin) tournaments.
"""
from typing import List, Dict, Any
from datetime import timedelta

from app.models.semester import Semester
from app.models.tournament_type import TournamentType
from app.models.tournament_enums import TournamentPhase
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from .base_format_generator import BaseFormatGenerator
from ..algorithms import RoundRobinPairing
from ..utils import get_tournament_venue, pick_campus, pick_pitch, dedup_participant_ids


class LeagueGenerator(BaseFormatGenerator):
    """
    Generates league tournament sessions

    INDIVIDUAL_RANKING: N rounds where ALL players compete and rank together.
    HEAD_TO_HEAD: Traditional round robin (1v1 pairing).
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
        Generate league sessions based on tournament format
        """
        sessions = []
        campus_ids = kwargs.get('campus_ids')

        # ✅ Use tournament.format (from Semester table) to determine match structure
        # This is set by admin in UI and stored in semesters.format column
        tournament_format = tournament.format

        if tournament_format == 'HEAD_TO_HEAD':
            # ✅ HEAD_TO_HEAD: Traditional round robin (1v1 pairings)
            # Total matches = n*(n-1)/2 per leg
            # Use pairing algorithm for fair scheduling
            sessions = self._generate_head_to_head_pairings(
                tournament, tournament_type, player_count, parallel_fields, session_duration, break_minutes,
                campus_ids=campus_ids,
                team_ids=kwargs.get('team_ids'),
                team_mode=kwargs.get('team_mode', False),
                number_of_legs=kwargs.get('number_of_legs', 1),
                track_home_away=kwargs.get('track_home_away', False),
            )
        else:
            # ✅ INDIVIDUAL_RANKING: Multi-player ranking rounds
            # Get number of ranking rounds from config (default: n-1 rounds)
            number_of_rounds = tournament_type.config.get('ranking_rounds', player_count - 1)

            # ✅ Get enrolled players for participant_user_ids
            enrolled_players = self.db.query(SemesterEnrollment).filter(
                SemesterEnrollment.semester_id == tournament.id,
                SemesterEnrollment.is_active == True,
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
            ).all()
            player_ids = [enrollment.user_id for enrollment in enrolled_players]

            current_time = tournament.start_date

            for round_num in range(1, number_of_rounds + 1):
                session_start = current_time
                session_end = session_start + timedelta(minutes=session_duration)

                sessions.append({
                    'title': f'{tournament.name} - Ranking Round {round_num}',
                    'description': f'All {player_count} players compete and rank in this round',
                    'date_start': session_start,
                    'date_end': session_end,
                    'game_type': f'Ranking Round {round_num}',
                    'tournament_phase': 'League - Multi-Player Ranking',
                    'tournament_round': round_num,
                    'tournament_match_number': round_num,
                    'location': get_tournament_venue(tournament),
                    'session_type': 'on_site',
                    # ✅ UNIFIED RANKING: Ranking metadata
                    'ranking_mode': 'ALL_PARTICIPANTS',
                    'round_number': round_num,  # ✅ MANDATORY: Round number for fixtures display
                    'expected_participants': player_count,
                    'participant_filter': None,
                    'group_identifier': None,
                    'pod_tier': None,
                    # ✅ MATCH STRUCTURE: Format and scoring metadata (from tournament config)
                    'match_format': tournament.format,
                    'scoring_type': tournament.scoring_type,
                    'structure_config': {
                        'expected_participants': player_count,
                        'ranking_criteria': 'final_placement'
                    },
                    # ✅ FIX: Add participant_user_ids with all enrolled players
                    'participant_user_ids': player_ids,
                    # ✅ Multi-campus: round-robin campus assignment
                    'campus_id': pick_campus(len(sessions), campus_ids),
                    'pitch_id': pick_pitch(len(sessions), pick_campus(len(sessions), campus_ids), parallel_fields, self.db),
                })

                # Move to next time slot
                current_time += timedelta(minutes=session_duration + break_minutes)

        return sessions

    def _generate_head_to_head_pairings(
        self,
        tournament: Semester,
        config: TournamentType,
        player_count: int,
        parallel_fields: int,
        session_duration: int,
        break_minutes: int,
        campus_ids=None,
        team_ids=None,
        team_mode=False,
        number_of_legs: int = 1,
        track_home_away: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Generate HEAD_TO_HEAD round robin sessions (1v1 pairings).

        For TEAM tournaments: team_ids provided → pairings are between teams,
        sessions carry participant_team_ids instead of participant_user_ids.
        For INDIVIDUAL tournaments: queries SemesterEnrollment for player IDs.

        number_of_legs: how many full round-robin cycles to generate.
        track_home_away: when True, even legs reverse each pairing (home↔away swap).
        """
        sessions = []

        import logging
        logger = logging.getLogger(__name__)

        if team_mode and team_ids:
            participant_ids = team_ids
        else:
            enrolled_players = self.db.query(SemesterEnrollment).filter(
                SemesterEnrollment.semester_id == tournament.id,
                SemesterEnrollment.is_active == True,
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            ).all()
            participant_ids = dedup_participant_ids(
                [e.user_id for e in enrolled_players],
                tournament.id, logger,
                context="league._generate_head_to_head_pairings",
            )

        num_rounds = RoundRobinPairing.calculate_rounds(player_count)
        current_time = tournament.start_date
        field_slots = [current_time for _ in range(parallel_fields)]

        for leg in range(1, number_of_legs + 1):
            for round_num in range(1, num_rounds + 1):
                round_pairings = RoundRobinPairing.get_round_pairings(participant_ids, round_num)
                field_index = 0

                for match_num, (id1, id2) in enumerate(round_pairings, start=1):
                    if id1 is None or id2 is None:
                        continue
                    if id1 == id2:                           # P0-A: self-match guard
                        logger.error(
                            "🚨 SELF-MATCH BLOCKED | tournament=%s | participant_id=%s",
                            tournament.id, id1,
                        )
                        continue

                    # Even legs with home/away tracking: swap so the "home" side becomes "away"
                    if track_home_away and leg % 2 == 0:
                        id1, id2 = id2, id1

                    session_start = field_slots[field_index]
                    session_end = session_start + timedelta(minutes=session_duration)

                    leg_label = f' (Leg {leg})' if number_of_legs > 1 else ''
                    session_data = {
                        'title': f'{tournament.name} - Round {round_num} - Match {match_num}{leg_label}',
                        'description': f'Leg {leg}, Round {round_num} head-to-head match (Field {field_index + 1})',
                        'date_start': session_start,
                        'date_end': session_end,
                        'game_type': f'Round {round_num}',
                        'tournament_phase': TournamentPhase.GROUP_STAGE.value,
                        'tournament_round': round_num,
                        'tournament_match_number': match_num,
                        'leg_number': leg,
                        'location': get_tournament_venue(tournament),
                        'session_type': 'on_site',
                        'ranking_mode': 'ALL_PARTICIPANTS',
                        'round_number': round_num,
                        'expected_participants': 2,
                        'participant_filter': None,
                        'group_identifier': None,
                        'pod_tier': None,
                        'match_format': tournament.format,
                        'scoring_type': tournament.scoring_type,
                        'structure_config': {
                            'expected_participants': 2,
                            'match_type': 'round_robin',
                            'field_number': field_index + 1,
                            'leg_number': leg,
                            'is_home_game': (leg % 2 == 1) if track_home_away else None,
                        },
                        'campus_id': pick_campus(len(sessions), campus_ids),
                        'pitch_id': pick_pitch(len(sessions), pick_campus(len(sessions), campus_ids), parallel_fields, self.db),
                    }
                    if team_mode:
                        session_data['participant_team_ids'] = [id1, id2]
                        session_data['participant_user_ids'] = None
                    else:
                        session_data['participant_user_ids'] = [id1, id2]

                    sessions.append(session_data)
                    field_slots[field_index] += timedelta(minutes=session_duration + break_minutes)
                    field_index = (field_index + 1) % parallel_fields

        return sessions
