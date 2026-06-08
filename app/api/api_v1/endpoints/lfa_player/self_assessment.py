"""
LFA Football Player — Baseline Skill Self-Assessment
=====================================================
POST /api/v1/lfa-player/self-assessment

Patches football_skills[key].self_assessment for all 44 skills on the
user's most recent LFA_FOOTBALL_PLAYER license.

Rules (invariants that must not change):
  - current_level   stays at 60.0 (SYSTEM_BASELINE)
  - system_baseline stays at 60.0
  - baseline        stays at 60.0
  - OVR / EMA engine is NOT triggered
  - onboarding_completed is NOT modified
  - motivation_scores is only PATCHED (existing keys preserved)

On success sets motivation_scores["self_assessment_completed"] = True,
which is the flag the GET /api/v1/licenses/motivation-assessment endpoint
reads to report completed = True.
"""

from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .....database import get_db
from .....dependencies import get_current_user
from .....models.license import UserLicense
from .....models.user import User
from .....skills_config import get_all_skill_keys

router = APIRouter()

_SYSTEM_BASELINE = 60.0
_EXPECTED_KEYS = set(get_all_skill_keys())   # 44 keys


# ── Schema ──────────────────────────────────────────────────────────────────

class SelfAssessmentRequest(BaseModel):
    skills: Dict[str, int] = Field(
        ...,
        description="All 44 skill keys mapped to integer self-ratings (0–99)."
    )

    @validator("skills")
    def validate_skills(cls, v):
        missing = _EXPECTED_KEYS - set(v.keys())
        if missing:
            raise ValueError(f"Missing skills: {sorted(missing)}")
        extra = set(v.keys()) - _EXPECTED_KEYS
        if extra:
            raise ValueError(f"Unknown skills: {sorted(extra)}")
        for key, val in v.items():
            if not (0 <= val <= 99):
                raise ValueError(f"Skill value out of range: {key}={val} (must be 0–99)")
        return v


class SelfAssessmentResponse(BaseModel):
    success:                bool
    self_assessment_average: float


# ── Endpoint ────────────────────────────────────────────────────────────────

@router.post("/self-assessment", response_model=SelfAssessmentResponse)
def submit_self_assessment(
    data: SelfAssessmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Patch football_skills[key].self_assessment for all 44 skills.

    Only self_assessment is written; current_level, system_baseline,
    baseline, EMA deltas, and onboarding_completed are untouched.
    Sets motivation_scores["self_assessment_completed"] = true.
    """
    license = db.query(UserLicense).filter(
        UserLicense.user_id == current_user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).order_by(UserLicense.id.desc()).first()

    if not license:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active LFA Football Player license found.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    # --- Patch football_skills (never overwrite current_level) --------------
    football_skills: dict = dict(license.football_skills or {})

    for skill_key, value in data.skills.items():
        if skill_key in football_skills and isinstance(football_skills[skill_key], dict):
            football_skills[skill_key]["self_assessment"] = float(value)
        else:
            # Skill missing from JSONB — initialise with full canonical structure
            football_skills[skill_key] = {
                "system_baseline":  _SYSTEM_BASELINE,
                "self_assessment":  float(value),
                "baseline":         _SYSTEM_BASELINE,
                "current_level":    _SYSTEM_BASELINE,
                "total_delta":      0.0,
                "tournament_delta": 0.0,
                "assessment_delta": 0.0,
                "last_updated":     now_iso,
                "assessment_count": 0,
                "tournament_count": 0,
            }

    license.football_skills = football_skills
    flag_modified(license, "football_skills")

    # --- Patch motivation_scores (preserve existing keys) -------------------
    average = sum(data.skills.values()) / len(data.skills)

    motivation_scores: dict = dict(license.motivation_scores or {})
    motivation_scores["self_assessment_completed"]   = True
    motivation_scores["self_assessment_average"]     = round(average, 1)
    motivation_scores["self_assessment_submitted_at"] = now_iso

    license.motivation_scores    = motivation_scores
    license.average_motivation_score = average
    flag_modified(license, "motivation_scores")

    db.commit()

    return SelfAssessmentResponse(
        success=True,
        self_assessment_average=round(average, 1),
    )
