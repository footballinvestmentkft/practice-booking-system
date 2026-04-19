"""Session query helpers shared across simulation modules."""
import json as _json
import logging as _logging


def _get_tournament_sessions(
    db,
    tournament_id: int,
    ordered: bool = False,
    with_phase: bool = False,
):
    """Fetch all MATCH-category sessions for a tournament.

    Consolidates the repeated:
        db.query(SessionModel).filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.event_category == EventCategory.MATCH,
        ).order_by(...).all()
    pattern used across every simulation function.

    Args:
        db:            SQLAlchemy session.
        tournament_id: Semester / tournament primary key.
        ordered:       Sort by (tournament_round ASC, tournament_match_number ASC).
        with_phase:    Sort by (tournament_phase, round ASC, match_number ASC).
                       Takes precedence over ``ordered``.

    Returns:
        List of SessionModel instances.
    """
    from app.models.session import Session as _SM, EventCategory as _EC
    from sqlalchemy import asc as _asc
    q = db.query(_SM).filter(
        _SM.semester_id == tournament_id,
        _SM.event_category == _EC.MATCH,
    )
    if with_phase:
        q = q.order_by(_SM.tournament_phase, _asc(_SM.tournament_round), _asc(_SM.tournament_match_number))
    elif ordered:
        q = q.order_by(_asc(_SM.tournament_round), _asc(_SM.tournament_match_number))
    return q.all()


def _build_h2h_game_results(
    participants: list,
    round_number: int,
) -> str:
    """Serialise a HEAD_TO_HEAD game_results dict to JSON.

    Consolidates the repeated:
        {"match_format": "HEAD_TO_HEAD", "round_number": ..., "participants": [...]}
    pattern used in every simulation function.

    Args:
        participants:  List of participant dicts, each with keys
                       ``user_id``, ``result`` ("win"/"loss"), ``score`` (int).
        round_number:  Tournament round (used by ranking strategies for bracket ordering).

    Returns:
        JSON string ready to assign to ``session.game_results``.
    """
    return _json.dumps({
        "match_format": "HEAD_TO_HEAD",
        "round_number": round_number,
        "participants": participants,
    })
