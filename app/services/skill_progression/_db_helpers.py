"""
DB-backed helper layer for skill progression.

Computes ELO-inspired opponent strength and match-level performance modifiers
from live DB state (TournamentParticipation, UserLicense, Session).

No EMA formula logic, no view building.  At most 2 queries per helper
(1 bulk + N individual lookups for _compute_opponent_factor;
 1 query for _compute_match_performance_modifier).

Extracted from skill_progression_service.py (Layer 3).
"""
import math

from sqlalchemy.orm import Session

from app.models.license import UserLicense
from app.models.tournament_achievement import TournamentParticipation
from ._formulas import DEFAULT_BASELINE


def _compute_opponent_factor(
    db: Session,
    tournament_id: int,
    player_user_id: int,
    player_baseline_avg: float,
) -> float:
    """
    Compute ELO-inspired opponent strength factor for one tournament.

    Returns avg_opponent_baseline / player_baseline_avg, clamped to [0.5, 2.0].
    Uses onboarding baselines (football_skills JSON) to avoid any circular
    dependency with the running skill values being computed.

    A value > 1.0 means the field was stronger than the player → bigger reward
    for winning, smaller penalty for losing.  A value < 1.0 means weaker field.
    """
    # All participants in this tournament except the focal player
    opponents = (
        db.query(TournamentParticipation)
        .filter(
            TournamentParticipation.semester_id == tournament_id,
            TournamentParticipation.user_id != player_user_id,
        )
        .all()
    )

    if not opponents:
        return 1.0  # Solo tournament → no adjustment

    baseline_avgs = []
    for opp in opponents:
        # Load the opponent's active football-player license
        lic = db.query(UserLicense).filter(
            UserLicense.user_id == opp.user_id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            UserLicense.is_active == True,
        ).first()

        if not lic or not isinstance(lic.football_skills, dict):
            continue

        # Average the numeric values from football_skills
        vals = []
        for v in lic.football_skills.values():
            if isinstance(v, dict):
                raw = v.get("baseline", DEFAULT_BASELINE)
            else:
                raw = v
            try:
                vals.append(float(raw))
            except (TypeError, ValueError):
                pass

        if vals:
            baseline_avgs.append(sum(vals) / len(vals))

    if not baseline_avgs:
        return 1.0  # Could not resolve any opponent baseline → neutral

    avg_opponent = sum(baseline_avgs) / len(baseline_avgs)

    # Guard against division by zero
    if player_baseline_avg <= 0:
        return 1.0

    raw_factor = avg_opponent / player_baseline_avg
    return round(max(0.5, min(2.0, raw_factor)), 4)


def _compute_match_performance_modifier(
    db: Session,
    tournament_id: int,
    user_id: int,
) -> float:
    """
    Compute match-level performance modifier for the V3 EMA formula.

    Returns a delta-scale factor in [-1.0, +1.0] used as a multiplicative
    modifier on the EMA raw_delta (not a target shift).

    Formula:
      win_rate_signal = (wins/total - 0.5) × 2       range [-1, +1]
      score_signal    = (gf-ga)/(gf+ga)               range [-1, +1]  (0 if no scores)
      raw_signal      = 0.7 × win_rate_signal + 0.3 × score_signal
      confidence      = 1 - exp(-n/5)                 dampens small samples
      modifier        = raw_signal × confidence        range [-1, +1]

    Confidence behaviour:
      n=1  → 0.18  (minimal weight — 1-match tournament barely shifts delta)
      n=5  → 0.63
      n=10 → 0.86
      n=∞  → 1.00

    0.0 returned if no match data is available.
    For INDIVIDUAL_RANKING tournaments (no score data), score_signal=0 naturally.
    """
    import json as _json
    from app.models.session import Session as SessionModel, EventCategory

    sessions = db.query(SessionModel).filter(
        SessionModel.semester_id == tournament_id,
        SessionModel.event_category == EventCategory.MATCH,
        SessionModel.game_results.isnot(None),
    ).all()

    wins = losses = draws = 0
    goals_for = goals_against = 0.0

    for sess in sessions:
        if user_id not in (sess.participant_user_ids or []):
            continue
        raw = sess.game_results
        results = _json.loads(raw) if isinstance(raw, str) else raw
        if not results:
            continue
        participants = results.get("participants") or []
        for p in participants:
            if p.get("user_id") == user_id:
                r = str(p.get("result", "")).upper()
                if r == "WIN":
                    wins += 1
                elif r == "LOSS":
                    losses += 1
                else:
                    draws += 1
                goals_for += float(p.get("score") or 0)
            else:
                goals_against += float(p.get("score") or 0)

    total_matches = wins + losses + draws
    if total_matches == 0:
        return 0.0

    win_rate_signal = ((wins / total_matches) - 0.5) * 2.0   # [-1, +1]

    total_goals = goals_for + goals_against
    score_signal = (
        (goals_for - goals_against) / total_goals
        if total_goals > 0 else 0.0
    )                                                          # [-1, +1]

    raw_signal = 0.7 * win_rate_signal + 0.3 * score_signal
    confidence = 1.0 - math.exp(-total_matches / 5.0)
    modifier = raw_signal * confidence

    return round(max(-1.0, min(1.0, modifier)), 4)
