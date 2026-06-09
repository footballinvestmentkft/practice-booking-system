# AN-3B2B — Sportelemzési Alapréteg — Teljes Audit + Implementációs Terv

Státusz: **AUDIT + TERV — implementáció nem kezdődött el.**  
Alap: PR #300 (AN-3B2A Phase 2A) merged 2026-06-17, main HEAD `87d34bab`.  
Audit dátuma: 2026-06-17.  
Frissítve: 2026-06-17 — `training_video_type` type-aware analysis layer döntés.  
Részletes AN-3B2B-1 terv: `docs/AN3B2B_1_BALL_DETECTION_IMPLEMENTATION_PLAN.md`.

---

## 0. Összefoglaló

A Phase 2A (PR #300) sikeres merge-e után a main branch tartalmaz:
- `juggling_pose_snapshots` táblát (UNIQUE per event, iOS Vision 19-joint)
- 3 Phase 2A endpointot (POST/GET pose-snapshot, PATCH rotation)
- Feature flag: `POSE_SNAPSHOT_ENABLED=False`
- 462 iOS test (0 failure), 13 backend PS-* test

A Phase 2B (AN-3B2B) célja: térbeli kontextus (labdarúgó pálya, labda, referencia-objektumok), derived metrikák (ízületi szögek), és aggregált nézetek (hőtérkép) hozzáadása. Ez **4 egymásra épülő PR-t** jelent.

---

## 1. Jelenlegi main állapot — meglévő alapok

### 1.1 DB (alembic head: `2026_06_17_1200`)

| Tábla | Állapot |
|---|---|
| `juggling_videos` | ✅ létezik |
| `juggling_contact_events` | ✅ létezik, `annotation_review_status` CHECK constraint |
| `juggling_pose_snapshots` | ✅ létezik (Phase 2A) |
| `juggling_ball_detections` | ❌ hiányzik |
| `juggling_pitch_configs` | ❌ hiányzik |
| `juggling_reference_objects` | ❌ hiányzik |

### 1.2 Backend services

| Service | Állapot |
|---|---|
| `pose_snapshot_service.py` | ✅ storage-only, upsert/fetch |
| `ball_detection_service.py` | ❌ hiányzik |
| `pitch_config_service.py` | ❌ hiányzik |
| `movement_metrics_service.py` | ❌ hiányzik |
| `heatmap_service.py` | ❌ hiányzik |

### 1.3 Celery

| Elem | Állapot |
|---|---|
| `juggling_videos` queue | ✅ létezik (transcode + quality) |
| `analysis` queue | ❌ hiányzik |
| `detect_ball_position` task | ❌ hiányzik |
| `compute_movement_metrics` task | ❌ hiányzik |
| Approval → analysis trigger hook | ❌ hiányzik |
| YOLOv8n model download script | ❌ hiányzik |

### 1.4 Python dependencies

| Package | Állapot | Megjegyzés |
|---|---|---|
| `numpy` | ✅ (via onnxruntime) | |
| `Pillow` | ✅ | |
| `onnxruntime==1.26.0` | ✅ | YOLOv8n ONNX exporthoz elegendő |
| `scipy` | ❌ hiányzik | KDE heatmaphoz szükséges |
| `ultralytics` | ❌ nem szükséges | AGPL-3.0 kockázat; ONNX-ba exportált modellt használunk runtime-ban |
| `cv2` / `opencv-python-headless` | ❌ hiányzik | Videóból frame extraction |

> **Licenc megjegyzés**: az `ultralytics` csomag AGPL-3.0. A runtime-függőség elkerülhető: a modellt egy egyszeri `scripts/adaface_onnx_export.py`-hoz hasonló script exportálja ONNX-ba, és a production worker kizárólag `onnxruntime`-ot (MIT) használ. Ez ugyanaz a minta, amit a biometrika (`adaface_onnx_export.py`) követ.

### 1.5 iOS (main-en meglévő)

| Komponens | Állapot |
|---|---|
| `PoseSnapshotService.swift` | ✅ Vision body pose |
| `PoseSnapshotOverlayView.swift` | ✅ skeleton overlay |
| `BodyZonePickerView.swift` | ✅ labeling UX |
| `EventPreviewSession.swift` | ✅ preview player |
| `EventLabelDetailView.swift` | ✅ labeling form |
| `MovementHeatmapView.swift` | ❌ hiányzik |
| Pitch config 4-point picker | ❌ hiányzik |
| `EventDetailView` metrika-kiterjesztés | ❌ hiányzik |

### 1.6 Tesztek (main baseline)

| Suite | Szám |
|---|---|
| iOS unit tesztek | 462 (0 failure) |
| Backend PS-* (pose snapshot) | 13 PASS |
| Backend BDT-* (ball detection) | ❌ nem létezik |
| Backend PCT-* (pitch config) | ❌ nem létezik |
| Backend MMT-* (movement metrics) | ❌ nem létezik |
| Backend HMP-* (heatmap) | ❌ nem létezik |

---

## 2. Phase 2B scope — 4 PR

### AN-3B2B-1: Ball Detection (BDT)

**Cél**: automatikusan érzékeli a labda pozícióját a contact event frame-jén.

**Függőségek**: nincs Phase 2B-n belüli függőség — elsőként futtatható, a Phase 2A pose snapshot infrastruktúrán kívül csak `juggling_contact_events` és `juggling_videos` kell.

**Új DB tábla**:
```sql
juggling_ball_detections (
  id UUID PK,
  contact_event_id UUID FK→juggling_contact_events ON DELETE CASCADE UNIQUE,
  detection_source VARCHAR(20) CHECK IN ('yolo_coco_v8n','manual'),
  ball_x FLOAT,                 -- screen-normalized [0,1], origin top-left
  ball_y FLOAT,
  world_x_m FLOAT NULLABLE,     -- NULL amíg pitch_config nincs
  world_y_m FLOAT NULLABLE,
  confidence FLOAT,
  excluded_from_training BOOLEAN DEFAULT TRUE,  -- Policy B
  created_at TIMESTAMPTZ DEFAULT now()
)
```

**Celery task** (`detect_ball_position`, queue `analysis`):
1. Event `annotation_review_status` → `approved` triggereli (webhook a `contact_service.py`-ban)
2. `cv2.VideoCapture` a 360p stored video-ra (local path, `storage_path` column)
3. Frame kinyerése `timestamp_ms`-nél (`cap.set(cv2.CAP_PROP_POS_MSEC, ...)`)
4. YOLOv8n ONNX futtatása `onnxruntime`-mal, class 32 (`sports_ball`) keresése
5. Max confidence bounding box → center koordináta, normált [0,1]
6. Upsert `juggling_ball_detections`

**Új config flag**: `BALL_DETECTION_ENABLED: bool = False`

**Új endpoints (2)**:
- `POST /api/v1/users/videos/{vid}/contacts/{eid}/ball-detection` — manuális override
- `GET /api/v1/users/videos/{vid}/contacts/{eid}/ball-detection` — eredmény lekérés

**Új fájlok**:
- `alembic/versions/2026_06_17_2000_add_juggling_ball_detections.py`
- `app/models/juggling.py` + `JugglingBallDetection`
- `app/schemas/juggling.py` + `BallDetectionOut`, `BallDetectionManualRequest`
- `app/services/juggling/ball_detection_service.py`
- `app/tasks/juggling_analysis_task.py` (`detect_ball_position`)
- `app/api/api_v1/endpoints/users/juggling_ball_detection.py`
- `scripts/export_yolov8n_onnx.py` (egyszeri export, nem runtime dependency)
- `app/ml_models/yolov8n_sports_ball.onnx` (gitignored)
- `tests/test_juggling_ball_detection.py` (BDT-01..BDT-12)

**Requirements**:
```
scipy>=1.11.0          # KDE heatmap (AN-3B2B-4-hez is kell)
opencv-python-headless>=4.8.0  # frame extraction
```

**Tesztek (BDT-01..BDT-12)**:
1. BDT-01: `BALL_DETECTION_ENABLED=False` → 503
2. BDT-02: manuális override POST → 201, DB-ben létrehozva
3. BDT-03: manuális override POST ismeretlen video_id → 404
4. BDT-04: manuális override POST más user video-ja → 404
5. BDT-05: manuális override POST érvénytelen koordináta (> 1.0) → 422
6. BDT-06: GET → 200, visszaadja a detekciót
7. BDT-07: GET nem létező esemény → 404
8. BDT-08: GET nincs még detekció → 404
9. BDT-09: manuális override POST upsert — második POST felülírja az elsőt
10. BDT-10: `detection_source='yolo_coco_v8n'` mock task → DB-ben tárolt
11. BDT-11: `excluded_from_training` mindig True (Policy B)
12. BDT-12: `world_x_m` NULL, ha nincs pitch_config (következetesség)

---

### AN-3B2B-2: Pitch Config + Reference Objects (PFC)

**Cél**: pályakalibrálás — 4 sarokpont (kép ↔ valóvilág koordinátapárok), homográfia mátrix, referencia-objektumok.

**Függőségek**: AN-3B2B-1 előtt vagy párhuzamosan futtatható (nincs közvetlen függőség a ball detection-től).

**Új DB táblák (2)**:
```sql
juggling_pitch_configs (
  id UUID PK,
  video_id UUID FK→juggling_videos UNIQUE,
  corners_image JSONB NOT NULL,   -- [{x,y}, ...] x4 screen-normalized
  corners_world JSONB NOT NULL,   -- [{x_m,y_m}, ...] x4 valós koordináta
  homography JSONB NOT NULL,      -- 3×3 mátrix [[a,b,c],[d,e,f],[g,h,i]]
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
)

juggling_reference_objects (
  id UUID PK,
  video_id UUID FK→juggling_videos ON DELETE CASCADE,
  object_type VARCHAR(20) CHECK IN ('cone','buoy','pole','goal_post','disc'),
  image_x FLOAT,   -- screen-normalized
  image_y FLOAT,
  world_x_m FLOAT NULLABLE,
  world_y_m FLOAT NULLABLE,
  created_at TIMESTAMPTZ DEFAULT now()
)
```

**Homográfia számítás**: `numpy.linalg` + custom DLT implementáció (nincs opencv szükség a számításhoz — ha `opencv-python-headless` már requirement az AN-3B2B-1-ben, akkor `cv2.getPerspectiveTransform` is használható).

**Új endpoints (5)**:
- `POST /api/v1/users/videos/{vid}/pitch-config` — 4 pont + world koordináta + homográfia számítás
- `GET /api/v1/users/videos/{vid}/pitch-config` — aktuális konfiguráció
- `DELETE /api/v1/users/videos/{vid}/pitch-config` — törlés
- `POST /api/v1/users/videos/{vid}/reference-objects` — referencia pont hozzáadása
- `DELETE /api/v1/users/videos/{vid}/reference-objects/{id}` — törlés

**iOS**: 4-point pitch calibration picker — `PitchCalibrationView.swift`:
- `EventStillFrameGenerator`-ral kinyert still frame a háttérben
- 4 draggable marker (`DragGesture`, `@State var corners: [CGPoint]`)
- Real-world koordináta bevitel minden sarokhoz (méterben)
- "Kalibrálás mentése" gomb → POST `/pitch-config`

**Tesztek (PCT-01..PCT-10)**:
1. PCT-01: POST → 201, homográfia kiszámítva és tárolva
2. PCT-02: POST érvénytelen corners (< 4 pont) → 422
3. PCT-03: GET → 200, mátrix visszaadva
4. PCT-04: GET nincs konfig → 404
5. PCT-05: POST upsert — második POST frissíti az elsőt
6. PCT-06: DELETE → 204
7. PCT-07: DELETE nem létező → 404
8. PCT-08: reference object POST → 201
9. PCT-09: reference object POST más user videója → 404
10. PCT-10: homográfia alkalmazása: adott image pont → helyes world koordináta (numerikus ellenőrzés)

---

### AN-3B2B-3: Movement Metrics (MMT)

**Cél**: a pose snapshot JSONB-ből derived metrikák (ízületi szögek), composite analysis endpoint.

**Függőségek**: AN-3B2B-1 (ball position) és AN-3B2B-2 (pitch mapping) szükséges a teljes composite válaszhoz, de a service maga fallback-el, ha ezek nem állnak rendelkezésre.

**Számítás (pure Python, numpy)**:
- `trunk_lean_deg`: a gerinc vetülete a függőlegestől (left_hip + right_hip mid → left_shoulder + right_shoulder mid szög)
- `knee_flexion_deg`: bal/jobb knee joint szög (hip → knee → ankle vektorizálás)
- `ankle_dorsiflexion_deg`: boka szög (knee → ankle → toe)
- Minden metrika `null`, ha a szükséges joint-ok confidence < 0.3

**Nincs új DB tábla** — a metrikák számítása on-the-fly a `juggling_pose_snapshots.keypoints` JSONB-ből. (Ha a jövőben cache-elni kell, az egy Phase 2C döntés.)

**Új endpoint (1)**:
```
GET /api/v1/users/videos/{vid}/contacts/{eid}/analysis
```
Composite válasz:
```json
{
  "event": { ...ContactEventOut... },
  "pose": { ...PoseSnapshotOut vagy null... },
  "ball_detection": { ...BallDetectionOut vagy null... },
  "pitch_config_applied": true/false,
  "metrics": {
    "trunk_lean_deg": 12.4,
    "left_knee_flexion_deg": 34.1,
    "right_knee_flexion_deg": 31.8,
    "left_ankle_dorsiflexion_deg": null,
    "right_ankle_dorsiflexion_deg": null
  }
}
```

**iOS `EventDetailView` kiterjesztés**:
- Meglévő `EventDetailView.swift` (ha van ilyen a main-en) vagy az `EventTimelineView` row kiterjesztése
- Mutatja: `trunk_lean_deg`, `left/right_knee_flexion_deg`, labda pozíció (ha van)
- Csak akkor rendereli a metrika szekciót, ha az analysis endpoint-ot sikeresen lekérte
- iOS 14 compatible: `List`/`VStack`, nincs `Table`, nincs `chart`

**Tesztek (MMT-01..MMT-08)**:
1. MMT-01: GET /analysis → 200, event + null pose/ball/metrics (nincs adat)
2. MMT-02: GET /analysis pose létezik → metrikák számítva
3. MMT-03: GET /analysis pose + pitch config → `pitch_config_applied=true`, `world_x_m` nem null
4. MMT-04: `compute_movement_metrics` unit: helyes `trunk_lean_deg` adott keypoints-ra
5. MMT-05: `compute_movement_metrics` unit: missing joint → null, nem dob
6. MMT-06: `compute_movement_metrics` unit: low confidence joint (< 0.3) → null metrika
7. MMT-07: GET /analysis más user → 404
8. MMT-08: GET /analysis nem létező event → 404

---

### AN-3B2B-4: Heatmap (HMP)

**Cél**: labda-érintési pozíciók 2D sűrűségtérkép (scipy KDE), iOS `MovementHeatmapView`.

**Függőségek**: AN-3B2B-3 (ball positions kötelező, pitch config opcionális de javasolt az értelmes térképhez).

**Számítás**:
- Input: az összes `juggling_ball_detections` sor a videohoz (ahol `world_x_m` IS NOT NULL)
- Fallback (nincs pitch config): `ball_x`/`ball_y` screen koordinátákból normalizált hőtérkép
- `scipy.stats.gaussian_kde` → 20×14 rácsra interpolálva (standard 105m×68m pálya arány)
- Eredmény: 20×14 float mátrix + scatter pontok listája

**Új endpoint (1)**:
```
GET /api/v1/users/videos/{vid}/movement-summary
```
Válasz:
```json
{
  "has_pitch_config": true,
  "point_count": 47,
  "scatter_world": [{"x_m": 12.3, "y_m": 8.7, "event_id": "..."}, ...],
  "scatter_screen": [...],
  "heatmap_grid": [[0.12, 0.34, ...], ...],  -- 20×14
  "grid_rows": 14,
  "grid_cols": 20
}
```

**iOS `MovementHeatmapView`**:
- iOS 14 kompatibilis: `Path`-alapú pályakontúr (nem `Canvas`)
- Hőtérkép: nested `ForEach` + `Rectangle`, szín: `hsl(200, 70%, lerp(40%, 90%, density))`
- Scatter pontok: kis körök overlay
- "Nincs elegendő adat" placeholder, ha `point_count < 5`
- Integrálás: `JugglingVideoListView` vagy egy új `VideoAnalysisView` push-on megnyílik

**Tesztek (HMP-01..HMP-06)**:
1. HMP-01: GET → 200, `point_count=0`, üres scatter, null grid
2. HMP-02: GET → `point_count > 0`, heatmap_grid 14 sor × 20 col
3. HMP-03: GET nincs pitch config → `has_pitch_config=false`, screen koordináták
4. HMP-04: GET más user → 404
5. HMP-05: KDE unit: 5 pont → 20×14 grid összes értéke > 0
6. HMP-06: KDE unit: 1 pont → nem dob (scipy KDE edge case)

---

## 3. Teljes fájllista (tervezett)

### Backend

| Fájl | Típus |
|---|---|
| `alembic/versions/2026_06_17_2000_add_juggling_ball_detections.py` | ÚJ |
| `alembic/versions/2026_06_17_2100_add_juggling_pitch_config_reference_objects.py` | ÚJ |
| `app/models/juggling.py` | MÓDOSÍTOTT (+3 model class) |
| `app/schemas/juggling.py` | MÓDOSÍTOTT (+BallDetection/PitchConfig/ReferenceObj/Analysis/MovementSummary schemas) |
| `app/config.py` | MÓDOSÍTOTT (+BALL_DETECTION_ENABLED) |
| `app/services/juggling/ball_detection_service.py` | ÚJ |
| `app/services/juggling/pitch_config_service.py` | ÚJ |
| `app/services/juggling/movement_metrics_service.py` | ÚJ |
| `app/services/juggling/heatmap_service.py` | ÚJ |
| `app/tasks/juggling_analysis_task.py` | ÚJ |
| `app/celery_app.py` | MÓDOSÍTOTT (analysis task include) |
| `app/api/api_v1/endpoints/users/juggling_ball_detection.py` | ÚJ |
| `app/api/api_v1/endpoints/users/juggling_pitch_config.py` | ÚJ |
| `app/api/api_v1/endpoints/users/juggling_movement_summary.py` | ÚJ |
| `app/api/api_v1/__init__.py` / router | MÓDOSÍTOTT |
| `app/tests/test_juggling_ball_detection.py` | ÚJ (BDT-01..12) |
| `app/tests/test_juggling_pitch_config.py` | ÚJ (PCT-01..10) |
| `app/tests/test_juggling_movement_metrics.py` | ÚJ (MMT-01..08) |
| `app/tests/test_juggling_heatmap.py` | ÚJ (HMP-01..06) |
| `scripts/export_yolov8n_onnx.py` | ÚJ (egyszeri export script) |
| `requirements.txt` | MÓDOSÍTOTT (+scipy, +opencv-python-headless) |

### iOS

| Fájl | Típus |
|---|---|
| `ios/.../Juggling/Annotation/Screen/PitchCalibrationView.swift` | ÚJ |
| `ios/.../Juggling/Annotation/Screen/MovementHeatmapView.swift` | ÚJ |
| `ios/.../Juggling/JugglingAnnotationAPIClient.swift` | MÓDOSÍTOTT (+analysis/movement-summary API hívások) |
| `ios/.../Juggling/JugglingAnnotationViewModel.swift` | MÓDOSÍTOTT (+fetchAnalysis, fetchMovementSummary) |
| `ios/LFAEducationCenterTests/Juggling/MovementHeatmapViewTests.swift` | ÚJ |
| `ios/LFAEducationCenterTests/Juggling/PitchCalibrationViewTests.swift` | ÚJ |
| `ios/LFAEducationCenter.xcodeproj/project.pbxproj` | MÓDOSÍTOTT |

### Új OpenAPI routes

| Endpoint | PR |
|---|---|
| `POST /api/v1/users/videos/{vid}/contacts/{eid}/ball-detection` | AN-3B2B-1 |
| `GET /api/v1/users/videos/{vid}/contacts/{eid}/ball-detection` | AN-3B2B-1 |
| `POST /api/v1/users/videos/{vid}/pitch-config` | AN-3B2B-2 |
| `GET /api/v1/users/videos/{vid}/pitch-config` | AN-3B2B-2 |
| `DELETE /api/v1/users/videos/{vid}/pitch-config` | AN-3B2B-2 |
| `POST /api/v1/users/videos/{vid}/reference-objects` | AN-3B2B-2 |
| `DELETE /api/v1/users/videos/{vid}/reference-objects/{id}` | AN-3B2B-2 |
| `GET /api/v1/users/videos/{vid}/contacts/{eid}/analysis` | AN-3B2B-3 |
| `GET /api/v1/users/videos/{vid}/movement-summary` | AN-3B2B-4 |

Jelenlegi route szám: 901 → Phase 2B után: **910** (9 új endpoint).

---

## 4. Celery architecture változás

### Jelenlegi állapot
```
app/celery_app.py includes:
  - app.tasks.juggling_tasks
  - app.tasks.juggling_transcode_task   → queue="juggling_videos"
  - app.tasks.juggling_retention_task   → queue="juggling_videos"
```

### Phase 2B után
```
app/celery_app.py includes (új):
  - app.tasks.juggling_analysis_task    → queue="analysis"

Worker parancs (új, .env-be dokumentálva):
  celery -A app.celery_app worker -Q analysis --pool=solo -c 1
```

**Trigger**: a `contact_service.py` `finish_annotation()` függvénye, amikor az annotation state `in_progress→human_review_pending` átmenet megtörténik ÉS a video quality gate `approved` állapotban van. (Az `annotation_review_status` → `approved` explicit transition a jelenlegi state machine-ben nincs adminisztrációs UI-hoz kötve — Phase 2B implementációnál pontosan meg kell határozni, melyik esemény triggereli.)

---

## 5. iOS 14 kompatibilitás — kockázati pontok

| Elem | Kockázat | Megoldás |
|---|---|---|
| `MovementHeatmapView` hőtérkép grid | `Canvas` (iOS15+) nem elérhető | nested `ForEach` + `Rectangle` `Path`-ok — iOS14 OK |
| `PitchCalibrationView` drag markers | `DragGesture` iOS 13+ | OK |
| `Chart` framework | iOS16+ | NEM használható — custom `Path`/`Shape` |
| `safeAreaInset(edge:)` | iOS15+ | Régi `VStack`+`Spacer` minta (mint a többi screen) |
| Analysis endpoint poll | `async/await` az `@MainActor` kontextusban | iOS15+ `.task` modifier NEM elérhető → `.onAppear`+`Task{}` minta (mint Phase 2A) |

---

## 6. Implementációs sorrend (javaslat)

```
AN-3B2B-1 (BDT)  ──────────────────────────────────────────────────────── PR #301
                                                                              │
AN-3B2B-2 (PFC)  ──────────────────────────────────── (párhuzamos) ──── PR #302
                                     │                      │
                                     └──── merge mindkettő ─┘
                                                  │
                                             AN-3B2B-3 (MMT) ──────────── PR #303
                                                  │
                                             AN-3B2B-4 (HMP) ──────────── PR #304
```

- **AN-3B2B-1 és AN-3B2B-2 párhuzamosan** futtatható (nincs köztük DB függőség)
- **AN-3B2B-3** csak AN-3B2B-1+2 merge után indul (composite endpoint mindkét adatot használja)
- **AN-3B2B-4** csak AN-3B2B-3 merge után indul (heatmap a ball detections-t és pitch config-ot feltételezi)

---

## 7. Nyitott kérdések jóváhagyáshoz

1. **`detect_ball_position` trigger**: melyik esemény triggereli? Az `annotation_review_status` → `approved` átmenet manuális (admin tool, API?) vagy automatikus (videóminőség + annotáció kész kombinációja)? Ez befolyásolja, hova kerül a Celery task dispatch.

2. **YOLOv8n ONNX export license**: az `ultralytics` csomag (AGPL-3.0) egyszeri script futtatáshoz (offline, fejlesztői gépen) elfogadható? A production runtime csak `onnxruntime` (MIT) lesz — azonos a biometrika precedensével (`adaface_onnx_export.py`). Ha igen: `scripts/export_yolov8n_onnx.py` + ONNX model gitignored, CI-ban le kell tölteni, vagy a modelt egy privát S3-ban tárolni.

3. **Model tárolás**: `app/ml_models/yolov8n_sports_ball.onnx` — gitignored, de a CI-ban szükséges. Javaslat: `scripts/download_ml_models.py` script (hasonlóan a biometrikához), amely az első indításkor letölti az ONNX fájlt egy belső URL-ről vagy a hivatalos YOLOv8n forrásból.

4. **Pitch config UI**: az iOS 4-point picker a `JugglingAnnotationScreen`-en belülre kerüljön (gomb/FAB), vagy egy külön admin-only felületre? Javaslat: jobb felső sarokban egy `(toolbar → "Pályakalibrálás")` gomb, amely a `PitchCalibrationView`-t push-navigációval nyitja.

5. **Movement summary elérhetősége**: a `MovementHeatmapView` hol jelenik meg? Opciók:
   - A) `JugglingVideoListView`-ban videó swipe → "Elemzés" gomb
   - B) Külön `VideoAnalysisView` navigáció a videó sor tap-jén
   - C) A jelenlegi `JugglingPlayerView`-ban tab/szegmens a lejátszó mellett

6. **`scipy` verzió**: `scipy>=1.11.0` — van ismert inkompatibilitás a jelenlegi `onnxruntime==1.26.0`-val? (Ellenőrizni kell numpy verzión keresztül.)

---

## 8. Kockázatok

| Kockázat | Súlyosság | Mitigáció |
|---|---|---|
| YOLOv8n pontossága kis felbontáson (360p) | Közepes | `inference_confidence` tárolt → küszöb alatti detekciók manuális override-dal jelölhetők |
| `opencv-python-headless` CI install idő | Alacsony | Pre-built wheel, ~30s extra CI idő |
| scipy KDE memória 1 pont esetén | Alacsony | `point_count < 2` → KDE kihagyva, scatter-only válasz |
| Celery `analysis` worker hiánya staging-en | Közepes | Feature flag `BALL_DETECTION_ENABLED=False` véd; task csak flag bekapcsolt deployment-en fut |
| homográfia numerikus instabilitás (közel-kollineáris sarokpontok) | Alacsony | `numpy.linalg.cond` ellenőrzés + 422 visszadobás, ha kondíciószám > 1e8 |

---

**AN-3B2B implementáció nem indult el — külön jóváhagyás szükséges a fenti tervre és a 7. szakasz kérdéseire.**
