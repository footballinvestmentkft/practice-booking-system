# AN-3B2D: Folyamatos Ball Tracking — Implementációs Terv

**Dátum:** 2026-06-17  
**Branch:** `feat/an3b2b-1-ball-detection` (kiindulás)  
**Státusz:** Terv jóváhagyás alatt — implementáció NEM kezdődhet el  
**Érintett fájlok (TILOS módosítani):** `juggling_analysis_task.py`, `onnx_ball_detector.py`, `frame_extractor.py`

---

## 1. Jelenlegi állapot (baseline)

### 1.1 Backend pipeline — event-snapshot only

```
Admin trigger (POST /admin/juggling/detect-ball)
    ↓
detect_ball_for_event (Celery task)
    ↓
frame_extractor.extract_frame_at_ms(video_path, event_ms)  ← 1 frame
    ↓
onnx_ball_detector.detect(frame)  ← SSD MobileNet v1
    ↓
juggling_contact_events.ball_x / ball_y / no_ball_detected
```

**Limitációk:**
- 1 frame/event — nem folyamatos
- Admin-only trigger — automatikus feldolgozás nincs
- SSD MobileNet v1 — általános célú, nem sportspecifikus
- Tesztelési eredmény: 1/9 sikeres detektálás (11%)
- Nincs tracking — egymástól független frame-ek

### 1.2 iOS pipeline — polling + ±500ms ablak

```swift
vm.bulkFetchBallDetections()  // load screen
    ↓
GET /api/v1/users/me/juggling/videos/{videoId}/contacts/{eventId}/ball-detection
    ↓
closestBallDetection(toMs:)  // ±500ms window, noBallDetected=false szűrés
    ↓
BallVideoOverlayView  // single marker circle
```

**Limitációk:**
- Legfeljebb N marker, ahol N = event count
- Events közötti frame-ek: nincs labda marker
- Trajektória nincs: a felhasználó nem látja a labda mozgását
- Manual correction → csak az adott event-et frissíti, a szomszéd frame-eket nem

---

## 2. Célállapot

**User-facing élmény:**
- A fő juggling annotáció videón a labda folyamatosan követhető legyen — nem csak event-snapshot pillanatokban
- A labda trajektóriája legyen látható (halvány "nyom" az előző N pozíción)
- Ha az auto-detektálás elvész, a felhasználó manuálisan "visszaadhat" egy pozíciót, és onnan folytatódik a tracking
- Tracking elvesztésekor informatív UI üzenet jelenik meg

---

## 3. Architektúra döntés: Backend-dense vs. On-device

### Opció A: Backend dense sampling (ajánlott)

```
Video upload (kész)
    ↓
dense_ball_trajectory_task (új Celery task) [AUTO trigger]
    ↓
Minden ~100ms-ban 1 frame kivonás
    ↓
SSD MobileNet v1 (meglévő) per-frame  OR  YOLOv8n-tiny (új, pontosabb)
    ↓
SORT tracker (új Python csomag) — Inter-frame smoothing + ID stabilizálás
    ↓
juggling_ball_trajectories tábla (új) — trajectory points tömeges INSERT
    ↓
iOS: GET /trajectory endpoint → időablak alapú szűrés → trajektória overlay
```

**Előnyök:**
- Computationally heavy munka szerveren van, nem iPhone-on
- Határtalan videóhossz
- A SORT tracker inter-frame smoothing adatot ad, ami önmagában is javítja az 11%-os accuracy-t
- Feature flag mögé zárható

**Hátrányok:**
- Celery job hosszú futásidő (10 perc? 1 óra?) → async, progress endpoint kell
- DB méret növekedés (~30 pont/mp → 1800 pont/perc → 108K pont/óra)
- Backend deployment change

### Opció B: On-device Core ML tracking

```
iOS: AVAssetImageGenerator streaming
    ↓
VNDetectHumanBodyPoseRequest + custom CoreML ball detection model
    ↓
Real-time tracking on device
    ↓
POST results to server
```

**Hátrányok:**
- iOS 15+ API limit: nincs beépített ball detector
- Custom CoreML model konverzió szükséges (ONNX → CoreML)
- iPhone battery/CPU cost
- Lassabb (processing while playback)

### Döntés: Opció A — Backend dense sampling

---

## 4. Backend implementációs terv

### 4.1 Új DB tábla: `juggling_ball_trajectories`

```sql
CREATE TABLE juggling_ball_trajectories (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    video_id            UUID NOT NULL REFERENCES juggling_annotation_videos(id) ON DELETE CASCADE,
    frame_ms            INTEGER NOT NULL,        -- frame timestamp milliseconds from video start
    ball_x              FLOAT,                   -- normalized [0,1], NULL if no detection
    ball_y              FLOAT,                   -- normalized [0,1], NULL if no detection
    confidence          FLOAT,                   -- detector confidence [0,1]
    is_manual           BOOLEAN NOT NULL DEFAULT FALSE,  -- user-placed marker
    tracker_id          INTEGER,                 -- SORT tracker object ID
    no_ball_detected    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_ball_trajectories_video_ms
    ON juggling_ball_trajectories(video_id, frame_ms);
CREATE INDEX idx_ball_trajectories_video_manual
    ON juggling_ball_trajectories(video_id, is_manual)
    WHERE is_manual = TRUE;
```

**Migrációs fájl:** `alembic/versions/2026_06_17_1000_add_juggling_ball_trajectories.py`

### 4.2 Celery task: `dense_ball_trajectory_task`

**Fájl:** `app/tasks/juggling_trajectory_task.py` (ÚJ fájl)

```
Input: video_id: UUID
Steps:
  1. Load video path from DB
  2. Extract frames at 100ms intervals using frame_extractor (MEGLÉVŐ, NEM MÓDOSÍTJUK)
  3. Detektálás minden frame-en SSD MobileNet v1-gyel (MEGLÉVŐ onnx_ball_detector, NEM MÓDOSÍTJUK)
  4. SORT tracker alkalmazása a detection sequence-re
  5. Bulk INSERT juggling_ball_trajectories
  6. UPDATE juggling_annotation_videos.trajectory_status = 'complete'

juggling_annotation_videos-hoz ÚJ oszlop:
  trajectory_status: Enum ('pending', 'processing', 'complete', 'failed')
```

**FONTOS:** `juggling_analysis_task.py` NEM MÓDOSÍTHATÓ. Az új task KÜLÖN fájl.

**SORT tracker integration:**
```python
# Új dependency: filterpy (MIT licence) — SORT implementáció
# Alternatíva: saját kalman filter (nincs új dependency)
# Ajánlás: saját egyszerű Kalman filter (3 sor) — no new dependency
```

### 4.3 Auto-trigger on video upload

**Hol:** `app/api/api_v1/endpoints/juggling_videos.py` — `POST /videos` endpoint handler

```python
# Videó feldolgozás befejezésekor:
from app.tasks.juggling_trajectory_task import dense_ball_trajectory_task
dense_ball_trajectory_task.delay(video_id=str(video.id))
```

**Feature flag:** `BALL_TRAJECTORY_ENABLED=false` (`.env`) — ha false, a task nem fut.

### 4.4 Új API endpoint: `GET /trajectory`

```
GET /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory
    ?from_ms=0
    &to_ms=60000

Response:
{
  "status": "complete" | "processing" | "pending" | "failed",
  "progress_pct": 45,
  "points": [
    { "frame_ms": 1200, "ball_x": 0.42, "ball_y": 0.71, "confidence": 0.88, "is_manual": false },
    { "frame_ms": 1300, "ball_x": 0.44, "ball_y": 0.70, "confidence": 0.91, "is_manual": false },
    ...
  ]
}
```

**Pagination:** Max 600 pont per request (= 60 másodperc 100ms-os granularitásnál).

### 4.5 Manual correction → trajectory frissítés

A meglévő `POST /videos/{videoId}/contacts/{eventId}/ball-detection` megmarad (event-szintű). Az ÚJ behavior:

```
POST manual ball position
    ↓
Upsert juggling_ball_trajectories (is_manual=TRUE, frame_ms=event_ms)
    ↓
Propagate: Kalman re-seed a manuálisan jelölt pozícióból ±5 másodperc
    ↓
iOS polling látja az updated trajectory-t
```

---

## 5. iOS implementációs terv

### 5.1 ÚJ model: `BallTrajectoryPoint`

```swift
struct BallTrajectoryPoint: Decodable, Equatable {
    let frameMs:    Int
    let ballX:      Double?
    let ballY:      Double?
    let confidence: Double?
    let isManual:   Bool
}

struct BallTrajectoryResponse: Decodable {
    let status:      TrajectoryStatus     // pending / processing / complete / failed
    let progressPct: Int?
    let points:      [BallTrajectoryPoint]
}

enum TrajectoryStatus: String, Decodable {
    case pending, processing, complete, failed
}
```

### 5.2 ViewModel változások

**ÚJ `@Published` tulajdonságok:**

```swift
@Published var trajectoryPoints:  [BallTrajectoryPoint] = []
@Published var trajectoryStatus:  TrajectoryStatus = .pending
@Published var trajectoryProgress: Int = 0
```

**ÚJ metódusok:**

```swift
func fetchTrajectoryWindow(fromMs: Int, toMs: Int) async throws
// Hívja: GET /ball-trajectory?from_ms=...&to_ms=...
// Update: trajectoryPoints, trajectoryStatus, trajectoryProgress

func startTrajectoryPolling()
// Ha status != .complete és != .failed: 3mp-enként fetchTrajectoryWindow(currentWindow)
// Ha .complete: polling leáll

func stopTrajectoryPolling()
```

**`bulkFetchBallDetections()` megmarad** — a régi event-snapshot detektálás nem törlődik, visszafallback.

### 5.3 ÚJ SwiftUI nézet: `BallTrajectoryOverlayView`

```swift
struct BallTrajectoryOverlayView: View {
    let points: [BallTrajectoryPoint]   // current window (±3 másodperc)
    let currentMs: Int

    // Vizualizáció:
    // - Jelenlegi pozíció: sárga kör (meglévő BallVideoOverlayView logika)
    // - Múltbeli pozíciók (utolsó 10): halvány narancs/sárga trail, csökkenő opacity
    // - Manuálisan jelölt: kék kör (erős szegély, csillag ikon)
    // - "Tracking lost": nincs pont a ±500ms ablakban → státusz banner

    var body: some View { ... }
}
```

**Trail rendering (past positions):**
```swift
// Utolsó 10 point, 100ms lépések
// opacity: 1.0 - (index * 0.09)
// radius: 6px → 2px (csökkenő)
// szín: manual=blue, auto-high-conf=green, auto-low-conf=orange
```

### 5.4 JugglingAnnotationScreen változások

**Overlay prioritizálás (ZStack sorrend):**

```swift
// 1. Videó
// 2. Skeleton overlay (PoseSnapshotOverlayView)
// 3. Ball trajectory overlay (BallTrajectoryOverlayView) ← ÚJ
// 4. Ball selection overlay (ballSelectionOverlay) — interaktív
// 5. UI (controls, timeline)
```

**Trajectory processing state banner:**

```swift
if vm.trajectoryStatus == .processing {
    HStack {
        ProgressView().scaleEffect(0.7)
        Text("Labda pálya feldolgozás \(vm.trajectoryProgress)%")
            .font(.system(size: 11))
    }
    .padding(6)
    .background(Color.black.opacity(0.6))
    .cornerRadius(6)
}
```

### 5.5 Ball overlay toggle — kibővített

A meglévő ball toggle mostantól a trajectory overlay-t is vezérli. A toggle prioritás:

1. Ha trajectory status == `.complete` és van pont ±500ms-ban → `BallTrajectoryOverlayView`
2. Ha trajectory status == `.complete` de nincs pont → "Tracking lost" banner
3. Ha trajectory status == `.processing` → processing banner + meglévő event-snapshot fallback
4. Ha trajectory status == `.pending` → "Feldolgozás várakozik" banner
5. Ha trajectory status == `.failed` → "Feldolgozás sikertelen" banner + event-snapshot fallback

---

## 6. State machine: trajectory lifecycle

```
[pending] ──auto-trigger──→ [processing] ──done──→ [complete]
                                   │                     │
                                   └───error────→ [failed]
                                                         │
                                              user retry → [processing]
```

**iOS polling strategy:**
- `processing`: 3mp polling (cache stays warm < 5 perc)
- `pending`: 10mp polling (várakozó sor)
- `complete`/`failed`: no polling

---

## 7. Accuracy improvement plan

### 7.1 Jelenlegi: SSD MobileNet v1

- Általános object detector
- COCO dataset (labda nem kiemelkedő osztály)
- Accuracy futball-labdán: ~11% (1/9 test frame)
- Nem módosítható (onnx_ball_detector.py TILOS)

### 7.2 Javasolt Model: YOLOv8n-tiny (opcionális, AN-3B2D-2)

- Futball-specifikus fine-tuning lehetséges
- ONNX export támogatott
- Inference time: ~15ms/frame CPU-n
- Külön PR — NEM az AN-3B2D-1 scope-jában
- Az ONNX detector wrapper ÚJ fájlban kerülne, az `onnx_ball_detector.py` NEM módosul

### 7.3 Rövid távú accuracy javítás SORT trackerrel

A SORT tracker (Simple Online and Realtime Tracking) Kalman filtert alkalmaz az egymást követő detektálásokra. Hatás:

| Helyzet | Detector alone | SORT tracker |
|---------|---------------|-------------|
| Jó detektálás | 100% | 100% |
| 1 kihagyott frame | 0% | ~85% (interpolált) |
| 2 consecutive miss | 0% | ~60% (extrapolált) |
| 3+ consecutive miss | 0% | tracking lost → kézi re-seed |

---

## 8. Feature flag és rollback terv

### 8.1 Feature flag

```python
# .env
BALL_TRAJECTORY_ENABLED=false  # default OFF, külön bekapcsolható

# app/core/config.py
BALL_TRAJECTORY_ENABLED: bool = False
```

Ha `BALL_TRAJECTORY_ENABLED=false`:
- `dense_ball_trajectory_task` nem kerül ütemezésre
- `GET /ball-trajectory` → 501 Not Implemented (iOS fallback: régi event-snapshot)
- DB tábla létezhet (migration fut), de üres

### 8.2 iOS fallback

```swift
// Ha GET /trajectory → 501 vagy hálózati hiba:
// gracefully degradál az event-snapshot logikára
// trajectoryStatus = .failed → fallback to closestBallDetection(toMs:)
```

### 8.3 Rollback

1. `BALL_TRAJECTORY_ENABLED=false` → minden trajectory munka leáll
2. `juggling_ball_trajectories` tábla érintetlen marad (alembic downgrade nem szükséges)
3. iOS event-snapshot path automatikusan aktív marad

---

## 9. PR breakdown

| PR | Scope | Becslés |
|----|-------|---------|
| **AN-3B2D-1** | DB migration + Celery dense task + GET /trajectory endpoint + feature flag | ~2 nap |
| **AN-3B2D-2** | iOS BallTrajectoryOverlayView + ViewModel polling + screen integration | ~1.5 nap |
| **AN-3B2D-3** (opcionális) | YOLOv8n-tiny ONNX integráció ÚJ wrapper fájlban | ~1 nap |

**Sorrend:** AN-3B2D-1 → AN-3B2D-2 (blocker) → AN-3B2D-3 (opcionális)

---

## 10. Nem módosított fájlok — explicite megerősítve

| Fájl | Állapot |
|------|---------|
| `juggling_analysis_task.py` | NEM módosul |
| `onnx_ball_detector.py` | NEM módosul |
| `frame_extractor.py` | NEM módosul |
| `football_skill_service.py` | NEM módosul |
| `segment_reward_service.py` | NEM módosul |
| `virtual_training_metrics.py` | NEM módosul |
| `tournament_participation_service.py` | NEM módosul |

Az új Celery task **importálja** a meglévő `frame_extractor` és `onnx_ball_detector` modulokat — nem módosítja.

---

## 11. Tesztek

### Backend (AN-3B2D-1)

- `BT-01..05`: `juggling_ball_trajectories` CRUD (insert, query by window, cascade delete)
- `BT-06..08`: `dense_ball_trajectory_task` mock (mocked detector, verifies bulk insert count)
- `BT-09..11`: `GET /trajectory` endpoint (status=pending/processing/complete, pagination)
- `BT-12..13`: Manual correction upsert → trajectory point created with `is_manual=TRUE`
- `BT-14`: Feature flag OFF → 501 response

### iOS (AN-3B2D-2)

- `BT-iOS-01..04`: `BallTrajectoryOverlayView` — empty points, current marker, trail rendering, manual point styling
- `BT-iOS-05..07`: ViewModel trajectory polling start/stop, status transitions
- `BT-iOS-08..09`: Fallback to event-snapshot when trajectory unavailable (status=failed, 501)
- `BT-iOS-10`: "Tracking lost" state — no point in ±500ms

---

*Implementáció NEM kezdődhet el jóváhagyás nélkül.*

*A meglévő event-snapshot pipeline és a manuális labda-jelölés (AN-3B2B-1/2C-1) változatlan marad — az AN-3B2D opcionálisan ráépül.*
