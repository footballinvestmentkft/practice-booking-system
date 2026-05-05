"""
Swiss System Format Generator

Generates sessions for Swiss system tournaments.
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


class SwissGenerator(BaseFormatGenerator):
    """
    Generates Swiss system tournament sessions

    Two modes based on tournament.format:
    1. INDIVIDUAL_RANKING: Performance-based pods (top performers vs top, middle vs middle)
    2. HEAD_TO_HEAD: 1v1 Swiss pairings (similar score players paired together)

    Typical rounds = log2(n) rounded up
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
        Generate Swiss system tournament sessions
        """
        sessions = []
        logger = logging.getLogger(__name__)
        campus_ids = kwargs.get('campus_ids')
        team_ids = kwargs.get('team_ids')
        team_mode = kwargs.get('team_mode', False)
        total_rounds = math.ceil(math.log2(player_count))

        # Resolve seeding pool: teams for TEAM tournaments, players otherwise
        if team_mode and team_ids:
            player_ids = team_ids
        else:
            enrolled_players = self.db.query(SemesterEnrollment).filter(
                SemesterEnrollment.semester_id == tournament.id,
                SemesterEnrollment.is_active == True,
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            ).all()
            player_ids = dedup_participant_ids(
                [e.user_id for e in enrolled_players],
                tournament.id, logger, context="swiss.generate",
            )

        current_time = tournament.start_date

        if tournament.format == 'HEAD_TO_HEAD':
            # ============================================================================
            # HEAD_TO_HEAD MODE: 1v1 Swiss Pairings
            # ============================================================================
            # Round 1: Random/seeded pairing
            # Round 2+: Pair players with similar scores (Swiss pairing algorithm)

            field_slots = [current_time for _ in range(parallel_fields)]

            for round_num in range(1, total_rounds + 1):
                # Generate 1v1 pairings for this round
                # Round 1: Simple sequential pairing (can be randomized later)
                # Round 2+: Would need performance-based pairing (placeholder for now)

                match_num = 1
                for i in range(0, len(player_ids), 2):
                    if i + 1 >= len(player_ids):
                        # Odd number of players - last player gets a BYE
                        break

                    player1_id = player_ids[i]
                    player2_id = player_ids[i + 1]

                    if player1_id == player2_id:             # P0-A: self-match guard
                        logger.error(
                            "🚨 SELF-MATCH BLOCKED | tournament=%s | participant_id=%s",
                            tournament.id, player1_id,
                        )
                        continue

                    # Assign to next available field
                    field_index = (match_num - 1) % parallel_fields
                    session_start = field_slots[field_index]
                    session_end = session_start + timedelta(minutes=session_duration)

                    sessions.append({
                        'title': f'{tournament.name} - Round {round_num} - Match {match_num}',
                        'description': f'Swiss Round {round_num} - 1v1 match',
                        'date_start': session_start,
                        'date_end': session_end,
                        'game_type': f'Round {round_num}',
                        'tournament_phase': TournamentPhase.SWISS.value,
                        'tournament_round': round_num,
                        'tournament_match_number': match_num,
                        'location': get_tournament_venue(tournament),
                        'session_type': 'on_site',
                        # ✅ HEAD_TO_HEAD: 1v1 match metadata
                        'ranking_mode': 'PERFORMANCE_PAIRING',
                        'round_number': round_num,
                        'expected_participants': 2,
                        'participant_filter': 'swiss_pairing',
                        'group_identifier': None,
                        'pod_tier': None,  # No pods in HEAD_TO_HEAD
                        # ✅ MATCH STRUCTURE: HEAD_TO_HEAD format
                        'match_format': tournament.format,
                        'scoring_type': tournament.scoring_type,
                        'structure_config': {
                            'expected_participants': 2,
                            'pairing_type': 'random' if round_num == 1 else 'performance_based'
                        },
                        'participant_team_ids': [player1_id, player2_id] if team_mode else None,
                        'participant_user_ids': None if team_mode else [player1_id, player2_id],
                        # ✅ Multi-campus: round-robin campus assignment
                        'campus_id': pick_campus(len(sessions), campus_ids),
                        'pitch_id': pick_pitch(len(sessions), pick_campus(len(sessions), campus_ids), parallel_fields, self.db),
                    })

                    # Update field slot time
                    field_slots[field_index] = session_end + timedelta(minutes=break_minutes)
                    match_num += 1

                # Move to next round - wait for all fields to finish
                current_time = max(field_slots) + timedelta(minutes=break_minutes)
                field_slots = [current_time for _ in range(parallel_fields)]

        else:
            # ============================================================================
            # INDIVIDUAL_RANKING MODE: Performance-based pods
            # ============================================================================
            # Get pod configuration from config (default: 4 players per pod)
            pod_size = tournament_type.config.get('pod_size', 4)
            pods_count = max(1, player_count // pod_size)

            for round_num in range(1, total_rounds + 1):
                # In Swiss System, players are divided into performance-based pods after Round 1
                for pod_num in range(1, pods_count + 1):
                    session_start = current_time
                    session_end = session_start + timedelta(minutes=session_duration)

                    # Pod tier naming: Pod 1 = Top performers, Pod 2 = Mid-tier, etc.
                    pod_name = f"Pod {pod_num}" if pods_count > 1 else "Main"

                    sessions.append({
                        'title': f'{tournament.name} - Round {round_num} - {pod_name}',
                        'description': f'Swiss system round {round_num} - {pod_name} ({pod_size} players)',
                        'date_start': session_start,
                        'date_end': session_end,
                        'game_type': f'Round {round_num}',
                        'tournament_phase': TournamentPhase.SWISS.value,
                        'tournament_round': round_num,
                        'tournament_match_number': pod_num,
                        'location': get_tournament_venue(tournament),
                        'session_type': 'on_site',
                        # ✅ UNIFIED RANKING: Swiss performance pod metadata
                        'ranking_mode': 'PERFORMANCE_POD',
                        'round_number': round_num,
                        'expected_participants': pod_size,
                        'participant_filter': 'dynamic_swiss_pairing',
                        'group_identifier': None,
                        'pod_tier': pod_num,  # Pod tier (1=top performers, 2=middle, etc.)
                        # ✅ MATCH STRUCTURE: INDIVIDUAL_RANKING format
                        'match_format': tournament.format,
                        'scoring_type': tournament.scoring_type,
                        'structure_config': {
                            'pod': pod_num,
                            'pod_size': pod_size,
                            'expected_participants': pod_size,
                            'performance_tier': pod_num
                        },
                        # ✅ FIX: Add participant_user_ids - Initially all players in Round 1, then dynamic allocation by performance
                        'participant_user_ids': player_ids if round_num == 1 else player_ids[(pod_num-1)*pod_size:pod_num*pod_size] if len(player_ids) >= pod_num*pod_size else player_ids[(pod_num-1)*pod_size:],
                        # ✅ Multi-campus: round-robin campus assignment
                        'campus_id': pick_campus(len(sessions), campus_ids),
                        'pitch_id': pick_pitch(len(sessions), pick_campus(len(sessions), campus_ids), parallel_fields, self.db),
                    })

                    # Schedule parallel pods
                    if pod_num % parallel_fields != 0:
                        continue
                    else:
                        current_time += timedelta(minutes=session_duration + break_minutes)

                # Break between rounds
                current_time += timedelta(minutes=break_minutes * 2)

        return sessions
