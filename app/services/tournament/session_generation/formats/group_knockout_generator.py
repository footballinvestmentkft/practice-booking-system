"""
Group + Knockout Format Generator

Generates sessions for group stage followed by knockout stage tournaments.
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
from ..algorithms import RoundRobinPairing, GroupDistribution, KnockoutBracket
from ..utils import get_tournament_venue, pick_campus, pick_pitch, dedup_participant_ids


class GroupKnockoutGenerator(BaseFormatGenerator):
    """
    Generates group stage + knockout tournament sessions

    ✅ MATCH PARTICIPANTS: Explicit participant_user_ids for each match
    ✅ FIX: Group sessions ONLY include group members (not all tournament players)

    Group Stage: Players divided into groups, each group ranks separately.
    Knockout Stage: Top qualifiers from each group advance to multi-player knockout.
    """

    def generate(
        self,
        tournament: Semester,
        tournament_type: TournamentType,
        player_count: int,
        parallel_fields: int,
        session_duration: int,
        break_minutes: int,
        campus_ids: List[int] = None,
        campus_configs: Dict[int, dict] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Generate group stage + knockout tournament sessions
        """
        sessions = []
        logger = logging.getLogger(__name__)
        team_ids = kwargs.get('team_ids')
        team_mode = kwargs.get('team_mode', False)
        number_of_legs = kwargs.get('number_of_legs', 1)
        track_home_away = kwargs.get('track_home_away', False)

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
                tournament.id, logger, context="group_knockout.generate",
            )

        # Use actual enrolled count, not configured max
        actual_player_count = len(player_ids)

        # ✅ NEW: Use dynamic group distribution (supports odd player counts)
        # Try config first, then fall back to dynamic calculation
        group_config = tournament_type.config.get('group_configuration', {}).get(f'{actual_player_count}_players')

        if group_config:
            # Use predefined config from tournament_type
            groups_count = group_config['groups']
            qualifiers_per_group = group_config['qualifiers']
            group_rounds = group_config.get('rounds', 3)
            # Sequential distribution (config doesn't specify sizes)
            group_sizes = [len(player_ids) // groups_count] * groups_count
            remainder = len(player_ids) % groups_count
            for i in range(remainder):
                group_sizes[i] += 1
        else:
            # ✅ NEW: Dynamic calculation for flexible player counts
            distribution = GroupDistribution.calculate_optimal_distribution(actual_player_count)
            groups_count = distribution['groups_count']
            group_sizes = distribution['group_sizes']
            qualifiers_per_group = distribution['qualifiers_per_group']
            group_rounds = distribution['group_rounds']

        # ── Qualification policy override ──────────────────────────────────────
        # Policy is scoped per player-count inside group_configuration[N_players].
        # Reading from top-level config would affect ALL sizes sharing the same
        # TournamentType (e.g. 16p would break if 9p policy were at top level).
        _q_policy = (group_config or {}).get('qualification_policy', 'fixed_per_group')
        _best_runner_up_count = int((group_config or {}).get('best_runner_up_count', 0))
        if _q_policy == 'winners_plus_best_runner_up' and _best_runner_up_count > 0:
            qualifiers_per_group = 1   # only group winners qualify automatically
        else:
            _best_runner_up_count = 0  # policy inactive — zero out to prevent drift

        # ✅ NEW: Assign players to groups with variable sizes
        group_assignments = {}  # {group_name: [user_id1, user_id2, ...]}
        player_index = 0

        for group_num in range(1, groups_count + 1):
            group_name = chr(64 + group_num)  # A, B, C, D, E, ...
            group_size = group_sizes[group_num - 1]
            group_assignments[group_name] = player_ids[player_index:player_index + group_size]
            player_index += group_size

        # ✅ MULTI-CAMPUS SUPPORT: Load campus names for distributed sessions
        group_to_campus = {}     # {group_name: campus_location_string}
        group_to_campus_id = {}  # {group_name: campus_id (int)}
        knockout_location = get_tournament_venue(tournament)  # Default fallback

        if campus_ids and len(campus_ids) > 0:
            from app.models.campus import Campus
            campuses = self.db.query(Campus).filter(
                Campus.id.in_(campus_ids),
                Campus.is_active == True
            ).order_by(Campus.id).all()

            campus_locations = [f"{c.name} ({c.venue})" if c.venue else c.name for c in campuses]
            # Keep ordered campus_id list in the same order as campuses query
            ordered_campus_ids = [c.id for c in campuses]

            # Round-robin assignment: Group A → Campus 0, Group B → Campus 1, etc.
            for group_num in range(1, groups_count + 1):
                group_name = chr(64 + group_num)
                campus_idx = (group_num - 1) % len(campus_locations)
                group_to_campus[group_name] = campus_locations[campus_idx]
                group_to_campus_id[group_name] = ordered_campus_ids[campus_idx]

            # Knockout phases use first campus (main venue) when multi-campus is enabled
            knockout_location = campus_locations[0] if campus_locations else get_tournament_venue(tournament)

        current_time = tournament.start_date
        # field_slots tracks the next available start time per field (parallel scheduling)
        # Global fallback (used when no campus_configs, or for knockout stage)
        field_slots = [current_time for _ in range(parallel_fields)]

        # Per-campus field slots: each campus has its own parallel field pool
        campus_field_slots = {}   # {campus_id: [datetime, ...]}  — length = campus parallel_fields
        campus_field_index = {}   # {campus_id: int}
        if campus_configs:
            for _cid, _cfg in campus_configs.items():
                _pf = _cfg["parallel_fields"]
                campus_field_slots[_cid] = [current_time for _ in range(_pf)]
                campus_field_index[_cid] = 0

        # ============================================================================
        # PHASE 1: GROUP STAGE (ISOLATED GROUPS)
        # ============================================================================
        # ✅ NEW: Use semester.format first (admin override), fallback to tournament_type.format
        tournament_format = getattr(tournament, 'format', None) or getattr(tournament_type, 'format', 'INDIVIDUAL_RANKING')

        if tournament_format == 'HEAD_TO_HEAD':
            # ✅ HEAD_TO_HEAD: Generate round robin pairings within each group
            field_index = 0  # global fallback field index
            for group_num in range(1, groups_count + 1):
                group_name = chr(64 + group_num)  # A, B, C, D
                group_participant_ids = group_assignments.get(group_name, [])
                group_size = len(group_participant_ids)

                # Per-campus field routing (if multi-campus configured)
                _grp_campus_id = group_to_campus_id.get(group_name)
                _use_campus = _grp_campus_id is not None and _grp_campus_id in campus_field_slots

                # Calculate rounds for this group
                num_rounds = RoundRobinPairing.calculate_rounds(group_size)

                for leg in range(1, number_of_legs + 1):
                    for round_num in range(1, num_rounds + 1):
                        # Generate pairings for this round
                        round_pairings = RoundRobinPairing.get_round_pairings(group_participant_ids, round_num)

                        for match_num, (player1_id, player2_id) in enumerate(round_pairings, start=1):
                            if player1_id is None or player2_id is None:
                                continue
                            if player1_id == player2_id:             # P0-A: self-match guard
                                logger.error(
                                    "🚨 SELF-MATCH BLOCKED | tournament=%s | participant_id=%s",
                                    tournament.id, player1_id,
                                )
                                continue

                            # Even legs with home/away tracking: swap sides
                            if track_home_away and leg % 2 == 0:
                                player1_id, player2_id = player2_id, player1_id

                            if _use_campus:
                                # ✅ Per-campus field pool
                                _slots = campus_field_slots[_grp_campus_id]
                                _idx = campus_field_index[_grp_campus_id]
                                _pf = len(_slots)
                                _dur = campus_configs[_grp_campus_id]["match_duration_minutes"]
                                _brk = campus_configs[_grp_campus_id]["break_duration_minutes"]
                                session_start = _slots[_idx]
                                session_end = session_start + timedelta(minutes=_dur)
                                _slots[_idx] += timedelta(minutes=_dur + _brk)
                                campus_field_index[_grp_campus_id] = (_idx + 1) % _pf
                                active_field_num = _idx + 1
                            else:
                                # Global fallback
                                session_start = field_slots[field_index]
                                session_end = session_start + timedelta(minutes=session_duration)
                                field_slots[field_index] += timedelta(minutes=session_duration + break_minutes)
                                field_index = (field_index + 1) % parallel_fields
                                active_field_num = field_index  # already advanced

                            # ✅ MULTI-CAMPUS: Use group's assigned campus or fallback to tournament venue
                            session_location = group_to_campus.get(group_name) or get_tournament_venue(tournament)

                            leg_label = f' (Leg {leg})' if number_of_legs > 1 else ''
                            sessions.append({
                                'title': f'{tournament.name} - Group {group_name} - Round {round_num} - Match {match_num}{leg_label}',
                                'description': f'Leg {leg}, Group {group_name} head-to-head match (Field {active_field_num})',
                                'date_start': session_start,
                                'date_end': session_end,
                                'game_type': f'Group {group_name} - Round {round_num}',
                                'tournament_phase': TournamentPhase.GROUP_STAGE.value,
                                'tournament_round': round_num,
                                'tournament_match_number': match_num,
                                'leg_number': leg,
                                'location': session_location,
                                'session_type': 'on_site',
                                # ✅ HEAD_TO_HEAD: Group stage metadata
                                'ranking_mode': 'GROUP_ISOLATED',
                                'group_identifier': group_name,
                                'round_number': round_num,
                                'expected_participants': 2,
                                'participant_filter': 'group_membership',
                                'pod_tier': None,
                                # ✅ MATCH STRUCTURE: HEAD_TO_HEAD format (from tournament config)
                                'match_format': tournament.format,  # Should be HEAD_TO_HEAD
                                'scoring_type': tournament.scoring_type,
                                'structure_config': {
                                    'group': group_name,
                                    'group_size': group_size,
                                    'expected_participants': 2,
                                    'field_number': active_field_num,
                                    'leg_number': leg,
                                    'is_home_game': (leg % 2 == 1) if track_home_away else None,
                                },
                                'participant_team_ids': [player1_id, player2_id] if team_mode else None,
                                'participant_user_ids': None if team_mode else [player1_id, player2_id],
                                # ✅ Multi-campus: use group's assigned campus for pitch assignment
                                'campus_id': _grp_campus_id,
                                'pitch_id': pick_pitch(len(sessions), _grp_campus_id, parallel_fields, self.db),
                            })
        else:
            # ✅ INDIVIDUAL_RANKING: Multi-player ranking within each group
            # Each group round occupies one field; groups in the same round can run in parallel
            field_index = 0  # global fallback field index
            for group_num in range(1, groups_count + 1):
                group_name = chr(64 + group_num)  # A, B, C, D

                # ✅ CRITICAL: Get explicit participant list for this group
                group_participant_ids = group_assignments.get(group_name, [])

                # Per-campus field routing (if multi-campus configured)
                _grp_campus_id = group_to_campus_id.get(group_name)
                _use_campus = _grp_campus_id is not None and _grp_campus_id in campus_field_slots

                for round_num in range(1, group_rounds + 1):
                    if _use_campus:
                        # ✅ Per-campus field pool
                        _slots = campus_field_slots[_grp_campus_id]
                        _idx = campus_field_index[_grp_campus_id]
                        _pf = len(_slots)
                        _dur = campus_configs[_grp_campus_id]["match_duration_minutes"]
                        _brk = campus_configs[_grp_campus_id]["break_duration_minutes"]
                        session_start = _slots[_idx]
                        session_end = session_start + timedelta(minutes=_dur)
                        _slots[_idx] += timedelta(minutes=_dur + _brk)
                        campus_field_index[_grp_campus_id] = (_idx + 1) % _pf
                        active_field_num = _idx + 1
                    else:
                        # Global fallback
                        session_start = field_slots[field_index]
                        session_end = session_start + timedelta(minutes=session_duration)
                        field_slots[field_index] += timedelta(minutes=session_duration + break_minutes)
                        field_index = (field_index + 1) % parallel_fields
                        active_field_num = field_index  # already advanced

                    # ✅ MULTI-CAMPUS: Use group's assigned campus or fallback
                    session_location = group_to_campus.get(group_name) or get_tournament_venue(tournament)

                    sessions.append({
                        'title': f'{tournament.name} - Group {group_name} - Round {round_num}',
                        'description': f'Group {group_name} ranking round ({len(group_participant_ids)} players)',
                        'date_start': session_start,
                        'date_end': session_end,
                        'game_type': f'Group {group_name} - Round {round_num}',
                        'tournament_phase': TournamentPhase.GROUP_STAGE.value,
                        'tournament_round': round_num,
                        'tournament_match_number': (group_num - 1) * group_rounds + round_num,
                        'location': session_location,
                        'session_type': 'on_site',
                        # ✅ UNIFIED RANKING: Group isolation metadata
                        'ranking_mode': 'GROUP_ISOLATED',
                        'group_identifier': group_name,
                        'round_number': round_num,  # ✅ MANDATORY: Round number for fixtures display
                        'expected_participants': len(group_participant_ids),
                        'participant_filter': 'group_membership',
                        'pod_tier': None,
                        # ✅ MATCH STRUCTURE: Format and scoring metadata (from tournament config)
                        'match_format': tournament.format,  # Should be INDIVIDUAL_RANKING for group stage
                        'scoring_type': tournament.scoring_type,
                        'structure_config': {
                            'group': group_name,
                            'group_size': len(group_participant_ids),
                            'expected_participants': len(group_participant_ids),
                            'field_number': active_field_num
                        },
                        'participant_team_ids': group_participant_ids if team_mode else None,
                        'participant_user_ids': None if team_mode else group_participant_ids,
                        # ✅ Multi-campus: use group's assigned campus for pitch assignment
                        'campus_id': _grp_campus_id,
                        'pitch_id': pick_pitch(len(sessions), _grp_campus_id, parallel_fields, self.db),
                    })

        # Break between phases: advance current_time to max of ALL field slots + inter-phase break
        all_last_times = list(field_slots)
        for _slots in campus_field_slots.values():
            all_last_times.extend(_slots)
        current_time = max(all_last_times) + timedelta(minutes=break_minutes * 4)

        # ============================================================================
        # PHASE 2: KNOCKOUT STAGE (TOP QUALIFIERS ONLY) - WITH BYE LOGIC
        # ============================================================================
        knockout_players = groups_count * qualifiers_per_group + _best_runner_up_count
        round_names = tournament_type.config.get('round_names', {})

        # ✅ NEW: Calculate knockout structure (byes, play-in, bronze)
        structure = KnockoutBracket.calculate_structure(knockout_players)
        bracket_size = structure['bracket_size']
        play_in_matches = structure['play_in_matches']
        has_bronze = structure['has_bronze']

        # ============================================================================
        # PHASE 2A: PLAY-IN ROUND (if needed)
        # ============================================================================
        if play_in_matches > 0:
            # Seeds (byes + 1) onwards compete in play-in
            for match_num in range(1, play_in_matches + 1):
                seed_high = structure['byes'] + match_num
                seed_low = knockout_players - (match_num - 1)

                session_start = current_time
                session_end = session_start + timedelta(minutes=session_duration)

                sessions.append({
                    'title': f'{tournament.name} - Play-in - Match {match_num}',
                    'description': f'Play-in match: Seed {seed_high} vs Seed {seed_low}',
                    'date_start': session_start,
                    'date_end': session_end,
                    'game_type': 'Play-in Round',
                    'tournament_phase': TournamentPhase.KNOCKOUT.value,
                    'tournament_round': 0,  # Play-in is round 0
                    'tournament_match_number': match_num,
                    'location': knockout_location,
                    'session_type': 'on_site',
                    # ✅ UNIFIED RANKING: Play-in metadata
                    'ranking_mode': 'QUALIFIED_ONLY',
                    'round_number': 0,  # ✅ MANDATORY: Round number for fixtures display (play-in = round 0)
                    'expected_participants': 2,  # Head-to-head
                    'participant_filter': 'seeded_qualifiers',
                    'group_identifier': None,
                    'pod_tier': None,
                    # ✅ MATCH STRUCTURE: Format and scoring metadata (play-in is always HEAD_TO_HEAD)
                    'match_format': 'HEAD_TO_HEAD',  # 1v1 elimination
                    'scoring_type': tournament.scoring_type,
                    'structure_config': {
                        'expected_participants': 2,
                        'round_name': 'Play-in Round',
                        'seed_high': seed_high,
                        'seed_low': seed_low,
                        'qualified_count': knockout_players
                    },
                    # ⚠️ participant_user_ids = NULL until group stage completes
                    'participant_user_ids': None
                })

                # ✅ SEQUENTIAL SCHEDULING: All matches happen one after another (one-day tournament)
                current_time += timedelta(minutes=session_duration + break_minutes)

            # Break after play-in round
            current_time += timedelta(minutes=break_minutes * 2)

        # ============================================================================
        # PHASE 2B: MAIN KNOCKOUT BRACKET
        # ============================================================================
        knockout_rounds = math.ceil(math.log2(bracket_size))

        # ✅ Calculate seeding placeholders for first round (based on group results)
        # For 2 groups (A, B): A1 vs B2, B1 vs A2
        # For 3 groups (A, B, C): A1 vs C2, B1 vs A2, C1 vs B2
        # etc.
        group_letters = [chr(65 + i) for i in range(groups_count)]  # ['A', 'B', 'C', ...]

        for round_num in range(1, knockout_rounds + 1):
            players_in_round = bracket_size // (2 ** (round_num - 1))
            matches_in_round = players_in_round // 2

            round_name = round_names.get(str(players_in_round), f"Round of {players_in_round}")

            for match_in_round in range(1, matches_in_round + 1):
                session_start = current_time
                session_end = session_start + timedelta(minutes=session_duration)

                # ── Seeding / qualification source metadata ──────────────────
                # Round 1: slot labels written at generation time; resolved to
                #   concrete participant_user_ids at ranking-calculation time
                #   by assign_semifinal_participants() in the qualification service.
                # Round > 1: source-label only; participants assigned when
                #   the preceding round's results are entered (out of scope v1).
                seeding_info = {}
                if round_num == 1:
                    if knockout_players == 4 and _best_runner_up_count > 0:
                        # 3 groups of 3: 3 winners + 1 best runner-up
                        if match_in_round == 1:
                            seeding_info = {
                                'matchup': 'Group A winner vs Best runner-up',
                                'seed_1': 'A1', 'seed_2': 'BR',
                            }
                        elif match_in_round == 2:
                            seeding_info = {
                                'matchup': 'Group B winner vs Group C winner',
                                'seed_1': 'B1', 'seed_2': 'C1',
                            }
                    elif knockout_players == 4:
                        # Standard 2-group case: A1 vs B2, B1 vs A2
                        if match_in_round == 1:
                            seeding_info = {'matchup': 'A1 vs B2', 'seed_1': 'A1', 'seed_2': 'B2'}
                        elif match_in_round == 2:
                            seeding_info = {'matchup': 'B1 vs A2', 'seed_1': 'B1', 'seed_2': 'A2'}
                    elif knockout_players == 8:
                        # 4-group case
                        matchups = [
                            ('A1', 'D2'), ('B1', 'C2'), ('C1', 'B2'), ('D1', 'A2')
                        ]
                        if match_in_round <= len(matchups):
                            seed_1, seed_2 = matchups[match_in_round - 1]
                            seeding_info = {'matchup': f'{seed_1} vs {seed_2}', 'seed_1': seed_1, 'seed_2': seed_2}
                elif round_num == knockout_rounds:
                    # Final round
                    seeding_info = {'matchup': 'SF1 winner vs SF2 winner'}
                else:
                    seeding_info = {'matchup': f'Round {round_num - 1} winners'}

                sessions.append({
                    'title': f'{tournament.name} - {round_name} - Match {match_in_round}',
                    'description': f'Knockout stage match - top {knockout_players} qualifiers',
                    'date_start': session_start,
                    'date_end': session_end,
                    'game_type': round_name,
                    'tournament_phase': TournamentPhase.KNOCKOUT.value,
                    'tournament_round': round_num,
                    'tournament_match_number': match_in_round,
                    'location': knockout_location,
                    'session_type': 'on_site',
                    # ✅ UNIFIED RANKING: Knockout stage metadata
                    'ranking_mode': 'QUALIFIED_ONLY',
                    'round_number': round_num,  # ✅ MANDATORY: Round number for fixtures display
                    'expected_participants': 2 if round_num >= knockout_rounds - 1 else players_in_round,
                    'participant_filter': 'seeded_qualifiers',
                    'group_identifier': None,
                    'pod_tier': None,
                    # ✅ MATCH STRUCTURE: Format and scoring metadata (business logic: finals use HEAD_TO_HEAD)
                    'match_format': 'HEAD_TO_HEAD' if round_num >= knockout_rounds - 1 else 'INDIVIDUAL_RANKING',
                    'scoring_type': tournament.scoring_type,
                    'structure_config': {
                        'expected_participants': players_in_round,
                        'round_name': round_name,
                        'qualified_count': knockout_players,
                        **seeding_info  # ✅ Add seeding placeholders (A1 vs B2, etc.)
                    },
                    # ⚠️ participant_user_ids = NULL until previous round completes
                    'participant_user_ids': None
                })

                # ✅ SEQUENTIAL SCHEDULING: All matches happen one after another (one-day tournament)
                # Each match gets: session_duration + break_minutes
                current_time += timedelta(minutes=session_duration + break_minutes)

            # Break between rounds
            current_time += timedelta(minutes=break_minutes * 2)

        # ============================================================================
        # PHASE 2C: BRONZE MATCH (3rd place playoff)
        # ============================================================================
        # ✅ Decision: Bronze match ONLY for 8+ knockout brackets
        if has_bronze:
            session_start = current_time
            session_end = session_start + timedelta(minutes=session_duration)

            sessions.append({
                'title': f'{tournament.name} - 3rd Place Match',
                'description': '3rd place playoff (bronze medal match)',
                'date_start': session_start,
                'date_end': session_end,
                'game_type': '3rd Place Match',
                'tournament_phase': TournamentPhase.KNOCKOUT.value,
                'tournament_round': knockout_rounds + 1,  # After final
                'tournament_match_number': 1,
                'location': knockout_location,
                'session_type': 'on_site',
                # ✅ UNIFIED RANKING: Bronze match metadata
                'ranking_mode': 'QUALIFIED_ONLY',
                'round_number': knockout_rounds + 1,  # ✅ MANDATORY: Round number for fixtures display (bronze = after final)
                'expected_participants': 2,
                'participant_filter': 'semifinal_losers',
                'group_identifier': None,
                'pod_tier': None,
                # ✅ MATCH STRUCTURE: Format and scoring metadata (bronze is always HEAD_TO_HEAD)
                'match_format': 'HEAD_TO_HEAD',
                'scoring_type': tournament.scoring_type,
                'structure_config': {
                    'expected_participants': 2,
                    'round_name': '3rd Place Match',
                    'matchup': 'SF1 loser vs SF2 loser',
                    'qualified_count': knockout_players,
                },
                # ⚠️ participant_user_ids = NULL until semifinal completes
                'participant_user_ids': None
            })

        return sessions
