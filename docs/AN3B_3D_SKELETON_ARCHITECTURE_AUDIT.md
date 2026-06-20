# AN-3B Multi-Camera 3D Skeleton Architecture — Audit & Roadmap v4

**Date:** 2026-06-20
**Status:** Audit-only — implementáció kizárólag külön jóváhagyás után.

---

## Alapelv

A 3D pipeline nem épülhet LiDAR-ra. A célarchitektúra eszközfüggetlen, többkamerás rendszer, amelyben kizárólag RGB kamerák vesznek részt. A 3D rekonstrukció alapja: 2D jointok + kamerakalibráció + időszinkronizáció + többnézetes trianguláció.

---

## I. Szinkronizáció — Pontosított Modell

### A. Bizonytalansági források — elkülönítve

| # | Forrás | Kategória | Hatás | Megjegyzés |
|---|--------|-----------|-------|-----------|
| 1 | Audio offset estimation | statisztikai | ±0.02ms | Cross-correlation @48kHz; ez NEM a frame alignment pontossága |
| 2 | Videó encoder timestamp quantizáció | szisztematikus | ±0.5 frame (±16.7ms @30fps) | Codec-függő, nem befolyásolható |
| 3 | MP4 container PTS precision | szisztematikus | microsecond | Elhanyagolható |
| 4 | Eltérő FPS (30 vs 60) | strukturális | max 8.3ms nearest-neighbor | Nem hiba, hanem sampling limit |
| 5 | Variable frame rate (iPhone) | stochasztikus | ±5-15ms per frame | iPhone VFR low-light-ban gyakori |
| 6 | Dropped frame | diszkrét | 33ms gap per dropped frame | PTS gap-ként detektálható |
| 7 | Clock drift (5 perc felvétel) | lineáris | 6-15ms (20-50ppm) | Lineáris modellel korrigálható |
| 8 | Rolling shutter skew | szisztematikus | ≤5ms frame-en belüli | Joint-szintű, nem korrigálható runtime |

**Fontos:** az audio cross-correlation az **audio offset estimation** pontossága. A videó-frame alignment pontossága a fenti 2-8 bizonytalanságok összege, tipikusan ±16-35ms tartományban.

### B. Mérhető acceptance gate-ek

| Gate | Metrika | Target | Mérés |
|------|---------|--------|-------|
| SY-G1 | Initial offset error (audio sync után) | < 16ms | Audio cross-correlation + vizuális ellenőrzés |
| SY-G2 | Drift error / perc | < 3ms/perc | Záró audio clap → Δoffset / duration |
| SY-G3 | Matched frame timestamp error (median) | < 20ms | Frame-párosítás timestamp különbség mediánja |
| SY-G4 | Matched frame timestamp error (p95) | < 35ms | 95. percentilis |
| SY-G5 | Frame mismatch rate | < 2% | Azon frame-párok aránya ahol error > 1 frame |
| SY-G6 | Dropped frame detection | 100% detected | PTS gap > 1.5× expected interval |

### C. Synchronization metadata contract

```python
class SyncMetadata(BaseModel):
    session_id:           uuid.UUID
    sync_method:          Literal["audio_clap", "software_start", "manual"]
    initial_offset_ms:    float
    drift_rate_ms_per_s:  float
    sync_reference_start_ns: Optional[int]   # nullable: ha nincs záró referencia
    sync_reference_end_ns:   Optional[int]
    matched_frame_count:  int
    dropped_frame_count:  int
    median_alignment_ms:  float
    p95_alignment_ms:     float
    sync_quality:         Literal["high", "acceptable", "degraded", "failed"]
```

---

## II. Canonical Skeleton Schema

### A. Kanonikus jointnév-lista — 17 triangulálható + 2 szintetikus

| # | Canonical name | Apple Vision source | MediaPipe source | Triangulálható |
|---|---------------|--------------------|--------------------|-------------|
| 0 | `nose` | `.nose` → `"nose"` | `NOSE` (idx 0) | ✅ |
| 1 | `left_eye` | `.leftEye` → `"left_eye"` | `LEFT_EYE` (idx 2) | ✅ |
| 2 | `right_eye` | `.rightEye` → `"right_eye"` | `RIGHT_EYE` (idx 5) | ✅ |
| 3 | `left_ear` | `.leftEar` → `"left_ear"` | `LEFT_EAR` (idx 7) | ✅ |
| 4 | `right_ear` | `.rightEar` → `"right_ear"` | `RIGHT_EAR` (idx 8) | ✅ |
| 5 | `neck` | `.neck` → `"neck"` (natív) | Nincs — **szintetikus** | ⚠️ |
| 6 | `left_shoulder` | `.leftShoulder` → `"left_shoulder"` | `LEFT_SHOULDER` (idx 11) | ✅ |
| 7 | `right_shoulder` | `.rightShoulder` → `"right_shoulder"` | `RIGHT_SHOULDER` (idx 12) | ✅ |
| 8 | `left_elbow` | `.leftElbow` → `"left_elbow"` | `LEFT_ELBOW` (idx 13) | ✅ |
| 9 | `right_elbow` | `.rightElbow` → `"right_elbow"` | `RIGHT_ELBOW` (idx 14) | ✅ |
| 10 | `left_wrist` | `.leftWrist` → `"left_wrist"` | `LEFT_WRIST` (idx 15) | ✅ |
| 11 | `right_wrist` | `.rightWrist` → `"right_wrist"` | `RIGHT_WRIST` (idx 16) | ✅ |
| 12 | `root` | `.root` → `"root"` (natív) | Nincs — **szintetikus** | ⚠️ |
| 13 | `left_hip` | `.leftHip` → `"left_hip"` | `LEFT_HIP` (idx 23) | ✅ |
| 14 | `right_hip` | `.rightHip` → `"right_hip"` | `RIGHT_HIP` (idx 24) | ✅ |
| 15 | `left_knee` | `.leftKnee` → `"left_knee"` | `LEFT_KNEE` (idx 25) | ✅ |
| 16 | `right_knee` | `.rightKnee` → `"right_knee"` | `RIGHT_KNEE` (idx 26) | ✅ |
| 17 | `left_ankle` | `.leftAnkle` → `"left_ankle"` | `LEFT_ANKLE` (idx 27) | ✅ |
| 18 | `right_ankle` | `.rightAnkle` → `"right_ankle"` | `RIGHT_ANKLE` (idx 28) | ✅ |

### B. Szintetikus jointok (neck, root)

- **Apple Vision:** natívan detektálja — `is_synthetic = false`
- **MediaPipe:** nem adja → szintetikus:
  - `neck` = `(left_shoulder + right_shoulder) / 2` — `is_synthetic = true`
  - `root` = `(left_hip + right_hip) / 2` — `is_synthetic = true`
  - Confidence: `min(left_confidence, right_confidence) * 0.9`
  - Ha bármelyik forrás-joint hiányzik → szintetikus joint NULL

### C. Confidence szemantika

Az Apple Vision `confidence` és a MediaPipe `visibility` **nem azonos statisztikai jelentésűek**:

| | Apple Vision | MediaPipe BlazePose |
|---|---|---|
| Mező neve | `confidence` | `visibility` |
| Tartomány | [0.0, 1.0] | [0.0, 1.0] |
| Jelentés | Joint detection confidence | Joint visibility (takarás-valószínűség) |
| Összehasonlíthatóság | ❌ nem direkt | ❌ nem direkt |

**PR-4A szabályok:**
1. A raw source confidence **mindig megőrzendő** `source_confidence` mezőben
2. A `image_confidence` mező a source-ból származik, **nem normalizált, nem kalibrált**
3. PR-4A-ban **nincs globális küszöbérték** (nem alkalmazunk közös 0.3 thresholdot cross-model)
4. Model-specifikus threshold config **előkészítve** (de nem alkalmazva):

```python
class PoseModelConfig(BaseModel):
    model_id:              str    # "apple_vision_body_pose_v1"
    confidence_field_name: str    # "confidence" or "visibility"
    default_threshold:     float  # 0.3 for Vision, TBD for MediaPipe
    threshold_calibrated:  bool   # False until empirically validated
```

### D. Left/right konvenció

Bal/jobb mindig az **alany anatomiai perspektívájából** értendő. Apple Vision és MediaPipe egyaránt ezt a konvenciót követi. A kamera tükrözése nem befolyásolja a joint naming-et.

### E. Auditálhatóság — teljes source trace

```json
{
  "canonical_joint_name": "left_shoulder",
  "source_model": "apple_vision_body_pose_v1",
  "source_joint_name": "leftShoulder1",
  "source_confidence": 0.95,
  "image_x": 0.41,
  "image_y": 0.83,
  "image_confidence": 0.95,
  "is_synthetic": false
}
```

---

## III. GoPro HERO12 Geometriai Stabilitás

### A. Capture mode audit

| Mód | FOV | Digitális korrekció | Kalibráció stabilitása | MVP |
|-----|-----|--------------------|-----------------------|-----|
| **Linear** | ~87° | Igen — firmware-szintű undistort | ✅ stabil, kalibrálható | **MVP preset** |
| Wide | ~122° | Minimális | Kalibrálható, erős k1/k2 | Nem MVP |
| SuperView | ~155° | Aszimmetrikus crop + stretch | Nem megbízhatóan kalibrálható | **TILTOTT** |
| HyperView | ~177° | Extrém dinamikus korrekció | Nem kalibrálható | **TILTOTT** |

**Megjegyzés:** a Linear mód maga is digitálisan korrigált kép. Az MVP-ben ez elfogadott, mert a korrekció **statikus** (azonos firmware + preset mellett determinisztikus). A tiltás nem a digitális lens correction általában, hanem a **nem kalibrált vagy dinamikusan változó** geometriai transzformáció.

### B. Stabilizáció és dinamikus transzformációk

| Feature | Miért tiltott az MVP-ben | Szabály |
|---------|------------------------|---------|
| HyperSmooth (bármelyik szint) | Frame-onként eltérő digitális crop → intrinsic instabil | **OFF kötelező** |
| Horizon Lock | Folyamatos forgatás → extrinsic invalid | **OFF kötelező** |
| Dinamikus lens correction | Runtime-ban változó undistort → kalibráció érvénytelen | **Nem használható** |

### C. MVP capture preset

```
GoPro HERO12 MVP Preset:
  Resolution:     1080p (1920×1080)
  FPS:            30
  Lens:           Linear
  HyperSmooth:    OFF
  Horizon Lock:   OFF
  Protune:        ON
  Bitrate:        High
  Audio:          ON (sync referencia)
```

**Invariáns:** a kalibráció kizárólag erre a presetre érvényes. Ha a felbontás, FPS, lens mode, crop vagy stabilizáció változik a session során → a kalibráció érvénytelen, a session elvetendő.

### D. Rolling shutter

| Kamera | Readout time | Hatás |
|--------|-------------|-------|
| GoPro HERO12 | ~8-10ms | Gyors mozgásnál joint-szintű skew |
| iPhone (rear) | ~5-8ms | Kisebb, de jelen van |

Kezelés: nem korrigáljuk (IMU adatot igényelne). A joint-level confidence degradációval jelöljük.

---

## IV. Kalibrációs MVP

### A. Setup

- 2 kamera, fix állványon, ~60-90° szög
- 3-5m a kalibrációs területtől
- ChArUco board: A3, 7×5, 40mm, DICT_6X6_250

### B. Workflow

```
1. Intrinsic (kameránként):
   - Minimum 15 kép, különböző szögek/távolságok, frame szélein is
   - cv2.calibrateCamera() → K (3×3), dist (5-elem vektor)
   - Gate: CA-G1 (reproj error < 0.5px)

2. Extrinsic (közös felvétel):
   - Minimum 10 képpár ahol mindkét kamera látja a boardot
   - cv2.stereoCalibrate() → R (3×3), t (3×1), F (3×3), E (3×3)
   - Gate: CA-G2 (stereo reproj error < 1.0px)

3. Mentés: calibration_id, per-camera K+dist+image_size,
   stereo R+t+F+E, reprojection_error, capture_preset, calibrated_at
```

### C. Gate-ek

| Gate | Metrika | Target |
|------|---------|--------|
| CA-G1 | Intrinsic reproj error (per camera) | < 0.5px |
| CA-G2 | Stereo reproj error | < 1.0px |
| CA-G3 | Extrinsic repeatability (2 independent calibration) | R delta < 0.5°, t delta < 1cm |
| CA-G4 | Session-start control point check | < 3px reprojection |

### D. Kalibráció-érvényesség

**Elsődleges ellenőrzés (session-start, kötelező):**
1. ChArUco vagy control-point reprojection < CA-G4 target
2. Calibration profile capture preset == aktuális kamera preset
3. Kameraazonosító + felbontás egyezés

**Másodlagos jelzések (runtime, warning-only):**
- Reprojection error növekedés a triangulált jointokban
- Bone-length CV > 10% 30 frame-es ablakban
- Joint dropout arány növekedés

A bone-length instability **nem elsődleges kameraelmozdulás-bizonyíték** — lehet a játékos mozgásából, takarásból, vagy detekciós zavarból is.

### E. Újrakalibráció szükséges

- Kamera fizikai elmozdulása
- Preset változás (felbontás, FPS, lens mode)
- CA-G4 session-start check failure

### F. Kizárt az MVP-ből

Mozgó kamera, Structure-from-Motion, visual odometry, self-calibration, automatikus extrinsic recovery.

---

## V. Koordinátarendszer — Matematikai Definíció

### A. World coordinate system

```
Típus:              Right-handed Cartesian
Origó:              Camera A (iPhone) optikai centruma
Mértékegység:       méter

Tengelyek:
  +X:  jobbra (Camera A image plane-jén balról jobbra)
  +Y:  felfelé (Camera A image plane-jén alulról felfelé)
  +Z:  előre, a jelenet irányába (Camera A optikai tengelye mentén,
       a kamerából a megfigyelt tér felé)

Ez az OpenCV standard camera coordinate convention:
  - Camera looks along +Z
  - Image u-axis maps to +X
  - Image v-axis maps to -Y (image origin top-left → Y flipped)
```

### B. Image coordinate system

```
Típus:              Normalized [0, 1]
Origó:              bal felső sarok
  image_x:  [0.0, 1.0]  — 0 = bal szél, 1 = jobb szél
  image_y:  [0.0, 1.0]  — 0 = felső szél, 1 = alsó szél

Pixel ↔ Normalized:
  image_x = pixel_x / image_width
  image_y = pixel_y / image_height
```

### C. Camera projection convention

```
Projection:  x_pixel = K @ [R|t] @ X_world
  K: 3×3 intrinsic matrix
  [R|t]: 3×4 extrinsic (Camera A: [I|0], Camera B: [R_AB|t_AB])
  X_world: [X, Y, Z, 1]ᵀ homogeneous world point

Inverse (3D reconstruction):
  cv2.triangulatePoints(P_A, P_B, pts_A, pts_B) → X_homogeneous
  X_world = X_h[:3] / X_h[3]
```

---

## VI. Licence Audit — Artefaktum-szintű

### A. Apple Vision Body Pose

| Réteg | Elem | Licenc | Commercial | Redisztribúció |
|-------|------|--------|-----------|---------------|
| Framework | Vision.framework | Apple SDK (proprietary) | ✅ | N/A (OS része) |
| Internal model | VNDetectHumanBodyPoseRequest | Apple (nem külön) | ✅ | N/A |
| Training data | Nem publikus | — | — | — |

Nincs attribution kötelezettség. 0 extra dependency.

### B. MediaPipe BlazePose

| Réteg | Elem | Licenc | Commercial | Redisztribúció |
|-------|------|--------|-----------|---------------|
| Source code | `google/mediapipe` repo | Apache-2.0 | ✅ | ✅ (NOTICE + LICENSE) |
| Architektúra | BlazePose GHUM topology | Apache-2.0 | ✅ | ✅ |
| Weights (TFLite) | `pose_landmark_lite.tflite` | Apache-2.0 | ✅ | ✅ app bundle-ben |
| Weights source | TF Hub / MediaPipe release assets | Apache-2.0 | ✅ | Hivatkozás szükséges |
| Training data | COCO (részben) + Google internal | **Nem publikus részletesen** | — | — |
| ONNX konverzió | `tf2onnx` (MIT) | MIT | ✅ | ✅ |

**Training data bizonytalanság:** a Google a BlazePose modelljét részben COCO (CC BY 4.0, commercial OK), részben nem publikus belső adatokkal tanította. A weights Apache-2.0 alatt redisztribúlhatók — a training data licenc a weights felhasználását nem korlátozza a kibocsátó (Google) eredeti licence alapján.

**Attribution:** Apache-2.0 LICENSE + NOTICE fájl szükséges az app bundle-ben.

### C. RTMPose (OpenMMLab)

| Réteg | Elem | Licenc | Commercial | Redisztribúció |
|-------|------|--------|-----------|---------------|
| Source code | `open-mmlab/mmpose` | Apache-2.0 | ✅ | ✅ |
| Weights (ONNX) | Model Zoo assets | Apache-2.0 (model zoo licence) | ✅ | ✅ |
| Weights source | OpenMMLab Model Zoo release | Apache-2.0 | ✅ | — |
| Training data | COCO (CC BY 4.0) + AIC (kutatási) + MPII (kutatási) | **Vegyes** | — | — |

**Training data bizonytalanság:** AIC és MPII kutatási licencűek. A weights Apache-2.0 alatt publikáltak. Az ML community standard értelmezés szerint a weights redisztribúciója a weights saját licencétől függ, nem a training data licencétől. Ennek ellenére ez **nem jogilag tesztelt terület** — magasabb kockázat mint a MediaPipe.

### D. OpenPose

**KIZÁRVA.** Custom non-commercial licence — semmilyen formában nem használható.

### E. OpenCV

Apache-2.0. Már a projektben (`opencv-python-headless>=4.8.0`).

### F. GPMF Parser

BSD-2-Clause / MIT (dual licence). Commercial OK.

### Senior modellválasztás

- **iPhone (on-device):** Apple Vision — 0 dependency
- **GoPro frame-ek (backend):** MediaPipe BlazePose Lite — Apache-2.0, 3 MB
- **PR-4A-ban:** nincs modell weights a repositoryban. Csak mapping contract.

---

## VII. Data Contract — Végleges

### A. Skeleton3DJoint — per-joint record

```python
class Skeleton3DJoint(BaseModel):
    canonical_joint_name:  str        # "left_shoulder"
    source_joint_name:     str        # "leftShoulder1" / "LEFT_SHOULDER"
    source_model:          str        # "apple_vision_body_pose_v1"
    source_confidence:     float      # Raw model output, [0,1]
    image_x:               float      # [0,1] normalized, left=0 right=1
    image_y:               float      # [0,1] normalized, top=0 bottom=1
    image_confidence:      float      # = source_confidence (PR-4A: no calibration)
    is_synthetic:          bool       # True for computed neck/root from MediaPipe

    # 3D world — all nullable (NULL when no triangulation)
    world_x:               Optional[float]  # meters, +X right
    world_y:               Optional[float]  # meters, +Y up
    world_z:               Optional[float]  # meters, +Z forward
    world_confidence:      Optional[float]  # [0,1] from reprojection error
    reprojection_error_px: Optional[float]  # mean reprojection in all views
    source_view_ids:       List[str]        # [] if single_view, ["cam_a","cam_b"] if triangulated
    triangulation_status:  str              # enum below
```

`triangulation_status`: `"triangulated"` | `"single_view_only"` | `"below_confidence"` | `"joint_missing"`

### B. Skeleton3DFrame — per-frame, per-person record

```python
class Skeleton3DFrame(BaseModel):
    schema_version:          str = "2"
    session_id:              uuid.UUID
    capture_id:              uuid.UUID
    camera_id:               str
    calibration_id:          Optional[uuid.UUID]   # null pre-calibration
    frame_id:                uuid.UUID
    source_timestamp_ns:     int                    # device-local monotonic
    synchronized_timestamp_ns: Optional[int]        # null pre-sync
    person_id:               int = 0
    joints:                  List[Skeleton3DJoint]
    coordinate_system:       str = "camera_a_origin_rh_meters"
    triangulation_method:    Optional[str]           # "dlt_two_view" | null
    processing_version:      str
```

### C. Calibration DTOs

```python
class IntrinsicCalibrationDTO(BaseModel):
    camera_id:            str
    intrinsic_matrix:     List[List[float]]     # 3×3 ([[fx,0,cx],[0,fy,cy],[0,0,1]])
    distortion_coeffs:    List[float]            # [k1,k2,p1,p2,k3]
    image_width_px:       int
    image_height_px:      int
    reprojection_error:   float
    capture_preset:       CapturePresetDTO

class CapturePresetDTO(BaseModel):
    resolution:           str                    # "1920x1080"
    fps:                  int
    lens_mode:            str                    # "linear"
    stabilization:        str                    # "off"

class StereoCalibrationDTO(BaseModel):
    camera_a_id:          str
    camera_b_id:          str
    rotation_matrix:      List[List[float]]      # 3×3
    translation_vector:   List[float]            # [tx,ty,tz] meters
    fundamental_matrix:   List[List[float]]      # 3×3
    essential_matrix:     List[List[float]]      # 3×3
    reprojection_error:   float
    calibration_id:       uuid.UUID
```

---

## VIII. Backward Compatibility — v1 → v2

### A. Forrás struktúra (v1)

A jelenlegi v1 `PoseKeypointsDTO` (iOS) / `juggling_pose_snapshots.keypoints` (backend):

```json
{
  "schema_version": "1",
  "body": [
    {"name": "left_shoulder", "x": 0.41, "y": 0.83, "confidence": 0.95}
  ],
  "left_hand": [],
  "right_hand": []
}
```

### B. Adapter szabályok — v1 → v2

Külön `LegacySkeletonAdapter` service, **NEM** a v2 decoder belsejébe rejtve:

| v1 mező | v2 mező | Átalakítás |
|---------|---------|-----------|
| `body[].name` | `canonical_joint_name` | Direkt mapping (a v1 nevek már canonical formátumúak) |
| `body[].name` | `source_joint_name` | Eredeti Vision rawValue (rekonstruálva: `jointNameMap` inverz) |
| — | `source_model` | `"apple_vision_body_pose_v1"` (fix, mert v1 csak Vision-ból jön) |
| `body[].confidence` | `source_confidence` | Direkt átmásolás |
| `body[].confidence` | `image_confidence` | Direkt átmásolás |
| `body[].x` | `image_x` | Direkt (v1 és v2 azonos: [0,1] screen-norm) |
| `body[].y` | `image_y` | Direkt (v1 és v2 azonos) |
| — | `is_synthetic` | `false` (Vision natívan detektálja a neck/root-ot) |
| — | `world_x/y/z` | `null` (v1 kizárólag 2D) |
| — | `world_confidence` | `null` |
| — | `reprojection_error_px` | `null` |
| — | `source_view_ids` | `[]` |
| — | `triangulation_status` | `"single_view_only"` |
| — | `camera_id` | `"iphone_primary"` (default, v1-ben nincs camera_id) |
| — | `capture_id` | Generált UUID |
| — | `session_id` | Generált UUID |
| — | `calibration_id` | `null` |
| — | `source_timestamp_ns` | `timestamp_ms * 1_000_000` |
| — | `synchronized_timestamp_ns` | `null` |
| — | `person_id` | `0` |
| hiányzó `schema_version` | `schema_version` | `"1"` (default) |

### C. Invariánsok

1. A meglévő `PoseKeypointsDTO` (iOS) és `PoseSnapshotOut` (backend) **semmilyen módon nem változik**
2. A `LegacySkeletonAdapter` egy **read-only adapter** — a v1 adatokat v2 formátumba konvertálja lekérdezéskor
3. Az adapter **nem ír** a v1 táblákba és **nem módosítja** a meglévő rekordokat

---

## IX. Acceptance Gate-ek — PR-4A előtt rögzítve

### Kalibráció (Phase 1 POC)

| CA-G1 | Intrinsic reproj error | < 0.5px |
| CA-G2 | Stereo reproj error | < 1.0px |
| CA-G3 | Extrinsic repeatability | R < 0.5°, t < 1cm |
| CA-G4 | Session-start check | < 3px |

### Szinkronizáció (Phase 2 POC)

| SY-G1 | Initial offset | < 16ms |
| SY-G2 | Drift / perc | < 3ms/perc |
| SY-G3 | Median alignment | < 20ms |
| SY-G4 | p95 alignment | < 35ms |
| SY-G5 | Mismatch rate | < 2% |
| SY-G6 | Dropped detection | 100% |

### Trianguláció (Phase 3 POC)

| TR-G1 | Valid triangulated ratio | > 70% |
| TR-G2 | Reproj error p50 | < 3px |
| TR-G3 | Reproj error p95 | < 8px |
| TR-G4 | Bone-length CV | < 5% |
| TR-G5 | Temporal jitter (static) | < 2cm RMS |
| TR-G6 | Joint dropout | < 15% |
| TR-G7 | Control point 3D error | < 5cm |

---

## X. Fejlesztési Sorrend — Megerősítve

| Phase | Scope | Előfeltétel | Kockázat |
|-------|-------|-------------|---------|
| 1 | Multi-camera contract + calibration foundation | — | Alacsony |
| 2 | iPhone + GoPro synchronized capture POC | Phase 1 | Magas |
| 3 | Single-player two-view triangulation | Phase 1, 2 | Közepes |
| 4 | iPhone 3D skeleton viewer | Phase 1, 3 | Közepes |
| 5 | Multi-person identity tracking | Phase 3 | Magas |
| 6 | Two-player multi-camera 3D reconstruction | Phase 3, 5 | Magas |
| 7 | AR visualization | Phase 4 | Közepes |

---

## XI. PR-4A Implementációs Terv — Migration-Free Contract Foundation

### Scope

PR-4A kizárólag domain contract: Pydantic schemák, Swift Codable modellek, joint mapping, calibration/sync DTO-k, validation, legacy adapter, közös fixture-ek. **Nincs ORM, nincs migration, nincs API endpoint, nincs DB tábla.**

### Fájlok

**Backend — Új:**

| Fájl | Tartalom |
|------|----------|
| `app/schemas/skeleton_3d.py` | `Skeleton3DFrame`, `Skeleton3DJoint`, `IntrinsicCalibrationDTO`, `StereoCalibrationDTO`, `CapturePresetDTO`, `SyncMetadata`, `CanonicalJoint` enum, `PoseModelConfig` |
| `app/services/skeleton/joint_mapping.py` | `CanonicalJoint` enum (19), `APPLE_VISION_MAP`, `MEDIAPIPE_BLAZEPOSE_MAP`, `map_to_canonical()`, `synthesize_midpoint()`, `PoseModelConfig` registry |
| `app/services/skeleton/legacy_adapter.py` | `adapt_v1_to_v2()`: `PoseKeypointsDTO` dict → `Skeleton3DFrame`, source trace, null world |
| `app/services/skeleton/calibration_contract.py` | `validate_intrinsic()`, `validate_stereo()`, `validate_capture_preset()` — DTO validation rules |
| `app/services/skeleton/synchronization_contract.py` | `SyncMetadata` construction + validation, `validate_sync_quality()` |
| `app/services/skeleton/__init__.py` | Package init |

**iOS — Új:**

| Fájl | Tartalom |
|------|----------|
| `ios/LFAEducationCenter/Skeleton3D/CanonicalJoint.swift` | `CanonicalJoint` enum (19 case), `AppleVisionJointMapping` dict |
| `ios/LFAEducationCenter/Skeleton3D/Skeleton3DModels.swift` | `Skeleton3DFrame`, `Skeleton3DJoint`, `IntrinsicCalibrationDTO`, `StereoCalibrationDTO`, `CapturePresetDTO`, `SyncMetadata` — Codable structs |
| `ios/LFAEducationCenter/Skeleton3D/JointMapper.swift` | `AppleVisionJointMapper.mapToCanonical()`: Vision joints → canonical, synthetic neck/root |
| `ios/LFAEducationCenter/Skeleton3D/LegacySkeletonAdapter.swift` | `adaptV1ToV2()`: `PoseKeypointsDTO` → `Skeleton3DFrame` |

**Közös fixture-ek (Python tesztek + Swift tesztek azonos JSON):**

| Fixture | Tartalom |
|---------|----------|
| `tests/fixtures/skeleton_3d/frame_v2_full.json` | Teljes v2 frame, 19 joint, all world filled |
| `tests/fixtures/skeleton_3d/frame_v2_2d_only.json` | v2 frame, world_* = null, triangulation_status = single_view_only |
| `tests/fixtures/skeleton_3d/frame_v1_source.json` | Eredeti v1 PoseKeypointsDTO (schema_version: "1") |
| `tests/fixtures/skeleton_3d/frame_v1_adapted.json` | v1 → v2 adapter kimenet (expected output) |
| `tests/fixtures/skeleton_3d/calibration_intrinsic.json` | Szintetikus iPhone intrinsic |
| `tests/fixtures/skeleton_3d/calibration_stereo.json` | Szintetikus stereo R, t, F, E |
| `tests/fixtures/skeleton_3d/sync_metadata.json` | Teljes sync metadata |

### Tesztesetek

**Backend — joint_mapping (12 teszt):**

| # | Teszt |
|---|-------|
| JM-01 | Apple Vision 19 joint → 19 canonical (1:1 mapping) |
| JM-02 | MediaPipe 33 joint → 19 canonical (14 ignored, 2 synthetic) |
| JM-03 | Synthetic neck = shoulder midpoint, is_synthetic=true |
| JM-04 | Synthetic root = hip midpoint, is_synthetic=true |
| JM-05 | Synthetic confidence = min(left, right) * 0.9 |
| JM-06 | Missing source joint → synthetic joint NULL |
| JM-07 | source_model + source_joint_name preserved in output |
| JM-08 | source_confidence preserved (not transformed) |
| JM-09 | Model-specific threshold config exists (not applied) |
| JM-10 | Unknown source model → ValueError |
| JM-11 | Empty body → empty canonical (no crash) |
| JM-12 | CanonicalJoint enum has exactly 19 members |

**Backend — calibration_contract (8 teszt):**

| # | Teszt |
|---|-------|
| CS-01 | Valid intrinsic 3×3 K matrix |
| CS-02 | Wrong intrinsic shape → ValidationError |
| CS-03 | Valid distortion 5-element vector |
| CS-04 | Wrong distortion count → ValidationError |
| CS-05 | Reprojection error within gate → accepted |
| CS-06 | Reprojection error above gate → rejected |
| CS-07 | Stereo R (3×3), t (3×1), F (3×3), E (3×3) valid shapes |
| CS-08 | CapturePreset immutability check (round-trip) |

**Backend — legacy_adapter (8 teszt):**

| # | Teszt |
|---|-------|
| LA-01 | v1 full body → v2 frame with 19 joints |
| LA-02 | v1 source → camera_id = "iphone_primary" |
| LA-03 | v1 source → source_model = "apple_vision_body_pose_v1" |
| LA-04 | v1 source → all world_* = null |
| LA-05 | v1 source → triangulation_status = "single_view_only" |
| LA-06 | Missing schema_version → defaults to "1" |
| LA-07 | Empty body → valid v2 with 0 joints |
| LA-08 | Adapted output matches fixture (frame_v1_adapted.json) |

**Backend — skeleton_3d_contract (8 teszt):**

| # | Teszt |
|---|-------|
| S3D-01 | Full v2 JSON roundtrip (fixture) |
| S3D-02 | v2 with null world (2D-only fallback) |
| S3D-03 | triangulation_status enum coverage (4 values) |
| S3D-04 | coordinate_system validation |
| S3D-05 | session_id required (not nullable) |
| S3D-06 | calibration_id nullable |
| S3D-07 | person_id defaults to 0 |
| S3D-08 | source_view_ids empty when single_view_only |

**Backend — sync_contract (4 teszt):**

| # | Teszt |
|---|-------|
| SN-01 | SyncMetadata full construction from fixture |
| SN-02 | sync_quality derivation rules |
| SN-03 | Optional fields nullable (reference_end, p95) |
| SN-04 | Fixture JSON roundtrip |

**iOS — CanonicalJoint (8 teszt):**

| # | Teszt |
|---|-------|
| CJ-01 | 19 enum members (rawValue string match) |
| CJ-02 | Apple Vision full mapping (19 → 19) |
| CJ-03 | Synthetic neck from shoulders |
| CJ-04 | Synthetic root from hips |
| CJ-05 | Missing shoulder → no synthetic neck |
| CJ-06 | source_model preserved in Skeleton3DJoint |
| CJ-07 | source_confidence preserved (passthrough) |
| CJ-08 | v2 JSON decode from fixture (cross-platform parity) |

**iOS — LegacyAdapter (4 teszt):**

| # | Teszt |
|---|-------|
| LA-S-01 | v1 PoseKeypointsDTO → Skeleton3DFrame |
| LA-S-02 | All world fields nil |
| LA-S-03 | camera_id = "iphone_primary" |
| LA-S-04 | Adapted output matches Python fixture (frame_v1_adapted.json) |

### Cross-Platform Contract Proof

Azonos JSON fixture-eket használ a Python és a Swift tesztcsomag:

| Ellenőrzés | Módszer |
|------------|--------|
| Python encode → Swift decode | `frame_v2_full.json` Python-ból generált, Swift-ben decode-olt |
| Swift-kompatibilis → Python decode | Swift CodingKeys (camelCase) + Python alias (snake_case) |
| Enum értékek egyezés | `CanonicalJoint` 19 member, rawValue string-ek identikusak |
| snake_case/camelCase | Python: `canonical_joint_name`, Swift CodingKey: `"canonical_joint_name"` |
| int64 timestamp | `source_timestamp_ns` Python `int` ↔ Swift `Int64` veszteségmentes |
| UUID | Python `uuid.UUID` ↔ Swift `UUID` — string serialization egyezik |
| Null mezők | Python `None` ↔ Swift `nil` — JSON `null` mindkettőben |

### Tiltott scope

- ❌ ORM modell / SQLAlchemy model
- ❌ Alembic migration
- ❌ Adatbázistábla
- ❌ API endpoint
- ❌ GoPro BLE/WiFi kapcsolat
- ❌ Trianguláció engine
- ❌ 3D viewer
- ❌ Multi-person implementáció
- ❌ Modell weights a repositoryban
- ❌ Meglévő `PoseKeypointsDTO` módosítás
- ❌ Meglévő `PoseSnapshotService` módosítás
- ❌ Meglévő `juggling_pose_snapshots` tábla módosítás
- ❌ Globális cross-model confidence threshold alkalmazása

### Acceptance criteria

1. ✅ `CanonicalJoint` enum: 19 member, Python + Swift, rawValue string-ek identikusak
2. ✅ `map_to_canonical()`: Apple Vision + MediaPipe mapping, synthetic neck/root
3. ✅ `source_confidence` megőrzése (nem normalizált, nem kalibrált)
4. ✅ `PoseModelConfig` registry (model-specific threshold, not applied in PR-4A)
5. ✅ `LegacySkeletonAdapter`: v1 → v2, explicit mező-mapping, null world
6. ✅ Calibration DTO validation: K shape, dist count, preset immutability
7. ✅ Sync metadata DTO validation
8. ✅ 7 közös JSON fixture (Python ÉS Swift tesztelve)
9. ✅ Cross-platform proof: azonos fixture → Python encode/decode + Swift encode/decode
10. ✅ 52 teszt (12 JM + 8 CS + 8 LA + 8 S3D + 4 SN + 8 CJ + 4 LA-S) — 0 FAIL
11. ✅ iOS BUILD SUCCEEDED
12. ✅ Meglévő skeleton pipeline NEM módosult
13. ✅ Nincs ORM, nincs migration, nincs API endpoint

---

**Implementációt, branchet vagy PR-t külön jóváhagyás nélkül nem kezdünk.**
