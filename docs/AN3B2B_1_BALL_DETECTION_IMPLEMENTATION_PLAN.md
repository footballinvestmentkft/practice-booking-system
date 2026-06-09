# AN-3B2B-1 — Ball Detection (BDT) — Részletes Implementációs Terv

Státusz: **TERV v3 — implementáció nem kezdődött el.**  
Előfeltétel: PR #300 merged (2026-06-17, main HEAD `87d34bab`).  
Alembic head: `2026_06_17_1200`.  
Terv dátuma: 2026-06-17. Frissítve: 2026-06-17 (type-aware + skill forward-compat).  
Skill integrációs audit: `docs/AN3B2B_1_SKILL_INTEGRATION_AUDIT.md`.

---

## 0. Összefoglaló

Az AN-3B2B-1 PR két architekturális elemet vezet be:

1. **`training_video_type`** — a videó edzéstípusát jelölő mező (`juggling`,
   `gan_footvolley`, `gan_foottennis`), amely a feltöltéstől kezdve végigkíséri a
   videót, és a downstream analysis pipeline-t típusonként specializálhatóvá teszi.

2. **Ball detection** — automatizált labda-pozíció detekció contact event
   frame-jeiből, type-aware dispatcher architektúrával. Első verzióban közös
   MobileNet SSD v2 (Apache-2.0) modell, de az adatmodell és a task routing már
   felkészítve per-type modellekre.

A detekció kizárólag `annotation_review_status = 'confirmed'` állapotú események
esetén fut, explicit admin trigger hatására (nem automata háttérfolyamat).

---

## 1. Döntési napló

| Kérdés | Döntés |
|---|---|
| Trigger | Explicit admin endpoint; `annotation_review_status = 'confirmed'` szükséges |
| Automatizmus | Nincs. Első verzióban manuális trigger. |
| Model licence | MobileNet SSD v2 (Apache-2.0) első körben; YOLOv8n csak jogi clearance után |
| Model storage | `scripts/download_ml_models.py` + kontrollált privát source |
| `approved` vs. `confirmed` | `confirmed` — nem vezetünk be új enum értéket |
| **Type-aware layer** | **Igen — `training_video_type` mező a `juggling_videos` táblán** |
| **Támogatott típusok** | **`juggling`, `gan_footvolley`, `gan_foottennis`** |
| **Fallback** | **Meglévő videók type nélkül → `juggling` (server_default + backfill)** |
| **Skill update** | **NEM ebben a PR-ben. Kizárólag mérési adat.** |
| **Skill pipeline modulok** | **NEM módosítjuk** (football_skill_service, segment_reward_service, virtual_training_metrics, tournament_participation_service) |
| **Forward-compat** | **Az adatmodell kompatibilis a jövőbeli skill pipeline-nal (triple-gate review model)** |

---

## 2. Type-aware analysis layer — architekturális terv

### 2.1 Koncepció

A `training_video_type` a videó edzéstípusát jelöli. Ez a mező határozza meg:
- Milyen analysis pipeline fut a videón (ball detection modell, paraméterei)
- Milyen taxonomy tartozik hozzá (jelenleg mind a `contact_types_v1.json`-t használja)
- Milyen metrikákat számol a Phase 2B-3 movement metrics (jövőbeli differenciálás)
- Hogyan csoportosulnak a videók az iOS listanézetben (jövőbeli szűrő)

### 2.2 Típus definíciók

| Típus | Leírás | Ball detection modell (v1) |
|---|---|---|
| `juggling` | Labdazsonglőrözés (solo, egyéni) | MobileNet SSD v2 sports_ball |
| `gan_footvolley` | GAN foot-volley edzésmeccs | MobileNet SSD v2 sports_ball |
| `gan_foottennis` | GAN foot-tennis edzésmeccs | MobileNet SSD v2 sports_ball |

Első implementációban **azonos modell** mindhárom típusra. A dispatcher
architektúra lehetővé teszi, hogy később típusonként eltérő modellt (pl.
fine-tuned foot-volley detector) adjunk hozzá kizárólag config-változtatással,
kód nélkül.

### 2.3 Bővíthetőség

Új sporttípus hozzáadása a jövőben:

1. `JugglingTrainingVideoType` enum-ban új érték (pl. `rondo`, `wall_pass`)
2. CHECK constraint migration (ALTER TABLE ADD CHECK value)
3. `ANALYSIS_MODEL_REGISTRY`-ben új entry (model path + COCO class + config)
4. iOS `TrainingVideoTypePicker` automatikusan megjeleníti (enum-driven)
5. **Nincs szükséges**: új endpoint, új service, új Celery task, új DB tábla

---

## 3. DB migration

### 3.1 Migration 1: `training_video_type` oszlop a `juggling_videos` táblán

**Migration file**: `alembic/versions/2026_06_18_1000_add_training_video_type.py`

**Alembic head (down_revision)**: `2026_06_17_1200`

```sql
-- Upgrade
ALTER TABLE juggling_videos
    ADD COLUMN training_video_type VARCHAR(30) NOT NULL DEFAULT 'juggling';

ALTER TABLE juggling_videos
    ADD CONSTRAINT ck_juggling_videos_training_video_type
    CHECK (training_video_type IN ('juggling', 'gan_footvolley', 'gan_foottennis'));

COMMENT ON COLUMN juggling_videos.training_video_type IS
    'Training activity type. Determines which analysis model runs. '
    'Default juggling for backward compatibility.';
```

**Backfill**: a `DEFAULT 'juggling'` server_default automatikusan kitölti az összes
meglévő sort. Nincs külön `UPDATE` szükséges, mert a `NOT NULL DEFAULT 'juggling'`
new-column-with-default az összes meglévő sorra azonnal érvényes (PostgreSQL
11+-ban ez instant, nem full table rewrite).

**Rollback**: `ALTER TABLE juggling_videos DROP COLUMN training_video_type` —
biztonságos, nincs adat-veszteség (meglévő sorok a default-ot kapták).

### 3.2 Migration 2: `juggling_ball_detections` tábla

**Migration file**: `alembic/versions/2026_06_18_1100_add_juggling_ball_detections.py`

**Down_revision**: `2026_06_18_1000`

```sql
CREATE TABLE juggling_ball_detections (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_event_id     UUID NOT NULL
                         REFERENCES juggling_contact_events(id) ON DELETE CASCADE,
    video_id             UUID NOT NULL
                         REFERENCES juggling_videos(id) ON DELETE CASCADE,

    -- Detekciós eredmény
    detection_source     VARCHAR(40) NOT NULL,
    ball_x               FLOAT,
    ball_y               FLOAT,
    confidence           FLOAT,

    -- World coordinates (NULL amíg pitch_config nincs → AN-3B2B-2)
    world_x_m            FLOAT,
    world_y_m            FLOAT,

    -- Metadata
    model_version        VARCHAR(60),
    image_width_px       INTEGER,
    image_height_px      INTEGER,
    no_ball_detected     BOOLEAN NOT NULL DEFAULT FALSE,
    excluded_from_training BOOLEAN NOT NULL DEFAULT TRUE,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ux_juggling_ball_detections_event
        UNIQUE (contact_event_id),
    CONSTRAINT ck_juggling_ball_detections_source
        CHECK (detection_source IN ('mobilenet_ssd_v2', 'manual')),
    CONSTRAINT ck_juggling_ball_detections_confidence
        CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    CONSTRAINT ck_juggling_ball_detections_coords
        CHECK (
            (no_ball_detected = TRUE AND ball_x IS NULL AND ball_y IS NULL)
            OR
            (no_ball_detected = FALSE AND ball_x IS NOT NULL AND ball_y IS NOT NULL)
        )
);

CREATE INDEX ix_juggling_ball_detections_video_id
    ON juggling_ball_detections(video_id);
```

**Megjegyzés a `detection_source` CHECK-hez**: ha a jövőben típusonként eltérő
modell lesz, a CHECK-et bővíteni kell (pl. `'footvolley_detector_v1'`). Ez egy
egyszerű `ALTER TABLE ... DROP CONSTRAINT + ADD CONSTRAINT` migration.

A `model_version` VARCHAR(60) szándékosan tágabb, mint a `detection_source` —
itt a pontos model verziószám kerül (pl. `mobilenet_ssd_v2_coco_2018_03_29_onnx`),
a `detection_source` a modell család azonosítója.

### 3.3 Rollback terv

Mindkét migration önállóan rollback-elhető:
- `2026_06_18_1100`: `DROP TABLE juggling_ball_detections`
- `2026_06_18_1000`: `ALTER TABLE juggling_videos DROP COLUMN training_video_type`

Nincs cross-table FK vagy trigger — teljes rollback két lépésben lehetséges.

---

## 4. Model layer

### 4.1 `app/models/juggling.py` — Enum + Video model változás

**Új enum** (a meglévő enum-ok mellé, ~87. sor):

```python
class JugglingTrainingVideoType(str, enum.Enum):
    juggling        = "juggling"
    gan_footvolley  = "gan_footvolley"
    gan_foottennis  = "gan_foottennis"
```

**`JugglingVideo` model bővítése** (az `upload_source` oszlop után, ~144. sor):

```python
training_video_type = Column(
    String(30), nullable=False,
    default=JugglingTrainingVideoType.juggling.value,
    server_default="juggling",
    comment="Training activity type: juggling | gan_footvolley | gan_foottennis",
)
```

**`__table_args__` bővítés** a `JugglingVideo`-n:

```python
CheckConstraint(
    "training_video_type IN ('juggling','gan_footvolley','gan_foottennis')",
    name="ck_juggling_videos_training_video_type",
),
```

### 4.2 `JugglingBallDetection` model (ÚJ class)

Változatlan a korábbi tervhez képest, kivéve:
- `detection_source` CHECK: `'mobilenet_ssd_v2'` (nem `'yolo_coco_v8n'`)
- `model_version` VARCHAR(60) (tágabb a pontos verzió-stringhez)

---

## 5. Schema layer

### 5.1 Upload flow — `training_video_type` integrálás

**`JugglingUploadInitRequest` bővítés:**

```python
class JugglingUploadInitRequest(BaseModel):
    source_type:          str = Field(...)
    upload_source:        str = Field(default="unknown")
    training_video_type:  str = Field(default="juggling",
                                       description="juggling | gan_footvolley | gan_foottennis")
    client_reported_metadata: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate_enums(self) -> "JugglingUploadInitRequest":
        # ... meglévő source_type/upload_source validáció ...
        valid_training_types = {"juggling", "gan_footvolley", "gan_foottennis"}
        if self.training_video_type not in valid_training_types:
            raise ValueError(
                f"training_video_type must be one of {sorted(valid_training_types)}, "
                f"got {self.training_video_type!r}"
            )
        return self
```

**`JugglingVideoItemOut` bővítés:**

```python
class JugglingVideoItemOut(BaseModel):
    # ... meglévő mezők ...
    training_video_type: str   # ← ÚJ
```

**Backward compat**: a `training_video_type` default `"juggling"`, tehát régi
kliensek, amelyek nem küldik a mezőt, automatikusan `juggling` típusú videót
hoznak létre. A meglévő iOS `JugglingUploadInitBody` (`sourceType` + `uploadSource`)
továbbra is működik — a hiányzó `training_video_type` a Pydantic default-ot kapja.

### 5.2 Ball detection schemák

Változatlan a korábbi tervhez képest.

### 5.3 Admin trigger bővítés

```python
class BallDetectionTriggerRequest(BaseModel):
    training_video_type: Optional[str] = None  # ha megadva, csak az adott típus videóit

class BallDetectionTriggerResult(BaseModel):
    video_id:               uuid.UUID
    training_video_type:    str
    model_used:             str
    events_queued:          int
    events_skipped:         int
    skipped_reasons:        list[str]
```

---

## 6. Service layer

### 6.1 `video_service.create_pending()` bővítés

```python
def create_pending(
    user_id: int,
    source_type: str,
    upload_source: str,
    training_video_type: str,          # ← ÚJ paraméter
    client_reported_metadata: ...,
    db: Session,
) -> JugglingVideo:
    video = JugglingVideo(
        ...,
        training_video_type=training_video_type,  # ← ÚJ
    )
```

**Endpoint változás** (`juggling_videos.py` / `upload_init`):

```python
video = video_service.create_pending(
    user_id=current_user.id,
    source_type=body.source_type,
    upload_source=body.upload_source,
    training_video_type=body.training_video_type,   # ← ÚJ
    client_reported_metadata=body.client_reported_metadata,
    db=db,
)
```

### 6.2 Analysis Model Registry (ÚJ)

**`app/services/juggling/analysis_model_registry.py`** (ÚJ fájl, ~60 sor)

```python
@dataclass(frozen=True)
class AnalysisModelConfig:
    model_path_key:    str       # config key → settings attr
    detection_source:  str       # CHECK constraint value (pl. 'mobilenet_ssd_v2')
    model_version:     str       # pontos verzió (pl. 'mobilenet_ssd_v2_coco_2018_03_29')
    target_class_id:   int       # COCO class ID (37 = sports_ball)
    target_class_name: str       # human-readable (pl. 'sports_ball')
    input_size:        int       # input image size (pl. 300 MobileNet, 640 YOLO)
    confidence_threshold: float  # minimum confidence (pl. 0.3)


ANALYSIS_MODEL_REGISTRY: dict[str, AnalysisModelConfig] = {
    "juggling": AnalysisModelConfig(
        model_path_key="BALL_DETECTION_MODEL_PATH",
        detection_source="mobilenet_ssd_v2",
        model_version="mobilenet_ssd_v2_coco_2018_03_29_onnx",
        target_class_id=37,
        target_class_name="sports_ball",
        input_size=300,
        confidence_threshold=0.3,
    ),
    "gan_footvolley": AnalysisModelConfig(
        model_path_key="BALL_DETECTION_MODEL_PATH",  # AZONOS modell v1-ben
        detection_source="mobilenet_ssd_v2",
        model_version="mobilenet_ssd_v2_coco_2018_03_29_onnx",
        target_class_id=37,
        target_class_name="sports_ball",
        input_size=300,
        confidence_threshold=0.3,
    ),
    "gan_foottennis": AnalysisModelConfig(
        model_path_key="BALL_DETECTION_MODEL_PATH",  # AZONOS modell v1-ben
        detection_source="mobilenet_ssd_v2",
        model_version="mobilenet_ssd_v2_coco_2018_03_29_onnx",
        target_class_id=37,
        target_class_name="sports_ball",
        input_size=300,
        confidence_threshold=0.3,
    ),
}

_FALLBACK_TYPE = "juggling"

def get_model_config(training_video_type: str) -> AnalysisModelConfig:
    return ANALYSIS_MODEL_REGISTRY.get(
        training_video_type,
        ANALYSIS_MODEL_REGISTRY[_FALLBACK_TYPE],
    )
```

**Bővítés a jövőben**: új típushoz vagy új modellhez csak egy entry a dict-ben.
Ha típusonként eltérő modell kell:
- Új config key (pl. `FOOTVOLLEY_DETECTION_MODEL_PATH`)
- Új ONNX modell a `app/ml_models/` alatt
- Eltérő `target_class_id` (ha nem COCO sports_ball)
- **Nincs kódváltozás** a service/task/endpoint rétegben

### 6.3 `ball_detection_service.py` — type-aware dispatch

```python
from .analysis_model_registry import get_model_config

def run_ball_detection(video_id, event_id, db) -> JugglingBallDetection:
    video = _get_video(video_id, db)
    event = _get_event(event_id, video, db)

    config = get_model_config(video.training_video_type)   # ← TYPE-AWARE

    frame, w, h = extract_frame_at_ms(
        _video_path(video),
        event.timestamp_ms,
    )

    result = detect_ball_onnx(
        frame,
        model_path=getattr(settings, config.model_path_key),
        input_size=config.input_size,
        target_class_id=config.target_class_id,
        confidence_threshold=config.confidence_threshold,
    )

    return _upsert_detection(
        event, video, result,
        detection_source=config.detection_source,
        model_version=config.model_version,
        image_width_px=w,
        image_height_px=h,
        db=db,
    )
```

### 6.4 Frame extraction + ONNX inference

Változatlan a korábbi tervhez képest, de az ONNX wrapper paraméterezett:

```python
def detect_ball_onnx(
    frame_rgb: np.ndarray,
    model_path: str,
    input_size: int,               # ← config-ból
    target_class_id: int,          # ← config-ból
    confidence_threshold: float,   # ← config-ból
) -> tuple[float, float, float] | None:
```

---

## 7. Celery task — type-aware routing

### 7.1 `app/tasks/juggling_analysis_task.py`

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
    training_video_type: str = "juggling",   # ← TYPE-AWARE
) -> dict:
    """
    Type-aware ball detection. A training_video_type határozza meg, melyik
    modell konfiguráció fut (AnalysisModelConfig registry).

    Első verzióban mindhárom típus azonos MobileNet SSD v2 modellt használ.
    """
    config = get_model_config(training_video_type)
    # ... frame extraction + inference a config-gal ...
```

### 7.2 Admin trigger → task dispatch

```python
def trigger_ball_detection(video_id, admin_user_id, db) -> dict:
    video = _get_video_admin(video_id, db)
    config = get_model_config(video.training_video_type)  # ← TYPE-AWARE

    events = _get_confirmed_events(video, db)
    existing = _get_existing_detections(video.id, db)
    eligible = [e for e in events if e.id not in existing]

    for event in eligible:
        detect_ball_for_event.delay(
            str(video.id),
            str(event.id),
            training_video_type=video.training_video_type,   # ← ÁTADVA
        )

    return {
        "video_id": video.id,
        "training_video_type": video.training_video_type,
        "model_used": config.model_version,
        "events_queued": len(eligible),
        "events_skipped": len(events) - len(eligible),
        "skipped_reasons": [...],
    }
```

### 7.3 Celery config

Változatlan a korábbi tervhez képest (analysis queue, task route, rate limit).

---

## 8. Endpoint layer

### 8.1 Változások a meglévő upload-init endpoint-on

**`app/api/api_v1/endpoints/users/juggling_videos.py`** (MÓDOSÍTOTT):

A `upload_init` endpoint a meglévő `JugglingUploadInitRequest` bővítésével
automatikusan fogadja a `training_video_type` mezőt. Nincs új endpoint szükséges —
a meglévő endpoint bővül.

**Backward compat guard**: ha a kliens nem küldi a mezőt, a Pydantic
`Field(default="juggling")` kitölti. Régi kliensek (`JugglingUploadInitBody`
két mezővel) továbbra is működnek — a harmadik mező egyszerűen `juggling`
default-ot kap.

### 8.2 Ball detection endpoints (ÚJ)

Változatlan a korábbi tervhez képest (3 endpoint: user POST/GET + admin trigger).

### 8.3 Érintett endpoint-ok összefoglalás

| Endpoint | Változás | Típus |
|---|---|---|
| `POST /users/me/juggling/videos/upload-init` | `training_video_type` mező hozzáadva | MÓDOSÍTOTT |
| `GET /users/me/juggling/videos` | `training_video_type` a response-ban | MÓDOSÍTOTT |
| `POST /users/me/juggling/videos/{vid}/contacts/{eid}/ball-detection` | ÚJ | ÚJ |
| `GET /users/me/juggling/videos/{vid}/contacts/{eid}/ball-detection` | ÚJ | ÚJ |
| `POST /admin/juggling/videos/{vid}/trigger-ball-detection` | ÚJ + type-aware | ÚJ |

---

## 9. iOS változások

### 9.1 `TrainingVideoType` enum (ÚJ Swift fájl)

```swift
enum TrainingVideoType: String, CaseIterable, Identifiable {
    case juggling       = "juggling"
    case ganFootvolley  = "gan_footvolley"
    case ganFoottennis  = "gan_foottennis"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .juggling:      return "Zsonglőrözés"
        case .ganFootvolley: return "Foot-volley"
        case .ganFoottennis: return "Foot-tennis"
        }
    }
}
```

### 9.2 `JugglingUploadInitBody` bővítés

```swift
struct JugglingUploadInitBody: Encodable {
    let sourceType:          String
    let uploadSource:        String
    let trainingVideoType:   String     // ← ÚJ
    enum CodingKeys: String, CodingKey {
        case sourceType        = "source_type"
        case uploadSource      = "upload_source"
        case trainingVideoType = "training_video_type"   // ← ÚJ
    }
}
```

### 9.3 API client bővítés

```swift
// Protokol
func uploadInit(sourceType: String, uploadSource: String,
                trainingVideoType: String) async throws -> JugglingUploadInitResponse

// Implementáció
func uploadInit(sourceType: String, uploadSource: String,
                trainingVideoType: String = "juggling") async throws -> JugglingUploadInitResponse {
    let body = JugglingUploadInitBody(
        sourceType: sourceType, uploadSource: uploadSource,
        trainingVideoType: trainingVideoType
    )
    // ...
}
```

### 9.4 Upload UI — `TrainingVideoTypePicker`

Minimális UI a `JugglingVideoUploadView`-ban, a videó kiválasztás **előtt**:

```swift
// A JugglingVideoUploadView content-jébe, a PHPicker megnyitás előtt:
Picker("Edzéstípus", selection: $viewModel.selectedTrainingType) {
    ForEach(TrainingVideoType.allCases) { type in
        Text(type.displayName).tag(type)
    }
}
.pickerStyle(.segmented)   // iOS 13+ — 3 elemhez ideális
```

**`JugglingVideoUploadViewModel` bővítés:**
```swift
@Published var selectedTrainingType: TrainingVideoType = .juggling
```

A `runUploadPipeline()` továbbítja:
```swift
let initResp = try await apiClient.uploadInit(
    sourceType: "uploaded_video",
    uploadSource: "gallery",
    trainingVideoType: selectedTrainingType.rawValue   // ← ÚJ
)
```

### 9.5 iOS 14 kompatibilitás

- `Picker(.segmented)` — iOS 13+ ✅
- `CaseIterable` + `ForEach` — iOS 13+ ✅
- `enum` with `rawValue: String` — iOS 13+ ✅
- Nincs iOS 15+ API szükséges

---

## 10. Fallback stratégia

### 10.1 DB szint

| Szcenárió | Viselkedés |
|---|---|
| Meglévő videó (pre-migration) | `training_video_type = 'juggling'` (server_default) |
| Új videó, kliens nem küldi a mezőt | `'juggling'` (Pydantic default) |
| Új videó, kliens explicit típust küld | A megadott típus kerül mentésre |
| Ismeretlen típus a request-ben | 422 (Pydantic validator rejecteli) |

### 10.2 Celery task szint

| Szcenárió | Viselkedés |
|---|---|
| Task `training_video_type` paraméter hiányzik | Default `'juggling'` (function default) |
| Task `training_video_type` nem a registry-ben | Fallback `'juggling'` config (`_FALLBACK_TYPE`) |
| Model file nem létezik | Task `FAILED` state, `max_retries=1` után végleges |

### 10.3 iOS szint

| Szcenárió | Viselkedés |
|---|---|
| Régi iOS app (nincs `trainingVideoType` mező) | Backend Pydantic default: `'juggling'` |
| Új iOS app, user nem változtat picker-en | Default `.juggling` (`selectedTrainingType` init) |

---

## 11. Config változások

```python
# app/config.py bővítés:

# BALL_DETECTION_ENABLED — Phase 2B feature flag.
BALL_DETECTION_ENABLED: bool = False

# Default model path (MobileNet SSD v2 ONNX).
# Per-type model paths: FOOTVOLLEY_DETECTION_MODEL_PATH, etc. (jövő).
BALL_DETECTION_MODEL_PATH: str = "app/ml_models/mobilenet_ssd_v2_sports_ball.onnx"
```

**`.env.example`:**
```bash
# Ball detection (Phase 2B) — requires analysis worker + ONNX model
# BALL_DETECTION_ENABLED=false
# BALL_DETECTION_MODEL_PATH=app/ml_models/mobilenet_ssd_v2_sports_ball.onnx
```

**`.gitignore`:**
```
app/ml_models/*.onnx
app/ml_models/*.pt
```

---

## 12. Python dependencies

```
# requirements.txt kiegészítés:
opencv-python-headless>=4.8.0
```

`scipy` még NEM szükséges. `onnxruntime` és `numpy` már megvan.

---

## 13. Teljes fájllista

### Új fájlok (12)

| # | Fájl | Sor (becslés) | Leírás |
|---|---|---|---|
| 1 | `alembic/versions/2026_06_18_1000_add_training_video_type.py` | ~35 | ADD COLUMN + CHECK constraint |
| 2 | `alembic/versions/2026_06_18_1100_add_juggling_ball_detections.py` | ~60 | CREATE TABLE + indexes |
| 3 | `app/services/juggling/analysis_model_registry.py` | ~60 | AnalysisModelConfig + REGISTRY dict |
| 4 | `app/services/juggling/ball_detection_service.py` | ~180 | upsert, get, trigger, run |
| 5 | `app/services/juggling/onnx_ball_detector.py` | ~100 | ONNX session wrapper |
| 6 | `app/tasks/juggling_analysis_task.py` | ~80 | Celery task: detect_ball_for_event |
| 7 | `app/api/api_v1/endpoints/users/juggling_ball_detection.py` | ~90 | User endpoints: POST + GET |
| 8 | `app/api/api_v1/endpoints/juggling_admin_ball_detection.py` | ~60 | Admin trigger |
| 9 | `app/tests/test_juggling_ball_detection.py` | ~400 | BDT tesztek |
| 10 | `scripts/download_ml_models.py` | ~80 | Model download + SHA256 verify |
| 11 | `app/ml_models/.gitkeep` | 0 | Directory placeholder |
| 12 | `ios/.../Juggling/TrainingVideoType.swift` | ~20 | Enum |

### Módosított fájlok (12)

| # | Fájl | Változás |
|---|---|---|
| 1 | `app/models/juggling.py` | +JugglingTrainingVideoType enum, +JugglingVideo.training_video_type column, +JugglingBallDetection class |
| 2 | `app/schemas/juggling.py` | +training_video_type az upload-init + video-list-out-ban; +BallDetection schemák |
| 3 | `app/config.py` | +BALL_DETECTION_ENABLED, +BALL_DETECTION_MODEL_PATH |
| 4 | `app/services/juggling/video_service.py` | `create_pending()` +training_video_type param |
| 5 | `app/api/api_v1/endpoints/users/juggling_videos.py` | upload_init training_video_type átadás |
| 6 | `app/celery_app.py` | +analysis queue, task route, include |
| 7 | `app/api/api_v1/endpoints/users/__init__.py` | +juggling_ball_detection import |
| 8 | `app/api/api_v1/api.py` | +admin ball detection router |
| 9 | `requirements.txt` | +opencv-python-headless |
| 10 | `.gitignore` | +app/ml_models/*.onnx |
| 11 | `ios/.../Annotation/JugglingAnnotationAPIClient.swift` | uploadInit +trainingVideoType param |
| 12 | `ios/.../Upload/JugglingVideoUploadViewModel.swift` | +selectedTrainingType, pipeline átadás |
| 13 | `ios/.../Upload/JugglingVideoUploadView.swift` | +Picker(.segmented) UI |
| 14 | `ios/LFAEducationCenter.xcodeproj/project.pbxproj` | +TrainingVideoType.swift regisztráció |

**Összesen**: ~12 új + ~14 módosított = **26 érintett fájl**, ~1400 sor nettó kód.

---

## 14. Tesztterv

### 14.1 Type-aware regression tesztek (TVT-01..TVT-08)

**Cél**: a meglévő juggling flow nem törik el.

| # | Teszt | Elvárt |
|---|---|---|
| TVT-01 | upload-init **type nélkül** → video létrejön, `training_video_type='juggling'` | 201 |
| TVT-02 | upload-init `training_video_type='juggling'` explicit → helyes | 201 |
| TVT-03 | upload-init `training_video_type='gan_footvolley'` → helyes | 201 |
| TVT-04 | upload-init `training_video_type='gan_foottennis'` → helyes | 201 |
| TVT-05 | upload-init `training_video_type='unknown_sport'` → 422 | 422 |
| TVT-06 | GET video list → `training_video_type` mező jelen a response-ban | 200 |
| TVT-07 | Meglévő videó (migration után) → `training_video_type='juggling'` | DB direct |
| TVT-08 | `AnalysisModelConfig` fallback: ismeretlen type → juggling config | unit |

### 14.2 Ball detection endpoint tesztek (BDT-01..BDT-14)

Változatlan a korábbi tervhez képest (503/201/200/404/422 coverage).

### 14.3 Type-aware dispatch tesztek (BDT-D-01..BDT-D-06)

| # | Teszt | Leírás |
|---|---|---|
| BDT-D-01 | `get_model_config('juggling')` → MobileNet config | unit |
| BDT-D-02 | `get_model_config('gan_footvolley')` → MobileNet config (v1-ben azonos) | unit |
| BDT-D-03 | `get_model_config('gan_foottennis')` → MobileNet config | unit |
| BDT-D-04 | `get_model_config('unknown')` → fallback juggling config | unit |
| BDT-D-05 | Admin trigger `gan_footvolley` videón → task `training_video_type='gan_footvolley'` | mock Celery |
| BDT-D-06 | Admin trigger response `model_used` mező helyes | mock Celery |

### 14.4 Service + Admin tesztek

Változatlan (BDT-S-01..06 + BDT-A-01..04).

### 14.5 iOS tesztek (TVT-iOS-01..TVT-iOS-03)

| # | Teszt | Leírás |
|---|---|---|
| TVT-iOS-01 | `JugglingUploadInitBody` encoder: `training_video_type` mező jelen a JSON-ban | unit |
| TVT-iOS-02 | `TrainingVideoType.allCases.count == 3` | unit |
| TVT-iOS-03 | `TrainingVideoType.juggling.rawValue == "juggling"` | unit |

### 14.6 Teljes tesztszám

| Kategória | Szám |
|---|---|
| TVT (type regression) | 8 |
| BDT (ball detection endpoints) | 14 |
| BDT-D (type-aware dispatch) | 6 |
| BDT-S (service unit) | 6 |
| BDT-A (admin trigger) | 4 |
| TVT-iOS (iOS type) | 3 |
| **Összesen** | **41** |

### 14.7 CI check hatás

| Check | Hatás |
|---|---|
| Unit Tests | +38 backend teszt |
| iOS Build + Tests | +3 iOS teszt; upload init API client változás |
| OpenAPI Snapshot | 901 → 904 route (3 új endpoint) + upload-init schema változás |
| Test Baseline Check | Frissítendő |

---

## 15. Implementációs sorrend (commit-bontás)

| Commit | Scope | Leírás |
|---|---|---|
| **C1** | DB migration 1 | `training_video_type` column + CHECK + backfill |
| **C2** | Model + enum | `JugglingTrainingVideoType` enum, `JugglingVideo` column, schema bővítés |
| **C3** | Service: upload flow | `create_pending()` + upload-init endpoint bővítés |
| **C4** | TVT tesztek | TVT-01..TVT-08 (type regression, zöld CI gate) |
| **C5** | DB migration 2 | `juggling_ball_detections` tábla |
| **C6** | Model registry | `analysis_model_registry.py` + `onnx_ball_detector.py` |
| **C7** | Service: ball detection | `ball_detection_service.py` (type-aware) |
| **C8** | Celery | `juggling_analysis_task.py` + `celery_app.py` bővítés |
| **C9** | Endpoints | User POST/GET + admin trigger + router regisztráció |
| **C10** | Backend tesztek | BDT-01..14 + BDT-D-01..06 + BDT-S-01..06 + BDT-A-01..04 |
| **C11** | iOS | `TrainingVideoType.swift` + API client + upload VM + upload UI picker |
| **C12** | iOS tesztek | TVT-iOS-01..03 |
| **C13** | Scripts + CI | `download_ml_models.py` + gitignore + OpenAPI snapshot + baseline |

**Fontos**: C1–C4 az upload flow type-aware bővítése, önálló mini-milestone
(zöld CI a C4 után). C5–C10 a ball detection, C11–C12 az iOS UI.

---

## 16. Kockázatok és mitigáció

| # | Kockázat | Súlyosság | Mitigáció |
|---|---|---|---|
| 1 | MobileNet SSD v2 pontossága 360p-n | Közepes | `no_ball_detected` flag + manuális override; jövőbeli YOLOv8n upgrade |
| 2 | `training_video_type` backfill: sok meglévő sor | Alacsony | PostgreSQL `DEFAULT` instant, nincs table rewrite |
| 3 | iOS régi kliens nem küldi a mezőt | Alacsony | Pydantic `Field(default="juggling")` — teljesen backward compat |
| 4 | upload-init API contract breaking change | Alacsony | Új opcionális mező, nincs meglévő mező módosítás/törlés |
| 5 | `detection_source` CHECK: új modell → migration szükséges | Alacsony | Szándékos szeparáció: `detection_source` (család) vs. `model_version` (pontos) |
| 6 | Model registry key typo → fallback running silently | Alacsony | Warning log, de nem hiba (fallback szándékos) |
| 7 | `opencv-python-headless` CI install time | Alacsony | Pre-built wheel, ~15-30s |
| 8 | Rollback komplexitás (2 migration) | Alacsony | Mindkettő önálló rollback, nincs cross-dependency |

---

## 17. Nyitott kérdések implementáció előtt

1. **Admin trigger auth**: a jelenlegi admin endpoint-ok (`admin_biometric_review.py`)
   milyen guard-ot használnak? (`get_current_admin_user` dependency? `is_admin` role check?`)
   Ez határozza meg az admin trigger endpoint auth mintáját.

2. **Processed vs. original path**: frame extraction `processed_path` (360p) ha létezik,
   fallback `storage_path` (original). Elfogadható?

3. **Privát model source URL**: `scripts/download_ml_models.py` honnan töltsön?
   - A) Belső S3/GCS bucket
   - B) GitHub Release asset
   - C) `.env` konfigurálható `BALL_DETECTION_MODEL_URL`

4. **MobileNet SSD v2 ONNX forrás**: a TensorFlow Model Zoo ONNX export-ja használható
   (tf2onnx converter), vagy ONNX Model Zoo-ból elérhető kész ONNX? Mindkettő Apache-2.0.

---

## 18. Skill pipeline forward-compatibility

### 18.1 Scope határ

Az AN-3B2B-1 PR **kizárólag mérési adatot** ír:
- `juggling_ball_detections` — labda pozíció (screen-normalized)
- `juggling_pose_snapshots` — body keypoints (Phase 2A, már létezik)
- `training_video_type` — edzéstípus azonosító

**NEM ír**: `FootballSkillAssessment`, `SkillReward`, `SessionSegmentResult`, semmilyen
skill pipeline táblát. Nem importál, nem módosít, nem hív meg egyetlen meglévő skill
service modult sem.

### 18.2 Forward-compatible adatmodell döntések

Az AN-3B2B-1 adatmodelljében az alábbi döntések biztosítják, hogy a jövőbeli
skill pipeline (AN-3B2B-5/6/7) probléma nélkül ráépülhessen:

**1. `training_video_type` az elsődleges routing kulcs.**

A jövőbeli `VideoAnalysisSkillComputer` a `training_video_type`-ból fogja
kiválasztani a skill mapping-et:
```
juggling       → ball_control, technique, touch, balance, concentration
gan_footvolley → ball_control, volleys, heading, positioning_off, agility
gan_foottennis → ball_control, technique, touch, positioning_def, composure, decisions
```

Ehhez az AN-3B2B-1-ben a `training_video_type`:
- `NOT NULL` (nincs null-eset, amit a skill pipeline-nak kezelni kellene)
- CHECK constraint-tel védett (nincs invalid érték)
- Server default `'juggling'` (backfill a meglévő videókra)

**2. `juggling_ball_detections.video_id` FK lehetővé teszi a join-t.**

A jövőbeli `VideoAnalysisResult` aggregáció:
```sql
SELECT v.training_video_type, COUNT(bd.id), AVG(bd.confidence)
FROM juggling_videos v
JOIN juggling_ball_detections bd ON bd.video_id = v.id
WHERE v.id = :video_id
GROUP BY v.training_video_type
```

**3. `excluded_from_training = TRUE` (Policy B) minden ball detection-ön.**

A skill pipeline majd saját `excluded_from_training` logikát alkalmaz a
`VideoAnalysisResult` szintjén. A ball detection sorok Policy B szerint
mindig excluded — ez nem ütközik a leendő skill delta számítással, mert a
skill delta a `VideoAnalysisResult`-ból jön, nem közvetlenül a raw detection-ből.

**4. `detection_source` + `model_version` lehetővé teszi a modell-minőség szűrést.**

A jövőbeli review gate figyelembe veheti:
- Melyik modell futott (MobileNet vs. jövőbeli fine-tuned modell)
- Confidence threshold-ot típusonként eltérően alkalmazhat
- `no_ball_detected = TRUE` esetén a skill delta számítás kihagyja az adott eventet

**5. `UNIQUE (contact_event_id)` a ball_detections-on biztosítja, hogy egy eventhez
pontosan egy detekció tartozik** — nincs aggregációs ambiguity a skill pipeline-ban.

### 18.3 Amit az AN-3B2B-1 szándékosan NEM tartalmaz

| Elem | Ok |
|---|---|
| `VideoAnalysisResult` tábla | AN-3B2B-5 scope — az aggregáció logika még nem specifikált |
| `skill_deltas` JSONB oszlop bárhol | AN-3B2B-7 scope — skill delta csak triple-gate után |
| `SkillReward.source_type = 'VIDEO_ANALYSIS'` | AN-3B2B-7 scope |
| `analysis_status` mező | AN-3B2B-5 scope — review gate a leendő `VideoAnalysisResult`-on |
| Bármilyen import a `football_skill_service`-ből | **Tiltott** az AN-3B2B-1-ben |
| Bármilyen import a `segment_reward_service`-ből | **Tiltott** az AN-3B2B-1-ben |
| Bármilyen import a `virtual_training_metrics`-ből | **Tiltott** az AN-3B2B-1-ben |

### 18.4 Jövőbeli skill pipeline fázisok (referencia, NEM AN-3B2B-1 scope)

```
AN-3B2B-5: VideoAnalysisResult tábla + aggregáció + analysis_status state machine
AN-3B2B-6: Admin/instructor review UI + approval flow
AN-3B2B-7: VideoAnalysisSkillComputer + SkillReward(VIDEO_ANALYSIS) + triple-gate write-once

Triple-gate model:
  annotation_review_status = 'confirmed'     ← annotátor megerősítette
  + analysis_status = 'approved'             ← reviewer jóváhagyta
  + skill_deltas_applied = FALSE             ← write-once duplikáció-védelem
  = skill update engedélyezett

Skill mapping típusonként:
  juggling       → ball_control, technique, touch, balance, concentration
  gan_footvolley → ball_control, volleys, heading, positioning_off, agility
  gan_foottennis → ball_control, technique, touch, positioning_def, composure, decisions
```

---

**AN-3B2B-1 implementáció nem indult el — külön jóváhagyás szükséges a fenti tervre
és a 17. szakasz kérdéseire.**
