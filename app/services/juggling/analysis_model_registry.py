"""
Analysis Model Registry — type-aware model config dispatcher.

Maps training_video_type → AnalysisModelConfig so the ball detection
task uses the correct model, class ID, and confidence threshold per
video type. First version: all types share SSD MobileNet v1 (Apache-2.0).

To add a new sport type or model:
  1. Add an entry to ANALYSIS_MODEL_REGISTRY
  2. Add the model file to app/ml_models/ (gitignored)
  3. No code changes needed in service/task/endpoint layers
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisModelConfig:
    model_path_key:        str
    detection_source:      str
    model_version:         str
    target_class_id:       int
    target_class_name:     str
    input_size:            int
    confidence_threshold:  float


_SHARED_MOBILENET = AnalysisModelConfig(
    model_path_key="BALL_DETECTION_MODEL_PATH",
    detection_source="mobilenet_ssd_v1",
    model_version="ssd_mobilenet_v1_12_onnx",
    target_class_id=37,
    target_class_name="sports_ball",
    input_size=300,
    confidence_threshold=0.3,
)

ANALYSIS_MODEL_REGISTRY: dict[str, AnalysisModelConfig] = {
    "juggling":        _SHARED_MOBILENET,
    "gan_footvolley":  _SHARED_MOBILENET,
    "gan_foottennis":  _SHARED_MOBILENET,
}

_FALLBACK_TYPE = "juggling"


def get_model_config(training_video_type: str) -> AnalysisModelConfig:
    return ANALYSIS_MODEL_REGISTRY.get(
        training_video_type,
        ANALYSIS_MODEL_REGISTRY[_FALLBACK_TYPE],
    )
