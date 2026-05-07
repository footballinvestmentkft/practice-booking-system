"""
Live Model Service — PR Live-2
==============================

Builds a format-aware snapshot dict for the live monitoring dashboard.

Entry point: ``build_live_model(db, tournament, instructor_roster=None) -> dict``

Supports:
  - group_knockout  (full: group standings + KO bracket)
  - knockout        (partial: KO bracket only)
  - league          (minimal: placeholder)

Group standings tiebreaker (no head-to-head in this PR):
  Pts DESC → GD DESC → GF DESC → user_id ASC
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_GK_CONFIG_PATH = Path(__file__).parents[2] / "tournament_types" / "group_knockout.json"
_gk_config_cache: Optional[dict] = None


def _load_gk_config() -> dict:
    global _gk_config_cache
    if _gk_config_cache is None:
        _gk_config_cache = json.loads(_GK_CONFIG_PATH.read_text(encoding="utf-8"))
    return _gk_config_cache


# ── Public entry point ────────────────────────────────────────────────────────

def build_live_model(db: Session, tournament: Any, instructor_roster: Optional[list] = None) -> dict:
    """
    Return a format-aware live model dict for the given tournament.

    The returned dict is directly serialisable to JSON and is consumed by:
    - tournament_live.html (Jinja2 template)
    - /admin/tournaments/{id}/live-snapshot (REST endpoint)
    """
    from app.models.session import Session as SessionModel
    from app.models.tournament_enums import TournamentPhase
    from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus

    tournament_id = tournament.id

    # ── sessions ─────────────────────────────────────────────────────────────
    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == tournament_id)
        .order_by(SessionModel.tournament_match_number)
        .all()
    )

    total = len(sessions)
    completed = sum(1 for s in sessions if (s.session_status or "").lower() == "completed")

    # ── enrollment / check-in counts ─────────────────────────────────────────
    enrollments = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.semester_id == tournament_id,
            SemesterEnrollment.is_active == True,
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
        )
        .all()
    )
    enrollment_count = len(enrollments)
    checkin_count = sum(1 for e in enrollments if e.tournament_checked_in_at is not None)

    # ── user lookup (all participants across sessions) ────────────────────────
    user_ids: set[int] = set()
    for s in sessions:
        if s.participant_user_ids:
            user_ids.update(s.participant_user_ids)
    users = _load_users(db, user_ids)

    # ── sponsor context ───────────────────────────────────────────────────────
    sponsor_context = _build_sponsor_context(tournament, enrollment_count, checkin_count)

    # ── summary ──────────────────────────────────────────────────────────────
    summary = {
        "tournament_id": tournament_id,
        "tournament_name": tournament.name,
        "tournament_status": tournament.tournament_status,
        "total_sessions": total,
        "completed_sessions": completed,
        "progress_pct": round(completed / total, 4) if total > 0 else 0.0,
        "enrollment_count": enrollment_count,
        "checkin_count": checkin_count,
    }

    # ── format-specific sections ──────────────────────────────────────────────
    format_type = _detect_format(tournament, sessions)

    group_stage = None
    knockout_bracket = None
    league_rounds = None

    if format_type == "group_knockout":
        group_sessions = [s for s in sessions if s.tournament_phase == TournamentPhase.GROUP_STAGE]
        ko_sessions = [s for s in sessions if s.tournament_phase == TournamentPhase.KNOCKOUT]
        gk_config = _load_gk_config()
        group_stage = _build_group_stage(group_sessions, ko_sessions, users, gk_config, enrollment_count)
        knockout_bracket = _build_knockout_bracket(ko_sessions, users)

    elif format_type == "knockout":
        ko_sessions = sessions
        knockout_bracket = _build_knockout_bracket(ko_sessions, users)

    elif format_type == "league":
        league_rounds = _build_league_placeholder(sessions)

    return {
        "format_type": format_type,
        "tournament_status": tournament.tournament_status,
        "summary": summary,
        "instructor_roster": instructor_roster or [],
        "sponsor_context": sponsor_context,
        "group_stage": group_stage,
        "knockout_bracket": knockout_bracket,
        "league_rounds": league_rounds,
        "league_standings": None,
    }


# ── Format detection ──────────────────────────────────────────────────────────

def _detect_format(tournament: Any, sessions: list) -> str:
    """Derive format string from tournament type code or session phases."""
    from app.models.tournament_enums import TournamentPhase

    try:
        code = (
            tournament.tournament_config_obj
            and tournament.tournament_config_obj.tournament_type
            and tournament.tournament_config_obj.tournament_type.code
        )
        if code == "group_knockout":
            return "group_knockout"
        if code in ("single_elimination", "double_elimination", "knockout"):
            return "knockout"
        if code in ("league", "round_robin"):
            return "league"
    except Exception:
        pass

    # Fallback: detect from session phases present
    phases = {s.tournament_phase for s in sessions if s.tournament_phase}
    if TournamentPhase.GROUP_STAGE in phases and TournamentPhase.KNOCKOUT in phases:
        return "group_knockout"
    if TournamentPhase.KNOCKOUT in phases:
        return "knockout"
    return "league"


# ── Sponsor context ───────────────────────────────────────────────────────────

def _build_sponsor_context(tournament: Any, enrollment_count: int, checkin_count: int) -> Optional[dict]:
    try:
        sponsor = getattr(tournament, "organizer_sponsor", None)
        campaign = getattr(tournament, "organizer_campaign", None)
        if sponsor is None and campaign is None:
            return None
        return {
            "sponsor_name": sponsor.name if sponsor else None,
            "campaign_name": campaign.name if campaign else None,
            "enrollment_count": enrollment_count,
            "checkin_count": checkin_count,
        }
    except Exception:
        return None


# ── User loader ───────────────────────────────────────────────────────────────

def _load_users(db: Session, user_ids: set[int]) -> dict[int, Any]:
    if not user_ids:
        return {}
    from app.models.user import User
    rows = db.query(User).filter(User.id.in_(user_ids)).all()
    return {u.id: u for u in rows}


# ── Group stage builder ───────────────────────────────────────────────────────

def _build_group_stage(
    group_sessions: list,
    ko_sessions: list,
    users: dict,
    gk_config: dict,
    enrollment_count: int,
) -> dict:
    # Bucket sessions by group_identifier
    groups_raw: dict[str, list] = {}
    for s in group_sessions:
        key = s.group_identifier or "?"
        groups_raw.setdefault(key, []).append(s)

    player_count_key = f"{enrollment_count}_players"
    group_conf = gk_config.get("group_configuration", {}).get(player_count_key, {})
    qualifiers_per_group: int = group_conf.get("qualifiers", 2)
    qualification_policy: Optional[str] = group_conf.get("qualification_policy")
    best_runner_up_count: int = group_conf.get("best_runner_up_count", 0)

    groups: dict[str, dict] = {}
    all_runner_ups: list[dict] = []

    for gid in sorted(groups_raw.keys()):
        g_sessions = groups_raw[gid]
        standings = _compute_group_standings(g_sessions, users)
        sessions_out = [_build_live_session_row(s, users) for s in g_sessions]

        group_complete = all(
            (s.session_status or "").lower() == "completed" for s in g_sessions
        )

        # qualification_state is only set when the group is mathematically closed.
        # While group is in progress, every row stays None — no misleading badge.
        for i, row in enumerate(standings):
            row["rank"] = i + 1
            if group_complete and i < qualifiers_per_group:
                row["qualification_state"] = "qualified"
            else:
                row["qualification_state"] = None

        # Collect runner-up candidates only from completed groups.
        if (group_complete
                and qualification_policy == "winners_plus_best_runner_up"
                and len(standings) > qualifiers_per_group):
            runner_up = standings[qualifiers_per_group]
            all_runner_ups.append({"group": gid, **runner_up})

        groups[gid] = {
            "group_id": gid,
            "sessions": sessions_out,
            "standings": standings,
            "complete": group_complete,
        }

    # group_stage_complete must be evaluated AFTER the loop so all groups are present.
    group_stage_complete = all(g["complete"] for g in groups.values()) if groups else False

    # Best runner-up resolution: only when every group has finished.
    # Cross-group comparison on a partial dataset would produce meaningless results.
    if (group_stage_complete
            and qualification_policy == "winners_plus_best_runner_up"
            and all_runner_ups):
        best_runner_ups = _pick_best_runner_ups(all_runner_ups, best_runner_up_count)
        best_ids = {r["user_id"] for r in best_runner_ups}
        for gid, gdata in groups.items():
            for row in gdata["standings"]:
                if row["qualification_state"] is None and row["user_id"] in best_ids:
                    row["qualification_state"] = "best_runner_up"

    return {
        "complete": group_stage_complete,
        "groups": groups,
        "qualifiers_per_group": qualifiers_per_group,
        "qualification_policy": qualification_policy,
        "best_runner_up_count": best_runner_up_count,
    }


def _compute_group_standings(sessions: list, users: dict) -> list[dict]:
    """
    Compute group standings from completed sessions.

    Tiebreaker order: Pts DESC → GD DESC → GF DESC → user_id ASC
    Head-to-head tiebreaker is explicitly deferred.
    """
    stats: dict[int, dict] = {}

    def _ensure(uid: int) -> dict:
        if uid not in stats:
            user = users.get(uid)
            name = f"{user.first_name} {user.last_name}" if user else f"Player #{uid}"
            stats[uid] = {"user_id": uid, "name": name, "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
        return stats[uid]

    for s in sessions:
        if (s.session_status or "").lower() != "completed":
            continue
        results = s.game_results
        if not results or not isinstance(results, dict):
            continue
        participants = results.get("participants", [])
        for p in participants:
            uid = p.get("user_id")
            score = p.get("score", 0) or 0
            result = p.get("result", "")
            row = _ensure(uid)
            row["p"] += 1
            row["gf"] += score
            if result == "win":
                row["w"] += 1
                row["pts"] += 3
            elif result == "tie":
                row["d"] += 1
                row["pts"] += 1
            else:
                row["l"] += 1

        # GA = opponent's GF
        for p in participants:
            uid = p.get("user_id")
            my_gf = p.get("score", 0) or 0
            total_gf = sum(q.get("score", 0) or 0 for q in participants)
            opponents_gf = total_gf - my_gf
            _ensure(uid)["ga"] += opponents_gf

    # Make sure all participants from non-completed sessions are present
    for s in sessions:
        if s.participant_user_ids:
            for uid in s.participant_user_ids:
                _ensure(uid)

    rows = list(stats.values())
    for r in rows:
        r["gd"] = r["gf"] - r["ga"]

    rows.sort(key=lambda r: (-r["pts"], -r["gd"], -r["gf"], r["user_id"]))
    return rows


def _pick_best_runner_ups(runner_ups: list[dict], count: int) -> list[dict]:
    """Pick the top `count` runner-ups by the same tiebreaker as group standings."""
    runner_ups.sort(key=lambda r: (-r["pts"], -r["gd"], -r["gf"], r["user_id"]))
    return runner_ups[:count]


# ── KO bracket builder ────────────────────────────────────────────────────────

def _build_knockout_bracket(ko_sessions: list, users: dict) -> dict:
    """
    Build KO bracket grouped by game_type (round name).

    Round display order: Semi-finals → Final → 3rd Place Match
    """
    # Bucket by game_type
    rounds_raw: dict[str, list] = {}
    for s in ko_sessions:
        key = s.game_type or "Unknown"
        rounds_raw.setdefault(key, []).append(s)

    _ROUND_ORDER = ["Semi-finals", "Quarter-finals", "Round of 16", "Round of 32", "Final", "3rd Place Match"]

    def _sort_key(game_type: str) -> int:
        try:
            return _ROUND_ORDER.index(game_type)
        except ValueError:
            return 99

    rounds: dict[str, list] = {}
    for game_type in sorted(rounds_raw.keys(), key=_sort_key):
        rounds[game_type] = [_build_ko_match_row(s, users) for s in sorted(rounds_raw[game_type], key=lambda x: x.tournament_match_number or 0)]

    bracket_resolved = all(
        (s.session_status or "").lower() == "completed"
        for s in ko_sessions
    ) if ko_sessions else False

    return {
        "rounds": rounds,
        "bracket_resolved": bracket_resolved,
    }


def _build_ko_match_row(session: Any, users: dict) -> dict:
    status = (session.session_status or "").lower()
    matchup_label = _get_matchup_label(session, users)
    result_label = _get_result_label(session, users) if status == "completed" else None

    return {
        "session_id": session.id,
        "game_type": session.game_type,
        "match_number": session.tournament_match_number,
        "status": status,
        "matchup_label": matchup_label,
        "result_label": result_label,
        "pending": status != "completed",
    }


def _get_matchup_label(session: Any, users: dict) -> str:
    """Return concrete player names if known, otherwise fall back to structure_config matchup."""
    participant_ids = session.participant_user_ids or []
    if participant_ids:
        names = []
        for uid in participant_ids:
            u = users.get(uid)
            names.append(f"{u.first_name} {u.last_name}" if u else f"#{uid}")
        return " vs ".join(names)

    # Fallback to matchup label from structure_config
    sc = session.structure_config
    if sc and isinstance(sc, dict):
        label = sc.get("matchup") or sc.get("round_name", "")
        if label:
            return f"⏳ {label}"

    return "⏳ TBD"


def _get_result_label(session: Any, users: dict) -> Optional[str]:
    """Return 'Name X – Y Name' for a completed session."""
    results = session.game_results
    if not results or not isinstance(results, dict):
        return None
    participants = results.get("participants", [])
    if len(participants) < 2:
        return None
    parts = []
    for p in participants:
        uid = p.get("user_id")
        score = p.get("score", 0)
        u = users.get(uid)
        name = f"{u.first_name} {u.last_name}" if u else f"#{uid}"
        parts.append(f"{name} {score}")
    return " – ".join(parts)


# ── Per-session row builder ───────────────────────────────────────────────────

def _build_live_session_row(session: Any, users: dict) -> dict:
    status = (session.session_status or "").lower()
    matchup = _get_matchup_label(session, users)
    result = _get_result_label(session, users) if status == "completed" else None
    return {
        "session_id": session.id,
        "match_number": session.tournament_match_number,
        "round_number": session.round_number,
        "status": status,
        "matchup_label": matchup,
        "result_label": result,
        "group_identifier": session.group_identifier,
        "game_type": session.game_type,
    }


# ── League placeholder ────────────────────────────────────────────────────────

def _build_league_placeholder(sessions: list) -> list:
    """Minimal league round data — full implementation in PR Live-3."""
    return []
