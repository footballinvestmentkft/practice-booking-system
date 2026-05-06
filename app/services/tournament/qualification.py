"""
Qualification Service — Group-to-Knockout participant resolution.

Called at ranking-calculation time, after all group stage results are
recorded.  NOT called at generation time.

Public API
----------
compute_group_standings(db, tournament_id)
    Per-group standings from completed GROUP_STAGE sessions.

compute_best_runner_up(db, tournament_id, count, standings=None)
    Cross-group runner-up selection with documented tiebreaker chain.

assign_semifinal_participants(db, tournament_id, best_runner_up_count)
    Writes participant_user_ids to KNOCKOUT round-1 sessions only.
    Final and 3rd Place sessions are never modified.
    Does NOT commit — caller owns the transaction.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy.orm import Session as DBSession

from app.models.session import Session as SessionModel
from app.utils.game_results import parse_game_results

logger = logging.getLogger(__name__)

_GROUP_STAGE = "GROUP_STAGE"
_KNOCKOUT = "KNOCKOUT"


# ── Public API ─────────────────────────────────────────────────────────────────

def compute_group_standings(
    db: DBSession,
    tournament_id: int,
) -> dict[str, list[dict]]:
    """
    Compute per-group standings from completed GROUP_STAGE sessions.

    Only sessions with non-null game_results contribute.  Sessions that
    have no results yet are silently skipped — the caller is responsible
    for validating that all group sessions are complete before invoking
    assign_semifinal_participants().

    Returns
    -------
    {
      "A": [
        {"user_id": int, "points": int, "gf": float, "ga": float,
         "wins": int, "draws": int, "losses": int, "rank": int},
        ...  # ascending rank within the group
      ],
      "B": [...],
    }
    """
    group_sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.tournament_phase == _GROUP_STAGE,
            SessionModel.game_results.isnot(None),
        )
        .all()
    )

    # {group_id: {user_id: mutable stats dict}}
    raw: dict[str, dict[int, dict]] = defaultdict(
        lambda: defaultdict(lambda: {
            "wins": 0, "draws": 0, "losses": 0,
            "gf": 0.0, "ga": 0.0, "points": 0,
        })
    )

    for session in group_sessions:
        match_data = parse_game_results(session.game_results)
        if match_data.get("match_format") != "HEAD_TO_HEAD":
            continue
        participants = match_data.get("participants") or []
        if len(participants) != 2:
            continue
        group_id = session.group_identifier or "A"

        for p in participants:
            uid = p.get("user_id")
            if uid is None:
                continue
            result = p.get("result", "loss")
            score = float(p.get("score", 0))
            opp = next((q for q in participants if q.get("user_id") != uid), {})
            opp_score = float(opp.get("score", 0))

            raw[group_id][uid]["gf"] += score
            raw[group_id][uid]["ga"] += opp_score
            if result == "win":
                raw[group_id][uid]["wins"] += 1
                raw[group_id][uid]["points"] += 3
            elif result == "draw":
                raw[group_id][uid]["draws"] += 1
                raw[group_id][uid]["points"] += 1
            else:
                raw[group_id][uid]["losses"] += 1

    standings: dict[str, list[dict]] = {}
    for group_id, user_stats in raw.items():
        rows = [
            {
                "user_id": uid,
                "points": s["points"],
                "gf": s["gf"],
                "ga": s["ga"],
                "wins": s["wins"],
                "draws": s["draws"],
                "losses": s["losses"],
            }
            for uid, s in user_stats.items()
        ]
        # Tiebreaker chain documented in compute_best_runner_up(); same key used here
        # for consistency within-group as well.
        rows.sort(key=lambda r: (-r["points"], -(r["gf"] - r["ga"]), -r["gf"], r["user_id"]))
        for idx, row in enumerate(rows):
            row["rank"] = idx + 1
        standings[group_id] = rows

    return standings


def compute_best_runner_up(
    db: DBSession,
    tournament_id: int,
    count: int,
    standings: dict[str, list[dict]] | None = None,
) -> list[int]:
    """
    Return user_ids of the `count` best runner-ups (rank-2 from each group),
    ranked across groups.

    Tiebreaker chain
    ----------------
    1. Points          — sport metric (W=3, D=1, L=0)
    2. Goal difference — sport metric (GF − GA)
    3. Goals for       — sport metric
    4. user_id ASC     — deterministic audit fallback; not a sport tiebreaker.
                         Guarantees a strict total ordering when all sport
                         criteria are equal, making results reproducible and
                         auditable without introducing randomness.

    Parameters
    ----------
    standings:
        Pre-computed output of compute_group_standings(); pass to avoid a
        redundant DB query when called after compute_group_standings().
    """
    if standings is None:
        standings = compute_group_standings(db, tournament_id)

    runners_up: list[dict] = []
    for _group_id, rows in standings.items():
        rank2 = [r for r in rows if r["rank"] == 2]
        if rank2:
            runners_up.append(rank2[0])

    runners_up.sort(
        key=lambda r: (-r["points"], -(r["gf"] - r["ga"]), -r["gf"], r["user_id"])
    )
    return [r["user_id"] for r in runners_up[:count]]


def assign_semifinal_participants(
    db: DBSession,
    tournament_id: int,
    best_runner_up_count: int,
) -> None:
    """
    Resolve and write participant_user_ids for KNOCKOUT round-1 sessions
    (semi-finals) after all group stage results are recorded.

    Invariants (enforced internally)
    ---------------------------------
    - Only sessions with tournament_round == 1 are modified.
    - Final and 3rd Place sessions (tournament_round > 1) are never touched.
    - Does NOT write [None, ...] — sessions with unresolvable seed labels
      are logged and skipped rather than receiving a partial participant list.
    - Idempotent: repeated calls overwrite with the same values.

    Precondition (caller must enforce)
    ------------------------------------
    All GROUP_STAGE sessions for this tournament must have non-null
    game_results before this function is called.  Partial group results
    will produce incorrect winner/runner-up resolution.

    Transaction
    -----------
    db.flush() is called but NOT db.commit().  The caller (endpoint) owns
    the surrounding transaction.
    """
    standings = compute_group_standings(db, tournament_id)

    if not standings:
        logger.warning(
            "assign_semifinal_participants: no group standings found for "
            "tournament %d — skipping assignment",
            tournament_id,
        )
        return

    # Group winners: rank-1 player per group letter
    group_winners: dict[str, int] = {}
    for group_id, rows in standings.items():
        rank1 = [r for r in rows if r["rank"] == 1]
        if rank1:
            group_winners[group_id] = rank1[0]["user_id"]

    # Best runner-up(s) — reuse already-computed standings
    best_runners_up = compute_best_runner_up(
        db, tournament_id, best_runner_up_count, standings=standings
    )

    # Build slot-label → user_id map
    # Slot labels match those written to structure_config at generation time:
    # "A1", "B1", "C1" → group winners; "BR" → best runner-up
    slot_map: dict[str, int] = {}
    for letter, uid in group_winners.items():
        slot_map[f"{letter}1"] = uid
    if best_runners_up:
        slot_map["BR"] = best_runners_up[0]

    if not slot_map:
        logger.warning(
            "assign_semifinal_participants: slot_map is empty for tournament %d, "
            "skipping",
            tournament_id,
        )
        return

    # Load semi-final sessions only (tournament_round == 1, KNOCKOUT phase)
    sf_sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.tournament_phase == _KNOCKOUT,
            SessionModel.tournament_round == 1,
        )
        .order_by(SessionModel.tournament_match_number)
        .all()
    )

    for session in sf_sessions:
        sc = session.structure_config or {}
        seed_1 = sc.get("seed_1")
        seed_2 = sc.get("seed_2")
        p1 = slot_map.get(seed_1) if seed_1 else None
        p2 = slot_map.get(seed_2) if seed_2 else None

        if p1 is None or p2 is None:
            logger.warning(
                "assign_semifinal_participants: session %d has unresolvable "
                "seeds (seed_1=%r → %r, seed_2=%r → %r); skipping to avoid "
                "writing partial participant list",
                session.id, seed_1, p1, seed_2, p2,
            )
            continue

        session.participant_user_ids = [p1, p2]
        logger.info(
            "assign_semifinal_participants: session %d → [%d, %d] "
            "(seed_1=%s, seed_2=%s)",
            session.id, p1, p2, seed_1, seed_2,
        )

    db.flush()
