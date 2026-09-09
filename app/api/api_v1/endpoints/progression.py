from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.license import UserLicense
from app.dependencies import get_current_user
from app.services import skill_progression_service
from pydantic import BaseModel

router = APIRouter()

class UserProgressResponse(BaseModel):
    internship_level: Optional[str] = None
    coach_foundation_level: Optional[str] = None
    coach_specializations: List[str] = []
    gancuju_level: Optional[str] = None
    completed_semesters: dict = {}

class UpdateProgressRequest(BaseModel):
    track: str  # 'internship', 'coach', 'gancuju'
    level: str
    specializations: Optional[List[str]] = None

# Progression system definitions
PROGRESSION_SYSTEMS = {
    "internship": {
        "levels": ["junior", "medior", "senior"],
        "prerequisites": {
            "medior": "junior",
            "senior": "medior"
        }
    },
    "coach": {
        "foundation_levels": [
            "pre_assistant", "pre_lead", "youth_assistant", "youth_lead",
            "amateur_assistant", "amateur_lead", "pro_assistant", "pro_lead"
        ],
        "specializations": ["goalkeeper", "fitness", "rehabilitation"],
        "prerequisites": {
            "pre_lead": "pre_assistant",
            "youth_assistant": "pre_lead",
            "youth_lead": "youth_assistant",
            "amateur_assistant": "youth_lead",
            "amateur_lead": "amateur_assistant",
            "pro_assistant": "amateur_lead",
            "pro_lead": "pro_assistant"
        },
        "specialization_prerequisite": "pre_lead"  # Need Pre Lead to access specializations
    },
    "gancuju": {
        "levels": [
            "bamboo", "dawn", "reed", "river",
            "root", "moon", "guardian", "dragon"
        ],
        "prerequisites": {
            "dawn": "bamboo",
            "reed": "dawn",
            "river": "reed",
            "root": "river",
            "moon": "root",
            "guardian": "moon",
            "dragon": "guardian"
        }
    }
}

SEMESTER_COUNTS = {
    # Internship track
    "junior": 1, "medior": 1, "senior": 1,
    
    # Coach foundation track
    "pre_assistant": 1, "pre_lead": 2, "youth_assistant": 2, "youth_lead": 3,
    "amateur_assistant": 4, "amateur_lead": 4, "pro_assistant": 5, "pro_lead": 5,
    
    # Coach specializations
    "goalkeeper": 2, "fitness": 2, "rehabilitation": 2,
    
    # GānCuju track
    "bamboo": 1, "dawn": 1, "reed": 1, "river": 1,
    "root": 1, "moon": 1, "guardian": 1, "dragon": 1
}

def validate_prerequisite(track: str, level: str, current_progress: dict) -> bool:
    """Check if user meets prerequisites for a level"""
    system = PROGRESSION_SYSTEMS.get(track)
    if not system:
        return False
    
    prerequisites = system.get("prerequisites", {})
    required_level = prerequisites.get(level)
    
    if not required_level:
        return True  # No prerequisite needed
    
    if track == "internship":
        current_level = current_progress.get("internship_level")
        levels = system["levels"]
        current_index = levels.index(current_level) if current_level in levels else -1
        required_index = levels.index(required_level)
        return current_index >= required_index
    
    elif track == "coach":
        if level in system["specializations"]:
            # Specialization requires pre_lead
            current_level = current_progress.get("coach_foundation_level")
            if not current_level:
                return False
            foundation_levels = system["foundation_levels"]
            current_index = foundation_levels.index(current_level) if current_level in foundation_levels else -1
            required_index = foundation_levels.index(system["specialization_prerequisite"])
            return current_index >= required_index
        else:
            # Foundation level
            current_level = current_progress.get("coach_foundation_level")
            levels = system["foundation_levels"]
            current_index = levels.index(current_level) if current_level in levels else -1
            required_index = levels.index(required_level)
            return current_index >= required_index
    
    elif track == "gancuju":
        current_level = current_progress.get("gancuju_level")
        levels = system["levels"]
        current_index = levels.index(current_level) if current_level in levels else -1
        required_index = levels.index(required_level)
        return current_index >= required_index
    
    return False

def calculate_completed_semesters(progress: dict) -> dict:
    """Calculate total completed semesters for each track"""
    result = {"internship": 0, "coach": 0, "gancuju": 0}
    
    # Internship track
    if progress.get("internship_level"):
        level = progress["internship_level"]
        levels = PROGRESSION_SYSTEMS["internship"]["levels"]
        current_index = levels.index(level) if level in levels else -1
        for i in range(current_index + 1):
            result["internship"] += SEMESTER_COUNTS[levels[i]]
    
    # Coach track (foundation + specializations)
    if progress.get("coach_foundation_level"):
        level = progress["coach_foundation_level"]
        levels = PROGRESSION_SYSTEMS["coach"]["foundation_levels"]
        current_index = levels.index(level) if level in levels else -1
        for i in range(current_index + 1):
            result["coach"] += SEMESTER_COUNTS[levels[i]]
    
    # Add specialization semesters
    for spec in progress.get("coach_specializations", []):
        if spec in SEMESTER_COUNTS:
            result["coach"] += SEMESTER_COUNTS[spec]
    
    # GānCuju track
    if progress.get("gancuju_level"):
        level = progress["gancuju_level"]
        levels = PROGRESSION_SYSTEMS["gancuju"]["levels"]
        current_index = levels.index(level) if level in levels else -1
        for i in range(current_index + 1):
            result["gancuju"] += SEMESTER_COUNTS[levels[i]]
    
    return result

@router.get("/progress", response_model=UserProgressResponse)
def get_user_progress(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """This legacy route has no persisted progression source."""
    raise HTTPException(
        status_code=501,
        detail="Legacy progression endpoint is unavailable; use specialization progression APIs",
    )

@router.post("/progress/update")
def update_user_progress(
    request: UpdateProgressRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """This legacy route never persisted updates and is deliberately disabled."""
    raise HTTPException(
        status_code=501,
        detail="Legacy progression endpoint is unavailable; use specialization progression APIs",
    )

@router.get("/systems")
def get_progression_systems():
    """Get all progression system definitions"""

    # Add UI metadata to the systems
    enhanced_systems = {
        "internship": {
            **PROGRESSION_SYSTEMS["internship"],
            "id": "internship",
            "title": "Internship Track",
            "subtitle": "LFA Gyakornoki Program",
            "emoji": "💼",
            "color": "#059669",
            "gradient": "linear-gradient(135deg, #10b981 0%, #059669 100%)"
        },
        "coach": {
            **PROGRESSION_SYSTEMS["coach"],
            "id": "coach",
            "title": "Coach Track",
            "subtitle": "LFA Edzői Specializáció",
            "emoji": "👨‍🏫",
            "color": "#DC2626",
            "gradient": "linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%)"
        },
        "gancuju": {
            **PROGRESSION_SYSTEMS["gancuju"],
            "id": "gancuju",
            "title": "GānCuju™️©️ Track",
            "subtitle": "8 Szintű Játékos Fejlesztési Rendszer",
            "emoji": "⚽",
            "color": "#4F46E5",
            "gradient": "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)"
        }
    }

    return {
        "systems": enhanced_systems,
        "semester_counts": SEMESTER_COUNTS
    }


# ============================================================================
# SKILL PROFILE ENDPOINT - Dynamic Skill Progression
# ============================================================================

class SkillData(BaseModel):
    """Individual skill progression data"""
    current_level: float
    baseline: float
    total_delta: float
    tournament_delta: Optional[float] = None  # Breakdown: tournament contribution
    assessment_delta: Optional[float] = None  # Breakdown: assessment contribution
    last_updated: str
    assessment_count: int
    tournament_count: int
    tier: str
    tier_emoji: str


class SkillProfileResponse(BaseModel):
    """Complete skill profile with dynamic calculations"""
    user_license_id: int
    specialization: str
    skills: Dict[str, SkillData]
    average_level: float
    total_assessments: int
    total_tournaments: int


@router.get("/skill-profile", response_model=SkillProfileResponse)
def get_skill_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get player's dynamic skill profile with placement-based calculation

    NEW V2 System:
        - Skills calculated from tournament placement (not points)
        - Can both INCREASE and DECREASE based on performance
        - 1st place → ~95-100, Last place → ~40-50
        - Weighted average: baseline + placement-based value

    Returns:
        - Current skill levels (baseline + tournament deltas)
        - Baseline values from onboarding
        - Tournament participation count per skill
        - Assessment count per skill
        - Average skill level (dynamically calculated)
        - Skill tiers (BEGINNER, DEVELOPING, INTERMEDIATE, ADVANCED, MASTER)
    """
    # Get active LFA_FOOTBALL_PLAYER license
    license = db.query(UserLicense).filter(
        UserLicense.user_id == current_user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True
    ).first()

    if not license:
        raise HTTPException(
            status_code=404,
            detail="No active LFA Player license found. Please complete onboarding first."
        )

    # Get skill profile using NEW placement-based system
    profile_data = skill_progression_service.get_skill_profile(db, current_user.id)

    skills = profile_data.get("skills", {})
    average_level = profile_data.get("average_level", 0.0)
    total_tournaments = profile_data.get("total_tournaments", 0)
    total_assessments = profile_data.get("total_assessments", 0)

    # Build response with tier info
    skills_response = {}
    for skill_name, skill_data in skills.items():
        current_level = skill_data.get("current_level", 0.0)
        tier = skill_data.get("tier", "BEGINNER")
        tier_emoji = skill_data.get("tier_emoji", "🌱")

        skills_response[skill_name] = SkillData(
            current_level=current_level,
            baseline=skill_data.get("baseline", 0.0),
            total_delta=skill_data.get("total_delta", 0.0),
            tournament_delta=skill_data.get("tournament_delta"),  # NEW: breakdown data
            assessment_delta=skill_data.get("assessment_delta"),  # NEW: breakdown data
            last_updated="",  # Not tracked in V2
            assessment_count=skill_data.get("assessment_count", 0),
            tournament_count=skill_data.get("tournament_count", 0),
            tier=tier,
            tier_emoji=tier_emoji
        )

    return SkillProfileResponse(
        user_license_id=license.id,
        specialization=license.specialization_type,
        skills=skills_response,
        average_level=average_level,
        total_assessments=total_assessments,
        total_tournaments=total_tournaments
    )


# ============================================================================
# SKILL TIMELINE ENDPOINT - Per-skill tournament history
# ============================================================================

class SkillTimelineEntry(BaseModel):
    """One data point in a skill's history (per tournament)"""
    tournament_id: int
    tournament_name: str
    achieved_at: str
    placement: Optional[int]
    total_players: int
    placement_skill: float
    skill_weight: float
    skill_value_after: float
    delta_from_baseline: float
    delta_from_previous: float


class SkillTimelineResponse(BaseModel):
    """Full timeline for a single skill"""
    skill: str
    baseline: float
    current_level: float
    total_delta: float
    timeline: List[SkillTimelineEntry]


@router.get("/skill-timeline", response_model=SkillTimelineResponse)
def get_skill_timeline(
    skill: str = Query(..., description="Skill key, e.g. 'passing', 'finishing'"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the per-tournament progression history for a single skill.

    Returns each tournament event chronologically with:
    - skill_value_after: The calculated skill level right after that tournament
    - delta_from_previous: Change vs previous tournament (or baseline for first)
    - placement, total_players, placement_skill for context

    Use this to power a line chart showing how a skill evolved over time.
    """
    # Validate license exists
    license = db.query(UserLicense).filter(
        UserLicense.user_id == current_user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True
    ).first()

    if not license:
        raise HTTPException(
            status_code=404,
            detail="No active LFA Player license found. Please complete onboarding first."
        )

    result = skill_progression_service.get_skill_timeline(db, current_user.id, skill)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{skill}' not found. Check the skill key spelling."
        )

    return SkillTimelineResponse(
        skill=result["skill"],
        baseline=result["baseline"],
        current_level=result["current_level"],
        total_delta=result["total_delta"],
        timeline=[SkillTimelineEntry(**entry) for entry in result["timeline"]]
    )


# ============================================================================
# SKILL AUDIT ENDPOINT - Per-tournament expected vs actual change log
# ============================================================================

class SkillAuditRow(BaseModel):
    """One audit row: one tournament × one mapped skill"""
    tournament_id: int
    tournament_name: str
    achieved_at: Optional[str]
    placement: Optional[int]
    total_players: int
    skill: str
    skill_weight: float
    avg_weight: float
    is_dominant: bool
    expected_change: bool
    placement_skill: float
    delta_this_tournament: float
    norm_delta: float
    actual_changed: bool
    fairness_ok: bool
    opponent_factor: float
    ema_path: bool


class SkillAuditResponse(BaseModel):
    total_entries: int
    unfair_entries: int
    audit: List[SkillAuditRow]


@router.get("/skill-audit", response_model=SkillAuditResponse)
def get_skill_audit(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Per-tournament skill audit log: expected vs actual change per skill.

    For each tournament the player participated in, returns one row per mapped skill:
    - expected_change: always True (skill is in the tournament's mapping)
    - actual_changed: whether abs(delta) > 0.001
    - delta_this_tournament: signed change produced by this tournament
    - is_dominant: this skill has weight > average weight for the tournament
    - fairness_ok: dominant skill had |delta| >= non-dominant peers
      (checked only when baselines are within 5 pts of each other)

    Useful for validating that dominant-weight skills produce larger changes than minor skills.
    """
    license = db.query(UserLicense).filter(
        UserLicense.user_id == current_user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True
    ).first()

    if not license:
        raise HTTPException(
            status_code=404,
            detail="No active LFA Player license found. Please complete onboarding first."
        )

    rows = skill_progression_service.get_skill_audit(db, current_user.id)
    unfair = sum(1 for r in rows if not r["fairness_ok"] and r.get("ema_path", False))

    return SkillAuditResponse(
        total_entries=len(rows),
        unfair_entries=unfair,
        audit=[SkillAuditRow(**r) for r in rows]
    )
