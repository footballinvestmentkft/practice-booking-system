# AN-3B2D: Folyamatos Skeleton + Ball Tracking — Teljes Implementációs Terv

**Dátum:** 2026-06-17  
**Kiindulás:** `feat/an3b2b-1-ball-detection` (HEAD dd54dab8)  
**Státusz:** Audit + terv — implementáció NEM kezdődhet jóváhagyás nélkül  
**Érintett modulok:** `juggling_analysis_task.py`, `onnx_ball_detector.py`, `frame_extractor.py` — NEM MÓDOSÍTHATÓK

---

## 0. A jelenlegi probléma

### Mi van most

| Funkció | Jelenlegi működés | Korlát |
|---------|-------------------|--------|
| Skeleton | PoseSnapshotService.captureAndUpload() → 1 frame / contact event | Event-snapshot. Contact eventek között nincs adat. |
| Ball | detect_ball_for_event Celery task → 1 frame / contact event | Event-snapshot. 11% accuracy (1/9). Eventek között nincs adat. |
| Overlay | PoseSnapshotOverlayView + BallVideoOverlayView | Csak akkor jelenik meg, amikor a playhead ±500ms-on belül van egy contact event-hez képest. |

### Mi kellene

- **A videó teljes lejátszása alatt** folyamatos skeleton tracking legyen — minden frame-en (vagy elég sűrű sampling-en) látszódjon a csontváz
- **A videó teljes lejátszása alatt** folyamatos ball tracking legyen — a labda pozíciója követhető legyen frame-ről frame-re
- **Manuális seed:** ha a labdát egyszer kijelölöm, onnan a rendszer próbálja tovább követni
- **Lábfej megjelenítés** amennyire technikailag lehetséges
- **Nem event-snapshot, nem ±500ms ablak** — valódi folyamatos követés

---

## 1. Folyamatos Skeleton Tracking

### 1.1 Architektúra döntés: On-Device (iOS Vision)

**Indoklás:** A skeleton tracking természetes helye az iPhone.

| Szempont | iOS Vision (ajánlott) | Backend (MediaPipe/OpenPose) |
|----------|----------------------|------------------------------|
| Meglévő kód | PoseSnapshotService + BodyPoseDetector teljesen kész | Nincs — új Python dependency kellene |
| Model quality | Apple Vision 2D: 19 joint, jó accuracy, optimalizált Apple Silicon-ra | MediaPipe: 33 landmark (beleértve lábfejek!), de Python+OpenCV dependency |
| Latency | Valós idejű a videó feldolgozásakor (8-15ms/frame iPhone 12+) | Network round-trip + szerver feldolgozási idő |
| Offline | Működik offline is — helyben fut | Szerver kell |
| Battery | Kezelhető: GPU acceleration, sampling csökkenti | Nincs iPhone hatás, de network cost van |

**Döntés: On-Device iOS Vision.**

A skeleton feldolgozás a videó megnyitásakor elindul a háttérben, és a teljes videó összes frame-jét (vagy sampled frame-jeit) végigfuttatja. Az eredmény helyben cachelt, és opcionálisan feltölthető a backendre.

### 1.2 Sampling Rate

| Stratégia | FPS | Frame / perc | Pro | Kontra |
|-----------|-----|--------------|-----|--------|
| Teljes FPS (30) | 30 | 1800 | Tökéletesen sima | Battery killer, nem szükséges |
| **10 FPS (ajánlott)** | 10 | 600 | Vizuálisan sima, kezelhető terhelés | Kis interpoláció kell |
| 5 FPS | 5 | 300 | Alacsony terhelés | Szaggatott lehet gyors mozgásnál |

**Választás: 10 FPS** (100ms sampling). Ez ~33ms Vision inference/frame mellett ~330ms/sec feldolgozási időt jelent — 3x gyorsabb mint valós idő.

Egy 60 másodperces videó: 600 frame × ~33ms = ~20 másodperc feldolgozás.

### 1.3 Joint Mapping — Apple Vision 2D Body Pose

Az Apple `VNDetectHumanBodyPoseRequest` 19 joint-ot ad:

```
Fej:   nose, left_eye, right_eye, left_ear, right_ear
Felső: neck, left_shoulder, right_shoulder, left_elbow, right_elbow, left_wrist, right_wrist
Alsó:  root (pelvis), left_hip, right_hip, left_knee, right_knee, left_ankle, right_ankle
```

**Bone connectivity** (meglévő PoseSnapshotOverlayView-ból):

```
Spine:       neck → root
Left arm:    neck → left_shoulder → left_elbow → left_wrist
Right arm:   neck → right_shoulder → right_elbow → right_wrist
Left leg:    root → left_hip → left_knee → left_ankle
Right leg:   root → right_hip → right_knee → right_ankle
Face:        nose → left_eye → left_ear, nose → right_eye → right_ear
```

### 1.4 Lábfej kérdés

**Apple Vision 2D body pose NEM ad foot/toe landmark-ot.** A legalsó detektálható pont a `left_ankle` és `right_ankle`.

| Opció | Leírás | Megvalósíthatóság |
|-------|--------|-------------------|
| A. Boka pont erősebb vizuális jelzéssel | A boka pontot nagyobb kör + lábfej ikon jelöli | Egyszerű, pontos |
| **B. Szintetikus lábfej (ajánlott)** | Ankle-ből lefelé ~15% shin_length offset + talaj irány heurisztika | Vizuálisan informatív, nem 100% pontos |
| C. Vision 3D (iOS 17+) | `VNDetectHumanBodyPose3DRequest` ad foot joint-ot | iOS 17+ limit — a deployment target iOS 15 |
| D. CoreML lábfej model | Külön foot detection model (pl. OpenPose foot) | Komplex, nagy méret, bizonytalan accuracy |

**Választás: Opció B — Szintetikus lábfej becslés**

```
Algoritmus:
1. shin_vector = ankle - knee  (irányvektort számolunk)
2. foot_tip = ankle + shin_vector * 0.25  (a boka irányában meghosszabbítjuk)
3. Ha a becsült foot_tip y > 1.0 (kívül esik a kép alján): clamp to 0.98

Vizuálisan:
- Ankle pontból rövid vonal lefelé + kis háromszög "lábfej" szimbólum
- Szaggatott vonallal (nem solid), jelezve hogy becslés, nem detektálás
- Confidence: ankle confidence * 0.7 (degradált, mert szintetikus)
```

Ez nem tökéletes, de vizuálisan jelzi a láb irányát és pozícióját.

### 1.5 Frame Extraction Pipeline (iOS)

A jelenlegi `PoseSnapshotService.extractFrame()` 1 frame-et von ki `AVAssetImageGenerator`-ral. A folyamatos trackerhez más megközelítés kell:

**Megközelítés: `AVAssetReader` + `AVAssetReaderTrackOutput`**

```swift
// DensePoseExtractor (ÚJ osztály)
//
// Streaming frame extraction + Vision pose detection pipeline.
// Nem generál CGImage-eket memóriában — streaming CVPixelBuffer.
//
// 1. AVAssetReader nyitja a videót
// 2. AVAssetReaderTrackOutput ad CVPixelBuffer-t frame-enként
// 3. Minden N-edik frame-en (10 FPS sampling) → VNImageRequestHandler
// 4. Eredmény: [DensePoseFrame] tömb (timestamp + keypoints)
// 5. Callback: progress % + partial results
```

**Miért nem AVAssetImageGenerator?**
- `AVAssetImageGenerator.generateCGImagesAsynchronously` nem hatékony 600+ frame-re
- `AVAssetReader` natívan streaming — nem tart memóriában felesleges frame-eket
- `CVPixelBuffer` közvetlenül adható `VNImageRequestHandler`-nek (nincs CGImage konverzió)

### 1.6 Skeleton Trajectory Tárolás

#### On-device cache (elsődleges — gyors playback)

```swift
// DensePoseCache — in-memory + opcionális disk cache
//
// Key: videoId
// Value: [DensePoseFrame]
//
// DensePoseFrame:
//   timestampMs: Int
//   keypoints: PoseKeypointsDTO  (meglévő DTO — body landmarks array)
//   confidence: Float?
//   syntheticFeet: (left: CGPoint?, right: CGPoint?)  // Opció B lábfej
```

**Memória becslés:**
- 600 frame (60 sec videó) × ~19 joint × ~32 byte/joint = ~365 KB
- 5 perces videó: ~1.8 MB — elfogadható

**Disk cache:** `FileManager.default.temporaryDirectory` + videoId hash. Törlődik ha a videó törlődik.

#### Backend storage (opcionális — future PR)

```sql
CREATE TABLE juggling_skeleton_trajectories (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    video_id        UUID NOT NULL REFERENCES juggling_annotation_videos(id) ON DELETE CASCADE,
    frame_ms        INTEGER NOT NULL,
    keypoints_json  JSONB NOT NULL,          -- PoseKeypointsDTO JSON
    confidence      FLOAT,
    model_version   VARCHAR(50) NOT NULL,    -- 'apple_vision_v1'
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_skeleton_traj_video_ms ON juggling_skeleton_trajectories(video_id, frame_ms);
```

**Méret becslés:** 600 row × ~2 KB/row = ~1.2 MB / 60 sec videó.

A backend upload **NEM az első PR scope-ja**. Először on-device, aztán ha szükséges, upload.

---

## 2. Folyamatos Ball Tracking

### 2.1 Architektúra döntés: Backend Dense Sampling

A labda tracking a backendre tartozik, mert:
- Az ONNX ball detector (`onnx_ball_detector.py`) Python + onnxruntime — nem fut iOS-en
- CoreML konverzió lehetséges, de a SSD MobileNet v1 accuracy (11%) nem indokolja az effort-öt
- A backend processing nem vesz el iPhone battery-t

### 2.2 Dense Sampling Pipeline

```
Video upload → transcode complete signal
    ↓
dense_ball_trajectory_task (ÚJ Celery task, NEM módosítja a meglévőt)
    ↓
for frame_ms in range(0, duration_ms, 100):  # 10 FPS
    frame_rgb = frame_extractor.extract_frame_at_ms(video_path, frame_ms)  # MEGLÉVŐ — nem módosul
    result = onnx_ball_detector.detect(frame_rgb)  # MEGLÉVŐ — nem módosul
    ↓
Kalman filter smoothing (inter-frame)
    ↓
Bulk INSERT → juggling_ball_trajectories
```

### 2.3 Detector + Tracker Pipeline

**Detector:** Meglévő `OnnxBallDetector.detect()` — SSD MobileNet v1, confidence threshold 0.3.

**Tracker:** Egyszerű Kalman filter (no new dependency — filterpy nem szükséges).

```python
# KalmanBallTracker — saját implementáció, ~50 sor
#
# State: [x, y, vx, vy]  (pozíció + sebesség)
# Measurement: [x, y]  (detector output)
# Predict → measure → update ciklus
#
# Ha nincs detektálás:
#   - predict-only (extrapolálás a sebesség alapján)
#   - max_miss_count = 5 (500ms at 10 FPS)  
#   - ha 5 egymást követő miss → tracking_lost = True
#
# Ha tracking_lost:
#   - Várakozik manuális seed-re VAGY új detektálásra
#   - Ha új detektálás jön: re-init tracker az új pozícióval
```

### 2.4 Manual Seed / Re-acquire

```
1. Felhasználó a videón megérint egy pontot → "manuális labda pozíció"
2. iOS → POST /videos/{videoId}/ball-trajectory/manual-seed
   Body: { frame_ms: 3200, ball_x: 0.42, ball_y: 0.71 }
3. Backend:
   a. INSERT a manuális pontot (is_manual=true)
   b. Re-run Kalman tracker a seed ponttól ±5 másodpercre
   c. Felülírja a korábbi auto-detected pontokat ebben az ablakban
4. iOS polling felveszi az updated trajectory-t
```

### 2.5 Tracking Lost állapot

```
Tracking active:
  - Van detektálás VAGY Kalman predict < 5 consecutive miss
  - Overlay: sárga/zöld kör a becsült pozíción

Tracking lost:
  - 5+ consecutive miss (500ms gap)
  - Overlay: nincs kör, halvány "?" ikon a videó közepén
  - iOS banner: "Labda elveszett — érintsd meg a labdát a videón"
  - A banner interaktív → tap → manuális seed flow

Re-acquired:
  - Új detektálás VAGY manuális seed
  - Overlay visszaáll
  - A gap-ben nincs interpoláció (üres marad)
```

### 2.6 Trajectory Storage (Backend)

```sql
CREATE TABLE juggling_ball_trajectories (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    video_id        UUID NOT NULL REFERENCES juggling_annotation_videos(id) ON DELETE CASCADE,
    frame_ms        INTEGER NOT NULL,
    ball_x          FLOAT,                    -- normalized [0,1], NULL if lost
    ball_y          FLOAT,                    -- normalized [0,1], NULL if lost
    confidence      FLOAT,                    -- detector confidence [0,1]
    is_manual       BOOLEAN NOT NULL DEFAULT FALSE,
    tracking_state  VARCHAR(20) NOT NULL DEFAULT 'active',  -- active / lost / manual_seed
    tracker_vx      FLOAT,                    -- Kalman velocity x (debug info)
    tracker_vy      FLOAT,                    -- Kalman velocity y (debug info)
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_ball_traj_video_ms ON juggling_ball_trajectories(video_id, frame_ms);
```

**JugglingVideo model — új oszlop:**

```python
ball_trajectory_status = Column(
    String(20), nullable=True, default=None,
    comment="pending / processing / complete / failed — dense ball tracking lifecycle"
)
```

### 2.7 API endpoint

```
GET /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory
    ?from_ms=0&to_ms=60000

Response:
{
  "status": "complete",           // pending / processing / complete / failed
  "progress_pct": 100,
  "points": [
    { "frame_ms": 0, "ball_x": null, "ball_y": null, "tracking_state": "lost" },
    { "frame_ms": 100, "ball_x": 0.42, "ball_y": 0.71, "confidence": 0.88, "tracking_state": "active" },
    ...
  ]
}

Pagination: max 600 pont / request (60 sec at 10 FPS).

POST /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory/manual-seed
Body: { "frame_ms": 3200, "ball_x": 0.42, "ball_y": 0.71 }
→ Re-seed tracker from this point.
```

### 2.8 iOS overlay: `BallTrajectoryOverlayView`

```swift
struct BallTrajectoryOverlayView: View {
    let points: [BallTrajectoryPoint]
    let currentMs: Int

    // Vizualizáció:
    // - Jelenlegi pozíció: színkódolt kör (green=high conf, yellow=medium, orange=low)
    // - Trail: utolsó 10 pont, csökkenő opacity (1.0 → 0.1)
    // - Manuális seed: kék kör, erősebb szegély
    // - Tracking lost: nincs kör, "?" overlay
    // - Tracking active: sima kör + trail
}
```

---

## 3. Közös Playback Overlay

### 3.1 Egyidejű megjelenítés

A fő videó nézeten skeleton + ball **egyszerre** látszódik. ZStack sorrend:

```
1. Videó (AVPlayerLayerView)
2. ContinuousSkeletonOverlayView   ← ÚJ: playhead-synced skeleton
3. BallTrajectoryOverlayView       ← ÚJ: playhead-synced ball + trail
4. Interaktív elemek (ball selection, controls, timeline)
```

### 3.2 Playhead-szinkronizált pozíciók

```swift
// A ViewModel figyeli a playhead pozíciót (CMTime → ms)
// Minden frame update-nél:

func updateOverlaysForCurrentTime(_ currentMs: Int) {
    // 1. Skeleton: keressük a legközelebbi DensePoseFrame-et
    //    Binary search a sorted timestampMs tömbben
    //    Ha a legközelebbi frame ≤ 50ms távol: exact match → megjelenítjük
    //    Ha 50-100ms: interpoláljuk a két szomszédos frame között
    //    Ha > 100ms: nincs adat (gap) → skeleton eltűnik

    // 2. Ball: keressük a legközelebbi BallTrajectoryPoint-ot
    //    Ugyanaz a logika: ≤50ms → exact, 50-100ms → interpoláció, >100ms → nincs

    // Interpoláció: lineáris a két szomszédos pont között
    //   interpolated_x = prev.x + (next.x - prev.x) * t
    //   ahol t = (currentMs - prev.ms) / (next.ms - prev.ms)
}
```

### 3.3 Folyamatos marker, nem event-only

**Jelenlegi (event-only):**
```
    Event 1          Event 2          Event 3
      ●                ●                ●
------+---..............+---..............+------→ timeline
      skeleton         skeleton         skeleton
      ±500ms           ±500ms           ±500ms
```

**Új (continuous):**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━→ timeline
████████████████████████████████████████████████  skeleton (mindig)
▓▓▓▓░░░░▓▓▓▓▓▓▓▓▓▓▓▓░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ball (ahol detektált / tracked)
                       ↑ gap = tracking lost
```

### 3.4 Toggle-ök

A meglévő skeleton toggle (`showSkeletonOverlay`) és egy ball toggle kezeli:

| Toggle | Hatás |
|--------|-------|
| Skeleton ON | ContinuousSkeletonOverlayView látható |
| Skeleton OFF | Skeleton eltűnik |
| Ball ON | BallTrajectoryOverlayView látható |
| Ball OFF | Ball eltűnik |
| Mindkettő ON | Egyszerre látszódik mindkettő |

---

## 4. Teljesítmény

### 4.1 iPhone Battery

| Művelet | Becsült fogyasztás | Időtartam |
|---------|-------------------|-----------|
| Dense pose extraction (60s videó, 10 FPS) | ~3-5% battery iPhone 12+ | ~20 sec (háttér) |
| Overlay rendering (playback közben) | Minimális — SwiftUI draw, no ML inference | Folyamatos, de könnyű |
| Network (trajectory fetch) | Minimális — 1 request ~100 KB | Egyszeri |

**Mitigáció:**
- Dense pose extraction csak egyszer fut videónként (cache-elt)
- Processing priority: `.utility` (nem blokkolja a UI-t)
- Ha a battery < 20%: figyelmeztetés, de nem tiltjuk le
- Processing közben a felhasználó már használhatja a videót — partial results megjelennek

### 4.2 Backend CPU

| Művelet | CPU / videó | Időtartam |
|---------|------------|-----------|
| Dense ball detection (60s, 10 FPS) | ~600 × 15ms = 9 sec | Celery solo worker |
| Kalman tracking | Negligible (< 10ms total) | Same task |
| DB bulk insert | ~100ms | Same task |

**Becsült összesítés:** ~10 másodperc / 60 sec videó.

5 perces videó: ~50 sec processing. Elfogadható.

### 4.3 Celery

**Queue:** `analysis` (meglévő — `--pool=solo -c 1`)

Egy időben 1 dense tracking task fut. Ha több videó van, sorban.

**Timeout:** `time_limit=600` (10 perc — elég 5 perces videóhoz is).

### 4.4 Storage

| Tábla | Méret / 60 sec videó | Méret / 5 perc videó |
|-------|---------------------|---------------------|
| `juggling_ball_trajectories` | 600 row × ~200 byte = ~120 KB | ~600 KB |
| `juggling_skeleton_trajectories` (ha upload) | 600 row × ~2 KB = ~1.2 MB | ~6 MB |
| iOS in-memory cache | ~365 KB | ~1.8 MB |

**Index méret:** B-tree on (video_id, frame_ms) — minimális.

**Retention:** A trajectory adatok a videóval együtt törlődnek (ON DELETE CASCADE).

### 4.5 Feldolgozási idő összesítés

| Videó hossz | Skeleton (on-device) | Ball (backend) | Összesen (parallel) |
|-------------|---------------------|----------------|---------------------|
| 30 sec | ~10 sec | ~5 sec | ~10 sec (bottleneck: skeleton) |
| 60 sec | ~20 sec | ~10 sec | ~20 sec |
| 5 perc | ~100 sec | ~50 sec | ~100 sec |

A skeleton és ball tracking **párhuzamosan** futhat:
- Skeleton: on-device, azonnal a videó megnyitásakor
- Ball: backend, a videó upload/transcode után

---

## 5. PR Bontás

### PR-1: Backend Trajectory Tables + Dense Ball Task (AN-3B2D-1)

**Scope:**
- Alembic migration: `juggling_ball_trajectories` + `ball_trajectory_status` oszlop a JugglingVideo-n
- Opcionálisan: `juggling_skeleton_trajectories` (üres, upload-hoz előkészítve)
- `app/tasks/juggling_trajectory_task.py` (ÚJ fájl — NEM módosítja `juggling_analysis_task.py`-t)
- `app/services/juggling/kalman_ball_tracker.py` (ÚJ fájl — saját Kalman filter)
- `GET /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory` endpoint
- `POST /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory/manual-seed` endpoint
- Feature flag: `BALL_TRAJECTORY_ENABLED=false`
- Auto-trigger: a videó transcode befejezésekor `dense_ball_trajectory_task.delay()`
- Tesztek: BT-01..BT-14

**Nem módosított fájlok:**
- `juggling_analysis_task.py` — TILOS
- `onnx_ball_detector.py` — TILOS (importálva, nem módosítva)
- `frame_extractor.py` — TILOS (importálva, nem módosítva)

**Becslés:** ~2 nap

### PR-2: iOS Dense Pose Extraction (AN-3B2D-2)

**Scope:**
- `DensePoseExtractor` osztály (AVAssetReader + Vision pipeline, streaming)
- `DensePoseCache` (in-memory + temp disk cache)
- Synthetic foot estimation (Opció B — ankle-based offset)
- `ContinuousSkeletonOverlayView` (teljes skeleton, playhead-synced, interpoláció)
- ViewModel: `startDensePoseExtraction()`, `denseSkeletonFrame(at:)` computed property
- Processing progress UI (% bar az extraction közben)
- Tesztek: DPSE-01..DPSE-10

**Fontos:** Ez a PR **nem** módosítja a meglévő PoseSnapshotOverlayView-t. Új nézet, ami a continuous adatot jeleníti meg.

**Becslés:** ~2.5 nap

### PR-3: iOS Ball Trajectory Overlay (AN-3B2D-3)

**Scope:**
- `BallTrajectoryPoint` / `BallTrajectoryResponse` DTOs
- `BallTrajectoryOverlayView` (trail + current marker + tracking lost)
- ViewModel: `fetchTrajectoryWindow()`, `startTrajectoryPolling()`, trajectory-related state
- Manual seed tap gesture + POST endpoint hívás
- "Tracking lost" banner + tap-to-seed interaction
- Fallback: ha trajectory nem elérhető → régi event-snapshot overlay
- JugglingAnnotationScreen ZStack integráció
- Tesztek: BT-iOS-01..BT-iOS-12

**Becslés:** ~2 nap

### PR-4: iOS Playback Sync + Combined Overlay (AN-3B2D-4)

**Scope:**
- `updateOverlaysForCurrentTime()` — binary search + interpoláció mindkét tracker-re
- CMTime → ms playhead figyelés (AVPlayer `addPeriodicTimeObserver`)
- Toggle-ök: skeleton ON/OFF, ball ON/OFF, combined
- Processing status dashboard (skeleton: X%, ball: Y%)
- Edge case-ek: videó seek, pause/resume, rotation
- Tesztek: SYNC-01..SYNC-08

**Becslés:** ~1.5 nap

### PR-5 (opcionális): QA/Debug Tooling (AN-3B2D-5)

**Scope:**
- Admin debug overlay: frame number, skeleton joint count, ball confidence, Kalman state
- Backend: `GET /admin/juggling/trajectory-stats/{videoId}` — coverage %, gap analysis
- iOS: debug mode toggle → minden joint név megjelenik a skeleton-on
- Export: skeleton + ball trajectory CSV dump (admin-only)

**Becslés:** ~1 nap

### Sorrend

```
PR-1 (backend tables + dense ball task)
  ↓
PR-2 (iOS dense pose extraction) — független a PR-1-től, de PR-4 előfeltétel
  ↓ ↓
PR-3 (iOS ball trajectory overlay) — függ PR-1-től (API endpoint kell)
  ↓
PR-4 (playback sync + combined) — függ PR-2 + PR-3-tól
  ↓
PR-5 (QA/debug tooling) — opcionális, bármikor
```

PR-1 és PR-2 **párhuzamosan** fejleszthető.

---

## 6. iPhone QA Terv

### 6.1 Mit kell Zoltánnak látnia

| Teszt | Elvárt eredmény | PASS feltétel |
|-------|-----------------|---------------|
| **Q1: Skeleton teljes videón** | A videó indításakor a skeleton megjelenik, és a lejátszás végéig folyamatosan látható | Nincs 2 mp-nél hosszabb gap, ahol a skeleton eltűnik |
| **Q2: Skeleton követi a mozgást** | A csontváz vonalai követik a játékos karjait, lábait, fejét | Vizuálisan korrekt pozíciók — nem csúsznak el >5% |
| **Q3: Lábfej jelölés** | A boka alatt rövid szaggatott vonal jelzi a becsült lábfej pozíciót | A jelölés kb. oda mutat, ahol a láb van, elfogadható ha ~20% eltérés |
| **Q4: Ball tracking teljes videón** | A labda marker folyamatosan látható ahol a labda detektálva van | Marker pozíciója vizuálisan elfogadható |
| **Q5: Ball trail** | A labda mögött halvány nyomvonal (utolsó 10 pozíció) | Trail sima, nem ugrik |
| **Q6: Tracking lost → manual seed** | Ha a labda eltűnik, banner jelenik meg; tap → manuális labda pozíció → tracking folytatódik | A seed után a tracker újra aktív lesz |
| **Q7: Seek test** | Videó közepére seek → skeleton + ball azonnal megjelenik az új pozíción | <200ms-on belül megjelenik |
| **Q8: Egyszerre mindkettő** | Skeleton + ball toggle ON → mindkettő egyszerre látszódik | Nincs vizuális interferencia |
| **Q9: Processing banner** | Videó megnyitáskor "Skeleton feldolgozás: X%" + "Ball feldolgozás: Y%" | A % növekszik, végül eltűnik |
| **Q10: Battery test** | 5 perces videó feldolgozása → battery monitor | <5% battery fogyasztás iPhone 12+ |

### 6.2 Hogyan ellenőrizzük, hogy NEM snapshot hanem continuous

**Kulcs teszt: Q1 + Q7**

1. **Pause teszt:** Szüneteltesd a videót bármelyik pillanatban (NEM contact event közelében). Ha a skeleton és/vagy ball marker látható → PASS. Ha nincs marker → FAIL (event-snapshot maradt).

2. **Scrub teszt:** Húzd a playhead-et lassan jobbra. Ha a skeleton + ball marker folyamatosan mozog → PASS. Ha csak villanásszerűen jelenik meg egyes pontoknál → FAIL.

3. **Gap teszt:** Nézd meg a videót teljes lejátszásban. Számold a >2 mp-es gap-eket ahol a skeleton eltűnik. Ha 0 gap → PASS. Ha >2 gap → FAIL.

### 6.3 Validációs videók

| Videó típus | Miért | Elvárt |
|-------------|-------|--------|
| **Jó minőségű, napfényes, 1 személy** | Happy path | Skeleton 95%+ coverage, ball 50%+ coverage |
| **Alacsony fény, beltéri** | Edge: Vision accuracy romlás | Skeleton 80%+ coverage, ball 30%+ coverage |
| **Több személy a háttérben** | Edge: melyik skeleton? | A legelső (legmagasabb confidence) személyt követi |
| **Gyors mozgás** | Edge: blur, tracking lost | Skeleton néha elvész, de visszatér <1 sec-en belül |
| **Labda kicsúszik a képből** | Edge: tracking lost → re-acquire | "Tracking lost" banner megjelenik, automatikus re-acquire ha visszajön |
| **Manuális seed videó** | Teszt: seed flow | Tap → seed → tracking indul → trail megjelenik |

---

## 7. Nem módosított fájlok — explicit lista

| Fájl | Státusz |
|------|---------|
| `app/tasks/juggling_analysis_task.py` | NEM MÓDOSUL |
| `app/services/juggling/onnx_ball_detector.py` | NEM MÓDOSUL (importálva) |
| `app/services/juggling/frame_extractor.py` | NEM MÓDOSUL (importálva) |
| `app/services/juggling/football_skill_service.py` | NEM MÓDOSUL |
| `ios/.../PoseSnapshotService.swift` | NEM MÓDOSUL (a meglévő event-snapshot path megmarad) |
| `ios/.../PoseSnapshotOverlayView.swift` | NEM MÓDOSUL (megmarad, de a continuous overlay veszi át a fő role-t) |
| `ios/.../BallVideoOverlayView.swift` | NEM MÓDOSUL (fallback marad ha trajectory nem elérhető) |

---

## 8. Feature Flags

```python
# .env
BALL_TRAJECTORY_ENABLED=false          # Backend dense ball tracking
# Nincs skeleton flag — az on-device, mindig elérhető ha a kód ott van
```

```swift
// iOS: nincs feature flag — a DensePoseExtractor egyszerűen lefut
// Ha nincs adat (pl. régi videó): graceful fallback az event-snapshot-ra
```

---

## 9. Összefoglalás

| Szempont | Skeleton | Ball |
|----------|----------|------|
| Hol fut | iOS (on-device, Apple Vision) | Backend (Celery, ONNX) |
| Sampling | 10 FPS (100ms) | 10 FPS (100ms) |
| Storage | iOS in-memory cache (opcionálisan backend) | Backend DB |
| Overlay | ContinuousSkeletonOverlayView | BallTrajectoryOverlayView |
| Playhead sync | Binary search + interpoláció | Binary search + interpoláció |
| Lábfej | Szintetikus (ankle + offset), szaggatott vonal | N/A |
| Tracking lost | Skeleton eltűnik (ritka — Vision elég jó) | Banner + manual seed prompt |
| Manual intervention | Nem szükséges | Tap-to-seed |

**Total PR-ok:** 5 (4 core + 1 opcionális QA)  
**Total becsült idő:** ~9 nap (4 core) + 1 nap (QA)  
**Párhuzamosítható:** PR-1 + PR-2 egyszerre indulhat

---

*Implementáció NEM kezdődhet el jóváhagyás nélkül.*

*A meglévő event-snapshot pipeline (PoseSnapshotService, detect_ball_for_event) változatlan marad — az AN-3B2D ráépül, nem cseréli le.*
