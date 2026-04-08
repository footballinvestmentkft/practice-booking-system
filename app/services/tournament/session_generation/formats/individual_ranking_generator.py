"""
Individual Ranking Format Generator

Generates sessions for simple individual ranking competitions.
"""
from typing import List, Dict, Any
from datetime import timedelta

from app.models.semester import Semester
from app.models.tournament_enums import TournamentPhase
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from .base_format_generator import BaseFormatGenerator
from ..utils import get_tournament_venue, pick_campus, pick_pitch


class IndividualRankingGenerator(BaseFormatGenerator):
    """
    Generates INDIVIDUAL_RANKING sessions (simple competition format)

    INDIVIDUAL_RANKING tournaments have NO tournament type structure.
    All players compete in a simple competition and are ranked by their results:
    - TIME_BASED: Lowest time wins (e.g., 100m sprint - 3 rounds, best time counts)
    - SCORE_BASED: Highest score wins (e.g., push-ups in 1 minute)
    - DISTANCE_BASED: Longest distance wins (e.g., long jump)
    - PLACEMENT: Manual placement (1st, 2nd, 3rd...)
    """

    def generate(
        self,
        tournament: Semester,
        tournament_type: None,  # Individual ranking has no tournament type
        player_count: int,
        parallel_fields: int,
        session_duration: int,
        break_minutes: int,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Generate INDIVIDUAL_RANKING sessions

        Args:
            number_of_rounds: Number of rounds to generate (1-10). For example, 100m sprint with 3 attempts.
        """
        import logging
        logger = logging.getLogger(__name__)

        try:
            logger.info(f"�� INDIVIDUAL_RANKING_GENERATOR.generate() START")
            logger.info(f"   tournament_id: {tournament.id}")
            logger.info(f"   tournament_name: {tournament.name}")
            logger.info(f"   player_count: {player_count}")
            logger.info(f"   parallel_fields: {parallel_fields}")
            logger.info(f"   session_duration: {session_duration}")
            logger.info(f"   break_minutes: {break_minutes}")
            logger.info(f"   kwargs: {kwargs}")

            sessions = []
            number_of_rounds = kwargs.get('number_of_rounds', 1)
            campus_ids = kwargs.get('campus_ids')
            team_mode = kwargs.get('team_mode', False)
            team_ids = kwargs.get('team_ids', [])
            logger.info(f"   number_of_rounds: {number_of_rounds}")

            # Get participant IDs: teams (TEAM mode) or enrolled players (INDIVIDUAL mode)
            if team_mode:
                player_ids = []  # Not used in team mode
                logger.info(f"   TEAM mode: using team_ids={team_ids}")
            else:
                enrolled_players = self.db.query(SemesterEnrollment).filter(
                    SemesterEnrollment.semester_id == tournament.id,
                    SemesterEnrollment.is_active == True,
                    SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
                ).all()
                player_ids = [enrollment.user_id for enrollment in enrolled_players]
                logger.info(f"   Enrolled players fetched: {len(enrolled_players)} players")
                logger.info(f"   Player IDs: {player_ids}")

            # 🔄 NEW ARCHITECTURE: Create 1 session for ALL rounds (not N sessions)
            # Total duration = (number_of_rounds * session_duration) + ((number_of_rounds - 1) * break_minutes)
            total_duration = (number_of_rounds * session_duration) + ((number_of_rounds - 1) * break_minutes)
            logger.info(f"   Total session duration calculated: {total_duration} minutes")

            session_start = tournament.start_date
            session_end = session_start + timedelta(minutes=total_duration)
            logger.info(f"   Session time: {session_start} to {session_end}")

            # Check tournament.scoring_type attribute
            logger.info(f"   Checking tournament.scoring_type...")
            if hasattr(tournament, 'scoring_type'):
                logger.info(f"   tournament.scoring_type exists: {tournament.scoring_type}")
            else:
                logger.warning(f"   ⚠️ tournament.scoring_type does NOT exist!")
                logger.info(f"   tournament attributes: {dir(tournament)}")

            # Determine description based on scoring type
            scoring_descriptions = {
                'TIME_BASED': f'{number_of_rounds} rounds - All players compete - lowest time wins',
                'SCORE_BASED': f'{number_of_rounds} rounds - All players compete - highest score wins',
                'DISTANCE_BASED': f'{number_of_rounds} rounds - All players compete - longest distance wins',
                'PLACEMENT': f'{number_of_rounds} rounds - All players compete - ranked by placement'
            }
            description = scoring_descriptions.get(
                tournament.scoring_type,
                f'{number_of_rounds} rounds - All players compete and are ranked'
            )
            logger.info(f"   Description: {description}")

            # Initialize rounds_data structure
            rounds_data = {
                'total_rounds': number_of_rounds,
                'completed_rounds': 0,
                'round_results': {}  # Will store: {'1': {'user_123': '12.5s', ...}, '2': {...}}
            }

            # Get tournament venue
            logger.info(f"   Getting tournament venue...")
            venue = get_tournament_venue(tournament)
            logger.info(f"   Venue: {venue}")

            # 🎯 CRITICAL FIX: For multi-round INDIVIDUAL tournaments, use ROUNDS_BASED scoring_type
            # This ensures the UI dispatcher routes to render_rounds_based_entry() instead of
            # trying to match on the underlying measurement type (TIME_BASED, SCORE_BASED, etc.)
            # The underlying measurement type is preserved in structure_config.scoring_method
            if number_of_rounds > 1:
                scoring_type_value = 'ROUNDS_BASED'
                logger.info(f"   🔄 Multi-round tournament detected ({number_of_rounds} rounds)")
                logger.info(f"   🔄 Setting scoring_type='ROUNDS_BASED' (underlying: {tournament.scoring_type})")
            else:
                scoring_type_value = tournament.scoring_type
                logger.info(f"   Single-round tournament - scoring_type='{tournament.scoring_type}'")

            session_dict = {
                'title': f'{tournament.name}',
                'description': description,
                'date_start': session_start,
                'date_end': session_end,
                'game_type': 'Individual Ranking Competition',
                'tournament_phase': TournamentPhase.INDIVIDUAL_RANKING.value,
                'tournament_round': 1,  # Always 1 since this session contains all rounds
                'tournament_match_number': 1,
                'location': venue,
                'session_type': self._resolve_session_type(tournament),
                'base_xp': self._resolve_base_xp(self._resolve_session_type(tournament)),
                # ✅ INDIVIDUAL_RANKING metadata
                'ranking_mode': 'ALL_PARTICIPANTS',
                'round_number': 1,
                'expected_participants': player_count,
                'participant_filter': None,
                'group_identifier': None,
                'pod_tier': None,
                # ✅ MATCH STRUCTURE: INDIVIDUAL_RANKING with scoring type
                'match_format': tournament.format,  # INDIVIDUAL_RANKING
                'scoring_type': scoring_type_value,  # ROUNDS_BASED (multi-round) or original (single-round)
                'structure_config': {
                    'expected_participants': player_count,
                    'scoring_method': tournament.scoring_type,
                    'description': description,
                    'number_of_rounds': number_of_rounds,
                    'mode': 'TEAM' if team_mode else 'INDIVIDUAL',
                },
                # ✅ Participants: teams (TEAM mode) or individual players
                'participant_user_ids': None if team_mode else player_ids,
                'participant_team_ids': team_ids if team_mode else None,
                # 🔄 NEW: Rounds data for multi-round tracking
                'rounds_data': rounds_data,
                # ✅ Multi-campus: pick first campus (single session format)
                'campus_id': pick_campus(0, campus_ids),
                'pitch_id': pick_pitch(0, pick_campus(0, campus_ids), parallel_fields, self.db),
            }

            logger.info(f"   Session dict created successfully")
            logger.info(f"   Session dict keys: {list(session_dict.keys())}")
            sessions.append(session_dict)

            logger.info(f"✅ INDIVIDUAL_RANKING_GENERATOR.generate() COMPLETE - Returning {len(sessions)} session(s)")
            return sessions

        except Exception as e:
            logger.error(f"❌❌❌ EXCEPTION IN INDIVIDUAL_RANKING_GENERATOR ❌❌❌")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception message: {str(e)}")
            import traceback
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            raise
