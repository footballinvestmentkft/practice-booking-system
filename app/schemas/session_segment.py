"""
Pydantic schemas for session segments.

SessionSegmentCreate — input for POST /api/v1/sessions/{id}/segments
SessionSegmentRead   — output for the same endpoint
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionSegmentCreate(BaseModel):
    """Input schema for creating a single session segment."""

    label: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Human-readable name for this drill/exercise.",
    )
    position: int = Field(
        ...,
        ge=0,
        le=32767,
        description="Zero-based display order within the session. Must be unique per session.",
    )
    duration_minutes: Optional[int] = Field(
        None,
        ge=1,
        le=300,
        description="Planned duration in minutes (informational only).",
    )
    skill_targets: Optional[Dict[str, float]] = Field(
        None,
        description=(
            "Map of skill_key → weight. NULL = inherit from session game_preset at result time. "
            "All weights must be > 0."
        ),
    )

    @model_validator(mode="after")
    def validate_skill_targets(self) -> "SessionSegmentCreate":
        if self.skill_targets is not None:
            for key, val in self.skill_targets.items():
                if val <= 0:
                    raise ValueError(
                        f"skill_targets['{key}'] must be > 0, got {val}"
                    )
        return self


class SessionSegmentRead(BaseModel):
    """Output schema for a session segment."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    position: int
    label: str
    duration_minutes: Optional[int] = None
    skill_targets: Optional[Dict[str, float]] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
