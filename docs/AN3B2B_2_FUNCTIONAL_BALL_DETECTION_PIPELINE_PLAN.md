# AN-3B2B-2 — Functional Ball Detection Pipeline — Implementációs Terv

Státusz: **TERV — implementáció nem kezdődött el.**  
Előfeltétel: AN-3B2B-1 branch `feat/an3b2b-1-ball-detection` (7 commit, pushed).  
Licence baseline: `docs/AN3B2B_LICENCE_COMPLIANCE_AUDIT.md` (elfogadva 2026-06-17).  
Terv dátuma: 2026-06-17.

---

## 0. Összefoglaló

Az AN-3B2B-2 a meglévő AN-3B2B-1 adatmodellre (ball_detections tábla, manual
POST/GET endpoints) építve hozzáadja:

1. **ONNX inference engine** — `ssd_mobilenet_v1_12.onnx` futtatása `onnxruntime`-mal
2. **Frame extraction** — `cv2.VideoCapture` a videó `processed_path`-jából
3. **Celery analysis task** — `detect_ball_for_event` az `analysis` queue-n
4. **Admin trigger endpoint** — explicit, manuális indítás
5. **Model download script** — SHA256-ellenőrzött, kontrollált forrás
6. **LICENSE-THIRD-PARTY.md** — teljes attribution

Kizárólag a licence auditban elfogadott stack-et használja.

---

## 1. Elfogadott licence baseline

| Komponens | Licence | Forrás |
|---|---|---|
| `ssd_mobilenet_v1_12.onnx` | Apache-2.0 | ONNX Model Zoo (HF) |
| `onnxruntime==1.26.0` | MIT | Már a repo-ban |
| `opencv-python-headless>=4.8.0` | Apache-2.0 | ÚJ dependency |
| `numpy` | BSD-3-Clause | Már a repo-ban |
| `Pillow` | MIT-CMU | Már a repo-ban |
| COCO annotációk (training data) | CC-BY 4.0 | Attribution szükséges |

**Tiltott**: YOLOv8 / Ultralytics (AGPL-3.0), runtime publikus model download.

---

## 2. ONNX inference engine

### 2.1 `app/services/juggling/onnx_ball_detector.py` (ÚJ fájl, ~120 sor)

**Modell I/O** (SSD MobileNet v1, opset 12):

```
Input:
  name: "image_tensor:0"
  shape: [1, H, W, 3]    — NHWC, uint8 (0–255)
  note: nem igényel fix 300×300; a modell bármilyen méretű képet fogad

Output (4 tensor):
  "num_detections:0"     — [1]        float  (max 100)
  "detection_boxes:0"    — [1, N, 4]  float  [ymin, xmin, ymax, xmax] normalized
  "detection_scores:0"   — [1, N]     float  confidence
  "detection_classes:0"  — [1, N]     float  COCO class ID
```

**Implementáció**:

```python
import numpy as np
import onnxruntime as ort

class OnnxBallDetector:
    def __init__(self, model_path: str):
        self._session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )

    def detect(
        self,
        frame_rgb: np.ndarray,
        target_class_id: int = 37,
        confidence_threshold: float = 0.3,
    ) -> tuple[float, float, float] | None:
        """
        Returns (center_x_norm, center_y_norm, confidence) or None.
        Coordinates normalized [0, 1], origin top-left.
        """
        h, w = frame_rgb.shape[:2]
        input_tensor = np.expand_dims(frame_rgb.astype(np.uint8), axis=0)

        outputs = self._session.run(None, {"image_tensor:0": input_tensor})
        # outputs: [num_det, boxes, scores, classes]
        boxes   = outputs[1][0]    # [N, 4]
        scores  = outputs[2][0]    # [N]
        classes = outputs[3][0]    # [N]

        best_score = 0.0
        best_box = None
        for i in range(int(outputs[0][0])):
            if int(classes[i]) == target_class_id and scores[i] >= confidence_threshold:
                if scores[i] > best_score:
                    best_score = scores[i]
                    best_box = boxes[i]

        if best_box is None:
            return None

        ymin, xmin, ymax, xmax = best_box
        cx = float((xmin + xmax) / 2.0)
        cy = float((ymin + ymax) / 2.0)
        return (cx, cy, float(best_score))
```

**Lazy loading**: a `InferenceSession` egyszer jön létre a worker process-ben
és újrahasználódik (a Celery `--pool=solo -c 1` garantálja, hogy egyetlen thread
használja).

### 2.2 Egységesített detector interface

```python
_detector_cache: dict[str, OnnxBallDetector] = {}

def get_detector(model_path: str) -> OnnxBallDetector:
    if model_path not in _detector_cache:
        _detector_cache[model_path] = OnnxBallDetector(model_path)
    return _detector_cache[model_path]
```

---

## 3. Frame extraction

### 3.1 `app/services/juggling/frame_extractor.py` (ÚJ fájl, ~50 sor)

```python
import cv2
import numpy as np

def extract_frame_at_ms(
    video_path: str,
    timestamp_ms: int,
) -> tuple[np.ndarray, int, int]:
    """
    Extract a single RGB frame from a video at the given timestamp.
    Returns (frame_rgb, width, height).
    Raises ValueError if extraction fails.
    """
    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp_ms)
        ret, frame_bgr = cap.read()
        if not ret or frame_bgr is None:
            raise ValueError(f"Frame extraction failed at {timestamp_ms}ms")
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w = frame_rgb.shape[:2]
        return frame_rgb, w, h
    finally:
        cap.release()
```

**Video path selection**: `processed_path` (360p transcode) ha létezik és a
fájl elérhető, egyébként `storage_path` (original). Ha egyik sem elérhető →
task FAILED.

---

## 4. Celery analysis task

### 4.1 `app/tasks/juggling_analysis_task.py` (ÚJ fájl, ~100 sor)

```python
@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    queue="analysis",
    time_limit=120,
    soft_time_limit=90,
)
def detect_ball_for_event(
    self,
    video_id: str,
    event_id: str,
    training_video_type: str = "juggling",
) -> dict:
```

**Task flow**:

```
1. BALL_DETECTION_ENABLED ellenőrzés → skip ha False
2. Video lekérés (DB) → video.processed_path or video.storage_path
3. Event lekérés (DB) → event.timestamp_ms
4. Meglévő detekció ellenőrzés → skip ha van (idempotens)
5. AnalysisModelConfig lookup (training_video_type)
6. Model path ellenőrzés (fájl létezik-e a disk-en) → FAILED ha nem
7. Frame extraction (cv2) → frame_rgb, w, h
8. ONNX inference → (cx, cy, confidence) or None
9. Upsert JugglingBallDetection:
   - Ha detekció van: ball_x=cx, ball_y=cy, no_ball_detected=False
   - Ha nincs: no_ball_detected=True, ball_x=NULL, ball_y=NULL
10. Return {"status": "detected|not_detected", ...}
```

**Error handling**:
- `cv2.VideoCapture` hiba → `self.retry()` egyszer, majd FAILED
- `onnxruntime` hiba → FAILED (nem retry-zható — ha a modell hibás, újra is az)
- DB hiba → `self.retry()` egyszer

### 4.2 `app/celery_app.py` változások

```python
# include lista:
"app.tasks.juggling_analysis_task",

# task_routes:
"app.tasks.juggling_analysis_task.detect_ball_for_event": {"queue": "analysis"},

# task_queues:
"analysis": {},

# task_annotations:
"app.tasks.juggling_analysis_task.detect_ball_for_event": {
    "rate_limit": "30/m",
},
```

### 4.3 Worker parancs

```bash
celery -A app.celery_app worker -Q analysis --pool=solo -c 1 --loglevel=info
```

---

## 5. Admin trigger endpoint

### 5.1 `app/api/api_v1/endpoints/juggling_admin_ball_detection.py` (ÚJ fájl, ~70 sor)

```
POST /api/v1/admin/juggling/videos/{video_id}/trigger-ball-detection
```

**Auth**: admin-only (meglévő admin guard pattern a `admin_biometric_review.py`-ból).

**Logic**:
1. Video lekérés (admin bárki videóját triggerelheti)
2. `BALL_DETECTION_ENABLED` ellenőrzés → 503
3. Confirmed eventek szűrése: `annotation_review_status = 'confirmed'`, `deleted_at IS NULL`
4. Meglévő detekciók kizárása (skip)
5. Eligible eventekhez `detect_ball_for_event.delay()` dispatch
6. Response: `BallDetectionTriggerResult`

### 5.2 Router regisztráció

**`app/api/api_v1/api.py`:**
```python
from .endpoints import juggling_admin_ball_detection
api_router.include_router(
    juggling_admin_ball_detection.router,
    prefix="/admin/juggling",
    tags=["admin", "juggling"],
)
```

---

## 6. Model download script

### 6.1 `scripts/download_ml_models.py` (ÚJ fájl, ~90 sor)

```
python scripts/download_ml_models.py --model ssd-mobilenet-v1-coco
  → Letölti: app/ml_models/ssd_mobilenet_v1_12.onnx
  → SHA256 hash ellenőrzés
  → Sikertelen hash → fájl törölve, hiba
```

**Forrás prioritás**:
1. `BALL_DETECTION_MODEL_URL` env var (privát/belső URL)
2. Fallback: Hugging Face Hub direct download (nem `huggingface_hub` Python package — egyszerű `urllib.request`)

**SHA256 hash** (hardcoded a scriptben):
```python
_MODELS = {
    "ssd-mobilenet-v1-coco": {
        "filename": "ssd_mobilenet_v1_12.onnx",
        "default_url": "https://huggingface.co/onnxmodelzoo/ssd_mobilenet_v1_12/resolve/main/ssd_mobilenet_v1_12.onnx",
        "sha256": "<letöltéskor kalkulált hash>",
        "size_mb": 29.5,
        "licence": "Apache-2.0",
    },
}
```

**A script NEM fut runtime-ban.** Egyszerű CLI tool: ops/fejlesztő futtatja
deployment előtt. Production-ben a modell már a disk-en van.

---

## 7. LICENSE-THIRD-PARTY.md

### 7.1 Fájl tartalom (ÚJ fájl, repo root)

```markdown
# Third-Party Licences

## ML Models

### SSD MobileNet v1 COCO (ONNX)
- Source: ONNX Model Zoo / Hugging Face
  https://huggingface.co/onnxmodelzoo/ssd_mobilenet_v1_12
- Original: TensorFlow Model Zoo (Google)
  https://github.com/tensorflow/models
- Licence: Apache-2.0
- Copyright: Copyright Google LLC
- Training data: MS COCO 2017 (annotations: CC-BY 4.0)

## Python Dependencies

### ONNX Runtime
- Source: https://pypi.org/project/onnxruntime/
- Licence: MIT
- Copyright: Copyright (c) Microsoft Corporation

### OpenCV (opencv-python-headless)
- Source: https://pypi.org/project/opencv-python-headless/
- Licence: Apache-2.0 (OpenCV library) + MIT (Python wrapper)
- Copyright: Copyright (C) 2000-2024, Intel Corporation; Willow Garage Inc.
- Note: Wheels ship with FFmpeg (LGPLv2.1) and other third-party
  libraries. Full third-party licence list:
  https://github.com/opencv/opencv-python/blob/master/LICENSE-3RD-PARTY.txt

### NumPy
- Source: https://pypi.org/project/numpy/
- Licence: BSD-3-Clause
- Copyright: Copyright (c) 2005-2024, NumPy Developers

### Pillow
- Source: https://pypi.org/project/pillow/
- Licence: MIT-CMU (HPND variant)
- Copyright: Copyright (c) 1997-2011 by Secret Labs AB;
  Copyright (c) 1995-2011 by Fredrik Lundh and contributors

## Datasets

### MS COCO
- Source: https://cocodataset.org/
- Annotations licence: CC-BY 4.0
- Attribution: Microsoft COCO: Common Objects in Context.
  Lin et al., 2014. https://arxiv.org/abs/1405.0312
- Note: Only model weights (trained on COCO) are used.
  No COCO images are stored, distributed, or displayed.

## opencv-python-headless Wheel Third-Party Dependencies

The opencv-python-headless binary wheel bundles compiled third-party
libraries under their respective licences. The authoritative list is at:
https://github.com/opencv/opencv-python/blob/master/LICENSE-3RD-PARTY.txt

Key bundled libraries:
- FFmpeg: LGPLv2.1 (dynamic linking, not modified)
- libjpeg-turbo: IJG/BSD
- libpng: libpng licence (BSD-like)
- zlib: zlib licence (BSD-like)
- libtiff: BSD-like

These are dynamically linked binary dependencies inside the wheel.
Our source code does not modify, recompile, or redistribute them
separately — they ship as part of the pre-built opencv-python-headless
PyPI wheel.
```

---

## 8. Config változások

### 8.1 `app/config.py` frissítés

Az AN-3B2B-1-ben már hozzáadott `BALL_DETECTION_MODEL_PATH` default értéke
frissítendő v1-re:

```python
BALL_DETECTION_MODEL_PATH: str = "app/ml_models/ssd_mobilenet_v1_12.onnx"
```

### 8.2 `.env.example` frissítés

```bash
# Ball detection (Phase 2B) — requires analysis worker + ONNX model
# BALL_DETECTION_ENABLED=false
# BALL_DETECTION_MODEL_PATH=app/ml_models/ssd_mobilenet_v1_12.onnx
# BALL_DETECTION_MODEL_URL=  # privát URL; ha üres, HF Hub fallback
```

---

## 9. AN-3B2B-1 code corrections

Az AN-3B2B-1-ben jelenleg v2 referenciák vannak — ezeket v1-re kell frissíteni:

| Fájl | Változás |
|---|---|
| `analysis_model_registry.py` | `detection_source`: `mobilenet_ssd_v2` → `mobilenet_ssd_v1`; `model_version`: `..._v2_...` → `ssd_mobilenet_v1_12_onnx`; docstring frissítés |
| `app/config.py` | `BALL_DETECTION_MODEL_PATH` default: `..._v2_...` → `ssd_mobilenet_v1_12.onnx` |
| `juggling_ball_detections` CHECK constraint | `mobilenet_ssd_v2` → `mobilenet_ssd_v1` (migration) |
| Tesztek | `detection_source` assert-ok frissítése |

Ez egy alembic migration: `ALTER TABLE ... DROP CONSTRAINT + ADD CONSTRAINT`
a `detection_source` CHECK-en.

---

## 10. Teljes fájllista

### Új fájlok (6)

| # | Fájl | Sor (becslés) | Leírás |
|---|---|---|---|
| 1 | `app/services/juggling/onnx_ball_detector.py` | ~80 | ONNX session wrapper + detect() |
| 2 | `app/services/juggling/frame_extractor.py` | ~40 | cv2 frame extraction |
| 3 | `app/tasks/juggling_analysis_task.py` | ~100 | Celery task: detect_ball_for_event |
| 4 | `app/api/api_v1/endpoints/juggling_admin_ball_detection.py` | ~70 | Admin trigger |
| 5 | `scripts/download_ml_models.py` | ~90 | Model download + SHA256 |
| 6 | `LICENSE-THIRD-PARTY.md` | ~80 | Third-party licence attribution |

### Módosított fájlok (8)

| # | Fájl | Változás |
|---|---|---|
| 1 | `app/celery_app.py` | +analysis queue, task route, include, rate limit |
| 2 | `app/api/api_v1/api.py` | +admin ball detection router |
| 3 | `app/config.py` | MODEL_PATH default v2→v1 |
| 4 | `app/services/juggling/analysis_model_registry.py` | v2→v1 references |
| 5 | `requirements.txt` | +opencv-python-headless>=4.8.0 |
| 6 | `.env.example` | +BALL_DETECTION_MODEL_URL |
| 7 | `alembic/versions/2026_06_18_1200_...` | CHECK constraint v2→v1 |
| 8 | `app/tests/test_juggling_ball_detection.py` | +admin trigger tesztek + v1 assertion frissítés |

### Új tesztek

| # | Teszt | Leírás |
|---|---|---|
| BDT-A-01 | Admin trigger → events_queued > 0 (mock Celery delay) |
| BDT-A-02 | Admin trigger nincs confirmed event → events_queued=0 |
| BDT-A-03 | Admin trigger nem admin user → 403 |
| BDT-A-04 | Admin trigger nem létező video → 404 |
| BDT-A-05 | Admin trigger BALL_DETECTION_ENABLED=False → 503 |
| BDT-T-01 | detect_ball_for_event task: mock ONNX → DB-ben detekció |
| BDT-T-02 | detect_ball_for_event task: mock ONNX no detection → no_ball_detected=True |
| BDT-T-03 | detect_ball_for_event task: meglévő detekció → skip (idempotens) |
| BDT-T-04 | detect_ball_for_event task: model file nem létezik → FAILED |
| BDT-T-05 | detect_ball_for_event task: video file nem létezik → FAILED |
| BDT-FR-01 | extract_frame_at_ms: mock cv2 → helyes frame |
| BDT-FR-02 | extract_frame_at_ms: érvénytelen path → ValueError |
| BDT-OD-01 | OnnxBallDetector.detect: mock session → helyes koordináta |
| BDT-OD-02 | OnnxBallDetector.detect: nincs sports_ball → None |
| BDT-OD-03 | OnnxBallDetector.detect: confidence < threshold → None |
| BDT-D-REG | registry v2→v1 assertion frissítés (meglévő BDT-D-01..04) |

**Összesen**: 15 új + 4 frissített = **19 teszt változás** (ÚJ: 15, MÓDOSÍTOTT: 4).

---

## 11. Implementációs sorrend (commit-bontás)

| Commit | Scope | Leírás |
|---|---|---|
| **C8** | Licence fix | `analysis_model_registry.py` v2→v1 + CHECK constraint migration + config fix |
| **C9** | LICENSE | `LICENSE-THIRD-PARTY.md` |
| **C10** | Dependency | `requirements.txt` + `opencv-python-headless` |
| **C11** | Frame extraction | `frame_extractor.py` + BDT-FR tesztek |
| **C12** | ONNX detector | `onnx_ball_detector.py` + BDT-OD tesztek |
| **C13** | Celery task | `juggling_analysis_task.py` + `celery_app.py` + BDT-T tesztek |
| **C14** | Admin endpoint | `juggling_admin_ball_detection.py` + router + BDT-A tesztek |
| **C15** | Model script | `scripts/download_ml_models.py` + `.env.example` |

Minden commit után: `python -m pytest` a releváns tesztek zöldjéhez.

---

## 12. CI elvárások

| Check | Hatás |
|---|---|
| Unit Tests | +15 új teszt |
| OpenAPI Snapshot | +1 route (admin trigger POST) → 905 összesen |
| iOS Build + Tests | Nincs hatás (backend-only) |
| Test Baseline | Frissítendő |
| ONNX model | **NEM szükséges CI-ban** — tesztek mock-olt session-t használnak |
| opencv-python-headless | CI-ban telepítve a `pip install -r requirements.txt`-ből |

---

## 13. Kockázatok

| # | Kockázat | Súlyosság | Mitigáció |
|---|---|---|---|
| 1 | SSD MobileNet v1 mAP (~23%) alacsony 360p-n | Közepes | confidence threshold hangolás + manual override POST + no_ball_detected flag |
| 2 | cv2.VideoCapture corrupt videón | Közepes | try/finally cap.release(); max_retries=1; FAILED state log |
| 3 | ONNX model nincs a disk-en deployment-kor | Közepes | BALL_DETECTION_ENABLED=False default; task explicit model file check |
| 4 | opencv-python-headless wheel FFmpeg LGPL | Alacsony | Dinamikus linkelés, nem módosított — LGPL compliant; dokumentálva LICENSE-THIRD-PARTY.md-ben |
| 5 | Admin trigger túl sok event egyszerre | Alacsony | rate_limit=30/m; Celery queue natural backpressure |

---

## 14. Nyitott kérdések

1. **SHA256 hash**: a `ssd_mobilenet_v1_12.onnx` fájl pontos SHA256 hash-jét a
   letöltéskor kell kalkulálni és hardcode-olni a scriptbe. Ez az implementáció
   első lépése (C15 commit).

2. **Admin auth guard pattern**: a `admin_biometric_review.py` milyen guard-ot
   használ? (Előzetes vizsgálat alapján: `get_current_admin_user` dependency vagy
   `UserRole.ADMIN` check.)

---

**AN-3B2B-2 implementáció nem indult el — külön jóváhagyás szükséges.**
