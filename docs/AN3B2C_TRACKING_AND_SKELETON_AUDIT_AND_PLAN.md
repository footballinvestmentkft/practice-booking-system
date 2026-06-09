# AN-3B2C: Ball Tracking + Skeleton Overlay — Teljes Audit és Implementációs Terv

**Dátum:** 2026-06-17  
**Branch:** `feat/an3b2b-1-ball-detection`  
**Státusz:** Audit kész — implementáció jóváhagyás előtt NEM kezdhető el  

---

## 1. Executive Summary

A jelenlegi rendszer **kizárólag event-snapshot alapú**: mindkét feature (ball detection, skeleton overlay) csak azokhoz az időpontokhoz tárol adatot, ahol a user FAB-gombbal kontaktot jelölt. Sem folyamatos ball tracking, sem folyamatos skeleton overlay nem létezik. Ez fundamentális architektúrális gap, nem UI-bug.

A két Screenshot közötti vizuális különbség (kék vonalak + pontok vs. csak sárga pontok) két különböző renderert mutat, amelyek teljesen eltérő pipeline-t használnak.

---

## 2. Ball Detection — Teljes Audit

### 2.1 Jelenlegi architektúra (kódbizonyítékkal)

**Backend pipeline — `app/tasks/juggling_analysis_task.py`:**

```python
# Celery task — egy esemény egy frame
def run_ball_detection_core(video_id, event_id, training_video_type, db):
    frame_rgb, w, h = _extract_frame(vpath, event.timestamp_ms)  # OpenCV, EGYETLEN frame
    detector = _get_detector(model_path)
    result = detector.detect(frame_rgb, ...)  # SSD MobileNet v1, EGYETLEN inferencia
    # → 1 sor a juggling_ball_detections táblában
```

**Trigger — `app/api/api_v1/endpoints/juggling_admin_ball_detection.py`:**

```python
# CSAK admin manuálisan triggeri
# CSAK 'confirmed' státuszú eseményekre fut
# NEM automatikus, NEM event létrehozáskor fut
for event in confirmed_events:
    detect_ball_for_event.delay(str(video.id), str(event.id), ...)
```

**iOS megjelenítés — `JugglingAnnotationScreen.swift:892`:**

```swift
// Csak ±500ms ablakon belüli event-snapshot jelenik meg
private func closestBallDetection(toMs ms: Int) -> BallDetectionOut? {
    vm.activeEvents
        .compactMap { draft -> ...? in
            guard ... case .loaded(let d) = vm.ballDetections[serverId],
                  !d.noBallDetected,          // no_ball_detected=True → SKIP
                  d.ballX != nil, d.ballY != nil else { return nil }
            let dist = abs(draft.timestampMs - ms)
            guard dist <= 500 else { return nil }   // ±500ms ablak
            return (dist, d)
        }
        .min(by: { $0.distance < $1.distance })
        .map(\.detection)
}
```

**DB séma — `juggling_ball_detections`:**

```
contact_event_id  UUID  NOT NULL  ← mindig eventhez kötött
ball_x            FLOAT NULL
ball_y            FLOAT NULL
no_ball_detected  BOOL  NOT NULL
```

**Nincs** `frame_number`, `frame_timestamp_ms` independent from event, `trajectory_id` mező.

### 2.2 Jelenlegi adatok (DB állapot, 2026-06-17)

```
9 ball detection sor:
  549ms   → no_ball_detected=True  (model failed)
  1266ms  → no_ball_detected=True  (model failed)
  2066ms  → no_ball_detected=True  (model failed)
  2733ms  → no_ball_detected=False, conf=0.317  ← EGYETLEN sikeres detektálás
  3300ms  → no_ball_detected=True
  4033ms  → no_ball_detected=True
  4633ms  → no_ball_detected=True
  5133ms  → no_ball_detected=True
  5733ms  → no_ball_detected=True
```

Modell pontossága ezen a videón: **1/9 = 11%.** Ez nem szoftverhibáé — az SSD MobileNet v1 football labda detektálása alacsony minőségű videón (távolról, kis labda) gyenge.

### 2.3 Mi működik

- Manuális ball position POST (`vm.postManualBallPosition`) ✓
- Optimistic update az iOS oldalon ✓
- Ball marker megjelenése ±500ms-on belüli eventeknél ✓
- `no_ball_detected=True` eventi badge a timelineon ✓
- "Megjelölöm" gomb (main-screen manual selection) ✓
- Status banner informatív szöveggel ✓

### 2.4 Mi nem működik / product gap

| Gap | Technikai ok |
|-----|-------------|
| Labda automatikus felismerése videón | Csak admin trigger, csak confirmed eventeknél |
| Labda folyamatos követése | Nincs tracker; csak event-snapshot |
| Manual kijelölés után tracking | A POST 1 pontot ment, nincs downstream tracking |
| Ball marker frame-by-frame | Nincs `juggling_ball_trajectories` tábla |
| ±500ms-on kívüli megjelenítés | `closestBallDetection` kemény ±500ms limit |

### 2.5 Mit tud az SSD MobileNet v1 modell

```python
# app/services/juggling/onnx_ball_detector.py
class OnnxBallDetector:
    def detect(self, frame_rgb, target_class_id=37, confidence_threshold=0.3):
        # → COCO class 37 = "sports ball"
        # → egyetlen frame, egyetlen inferencia
        # → nincs frame-közötti összefüggés
        # → nincs temporal context
        # → nincs tracker
```

Az SSD MobileNet v1 **single-frame object detector**, nem tracker. Képes:
- Megtalálni egy labdát egy képen, ha elég nagy és kontrasztos
- 300×300 px bemeneten dolgozik (az eredeti kép lereszeléssel)
- ~20ms/frame CPU-n (backend)

Nem képes:
- Kis labda (<20px) megbízható detektálása
- Gyors mozgás (motion blur) kezelése
- Frame-közötti tracking
- Okklúzió kezelése (ha a labda nem látszik)

### 2.6 Javasolt architektúra — Folyamatos ball tracking

#### Opció A: Backend dense sampling + lightweight tracker (AJÁNLOTT első lépésként)

```
Video upload → Celery task: dense_ball_trajectory_task
  → OpenCV: extract frame minden ~100ms-ban (60 frame/6s)
  → SSD MobileNet v1: detect per frame
  → SORT tracker (scipy, ~2ms overhead): frame-közötti összefüggés
  → juggling_ball_trajectories tábla: (video_id, frame_ms, ball_x, ball_y, conf, track_id)
```

**Előny:** Meglévő infrastruktúra, Python, backend.  
**Hátrány:** ~3-5 sec batch feldolgozás; nem real-time.

#### Opció B: YOLOv8 csere

- YOLOv8n + `ByteTrack` beépített tracker
- ~30ms/frame CPU-n (nehezebb)
- Lényegesen jobb pontosság kis labdán
- Közel 0% false negative, ha labda látható

**Előny:** Sokkal jobb accuracy.  
**Hátrány:** Modell ~6 MB (vs 28 MB jelenlegi) — de jobb; PyPI csomag szükséges.

#### Opció C: iPhone-oldalon Apple Vision / CoreML (NEM javasolt elsőnek)

- CoreML model (pl. konvertált YOLOv8) az iPhonen
- AVAssetReader → frame-by-frame → on-device inference
- ~100ms/frame iPhone on-device = 6 sec video → 60 sec feldolgozás
- Offline, no backend cost

**Hátrány:** Lassú iOS-on; model disztribúció (app update); battery impact.

#### Javasolt sorrend

1. **AN-3B2C-3 (approved PR bontásban):** Backend dense sampling, meglévő SSD modellel, SORT tracker, új `juggling_ball_trajectories` tábla
2. **AN-3B2D:** YOLOv8 modell csere, ha az accuracy nem elégséges
3. **AN-3B2E:** iOS on-device inference, ha offline tracking kell

### 2.7 Szükséges DB változások (Opció A)

```sql
CREATE TABLE juggling_ball_trajectories (
    id                UUID PRIMARY KEY,
    video_id          UUID NOT NULL REFERENCES juggling_videos(id),
    frame_ms          BIGINT NOT NULL,          -- független az eventtől
    ball_x            FLOAT,
    ball_y            FLOAT,
    confidence        FLOAT,
    track_id          INTEGER,                  -- SORT tracker sáv ID
    detection_source  VARCHAR NOT NULL,         -- 'auto_dense', 'manual_correction'
    model_version     VARCHAR,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON juggling_ball_trajectories(video_id, frame_ms);
```

A manuális korrekció (`manual_correction`) felülírhatja az auto pontot az adott `frame_ms`-nél.

### 2.8 Performance / battery / storage becslés

| Metric | Opció A (dense backend) |
|--------|-------------------------|
| Backend CPU/frame | ~20ms (SSD) + ~2ms (SORT) |
| 6s video teljes feldolgozás | ~3-4 sec |
| Storage per video | ~60 sor × ~50 byte = ~3 KB |
| Backend memory | ~150 MB (ONNX session) — megosztott |
| iOS battery impact | Nulla (backend dolgozik) |
| Celery queue | Új `trajectory` queue, 1 worker elegendő |

---

## 3. Skeleton / Pose Overlay — Teljes Audit

### 3.1 Jelenlegi architektúra

**iOS capture — `PoseSnapshotService.swift`:**

```swift
// FAB tap → captureAndUpload → AVAssetImageGenerator → VNDetectHumanBodyPoseRequest
// EGYETLEN frame extraction az event timestamp-jénél
static func captureAndUpload(at timestampMs: Int, ...) async {
    let (cgImage, imageSize) = await extractFrame(from: asset, atMs: timestampMs)
    // → VNDetectHumanBodyPoseRequest: 19 joint, confidence ≥ 0.3
    // → upload: 1 sor a juggling_pose_snapshots táblában
}
```

**iOS display — `PoseSnapshotOverlayView.swift`:**

```swift
// Megjelenítés: ±500ms ablak (azonos a ball detection ablakkal)
private func closestSnapshot(toMs ms: Int) -> PoseSnapshotOut? {
    let best = poseSnapshots.min(by: { abs($0.timestampMs - ms) < ... })!
    return abs(best.timestampMs - ms) <= 500 ? best : nil
}
```

**DB állapot:**

```
11 pose snapshot:
  9 db: video 319eb833, forrás: ios_retroactive
  2 db: video ef56f15a, forrás: ios_realtime
```

### 3.2 A két screenshot összehasonlítása

| | Screenshot 1 (kék vonalak) | Screenshot 2 (sárga pontok) |
|---|---|---|
| **Screen** | Biometric Spike / live kamera test | JugglingAnnotationScreen |
| **Pipeline** | AVCaptureSession real-time | Tárolt event snapshot |
| **FPS** | 29 (live!) | N/A (statikus overlay) |
| **Joints** | 19 (Vision API) | 19 (Vision API, tárolt) |
| **Bone lines** | Igen ✓ (külön renderer) | **NEM volt ✗ → MOST JAVÍTVA** |
| **Confidence threshold** | Ismeretlen (spike kód) | 0.3 (PoseSnapshotService) |
| **Frame-by-frame** | Igen (continuous) | Nem (event snapshot only) |
| **Renderer** | CALayer / CGContext (feltételezhetően) | SwiftUI `PoseSnapshotOverlayView` |
| **Kód helye** | `Biometric/Spike/` (untracked) | `Annotation/Screen/PoseSnapshotOverlayView.swift` |

### 3.3 Miért látszottak csak pontok a második képen

**Root cause: SwiftUI `@ViewBuilder` multi-binding `if let` bug**

```swift
// RÉGI (HIBÁS) kód — PoseSnapshotOverlayView.boneLayer:
@ViewBuilder
private func boneLayer(byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat) -> some View {
    ForEach(Self.bones.indices, id: \.self) { i in
        let (aName, bName) = Self.bones[i]
        if let pa = byName[aName], let pb = byName[bName] {  // ← MULTI-BINDING: silently drops view
            Path { ... }.stroke(Color.white.opacity(0.85), lineWidth: 3)
        }
    }
}
```

A SwiftUI `@ViewBuilder` context-ben a `if let pa = ..., let pb = ...` multi-binding pattern csendesen `EmptyView`-t bocsát ki az összes bone-ra. A `ForEach` fut, de minden iteráció üres view.

```swift
// ÚJ (JAVÍTOTT) kód:
private func boneLayer(byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat) -> some View {
    Path { path in
        for (aName, bName) in Self.bones {
            guard let pa = byName[aName], let pb = byName[bName] else { continue }
            path.move(to:    CGPoint(x: CGFloat(pa.x) * w, y: CGFloat(pa.y) * h))
            path.addLine(to: CGPoint(x: CGFloat(pb.x) * w, y: CGFloat(pb.y) * h))
        }
    }
    .stroke(Color.white.opacity(0.85), lineWidth: 3)
}
```

**Státusz: JAVÍTVA** (commit: `feat/an3b2b-1-ball-detection` branch, aktuális HEAD). 520/520 test PASS.

### 3.4 Miért különbözik a live vs. video skeleton?

A Screenshot 1 (kék vonalak) egy **teljesen más screen**: a Biometric Spike (`Biometric/Spike/`) live kamerás test, amely:
- `AVCaptureSession` + `VNDetectHumanBodyPoseRequest` real-time, 29 FPS-en fut
- Valószínűleg `CALayer` vagy `CGContext` alapú renderert használ (nem SwiftUI `@ViewBuilder`)
- **Nincs kapcsolatban** a JugglingAnnotationScreen-nel
- A csontvázrajzolás `@ViewBuilder` bug nem érinti, mert más renderer

A JugglingAnnotationScreen skeleton-je **tárolt snapshot-okat** jelenít meg, nem live Vision processort. Ez fundamentálisan különböző architektúra.

### 3.5 Landmark coverage — Vision API limit

Az Apple Vision 2D Body Pose (`VNDetectHumanBodyPoseRequest`) pontosan 19 joint-ot ad:

```
Face:       nose, left_eye, right_eye, left_ear, right_ear
Upper body: neck, left_shoulder, right_shoulder, left_elbow, right_elbow, left_wrist, right_wrist
Lower body: root, left_hip, right_hip, left_knee, right_knee, left_ankle, right_ankle
```

**Nincs:** `left_foot`, `right_foot`, `left_toe`, `right_toe`, `spine_mid`, `chest`.  
A lábfej/lábujj **elérhetetlen** a 2D Vision API-val. 3D Vision (VNDetectHumanBodyPose3DRequest) iOS 17+ és közeli kamerát igényel.

Ez nem bug — ez platform korlát. Dokumentálva van a `PoseSnapshotOverlayView.swift` kommentjében.

### 3.6 Confidence threshold hatása

```swift
// PoseSnapshotService.swift:32
static let confidenceThreshold: Float = 0.3
```

Ha egy joint confidence-je < 0.3, nem kerül be az upload-ba és nem jelenik meg. Sportvideón (mozgás, kamera távolság, takarás) az elbow/wrist/ankle joint-ok könnyen esnek a küszöb alá, ezért "hiányos" skeletonok keletkeznek. Ez helyes viselkedés — a low-confidence joint megjelenítése félrevezető lenne.

### 3.7 Mi működik (skeleton)

- Vision body pose capture event-snapshot-ként ✓
- 19 joint tároló + megjelenítő pipeline ✓
- Confidence-alapú joint szín kódolás (sárga/narancs/piros) ✓
- **Bone lines renderelés MOST JAVÍTVA** (single Path, nem ForEach) ✓
- Retroactive pose generation már meglévő eventekre ✓

### 3.8 Mi nem működik / product gap (skeleton)

| Gap | Technikai ok |
|-----|-------------|
| Folyamatos skeleton overlay | Csak event-snapshot, nincs frame-by-frame data |
| Testtartás / mozgás analízis | Nincs trajectory adat |
| Smooth interpoláció snapshots között | Nincs közbülső adat |
| Lábfej / lábujj | Vision API 2D korlát |
| iOS 14 kompatibilis lábfej | Nem elérhető semmilyen Vision verzióban 2D-ben |

### 3.9 Javasolt architektúra — Folyamatos skeleton trajectory

#### Opció A: Backend OpenPose / MoveNet dense sampling

- MoveNet Lightning (TFLite/ONNX, ~5ms/frame) — 17 COCO joint
- Dense sampling: minden ~150ms (40 frame/6s)
- `juggling_pose_trajectories` tábla

#### Opció B: iOS Apple Vision dense sampling

- `AVAssetReader` → frame-by-frame → `VNDetectHumanBodyPoseRequest`
- ~100ms/frame → 6s video → 60 sec feldolgozás (nem elfogadható)

#### Opció C: Interpoláció a meglévő event-snapshots között (AJÁNLOTT mint első lépés)

- Ha 9 snapshot van (6 sec videóban, ~666ms-enként), lerp/slerp a joint pozíciók között
- Vizuálisan kielégítő, nincsenek "ugráló" joint-ok
- Nincs backend változás
- Nincs új DB tábla

**Sorrend:**
1. **AN-3B2C-2 (már tervben):** Interpoláció a meglévő snapshots között — olcsó, gyors
2. **AN-3B2D:** Backend MoveNet dense sampling, ha analitikához frame-level adat kell

---

## 4. Product Elvárás vs. Jelenlegi Valóság

| Elvárás | Valóság | Gap |
|---------|---------|-----|
| Labda automatikus felismerése videón | Admin trigger, confirmed event, Celery | Nem automatikus |
| Ha rendszer hibázik, user 1× jelöli | Megvalósítva (Megjelölöm gomb) ✓ | — |
| 1 jelölés után rendszer követi | NEM létezik tracker | Tracking hiány |
| Skeleton folyamatos | Csak ±500ms event-snapshot | Snapshot-only |
| Karok/váll/törzs/csípő/térd/boka látszik | Igen, Vision 19 joint ✓ | — |
| Lábfej/lábujj | Vision 2D API korlát | Platform limit |
| Skeleton vonalakkal | JAVÍTVA ✓ | — |
| Ball tracking alapja pitch calibrationnek | Nincs trajectory | Blocking gap |
| Skeleton alapja movement analyticsnek | Nincs trajectory | Blocking gap |

---

## 5. Javasolt PR Bontás (implementáció jóváhagyás után)

### AN-3B2C-2: Skeleton interpoláció (KÖNNYŰ)
- `closestSnapshot` → `interpolatedKeypoints(atMs:)` — lerp pozíciók a legközelebbi két snapshot között
- Nincs DB változás
- Nincs backend változás
- iOS only
- Vizuális hatás: sima, folyamatos skeleton mozgás

### AN-3B2C-3: Automatikus ball detection trigger (KÖZEPES)
- `POST /users/me/juggling/videos/{videoId}/request-ball-detection` user-facing endpoint
- Celery task automatikus triggerelése PATCH annotation_review_status='confirmed' után
- iOS: `bulkFetchBallDetections` polling amíg 404 → loading indikátor
- Nincs tracking még, csak automatikus trigger

### AN-3B2D: Dense ball trajectory (ÚJ ARCHITEKTÚRA)
- `juggling_ball_trajectories` tábla
- `dense_ball_trajectory_task` Celery task (minden ~100ms frame)
- SORT tracker integrálás
- iOS `BallTrajectoryStore` — trajectory-alapú megjelenítés
- Manuális korrekció → felülírja az adott frame_ms pontját

### AN-3B2E: MoveNet skeleton trajectory (ÚJ ARCHITEKTÚRA)
- `juggling_pose_trajectories` tábla
- Backend vagy iOS dense sampling
- `PoseTrajectoryOverlayView` — interpoláció nélkül, valódi frame-adat

---

## 6. Kockázatok

| Kockázat | Hatás | Mitigation |
|----------|-------|-----------|
| SSD MobileNet v1 pontossága ~11% | Ball detection közel használhatatlan auto módban | AN-3B2D-ben model csere (YOLOv8) |
| Dense backend sampling CPU cost | Celery worker overload | Külön `trajectory` queue, 1 worker |
| iOS Vision dense sampling lassú (~100ms/frame) | 60s feldolgozás 6s videóra | Backend delegálás |
| `download_ml_models.py` felülírja az adaptált ONNX modellt | Ball detection leáll | Script módosítandó (ONNX_BALL_DETECTOR konstans-ban) |
| Vision 2D nem ad lábfejet | Biomechanikai elemzés hiányos | Dokumentált platform limit; iOS 17+ Vision 3D scope külön |

---

## 7. iPhone QA Terv (jelenlegi build után)

### A. Skeleton QA (most elérhető)
1. Nyiss videót, kapcsold be skeleton toggle-t
2. Elvárt: fehér vonalak + confidence-alapú színes dots
3. ±500ms-on belüli eventnél skelton látszik; azon kívül nem
4. Snapshot coverage: 9/9 event snapshot megvan `319eb833` videóra

### B. Ball overlay QA (most elérhető)
1. `319eb833` videó, 2:733ms-nél: ball marker megjelenik (egyetlen sikeres detection)
2. Egyéb pozíciókban: status banner, "Megjelölöm" gomb megjelenik ha event ±2s-on belül van
3. "Megjelölöm" → tap a labdára → sárga kör azonnal megjelenik → debug logban POST success

### C. Nem QA-zható most (nincs implementálva)
- Folyamatos ball tracking
- Ball következő frame-en is látszik manual jelölés után
- Skeleton interpoláció snapshots között

---

## 8. Összefoglalás: Mi volt félreértve

1. **"Ball detection folyamatban"** üzenet — nem hálózati hiba, hanem `vm.activeEvents.isEmpty` → betöltés-szöveg helyett "Nincs esemény" szöveg. **Javítva.**

2. **Skeleton csak pontok** — `@ViewBuilder` multi-binding `if let` bug. **Javítva.**

3. **A live kék vonal skeleton (Screenshot 1)** — teljesen más screen és pipeline (Biometric Spike, live kamera), NEM a juggling annotation screen.

4. **Manual kijelölés nem indít trackert** — egyetlen snapshot, nincs tracker. Ez nem bug, az architektúra határa.

5. **±0.5s ablak** — tudatos design: csak a jelölt eventekhez van adat. Az ablak az event pontosságát tükrözi, nem UI-korlát.

---

*Implementáció (AN-3B2C-2, AN-3B2C-3, AN-3B2D, AN-3B2E) jóváhagyás után kezdhető.*
