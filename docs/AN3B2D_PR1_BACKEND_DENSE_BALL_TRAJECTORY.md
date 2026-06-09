# AN-3B2D PR-1: Backend Dense Ball Trajectory

**Dátum:** 2026-06-17  
**Branch:** `feat/an3b2d-1-dense-ball-trajectory` (from `main`)  
**Státusz:** Terv — jóváhagyásra vár  
**Előfeltétel:** Nincs (első PR a sorozatban)  
**Nem módosított fájlok:** `juggling_analysis_task.py`, `onnx_ball_detector.py`, `frame_extractor.py`

---

## 1. Scope összefoglalás

| Elem | Leírás |
|------|--------|
| DB migration | `juggling_ball_trajectories` tábla + `ball_trajectory_status` oszlop a `juggling_videos`-n |
| Celery task | `dense_ball_trajectory_task` — ÚJ fájl, importálja a meglévő detector + extractor modult |
| Tracker | `KalmanBallTracker` — saját implementáció, 0 új dependency |
| API | GET `/ball-trajectory` + POST `/ball-trajectory/manual-seed` |
| Feature flag | `BALL_TRAJECTORY_ENABLED=false` |
| Auto-trigger | `transcode_video_task` → `analyze_video_task` → `dense_ball_trajectory_task` chain |
| Tesztek | 20 backend tesztek (BT-01..BT-20) |

---

## 2. DB migration

**Fájl:** `alembic/versions/2026_06_18_1500_add_juggling_ball_trajectories.py`

Revision chain: ← `2026_06_18_1400` (utolsó meglévő)

### 2.1 Új tábla: `juggling_ball_trajectories`

```sql
CREATE TABLE juggling_ball_trajectories (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    video_id        UUID NOT NULL
                    REFERENCES juggling_videos(id) ON DELETE CASCADE,
    frame_ms        INTEGER NOT NULL,
    ball_x          FLOAT,              -- normalized [0,1], NULL if lost
    ball_y          FLOAT,              -- normalized [0,1], NULL if lost
    confidence      FLOAT,              -- detector confidence [0,1]
    is_manual       BOOLEAN NOT NULL DEFAULT FALSE,
    tracking_state  VARCHAR(20) NOT NULL DEFAULT 'detected',
    model_version   VARCHAR(60),
    image_width_px  INTEGER,
    image_height_px INTEGER,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
```

**Constraintek:**

```sql
-- Egy frame_ms per videó (UPSERT-nál conflict target)
CREATE UNIQUE INDEX ux_ball_traj_video_frame
    ON juggling_ball_trajectories(video_id, frame_ms);

-- Gyors lekérdezés: frame ablak per videó
CREATE INDEX idx_ball_traj_video_ms
    ON juggling_ball_trajectories(video_id, frame_ms);

-- tracking_state enum
ALTER TABLE juggling_ball_trajectories ADD CONSTRAINT
    ck_ball_traj_tracking_state
    CHECK (tracking_state IN ('detected', 'predicted', 'lost', 'manual_seed'));

-- Ha ball_x/ball_y NULL, tracking_state KELL legyen 'lost'
-- Ha ball_x/ball_y NOT NULL, tracking_state NEM lehet 'lost'
ALTER TABLE juggling_ball_trajectories ADD CONSTRAINT
    ck_ball_traj_coords_state
    CHECK (
        (tracking_state = 'lost' AND ball_x IS NULL AND ball_y IS NULL)
        OR (tracking_state != 'lost' AND ball_x IS NOT NULL AND ball_y IS NOT NULL)
    );
```

**tracking_state értékek:**

| Érték | Jelentés |
|-------|----------|
| `detected` | Az ONNX detector megtalálta a labdát ezen a frame-en |
| `predicted` | A Kalman filter extrapolálta a pozíciót (detector miss, de ≤5 consecutive) |
| `lost` | 6+ consecutive miss — ball_x/ball_y NULL |
| `manual_seed` | A felhasználó manuálisan jelölte meg a labda pozícióját |

### 2.2 Új oszlop: `juggling_videos.ball_trajectory_status`

```sql
ALTER TABLE juggling_videos ADD COLUMN
    ball_trajectory_status VARCHAR(20) DEFAULT NULL;

ALTER TABLE juggling_videos ADD CONSTRAINT
    ck_juggling_videos_ball_trajectory_status
    CHECK (ball_trajectory_status IN ('pending', 'processing', 'complete', 'failed')
           OR ball_trajectory_status IS NULL);
```

NULL = feature nem fut / régi videó. `pending` = a task ütemezve van.

### 2.3 SQLAlchemy model kiegészítés

**`app/models/juggling.py`** — ÚJ osztály hozzáadása + JugglingVideo oszlop:

```python
class JugglingBallTrajectory(Base):
    __tablename__ = "juggling_ball_trajectories"

    id              = Column(UUID(as_uuid=True), primary_key=True,
                             server_default=text("gen_random_uuid()"))
    video_id        = Column(UUID(as_uuid=True),
                             ForeignKey("juggling_videos.id", ondelete="CASCADE"),
                             nullable=False)
    frame_ms        = Column(Integer, nullable=False)
    ball_x          = Column(Float, nullable=True)
    ball_y          = Column(Float, nullable=True)
    confidence      = Column(Float, nullable=True)
    is_manual       = Column(Boolean, nullable=False, default=False)
    tracking_state  = Column(String(20), nullable=False, default="detected")
    model_version   = Column(String(60), nullable=True)
    image_width_px  = Column(Integer, nullable=True)
    image_height_px = Column(Integer, nullable=True)
    created_at      = Column(DateTime(timezone=True), nullable=False,
                             server_default=text("now()"))
```

**JugglingVideo** — 1 új oszlop:

```python
ball_trajectory_status = Column(
    String(20), nullable=True, default=None,
    comment="pending / processing / complete / failed — dense ball tracking lifecycle"
)
```

---

## 3. Celery task: dense_ball_trajectory_task

**Fájl:** `app/tasks/juggling_trajectory_task.py` (ÚJ)

### 3.1 Fő logika: `run_dense_ball_trajectory()`

```python
def run_dense_ball_trajectory(
    video_id: str,
    db: Session,
    *,
    _extract_frame=None,      # inject for testing
    _get_detector=None,        # inject for testing
    sampling_interval_ms: int = 100,
    max_consecutive_miss: int = 5,
) -> dict:
    """
    Dense ball detection + Kalman tracking for a complete video.

    Steps:
      1. Feature flag check
      2. Load video, get duration from server_detected_metadata
      3. Set ball_trajectory_status = 'processing'
      4. Frame loop: 0, 100, 200, ..., duration_ms
         a. extract_frame_at_ms (MEGLÉVŐ — nem módosítjuk)
         b. onnx_ball_detector.detect (MEGLÉVŐ — nem módosítjuk)
         c. Kalman tracker update (detect / predict / lost)
      5. Bulk INSERT results → juggling_ball_trajectories
      6. Set ball_trajectory_status = 'complete'
    """
```

### 3.2 Frame loop részletek

```python
from app.services.juggling.frame_extractor import extract_frame_at_ms
from app.services.juggling.onnx_ball_detector import get_detector
from app.services.juggling.analysis_model_registry import get_model_config

# Duration kiszámítás:
#   server_detected_metadata.duration_seconds × 1000
#   Fallback: client_reported_metadata.duration_seconds × 1000
#   Ha nincs adat: return {"status": "failed", "reason": "no duration metadata"}

config = get_model_config(video.training_video_type or "juggling")
detector = _get_detector(model_path)
tracker = KalmanBallTracker(max_miss=max_consecutive_miss)

points = []
for frame_ms in range(0, duration_ms + 1, sampling_interval_ms):
    try:
        frame_rgb, w, h = _extract_frame(video_path, frame_ms)
    except ValueError:
        # Frame extraction failed (past end of video, corrupt segment)
        points.append(TrajectoryPoint(frame_ms=frame_ms, state="lost"))
        tracker.mark_miss()
        continue

    result = detector.detect(
        frame_rgb,
        target_class_id=config.target_class_id,
        confidence_threshold=config.confidence_threshold,
    )

    if result is not None:
        cx, cy, conf = result
        smoothed_x, smoothed_y = tracker.update(cx, cy)
        points.append(TrajectoryPoint(
            frame_ms=frame_ms,
            ball_x=smoothed_x, ball_y=smoothed_y,
            confidence=conf,
            state="detected",
            image_width_px=w, image_height_px=h,
        ))
    else:
        pred = tracker.predict_only()
        if pred is not None:
            px, py = pred
            points.append(TrajectoryPoint(
                frame_ms=frame_ms,
                ball_x=px, ball_y=py,
                confidence=None,
                state="predicted",
                image_width_px=w, image_height_px=h,
            ))
        else:
            points.append(TrajectoryPoint(
                frame_ms=frame_ms,
                state="lost",
            ))
```

### 3.3 Bulk INSERT

```python
# Batch méret: 200 pont / commit (kerüljük a túl nagy tranzakciót)
BATCH_SIZE = 200

for i in range(0, len(points), BATCH_SIZE):
    batch = points[i:i + BATCH_SIZE]
    objects = [
        JugglingBallTrajectory(
            video_id=vid_uuid,
            frame_ms=p.frame_ms,
            ball_x=p.ball_x,
            ball_y=p.ball_y,
            confidence=p.confidence,
            is_manual=False,
            tracking_state=p.state,
            model_version=config.model_version,
            image_width_px=p.image_width_px,
            image_height_px=p.image_height_px,
        )
        for p in batch
    ]
    db.bulk_save_objects(objects)
    db.commit()
```

### 3.4 Sampling rate: 100ms (10 FPS)

| Videó hossz | Pontok száma | Feldolgozási idő (becsült) |
|-------------|-------------|---------------------------|
| 30 sec | 300 | ~5 sec |
| 60 sec | 600 | ~10 sec |
| 5 perc | 3000 | ~50 sec |

A frame_extractor.extract_frame_at_ms ~5-10ms / frame (OpenCV seek + read).
Az onnx_ball_detector.detect ~10-15ms / frame (SSD MobileNet v1 CPU).
Összesen: ~15-25ms / frame × 10 FPS = 150-250ms / sec videó → 4-6.5× gyorsabb mint valós idő.

### 3.5 Celery wrapper

```python
@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    queue="analysis",
    time_limit=600,         # 10 perc max (5 perces videóhoz elég)
    soft_time_limit=540,    # 9 perc soft limit → graceful stop
)
def dense_ball_trajectory_task(self, video_id: str) -> dict:
    db = SessionLocal()
    try:
        return run_dense_ball_trajectory(video_id, db)
    except SoftTimeLimitExceeded:
        _set_status(video_id, "failed", db)
        return {"status": "failed", "reason": "timeout"}
    except Exception as exc:
        db.rollback()
        _set_status(video_id, "failed", db)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"status": "failed", "reason": str(exc)}
    finally:
        db.close()
```

---

## 4. Kalman Ball Tracker

**Fájl:** `app/services/juggling/kalman_ball_tracker.py` (ÚJ)

**0 új dependency** — numpy már a projektben van (onnxruntime függőség).

### 4.1 Implementáció

```python
"""
Minimal 2D Kalman filter for ball trajectory smoothing.

State:     [x, y, vx, vy]  (position + velocity)
Measure:   [x, y]           (detector output, normalized [0,1])
No external dependencies beyond numpy (already present via onnxruntime).
"""
import numpy as np


class KalmanBallTracker:
    """
    Simple constant-velocity Kalman filter for 2D ball tracking.

    After max_miss consecutive frames without detection the tracker
    enters 'lost' state and predict_only() returns None.
    Re-initialized on next detection (or manual seed).
    """

    def __init__(self, max_miss: int = 5, dt: float = 0.1):
        self._max_miss = max_miss
        self._dt = dt
        self._initialized = False
        self._miss_count = 0

        # State transition: constant velocity
        self._F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1,  0],
            [0, 0, 0,  1],
        ], dtype=np.float64)

        # Measurement matrix: observe x, y
        self._H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0],
        ], dtype=np.float64)

        # Process noise
        self._Q = np.eye(4, dtype=np.float64) * 0.001
        self._Q[2, 2] = 0.01
        self._Q[3, 3] = 0.01

        # Measurement noise
        self._R = np.eye(2, dtype=np.float64) * 0.005

        # State and covariance (initialized on first detection)
        self._x = np.zeros(4, dtype=np.float64)
        self._P = np.eye(4, dtype=np.float64)

    @property
    def is_lost(self) -> bool:
        return not self._initialized or self._miss_count > self._max_miss

    @property
    def miss_count(self) -> int:
        return self._miss_count

    def update(self, cx: float, cy: float) -> tuple[float, float]:
        """Feed a detection. Returns smoothed (x, y)."""
        z = np.array([cx, cy], dtype=np.float64)

        if not self._initialized:
            self._x = np.array([cx, cy, 0.0, 0.0], dtype=np.float64)
            self._P = np.eye(4, dtype=np.float64)
            self._initialized = True
            self._miss_count = 0
            return (cx, cy)

        # Predict
        x_pred = self._F @ self._x
        P_pred = self._F @ self._P @ self._F.T + self._Q

        # Update
        y_res = z - self._H @ x_pred
        S = self._H @ P_pred @ self._H.T + self._R
        K = P_pred @ self._H.T @ np.linalg.inv(S)
        self._x = x_pred + K @ y_res
        self._P = (np.eye(4) - K @ self._H) @ P_pred

        self._miss_count = 0
        return (float(self._x[0]), float(self._x[1]))

    def predict_only(self) -> tuple[float, float] | None:
        """Extrapolate one step without measurement. Returns None if lost."""
        if not self._initialized:
            return None

        self._miss_count += 1
        if self._miss_count > self._max_miss:
            return None

        self._x = self._F @ self._x
        self._P = self._F @ self._P @ self._F.T + self._Q
        return (float(self._x[0]), float(self._x[1]))

    def mark_miss(self) -> None:
        """Increment miss counter without predicting."""
        self._miss_count += 1

    def seed(self, cx: float, cy: float) -> None:
        """Manual re-initialize (after user tap)."""
        self._x = np.array([cx, cy, 0.0, 0.0], dtype=np.float64)
        self._P = np.eye(4, dtype=np.float64)
        self._initialized = True
        self._miss_count = 0
```

### 4.2 Tracking viselkedés

| Helyzet | Detector | Tracker output | tracking_state |
|---------|----------|---------------|----------------|
| Labda látható | cx, cy, conf | Kalman-smoothed x, y | `detected` |
| 1-5 consecutive miss | None | Kalman predict x, y | `predicted` |
| 6+ consecutive miss | None | None (lost) | `lost` |
| Manuális seed | user x, y | Re-init → smoothed x, y | `manual_seed` |
| Új detektálás lost után | cx, cy, conf | Re-init → x, y | `detected` |

---

## 5. Manual seed / re-acquire

### 5.1 POST endpoint

```
POST /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory/manual-seed

Body:
{
    "frame_ms": 3200,
    "ball_x": 0.42,
    "ball_y": 0.71
}

Response (200):
{
    "frame_ms": 3200,
    "ball_x": 0.42,
    "ball_y": 0.71,
    "tracking_state": "manual_seed",
    "is_manual": true
}
```

### 5.2 Backend logika

```python
def manual_seed_trajectory(
    video_id: str, user_id: int, frame_ms: int,
    ball_x: float, ball_y: float, db: Session,
) -> JugglingBallTrajectory:
    """
    1. UPSERT: ha van sor ezen a frame_ms-nél → felülírjuk
    2. Ha nincs → INSERT új sor
    3. tracking_state = 'manual_seed', is_manual = True
    4. NEM futtatjuk újra a Kalman trackert — az a Celery task dolga
       (future enhancement: re-run tracker ±5 sec ablakban)
    """
```

A manuális seed jelenleg **nem triggerel** újrafeldolgozást. A pont egyszerűen bekerül a trajectory-ba mint `manual_seed`, és az iOS overlay megjeleníti. Későbbi enhancement: a manual seed triggerelhet egy partial re-run-t a Celery-ben.

### 5.3 Re-acquire

Nincs külön mechanizmus — ha a detektálás `lost` állapotba került, az a trajectory-ban marad amíg:
1. A felhasználó manuálisan seed-el → `manual_seed` pont
2. A detector egy későbbi frame-en újra megtalálja → `detected` pont, tracker re-init

---

## 6. API endpointok

**Fájl:** `app/api/api_v1/endpoints/users/juggling_ball_trajectory.py` (ÚJ)

### 6.1 GET ball-trajectory

```
GET /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory
    ?from_ms=0          (default: 0)
    &to_ms=60000        (default: videó vége)

Response (200):
{
    "status": "complete",           // pending | processing | complete | failed
    "points": [
        {
            "frame_ms": 0,
            "ball_x": null,
            "ball_y": null,
            "confidence": null,
            "is_manual": false,
            "tracking_state": "lost"
        },
        {
            "frame_ms": 100,
            "ball_x": 0.42,
            "ball_y": 0.71,
            "confidence": 0.88,
            "is_manual": false,
            "tracking_state": "detected"
        },
        ...
    ]
}
```

**Szabályok:**
- Max 600 pont / response (60 sec @ 10 FPS). Ha az ablak nagyobb → 422 "Window too large"
- Ha `ball_trajectory_status` IS NULL → 404 "No trajectory data"
- Ha `BALL_TRAJECTORY_ENABLED=false` → 503 "Ball trajectory is not enabled"
- Üres response (0 pont, de status = complete) teljesen valid (rövid videó, nincs labda)

### 6.2 POST manual-seed

```
POST /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory/manual-seed

Body:
{
    "frame_ms": 3200,       // required, >= 0
    "ball_x": 0.42,         // required, [0.0, 1.0]
    "ball_y": 0.71          // required, [0.0, 1.0]
}

Response (200 upsert / 201 create):
{
    "frame_ms": 3200,
    "ball_x": 0.42,
    "ball_y": 0.71,
    "tracking_state": "manual_seed",
    "is_manual": true
}
```

**Szabályok:**
- `BALL_TRAJECTORY_ENABLED=false` → 503
- A videónak a user-é kell legyen → 404
- `frame_ms` nem lehet negatív → 422
- A seed pont bekerül (UPSERT) a `juggling_ball_trajectories` táblába
- Ha `ball_trajectory_status` = `pending` vagy `processing`: a seed még bekerül, de a futó task felülírhatja (a task idempotens, de a manuális pont `is_manual=TRUE` védi: a task NEM írja felül `is_manual=TRUE` pontokat)

### 6.3 Router regisztráció

```python
# app/api/api_v1/api.py — juggling section
from .endpoints.users import juggling_ball_trajectory
api_router.include_router(
    juggling_ball_trajectory.router,
    prefix="/users",
    tags=["juggling"],
)
```

### 6.4 Pydantic schemas

```python
# app/schemas/juggling.py — ÚJ osztályok

class BallTrajectoryPointOut(BaseModel):
    frame_ms:       int
    ball_x:         Optional[float]
    ball_y:         Optional[float]
    confidence:     Optional[float]
    is_manual:      bool
    tracking_state: str    # detected | predicted | lost | manual_seed

    model_config = {"from_attributes": True}


class BallTrajectoryResponse(BaseModel):
    status: str                         # pending | processing | complete | failed
    points: List[BallTrajectoryPointOut]

    model_config = {"from_attributes": True}


class BallTrajectoryManualSeedRequest(BaseModel):
    frame_ms: int   = Field(..., ge=0)
    ball_x:   float = Field(..., ge=0.0, le=1.0)
    ball_y:   float = Field(..., ge=0.0, le=1.0)


class BallTrajectoryManualSeedOut(BaseModel):
    frame_ms:       int
    ball_x:         float
    ball_y:         float
    tracking_state: str
    is_manual:      bool

    model_config = {"from_attributes": True}
```

---

## 7. Auto-trigger

### 7.1 Trigger pont

A dense trajectory task a transcode → analyze lánc **után** indul. A legegyszerűbb trigger: az `analyze_video_task` (app/tasks/juggling_tasks.py) végén.

De: `juggling_tasks.py` NEM módosítható (project rule).

**Alternatíva:** Az `transcode_video_task` (app/tasks/juggling_transcode_task.py) dispatch pontjánál, Step 5 után.

De: `juggling_transcode_task.py` sem módosítható ha ugyanaz a rule.

**Megoldás:** Külön lightweight trigger task, ami polling-alapon figyeli a videók státuszát. VAGY: a `POST /complete` endpoint végén közvetlenül ütemezve.

**Legjobb megoldás:** A `POST /complete` endpoint (app/api/api_v1/endpoints/users/juggling_videos.py) már ütemezi a `transcode_video_task.delay()` hívást. Ide egy **ETA-delayed** trajectory task-ot adunk:

```python
# juggling_videos.py — complete() handler, a meglévő transcode dispatch UTÁN:
if settings.BALL_TRAJECTORY_ENABLED:
    from app.tasks.juggling_trajectory_task import dense_ball_trajectory_task
    # 120 sec ETA: a transcode + analyze tipikusan 30-60 sec
    dense_ball_trajectory_task.apply_async(
        args=[video_id],
        countdown=120,
    )
    video.ball_trajectory_status = "pending"
    db.commit()
```

Ez **1 sor módosítás** a `juggling_videos.py`-ban — nem a tilos listán van. A tilos fájlok: `juggling_analysis_task.py`, `onnx_ball_detector.py`, `frame_extractor.py`.

### 7.2 Idempotencia

A task ellenőrzi, hogy a videó transcode-ja kész-e:
```python
if video.transcode_status not in ("done", "skipped"):
    return {"status": "skipped", "reason": "transcode not done yet"}
```

Ha a transcode még nem kész (az ETA korán ért): a task egyszerűen `skipped` státusszal tér vissza. Retry: 1× auto, vagy a felhasználó az admin trigger-rel manuálisan újraindítja.

---

## 8. Feature flag

### 8.1 Config

```python
# app/config.py — Settings osztály, a meglévő BALL_DETECTION_ENABLED mellé:
BALL_TRAJECTORY_ENABLED: bool = False
```

### 8.2 Guard-ok

| Hol | Viselkedés ha False |
|-----|---------------------|
| `dense_ball_trajectory_task` | `{"status": "skipped", "reason": "BALL_TRAJECTORY_ENABLED=False"}` |
| `GET /ball-trajectory` | 503 "Ball trajectory is not enabled" |
| `POST /manual-seed` | 503 "Ball trajectory is not enabled" |
| `POST /complete` trigger | Nem ütemezi a task-ot |

### 8.3 .env.example

```env
# Dense ball trajectory tracking (AN-3B2D-1)
# BALL_TRAJECTORY_ENABLED=true
```

---

## 9. Rollback terv

| Lépés | Akció |
|-------|-------|
| 1 | `BALL_TRAJECTORY_ENABLED=false` → minden trajectory munka leáll |
| 2 | A `juggling_ball_trajectories` tábla érintetlen marad |
| 3 | Az iOS event-snapshot path (BallVideoOverlayView) automatikusan aktív |
| 4 | Alembic downgrade: `DROP TABLE juggling_ball_trajectories; ALTER TABLE juggling_videos DROP COLUMN ball_trajectory_status;` |
| 5 | Celery task regisztráció eltávolítása `celery_app.py`-ból |

A rollback nem jár adatvesztéssel — a trajectory adatok az overlay-hez kellenek, nem a core annotation-höz.

---

## 10. Celery regisztráció

**`app/celery_app.py`:**

```python
include=[
    ...
    "app.tasks.juggling_trajectory_task",    # ÚJ
],

# task_routes:
"app.tasks.juggling_trajectory_task.dense_ball_trajectory_task": {"queue": "analysis"},

# task_annotations:
"app.tasks.juggling_trajectory_task.dense_ball_trajectory_task": {
    "rate_limit": "5/m",  # max 5 videó / perc
},
```

---

## 11. Service layer

**Fájl:** `app/services/juggling/ball_trajectory_service.py` (ÚJ)

```python
"""
Ball trajectory service — dense trajectory CRUD.

No ONNX inference here (that's in the Celery task).
This module: query trajectories, upsert manual seeds, status management.
"""

def get_trajectory_window(
    video_id: str, user_id: int,
    from_ms: int, to_ms: int,
    db: Session,
) -> BallTrajectoryResponse:
    """Query trajectory points in [from_ms, to_ms] for a user's video."""

def upsert_manual_seed(
    video_id: str, user_id: int,
    frame_ms: int, ball_x: float, ball_y: float,
    db: Session,
) -> tuple[JugglingBallTrajectory, bool]:
    """UPSERT manual seed point. Returns (point, created)."""

def set_trajectory_status(
    video_id: str, status: str, db: Session,
) -> None:
    """Update ball_trajectory_status on the video."""
```

---

## 12. Tesztek

**Fájl:** `tests/unit/juggling/test_ball_trajectory.py` (ÚJ)

### 12.1 DB / Model tesztek

| Test ID | Leírás |
|---------|--------|
| BT-01 | `JugglingBallTrajectory` INSERT + query by (video_id, frame_ms) |
| BT-02 | CASCADE delete: videó törlése → trajectory pontok törlődnek |
| BT-03 | UNIQUE constraint (video_id, frame_ms) → conflict on duplicate |
| BT-04 | CHECK constraint: `tracking_state='lost'` requires NULL ball_x/ball_y |
| BT-05 | CHECK constraint: `tracking_state='detected'` requires NOT NULL ball_x/ball_y |

### 12.2 Kalman tracker tesztek

| Test ID | Leírás |
|---------|--------|
| BT-06 | First detection → returns raw coordinates (no smoothing yet) |
| BT-07 | Consecutive detections → smoothed, converges to actual position |
| BT-08 | 1-3 miss → predict_only returns extrapolated position |
| BT-09 | 6+ miss → predict_only returns None (lost) |
| BT-10 | Detection after lost → re-initializes tracker |
| BT-11 | `seed()` → re-initializes from manual position |
| BT-12 | `is_lost` property reflects miss count vs threshold |

### 12.3 Dense task tesztek (mocked detector + extractor)

| Test ID | Leírás |
|---------|--------|
| BT-13 | Happy path: 10 frames, 7 detected, 3 missed → correct point count + states |
| BT-14 | All detected → all `tracking_state='detected'` |
| BT-15 | Detector always None → tracking lost after max_miss |
| BT-16 | Feature flag off → `{"status": "skipped"}` |
| BT-17 | Video not found → `{"status": "failed"}` |
| BT-18 | Transcode not done → `{"status": "skipped"}` |
| BT-19 | is_manual=TRUE pont nem felülírt by task |

### 12.4 API endpoint tesztek

| Test ID | Leírás |
|---------|--------|
| BT-20 | GET /ball-trajectory → 503 when disabled |
| BT-21 | GET /ball-trajectory → 404 when no trajectory data (NULL status) |
| BT-22 | GET /ball-trajectory → 200 + correct points within window |
| BT-23 | GET /ball-trajectory window too large → 422 |
| BT-24 | POST /manual-seed → 201 creates point |
| BT-25 | POST /manual-seed → 200 upserts existing frame_ms |
| BT-26 | POST /manual-seed → 503 when disabled |
| BT-27 | POST /manual-seed → 404 other user's video |
| BT-28 | POST /manual-seed → 422 invalid ball_x (> 1.0) |

---

## 13. Fájl lista

| Fájl | Akció |
|------|-------|
| `alembic/versions/2026_06_18_1500_add_juggling_ball_trajectories.py` | ÚJ |
| `app/models/juggling.py` | MÓDOSÍTVA (+JugglingBallTrajectory osztály, +ball_trajectory_status oszlop) |
| `app/tasks/juggling_trajectory_task.py` | ÚJ |
| `app/services/juggling/kalman_ball_tracker.py` | ÚJ |
| `app/services/juggling/ball_trajectory_service.py` | ÚJ |
| `app/api/api_v1/endpoints/users/juggling_ball_trajectory.py` | ÚJ |
| `app/schemas/juggling.py` | MÓDOSÍTVA (+4 schema osztály) |
| `app/config.py` | MÓDOSÍTVA (+BALL_TRAJECTORY_ENABLED) |
| `app/celery_app.py` | MÓDOSÍTVA (+task regisztráció, +route, +rate limit) |
| `app/api/api_v1/api.py` | MÓDOSÍTVA (+router include) |
| `app/api/api_v1/endpoints/users/juggling_videos.py` | MÓDOSÍTVA (+trajectory dispatch a complete() handler-ben) |
| `tests/unit/juggling/test_ball_trajectory.py` | ÚJ |
| `.env.example` | MÓDOSÍTVA (+BALL_TRAJECTORY_ENABLED) |

**NEM módosított fájlok (explicit):**

| Fájl | Státusz |
|------|---------|
| `app/tasks/juggling_analysis_task.py` | TILOS |
| `app/services/juggling/onnx_ball_detector.py` | TILOS (importálva, nem módosítva) |
| `app/services/juggling/frame_extractor.py` | TILOS (importálva, nem módosítva) |

---

## 14. Licence

| Dependency | Licence | Státusz |
|------------|---------|---------|
| numpy | BSD-3-Clause | Már a projektben (onnxruntime dep) |
| onnxruntime | MIT | Már a projektben |
| opencv-python-headless | Apache-2.0 | Már a projektben |

**0 új dependency ebben a PR-ben.**

---

*Implementáció NEM kezdődhet el jóváhagyás nélkül.*
