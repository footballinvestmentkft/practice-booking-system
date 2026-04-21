"""
Pydantic schemas for session segments.

SessionSegmentCreate — input for POST /api/v1/sessions/{id}/segments
SessionSegmentUpdate — input for PATCH /api/v1/sessions/{id}/segments/{seg_id}
SessionSegmentRead   — output for both endpoints
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


class SessionSegmentUpdate(BaseModel):
    """Partial update schema for a session segment.

    All fields are optional — omit a field to leave it unchanged.
    ``null`` is valid only for ``duration_minutes`` and ``skill_targets``
    (it clears the field); ``label``, ``position``, and ``is_active``
    reject explicit null.  An empty body ``{}`` is rejected with 422.
    """

    label: Optional[str] = Field(
        None,
        min_length=1,
        max_length=200,
        description="Omit to leave unchanged.",
    )
    position: Optional[int] = Field(
        None,
        ge=0,
        le=32767,
        description="Omit to leave unchanged.",
    )
    duration_minutes: Optional[int] = Field(
        None,
        ge=1,
        le=300,
        description="null clears the field.",
    )
    skill_targets: Optional[Dict[str, float]] = Field(
        None,
        description="null clears the field (reverts to preset at reward time).",
    )
    is_active: Optional[bool] = Field(
        None,
        description="Omit to leave unchanged.",
    )

    @model_validator(mode="after")
    def validate_patch(self) -> "SessionSegmentUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided for update.")
        for field in ("label", "position", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(
                    f"'{field}' cannot be set to null; omit it to leave unchanged."
                )
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
