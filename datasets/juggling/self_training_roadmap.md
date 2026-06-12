# Juggling Detection — Self-Training Roadmap

**Policy:** No paid models. No AGPL production dependencies. All inference runs on
our own trained models using free, permissively licensed tools. See `licence_policy.md`.

---

## Technology Stack (Licence-Audited)

| Component | Role | Licence |
|---|---|---|
| `ffmpeg` | Frame extraction, video processing | LGPL-2.1 |
| `opencv-python-headless` | Baseline detection, preprocessing | Apache 2.0 |
| `PyTorch` + `torchvision` | Model training | BSD-3-Clause |
| `ONNX` + `onnxruntime` | Model export and inference | Apache 2.0 / MIT |
| `MediaPipe Pose` | Body pose landmarks (Phase C) | Apache 2.0 |
| `LabelImg` | Bounding box annotation | MIT |
| Our own trained model | Ball / contact detector | Proprietary |

**Not allowed in production:** YOLO/Ultralytics (AGPL), any paid API, FootAndBall (unverified licence).

---

## Phase B0 — OpenCV Baseline (No ML, No Training)

**Goal:** Validate the debug pipeline and measurement framework before any model exists.
This is a reference implementation, not a production detector.

**Approach:**
- HSV color segmentation for white/yellow ball: `cv2.inRange(frame_hsv, lower, upper)`
- Hough Circle Transform: `cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, ...)`
- Frame differencing for motion: `cv2.absdiff(prev_frame, curr_frame)`
- Contour blob filtering by area and circularity

**Expected accuracy:** 40–60% recall in good conditions; many false positives.
Acceptable — the goal is pipeline plumbing, not detection quality.

**Outputs:**
- Debug JSON: `{frame_idx, timestamp_ms, ball_center, ball_confidence_proxy}`
- Debug overlay PNG: circle drawn on detected position (optional)

**Done when:** End-to-end pipeline runs on Phase A eval set and produces valid debug JSON.

---

## Phase B1 — Self-Trained Ball Detector Preparation

**Prerequisites:** Phase A dataset complete (≥ 20 annotated clips) and training-readiness decision approved.

### Dataset preparation

```
datasets/juggling/
├── frames/<video_id>/        ← extracted at 10fps (every 3rd frame)
├── labels/<video_id>/        ← YOLO bbox labels from LabelImg
└── training/
    ├── train.txt             ← list of training frame paths
    ├── val.txt               ← list of validation frame paths
    └── dataset_coco.json     ← COCO format export for torchvision
```

**Minimum for first training run:** 500 annotated ball bounding boxes across ≥ 16 clips (80/20 split).

### Model architecture

**First choice:** `torchvision.models.detection.ssdlite320_mobilenet_v3_large`
- Pre-trained on COCO (ball is not in COCO, but the backbone features transfer well)
- Fine-tune with our 1-class dataset (class 0 = ball)
- Input: 320×320 RGB
- Output: list of `(bbox, score)` per frame

**Alternative if SSD underperforms:** `torchvision.models.detection.retinanet_resnet50_fpn`
- Better small-object detection; heavier (~100MB vs ~30MB)
- Same training API, slower CPU inference (~800ms/frame vs ~300ms)

### Training script skeleton

```python
import torchvision
from torchvision.models.detection import ssdlite320_mobilenet_v3_large
from torchvision.models.detection.ssd import SSDClassificationHead
import torch

# Load pre-trained backbone, replace head for 1 class (ball)
model = ssdlite320_mobilenet_v3_large(weights="DEFAULT")
# Replace head: num_classes = 2 (background + ball)
in_channels = ...   # from model.head.classification_head
model.head.classification_head = SSDClassificationHead(in_channels, 2)

optimizer = torch.optim.SGD(model.parameters(), lr=1e-4, momentum=0.9)
# Train for 50–100 epochs on our dataset
```

### ONNX export

```python
import torch
model.eval()
dummy = torch.zeros(1, 3, 320, 320)
torch.onnx.export(
    model, dummy,
    "local_models/juggling/ball_detector_v1.onnx",
    opset_version=11,
    input_names=["image"],
    output_names=["boxes", "scores"],
)
```

**Note:** `local_models/juggling/*.onnx` is gitignored. Do not commit trained model files.

---

## Phase B2 — Self-Trained Ball Detector MVP

**Prerequisites:** Phase B1 training complete; ONNX model exported and benchmarked.

### Inference pipeline

```python
import onnxruntime as ort
import cv2, numpy as np

session = ort.InferenceSession("local_models/juggling/ball_detector_v1.onnx")

def detect_ball(frame_rgb: np.ndarray) -> list[dict]:
    img = cv2.resize(frame_rgb, (320, 320))
    inp = (img.transpose(2,0,1)[None] / 255.0).astype(np.float32)
    boxes, scores = session.run(None, {"image": inp})
    return [
        {"bbox": b.tolist(), "confidence": float(s)}
        for b, s in zip(boxes[0], scores[0])
        if s > 0.5
    ]
```

### Celery task (Phase B2 backend integration)

- Task name: `app.tasks.juggling_ball_detection_task.detect_balls_task`
- Queue: `juggling_videos`
- Input: `video_id` → reads `processed_path` from DB
- Output: writes `ball_detection_result` JSONB to DB
- Feature flag: `JUGGLING_BALL_DETECTION_ENABLED` (default: False)

### Debug JSON schema (per video)

See `datasets/juggling/annotation_schema_v1.json` `contact_events` for frame-level format.
Full debug JSON will include `ball_detections` array with per-frame entries.

### Benchmark

Run against Phase A eval set:
```bash
python3 scripts/benchmark_ball_detection.py \
  --manifest datasets/juggling/dataset_manifest.json \
  --model local_models/juggling/ball_detector_v1.onnx \
  --output datasets/juggling/benchmark_results/phase_b2_v1.json
```

**Acceptance thresholds:**
- Recall Easy ≥ 0.75
- Recall Hard ≥ 0.60
- Precision ≥ 0.85
- Processing ≤ 10× realtime on CPU

---

## Phase C — MediaPipe Pose (Body / Player Detection)

**Prerequisites:** Phase B2 ball detector accepted.

**Technology:** MediaPipe Pose (Apache 2.0, offline, no API)

```python
import mediapipe as mp
pose = mp.solutions.pose.Pose(
    static_image_mode=False,
    model_complexity=1,
    enable_segmentation=False,
    min_detection_confidence=0.5,
)
results = pose.process(frame_rgb)
landmarks = results.pose_landmarks.landmark  # 33 landmarks
```

**Important:** This is a separate scope from the Biometric Face Matching pipeline.
MediaPipe Pose for juggling uses `pose_landmarker.task`, not the face/hand models.
Do not share model files or service code with the biometric scope.

**Outputs:** 33 body landmarks per frame → body-part zone polygons (foot, knee, thigh, shoulder, head, chest).

---

## Phase D — Contact Event Detection

**Prerequisites:** Phase B2 (ball detector) + Phase C (pose) both accepted.

**Algorithm:**
1. For each frame: compute distance from `ball_center` to each body-part zone polygon
2. If distance < threshold AND velocity direction reversal detected: candidate contact event
3. Apply duplicate suppression: min 200ms between consecutive events on the same body part

**Velocity direction change:**
```python
def direction_reversal(pos_history: list) -> bool:
    # pos_history: last 5 ball center points
    if len(pos_history) < 5:
        return False
    dy_before = pos_history[-3][1] - pos_history[-5][1]  # moving down
    dy_after  = pos_history[-1][1] - pos_history[-3][1]  # moving up
    return dy_before > 5 and dy_after < -5  # pixel threshold
```

---

## Phase E — Juggling Count MVP

**Prerequisites:** Phase D contact event detection accepted.

**Algorithm:**
- Count = number of valid contact events in the clip
- Duplicate suppression: min 200ms gap enforced in Phase D
- Dropped ball detection: if ball trajectory shows free-fall parabola with no contact for > 1.5s, flag as dropped
- Final count excludes events after a dropped ball (unless the ball is picked up again)

**Outputs:** `juggling_count: int`, `juggling_confidence: float (0–1)`

---

## Phase F — Validation / Quality Layer (Real Detectors)

Replace POC placeholders in `quality_service.py`:

| Placeholder (current) | Replacement (Phase F) |
|---|---|
| `blur_score` (bitrate proxy) | OpenCV Laplacian variance: `cv2.Laplacian(gray, cv2.CV_64F).var()` |
| `dark_frame_ratio` (hardcoded 0.05) | Mean luminance per sampled frame: `cv2.mean(gray)[0] < threshold` |
| `subject_size_score` (None) | Player bbox area / frame area (from Phase C pose detector) |
| `ball_visible_score` (None) | Ball detection recall over clip (from Phase B2 detector) |

---

## Timeline (Indicative)

| Phase | Prerequisite | Estimated effort |
|---|---|---|
| A (Dataset) | Videók + annotátorok | 2–4 weeks |
| B0 (OpenCV baseline) | Phase A complete | 1 week |
| B1 (Training prep) | Phase A + 500 bbox labels | 2 weeks |
| B2 (Ball detector MVP) | Phase B1 + training run | 2–3 weeks |
| C (Pose) | Phase B2 accepted | 1–2 weeks |
| D (Contact events) | Phase C accepted | 2–3 weeks |
| E (Count MVP) | Phase D accepted | 1–2 weeks |
| F (Quality layer) | Phase E accepted | 1 week |
