"""
Config/mapping resolution layer for skill progression.

Handles skill key enumeration (from skills_config), baseline lookup
(from UserLicense.football_skills), and tournament-to-skill mapping
resolution (from reward_config JSON or TournamentSkillMapping table).

No EMA formula logic, no view building.  Minimal DB access (1 query per
function at most).

Extracted from skill_progression_service.py (Layer 2).
"""
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.license import UserLicense
from app.models.tournament_achievement import TournamentSkillMapping
from app.skills_config import SKILL_CATEGORIES
from ._formulas import DEFAULT_BASELINE


def get_all_skill_keys() -> List[str]:
    """
    Get list of all skill keys from skills_config.

    Returns:
        List of skill keys (e.g., ["ball_control", "dribbling", ...])
    """
    skill_keys = []
    for category in SKILL_CATEGORIES:
        for skill in category["skills"]:
            skill_keys.append(skill["key"])
    return skill_keys


def get_baseline_skills(db: Session, user_id: int) -> Dict[str, float]:
    """
    Get baseline skill values from UserLicense.football_skills (onboarding).

    Args:
        db: Database session
        user_id: User ID

    Returns:
        Dict of skill_key → baseline_value (0-100)

    ⚠️ FALLBACK BEHAVIOR FOR MISSING SKILLS:
        If a skill is NOT found in UserLicense.football_skills, it defaults to DEFAULT_BASELINE (60.0).
        This is INTENTIONAL and handles cases where:
        - User completed onboarding with old skill set (before migration to 29 skills)
        - User's onboarding data is incomplete
        - New skills were added to system after user onboarding

        DEFAULT_BASELINE (60.0) = SYSTEM_BASELINE — the fixed visible starting level for every
        new LFA Football Player.  Tournament placements adjust this value up or down.

    Baseline priority for dict-format skills:
        1. system_baseline  — new format; fixed 60.0 written at onboarding
        2. baseline         — legacy format; may hold a self-assessment value for older records
        3. DEFAULT_BASELINE — absolute fallback (60.0)

    Example:
        User has onboarding data: {"ball_control": 70, "dribbling": 65}
        System now has 29 skills total.
        Result: {"ball_control": 70.0, "dribbling": 65.0, "speed": 60.0, ...other skills... → 60.0}
    """
    # Get active LFA_FOOTBALL_PLAYER license
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True
    ).first()

    if not license or not license.football_skills:
        # 🔒 GUARD: No onboarding data at all → return all skills at DEFAULT_BASELINE
        return {skill_key: DEFAULT_BASELINE for skill_key in get_all_skill_keys()}

    # 🔒 GUARD: Ensure football_skills is a dict
    if not isinstance(license.football_skills, dict):
        return {skill_key: DEFAULT_BASELINE for skill_key in get_all_skill_keys()}

    baseline_skills = {}
    for skill_key in get_all_skill_keys():
        skill_value = license.football_skills.get(skill_key, DEFAULT_BASELINE)

        if isinstance(skill_value, dict):
            # Priority: system_baseline (new) → baseline (legacy) → DEFAULT_BASELINE
            baseline_skills[skill_key] = float(
                skill_value.get("system_baseline",
                    skill_value.get("baseline", DEFAULT_BASELINE)
                )
            )
        else:
            # Flat scalar format: {"ball_control": 70.0, ...}
            baseline_skills[skill_key] = float(skill_value)

    return baseline_skills


def _extract_tournament_skills(
    db: Session,
    tournament,
    skill_keys: set,
) -> Dict[str, float]:
    """
    Resolve which skills (with weights) a tournament affects.

    Priority 1: reward_config.skill_mappings (V2 config-based)
    Priority 2: TournamentSkillMapping table (legacy / E2E seeded tournaments)

    Returns a dict of {skill_name: weight} for enabled skills that are present in
    ``skill_keys``.  Returns an empty dict if no skills could be resolved.
    """
    result: Dict[str, float] = {}

    reward_config = tournament.reward_config or {}
    skill_mappings = reward_config.get("skill_mappings", [])

    if isinstance(skill_mappings, list) and skill_mappings:
        # V2 format: [{"skill": "passing", "enabled": true, "weight": 1.0}, ...]
        for mapping in skill_mappings:
            if mapping.get("enabled", False) and mapping.get("skill") in skill_keys:
                result[mapping["skill"]] = mapping.get("weight", 1.0)
    elif isinstance(skill_mappings, dict) and skill_mappings:
        # Legacy dict format: {"passing": {...}, ...}
        for sk in skill_mappings:
            if sk in skill_keys:
                result[sk] = 1.0

    # Fallback: TournamentSkillMapping table (covers tournaments without reward_config skill_mappings)
    if not result:
        table_mappings = (
            db.query(TournamentSkillMapping)
            .filter(TournamentSkillMapping.semester_id == tournament.id)
            .all()
        )
        for tm in table_mappings:
            if tm.skill_name in skill_keys:
                result[tm.skill_name] = float(tm.weight) if tm.weight else 1.0

    return result
