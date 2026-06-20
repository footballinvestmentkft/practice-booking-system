"""
Multi-camera 3D skeleton contract — AN-3B PR-4A.

Schema-only: no ORM, no migration, no API endpoint.
"""
from __future__ import annotations

import enum
import uuid
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class CanonicalJoint(str, enum.Enum):
    NOSE = "nose"
    LEFT_EYE = "left_eye"
    RIGHT_EYE = "right_eye"
    LEFT_EAR = "left_ear"
    RIGHT_EAR = "right_ear"
    NECK = "neck"
    LEFT_SHOULDER = "left_shoulder"
    RIGHT_SHOULDER = "right_shoulder"
    LEFT_ELBOW = "left_elbow"
    RIGHT_ELBOW = "right_elbow"
    LEFT_WRIST = "left_wrist"
    RIGHT_WRIST = "right_wrist"
    ROOT = "root"
    LEFT_HIP = "left_hip"
    RIGHT_HIP = "right_hip"
    LEFT_KNEE = "left_knee"
    RIGHT_KNEE = "right_knee"
    LEFT_ANKLE = "left_ankle"
    RIGHT_ANKLE = "right_ankle"


class TriangulationStatus(str, enum.Enum):
    TRIANGULATED = "triangulated"
    SINGLE_VIEW_ONLY = "single_view_only"
    BELOW_CONFIDENCE = "below_confidence"
    JOINT_MISSING = "joint_missing"


class SyncMethod(str, enum.Enum):
    AUDIO_CLAP = "audio_clap"
    SOFTWARE_START = "software_start"
    MANUAL = "manual"


class SyncQuality(str, enum.Enum):
    HIGH = "high"
    ACCEPTABLE = "acceptable"
    DEGRADED = "degraded"
    FAILED = "failed"


class CapturePresetDTO(BaseModel):
    resolution: str = Field(..., pattern=r"^\d+x\d+$")
    fps: int = Field(..., gt=0, le=240)
    lens_mode: str
    stabilization: str


class PoseModelConfig(BaseModel):
    model_config = {"protected_namespaces": ()}

    model_id: str
    confidence_field_name: str
    default_threshold: float = Field(..., ge=0.0, le=1.0)
    threshold_calibrated: bool = False


class Skeleton3DJoint(BaseModel):
    canonical_joint_name: str
    source_joint_name: str
    source_model: str
    source_confidence: float = Field(..., ge=0.0, le=1.0)
    image_x: float = Field(..., ge=0.0, le=1.0)
    image_y: float = Field(..., ge=0.0, le=1.0)
    image_confidence: float = Field(..., ge=0.0, le=1.0)
    is_synthetic: bool

    world_x: Optional[float] = None
    world_y: Optional[float] = None
    world_z: Optional[float] = None
    world_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    reprojection_error_px: Optional[float] = Field(None, ge=0.0)
    source_view_ids: List[str] = Field(default_factory=list)
    triangulation_status: str

    @field_validator("canonical_joint_name")
    @classmethod
    def _validate_canonical(cls, v: str) -> str:
        if v not in {j.value for j in CanonicalJoint}:
            raise ValueError(f"Unknown canonical joint: {v}")
        return v

    @field_validator("triangulation_status")
    @classmethod
    def _validate_tri_status(cls, v: str) -> str:
        if v not in {s.value for s in TriangulationStatus}:
            raise ValueError(f"Unknown triangulation_status: {v}")
        return v

    @model_validator(mode="after")
    def _world_consistency(self) -> "Skeleton3DJoint":
        world_fields = [self.world_x, self.world_y, self.world_z]
        non_none = sum(1 for f in world_fields if f is not None)
        if non_none not in (0, 3):
            raise ValueError("world_x/y/z must be all null or all filled")
        if self.triangulation_status == TriangulationStatus.TRIANGULATED.value:
            if non_none != 3:
                raise ValueError("triangulated joint must have world coordinates")
            if len(self.source_view_ids) < 2:
                raise ValueError("triangulated joint requires >= 2 source_view_ids")
        if self.triangulation_status == TriangulationStatus.SINGLE_VIEW_ONLY.value:
            if non_none != 0:
                raise ValueError("single_view_only must have null world coordinates")
            if len(self.source_view_ids) > 1:
                raise ValueError("single_view_only must have <= 1 source_view_ids")
        if self.reprojection_error_px is not None and non_none == 0:
            raise ValueError("reprojection_error without world coords is invalid")
        return self


class Skeleton3DFrame(BaseModel):
    schema_version: str = "2"
    session_id: uuid.UUID
    capture_id: uuid.UUID
    camera_id: str = Field(..., min_length=1)
    calibration_id: Optional[uuid.UUID] = None
    frame_id: uuid.UUID
    source_timestamp_ns: int = Field(..., ge=0)
    synchronized_timestamp_ns: Optional[int] = Field(None, ge=0)
    person_id: int = Field(default=0, ge=0)
    joints: List[Skeleton3DJoint]
    coordinate_system: str = "camera_a_origin_rh_meters"
    triangulation_method: Optional[str] = None
    processing_version: str = Field(..., min_length=1)

    @field_validator("coordinate_system")
    @classmethod
    def _validate_coord_sys(cls, v: str) -> str:
        allowed = {"camera_a_origin_rh_meters"}
        if v not in allowed:
            raise ValueError(f"coordinate_system must be one of {allowed}")
        return v


class IntrinsicCalibrationDTO(BaseModel):
    camera_id: str = Field(..., min_length=1)
    intrinsic_matrix: List[List[float]]
    distortion_coeffs: List[float]
    image_width_px: int = Field(..., gt=0)
    image_height_px: int = Field(..., gt=0)
    reprojection_error: float = Field(..., ge=0.0)
    capture_preset: CapturePresetDTO

    @field_validator("intrinsic_matrix")
    @classmethod
    def _validate_k(cls, v: List[List[float]]) -> List[List[float]]:
        if len(v) != 3 or any(len(row) != 3 for row in v):
            raise ValueError("intrinsic_matrix must be 3x3")
        return v

    @field_validator("distortion_coeffs")
    @classmethod
    def _validate_dist(cls, v: List[float]) -> List[float]:
        if len(v) not in (4, 5, 8, 12, 14):
            raise ValueError("distortion_coeffs must have 4, 5, 8, 12, or 14 elements")
        return v


class StereoCalibrationDTO(BaseModel):
    camera_a_id: str
    camera_b_id: str
    rotation_matrix: List[List[float]]
    translation_vector: List[float]
    fundamental_matrix: List[List[float]]
    essential_matrix: List[List[float]]
    reprojection_error: float = Field(..., ge=0.0)
    calibration_id: uuid.UUID

    @field_validator("rotation_matrix")
    @classmethod
    def _validate_r(cls, v: List[List[float]]) -> List[List[float]]:
        if len(v) != 3 or any(len(row) != 3 for row in v):
            raise ValueError("rotation_matrix must be 3x3")
        return v

    @field_validator("translation_vector")
    @classmethod
    def _validate_t(cls, v: List[float]) -> List[float]:
        if len(v) != 3:
            raise ValueError("translation_vector must have 3 elements")
        return v

    @field_validator("fundamental_matrix", "essential_matrix")
    @classmethod
    def _validate_3x3(cls, v: List[List[float]]) -> List[List[float]]:
        if len(v) != 3 or any(len(row) != 3 for row in v):
            raise ValueError("Matrix must be 3x3")
        return v


class SyncMetadata(BaseModel):
    session_id: uuid.UUID
    sync_method: str
    initial_offset_ms: float
    drift_rate_ms_per_s: float
    sync_reference_start_ns: Optional[int] = Field(None, ge=0)
    sync_reference_end_ns: Optional[int] = Field(None, ge=0)
    matched_frame_count: int = Field(..., ge=0)
    dropped_frame_count: int = Field(..., ge=0)
    median_alignment_ms: float = Field(..., ge=0.0)
    p95_alignment_ms: Optional[float] = Field(None, ge=0.0)
    sync_quality: str

    @field_validator("sync_method")
    @classmethod
    def _validate_method(cls, v: str) -> str:
        if v not in {m.value for m in SyncMethod}:
            raise ValueError(f"Unknown sync_method: {v}")
        return v

    @field_validator("sync_quality")
    @classmethod
    def _validate_quality(cls, v: str) -> str:
        if v not in {q.value for q in SyncQuality}:
            raise ValueError(f"Unknown sync_quality: {v}")
        return v


def validate_preset_compatibility(
    calibration_preset: CapturePresetDTO,
    session_preset: CapturePresetDTO,
) -> List[str]:
    errors: List[str] = []
    if calibration_preset.resolution != session_preset.resolution:
        errors.append(
            f"resolution mismatch: calibration={calibration_preset.resolution}, "
            f"session={session_preset.resolution}"
        )
    if calibration_preset.fps != session_preset.fps:
        errors.append(
            f"fps mismatch: calibration={calibration_preset.fps}, "
            f"session={session_preset.fps}"
        )
    if calibration_preset.lens_mode != session_preset.lens_mode:
        errors.append(
            f"lens_mode mismatch: calibration={calibration_preset.lens_mode}, "
            f"session={session_preset.lens_mode}"
        )
    if calibration_preset.stabilization != session_preset.stabilization:
        errors.append(
            f"stabilization mismatch: calibration={calibration_preset.stabilization}, "
            f"session={session_preset.stabilization}"
        )
    return errors
