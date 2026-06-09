# AN-3B2C-1 — iOS Ball Detection Visualization + Manual Correction
## Részletes Implementációs Terv

Státusz: **TERV VÉGLEGESÍTVE — minden döntés beépítve. Implementáció megkezdhető.**
Alap: PR #301 (AN-3B2B-1), HEAD `9a621c1f`, branch `feat/an3b2b-1-ball-detection`.
Terv dátuma: 2026-06-17. Döntések beépítve: 2026-06-17.

---

## 0. Összefoglaló

Ez a PR az iOS vizualizáción kívül három backend módosítást is tartalmaz:

1. **`no_ball_detected` schema fix** — a `BallDetectionManualRequest` nem támogatta a "nincs labda" jelölést; kötelező javítás.
2. **`auto_ball_x / auto_ball_y` migráció (Opció A)** — nullable mezők a `juggling_ball_detections` táblán, amelyek megőrzik az eredeti automatikus modellkoordinátát manuális korrekció esetén; modell-validációhoz szükséges, visszamenőleg nem pótolható.
3. **Service propagálás** — az `upsert_manual_detection` mostantól `req.no_ball_detected`-et használja a hardcoded `False` helyett, és manuális override előtt menti az automatikus koordinátákat.

A PR után a felhasználó látja a detektált labda pozícióját és a confidence szintjét, tudja korrigálni a pozíciót drag gesture-rel a teljes preview területén, tudja jelezni, ha nincs labda, és a "Labda volt" visszavonás az eredeti automatikus pozícióra áll vissza (ha elérhető), különben drag mode nyílik. Minden korrekció `detection_source` és `no_ball_detected` alapján megkülönböztethető a backend oldalon.

---

## 1. Backend Előfeltétel-Audit

### 1.1 Élő endpointok (nincs változás)

| Endpoint | Státusz | Leírás |
|---|---|---|
| `GET /api/v1/users/me/juggling/videos/{vid}/contacts/{eid}/ball-detection` | ✅ LIVE | `BallDetectionOut` visszaadása; 404 ha nincs detekció; 503 ha feature flag ki |
| `POST /api/v1/users/me/juggling/videos/{vid}/contacts/{eid}/ball-detection` | ✅ LIVE | Idempotent upsert; 201 new / 200 update; `BallDetectionOut` visszaadása |

Mindkét endpoint: `JUGGLING_POC_ENABLED=true` + `BALL_DETECTION_ENABLED=true` szükséges.

### 1.2 Kritikus rés: `no_ball_detected` mező (backend fix szükséges)

**Probléma**: a jelenlegi `BallDetectionManualRequest` séma és a service nem engedi a "nincs labda" jelölést felhasználói oldalról:

```python
# app/schemas/juggling.py — JELENLEGI ÁLLAPOT (hibás)
class BallDetectionManualRequest(BaseModel):
    ball_x:     float = Field(..., ge=0.0, le=1.0)   # required — nincs labda esetén értelmetlen
    ball_y:     float = Field(..., ge=0.0, le=1.0)   # required
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    # nincs no_ball_detected mező!

# app/services/juggling/ball_detection_service.py — upsert_manual_detection
existing.no_ball_detected = False   # hardcoded — user nem tudja true-ra állítani
```

**Javítás (1 fájl, 2 helyen, non-breaking)**:

```python
# app/schemas/juggling.py — JAVÍTOTT
from pydantic import model_validator

class BallDetectionManualRequest(BaseModel):
    ball_x:           Optional[float] = Field(None, ge=0.0, le=1.0)
    ball_y:           Optional[float] = Field(None, ge=0.0, le=1.0)
    confidence:       Optional[float] = Field(None, ge=0.0, le=1.0)
    no_ball_detected: bool            = Field(False)

    @model_validator(mode="after")
    def _coords_required_unless_no_ball(self) -> "BallDetectionManualRequest":
        if not self.no_ball_detected and (self.ball_x is None or self.ball_y is None):
            raise ValueError("ball_x and ball_y are required when no_ball_detected=False")
        return self
```

```python
# app/services/juggling/ball_detection_service.py — upsert_manual_detection (JAVÍTOTT)
existing.no_ball_detected = req.no_ball_detected   # volt: False (hardcoded)
existing.ball_x           = req.ball_x             # None nincs-labda esetén
existing.ball_y           = req.ball_y
# (ugyanígy a new detection ágban)
```

**Non-breaking jelleg**: meglévő hívók, amelyek `ball_x` és `ball_y` értéket adnak meg, változatlanul működnek — `no_ball_detected` alapértéke `False`.

**Szükséges új tesztek**: 2 db BDT (backend) teszt a `no_ball_detected=True` ághoz.

### 1.3 Automatikus koordináta megőrzése — Opció A (DÖNTÉS: implementálva)

**Probléma (megoldva)**: az upsert felülírja az automatikus detekció `ball_x / ball_y` értékeit manuális korrekciónál, így az eredeti modellkimenet elvész.

**Döntés**: Opció A — `auto_ball_x` és `auto_ball_y` nullable oszlopok a `juggling_ball_detections` táblán.

**Migráció** (`alembic/versions/2026_06_17_ball_detection_auto_coords.py`):
```sql
ALTER TABLE juggling_ball_detections ADD COLUMN auto_ball_x FLOAT;
ALTER TABLE juggling_ball_detections ADD COLUMN auto_ball_y FLOAT;
```

**Service logika** (`upsert_manual_detection` — módosított):
```python
if existing:
    # Auto koordináták megőrzése: csak akkor mentjük, ha az előző forrás "automatic" volt
    # és auto_ball_x még üres (első felülíráskor rögzítjük, nem írjuk felül újra)
    if existing.detection_source == "automatic" and existing.auto_ball_x is None:
        existing.auto_ball_x = existing.ball_x
        existing.auto_ball_y = existing.ball_y
    existing.detection_source  = "manual"
    existing.ball_x            = req.ball_x
    existing.ball_y            = req.ball_y
    existing.no_ball_detected  = req.no_ball_detected
    existing.confidence        = req.confidence
    existing.model_version     = None
    existing.image_width_px    = None
    existing.image_height_px   = None
    db.commit()
    db.refresh(existing)
    return existing, False

# Új rekord (manual-first, sosem volt auto): auto_ball_x = None
detection = JugglingBallDetection(
    ...
    no_ball_detected=req.no_ball_detected,
    auto_ball_x=None,
    auto_ball_y=None,
    excluded_from_training=True,
)
```

**`BallDetectionOut` séma bővítése** (`app/schemas/juggling.py`):
```python
class BallDetectionOut(BaseModel):
    # ... meglévő mezők ...
    auto_ball_x: Optional[float]   # eredeti automatikus x; None ha manual-first
    auto_ball_y: Optional[float]   # eredeti automatikus y; None ha manual-first
```

**`BallDetectionOut` Swift struct bővítése** (ld. 2.1 szakasz).

---

## 2. Swift Adatmodellek (Új)

Ezeket a struktúrákat a `JugglingAnnotationAPIClient.swift` végére kell illeszteni, a többi `struct` / `enum` mintájára.

### 2.1 `BallDetectionOut` (Decodable)

```swift
struct BallDetectionOut: Decodable, Equatable {
    let id:                   UUID
    let contactEventId:       UUID
    let videoId:              UUID
    let detectionSource:      String       // "automatic" | "manual"
    let ballX:                Double?      // nil ha no_ball_detected=true
    let ballY:                Double?
    let confidence:           Double?
    let worldXM:              Double?      // mindig nil Phase 2C-1-ben (pitch config hiányzik)
    let worldYM:              Double?
    let modelVersion:         String?      // pl. "ssd_mobilenet_v1_coco_2018_01_28"
    let noBallDetected:       Bool
    let excludedFromTraining: Bool         // manual: mindig true; auto: quality-függő
    // Opció A: eredeti automatikus koordináták (nil ha manual-first; nil ha auto sosem futott)
    let autoBallX:            Double?
    let autoBallY:            Double?
    let createdAt:            Date
    let updatedAt:            Date

    enum CodingKeys: String, CodingKey {
        case id, confidence
        case contactEventId       = "contact_event_id"
        case videoId              = "video_id"
        case detectionSource      = "detection_source"
        case ballX                = "ball_x"
        case ballY                = "ball_y"
        case worldXM              = "world_x_m"
        case worldYM              = "world_y_m"
        case modelVersion         = "model_version"
        case noBallDetected       = "no_ball_detected"
        case excludedFromTraining = "excluded_from_training"
        case autoBallX            = "auto_ball_x"
        case autoBallY            = "auto_ball_y"
        case createdAt            = "created_at"
        case updatedAt            = "updated_at"
    }
}
```

### 2.2 `BallDetectionManualRequest` (Encodable)

```swift
struct BallDetectionManualRequest: Encodable {
    let ballX:          Double?    // nil ha noBallDetected=true
    let ballY:          Double?
    let confidence:     Double?    // manual override esetén: 1.0; no_ball: nil
    let noBallDetected: Bool

    enum CodingKeys: String, CodingKey {
        case ballX          = "ball_x"
        case ballY          = "ball_y"
        case confidence
        case noBallDetected = "no_ball_detected"
    }
}
```

### 2.3 `BallDetectionState` (ViewModel állapot enum)

```swift
enum BallDetectionState: Equatable {
    case notFetched                    // onAppear előtt
    case fetching                      // GET fut
    case loaded(BallDetectionOut)      // sikeres fetch
    case notFound                      // 404 — Celery még nem futott; polling aktív
    case featureDisabled               // 503 — BALL_DETECTION_ENABLED=false
    case networkError(String)          // hálózati hiba

    static func == (lhs: BallDetectionState, rhs: BallDetectionState) -> Bool {
        switch (lhs, rhs) {
        case (.notFetched,      .notFetched):      return true
        case (.fetching,        .fetching):        return true
        case (.loaded(let a),   .loaded(let b)):   return a == b
        case (.notFound,        .notFound):        return true
        case (.featureDisabled, .featureDisabled): return true
        case (.networkError(let a), .networkError(let b)): return a == b
        default:                                   return false
        }
    }
}
```

---

## 3. `JugglingAnnotationAPIClientProtocol` — Bővítés

A protocol-t ki kell bővíteni 2 új metódussal, hogy a tesztek mock-ot tudnak injektálni:

```swift
@MainActor
protocol JugglingAnnotationAPIClientProtocol: AnyObject {
    // ... meglévő metódusok ...

    // AN-3B2C-1: ball detection
    func fetchBallDetection(videoId: String, eventId: UUID) async throws -> BallDetectionOut
    func postBallDetection(videoId: String, eventId: UUID, request: BallDetectionManualRequest) async throws -> BallDetectionOut
}
```

---

## 4. `JugglingAnnotationAPIClient` — Bővítés

A meglévő `Phase 2A: Pose Snapshot` szekció után, `Phase 2C-1: Ball Detection` fejléccel:

```swift
// MARK: — Phase 2C-1: Ball Detection
//
// fetchBallDetection: GET /contacts/{event_id}/ball-detection
//   200 → BallDetectionOut
//   404 → throws AnnotationAPIError.permanent(code: 404, ...)
//   503 → throws AnnotationAPIError.permanent(code: 503, ...) — feature flag off
//
// postBallDetection: POST /contacts/{event_id}/ball-detection
//   200/201 → BallDetectionOut (idempotent upsert)
//   422 → throws AnnotationAPIError.permanent (invalid coords)
//   503 → throws AnnotationAPIError.permanent — feature flag off

func fetchBallDetection(videoId: String, eventId: UUID) async throws -> BallDetectionOut {
    let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts/\(eventId.uuidString.lowercased())/ball-detection"
    do {
        return try await authManager.authenticatedGet(path: path)
    } catch let apiErr as APIError {
        throw classifyAPIError(apiErr, path: "fetchBallDetection")
    }
}

func postBallDetection(
    videoId: String,
    eventId: UUID,
    request: BallDetectionManualRequest
) async throws -> BallDetectionOut {
    let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts/\(eventId.uuidString.lowercased())/ball-detection"
    do {
        let (data, _) = try await authManager.authenticatedPostRaw(path: path, body: request)
        return try isoDecoder.decode(BallDetectionOut.self, from: data)
    } catch let apiErr as APIError {
        throw classifyAPIError(apiErr, path: "postBallDetection")
    }
}
```

**Decoding megjegyzés**: az `isoDecoder` (ISO 8601 dátum support) már a `JugglingAnnotationAPIClient`-ben él, újra kell használni — nem kell új decoder.

---

## 5. `JugglingAnnotationViewModel` — Bővítés

### 5.1 Új `@Published` állapotok

```swift
// AN-3B2C-1 — ball detection
@Published private(set) var ballDetections: [UUID: BallDetectionState] = [:]
```

Kulcs: `contactEventId` (UUID). Egy bejegyzés event-per-video szinten van.

### 5.2 Polling infrastruktúra (iOS 14 kompatibilis)

```swift
private var ballDetectionPollingTask: Task<Void, Never>? = nil
private let ballDetectionMaxPollingAttempts = 5
private let ballDetectionPollingIntervalSeconds: Double = 30.0
// Task.sleep(nanoseconds:) iOS 15+ — nem használjuk; DispatchQueue.asyncAfter alapú sleep helyett.
```

### 5.3 Új metódusok

```swift
// Fetch trigger — onAppear-ből vagy pull-to-refresh-ből hívva
func fetchBallDetection(videoId: String, eventId: UUID) async {
    ballDetections[eventId] = .fetching
    do {
        let out = try await (apiClient as? JugglingAnnotationAPIClient)?
            .fetchBallDetection(videoId: videoId, eventId: eventId)
        if let out {
            ballDetections[eventId] = .loaded(out)
        } else {
            // mock esetén — unit tesztek always return notFound (safe no-op)
            ballDetections[eventId] = .notFound
        }
    } catch AnnotationAPIError.permanent(let code, _) where code == 404 {
        ballDetections[eventId] = .notFound
        startPolling(videoId: videoId, eventId: eventId)
    } catch AnnotationAPIError.permanent(let code, _) where code == 503 {
        ballDetections[eventId] = .featureDisabled
    } catch let err as AnnotationAPIError {
        ballDetections[eventId] = .networkError(err.localizedDescription)
    } catch {
        ballDetections[eventId] = .networkError(error.localizedDescription)
    }
}

// Manual position correction
// Optimistic update ELŐBB — API call után commit vagy revert
func postManualBallPosition(videoId: String, eventId: UUID, x: Double, y: Double) async {
    let previous = ballDetections[eventId]
    // Optimista update — azonnali UI frissítés
    if case .loaded(let current) = previous {
        let optimistic = BallDetectionOut(
            id: current.id, contactEventId: current.contactEventId,
            videoId: current.videoId, detectionSource: "manual",
            ballX: x, ballY: y, confidence: 1.0,
            worldXM: nil, worldYM: nil, modelVersion: nil,
            noBallDetected: false, excludedFromTraining: true,
            createdAt: current.createdAt, updatedAt: Date()
        )
        ballDetections[eventId] = .loaded(optimistic)
    }
    let req = BallDetectionManualRequest(ballX: x, ballY: y, confidence: 1.0, noBallDetected: false)
    do {
        guard let client = apiClient as? JugglingAnnotationAPIClient else { return }
        let out = try await client.postBallDetection(videoId: videoId, eventId: eventId, request: req)
        ballDetections[eventId] = .loaded(out)
    } catch {
        // Revert az előző állapotba
        ballDetections[eventId] = previous ?? .notFound
        // A hívó View-ban kell toast megjeleníteni
        throw error
    }
}

// "Nincs labda" jelölés
func markNoBall(videoId: String, eventId: UUID) async {
    let previous = ballDetections[eventId]
    let req = BallDetectionManualRequest(ballX: nil, ballY: nil, confidence: nil, noBallDetected: true)
    do {
        guard let client = apiClient as? JugglingAnnotationAPIClient else { return }
        let out = try await client.postBallDetection(videoId: videoId, eventId: eventId, request: req)
        ballDetections[eventId] = .loaded(out)
    } catch {
        ballDetections[eventId] = previous ?? .notFound
        throw error
    }
}

// Polling (csak .notFound esetén indul, max 5 kísérlet, 30 mp-enként)
// iOS 14 kompatibilis: DispatchQueue.asyncAfter-alapú sleep, nem Task.sleep(nanoseconds:)
private func startPolling(videoId: String, eventId: UUID) {
    ballDetectionPollingTask?.cancel()
    ballDetectionPollingTask = Task { [weak self] in
        guard let self else { return }
        var attempts = 0
        while attempts < self.ballDetectionMaxPollingAttempts, !Task.isCancelled {
            await self.dispatchSleep(seconds: self.ballDetectionPollingIntervalSeconds)
            guard !Task.isCancelled else { return }
            await self.fetchBallDetection(videoId: videoId, eventId: eventId)
            if case .loaded = self.ballDetections[eventId] { return }
            if case .featureDisabled = self.ballDetections[eventId] { return }
            attempts += 1
        }
        // 5 kísérlet után: marad .notFound, polling leáll
    }
}

// iOS 14 kompatibilis sleep — DispatchQueue.asyncAfter, nem Task.sleep
@MainActor
private func dispatchSleep(seconds: Double) async {
    await withCheckedContinuation { cont in
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) {
            cont.resume()
        }
    }
}

// Hívandó, ha a felhasználó másik event-re lép (currentIndex change)
func cancelBallDetectionPolling() {
    ballDetectionPollingTask?.cancel()
    ballDetectionPollingTask = nil
}
```

**Megjegyzés a `postManualBallPosition` throwing viselkedéséhez**: a metódus `throws`, mert az `EventLabelDetailView`-nak tudnia kell a hibáról (toast megjelenítéshez). A meghívó `.task { try? await ... }` wrapperrel kezeli, és `@State var showBallCorrectionError: Bool`-t állít.

---

## 6. `BallOverlayView` — Architektúra

Új fájl: `ios/LFAEducationCenter/Juggling/Annotation/Screen/BallOverlayView.swift`

A `PoseSnapshotOverlayView` mintájára: `GeometryReader`-alapú, normált `[0,1]×[0,1]` koordináta-rendszer.

```
BallOverlayView
├── GeometryReader { geo }
│   ├── dragActiveBorder (ha isDragActive=true: kék Rectangle strokeBorder)
│   ├── ballMarker (a labda vizualizációja — ld. 6.1)
│   ├── dragHint (ha isDragActive: "Húzd a kör helyére" caption)
│   └── .gesture(isDragActive ? dragGesture(geo) : nil)
```

### 6.1 `ballMarker` — confidence-alapú vizuális kódolás

| Állapot | `detectionSource` | `noBallDetected` | `confidence` | Vizuális |
|---|---|---|---|---|
| Magas bizonyosság | `"automatic"` | false | ≥ 0.80 | Sárga tömör kör, 12pt, opacity 0.9 |
| Közepes bizonyosság | `"automatic"` | false | 0.50–0.79 | Narancs kör, opacity 0.6 |
| Alacsony bizonyosság | `"automatic"` | false | < 0.50 | Piros kör, `strokeBorder` szaggatott, 2pt |
| Manuális korrekció | `"manual"` | false | bármilyen | Kék tömör kör, 12pt — `detectionSource` dominál |
| Nincs labda | bármilyen | true | — | Nincs kör; szöveg overlay lent |
| Nincs adat (`.notFound`) | — | — | — | Szürke szaggatott kör, "Azonosítás folyamatban…" |
| Feature disabled (`.featureDisabled`) | — | — | — | Nincs overlay; semmi |

**iOS 14 kompatibilitás**: `Circle()`, `strokeBorder(_:lineWidth:)`, `ZStack`, `Text` — nem kell `Canvas` (iOS 15+).

```swift
struct BallOverlayView: View {

    let state:           BallDetectionState
    let isDragActive:    Bool
    @Binding var localPosition: CGPoint?    // normált [0,1]x[0,1]; nil = state-ből olvassuk
    let onPositionCommitted: (CGPoint) -> Void

    @State private var activeDragPoint: CGPoint? = nil

    var body: some View {
        GeometryReader { geo in
            ZStack {
                if isDragActive {
                    Rectangle()
                        .strokeBorder(Color.blue.opacity(0.4), lineWidth: 2)
                }

                if isDragActive, let hint = activeDragPoint ?? resolvedPosition {
                    Text("Húzd a kör helyére")
                        .font(.caption2)
                        .foregroundColor(.white)
                        .padding(4)
                        .background(Color.black.opacity(0.6))
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                        .position(x: geo.size.width / 2, y: 12)
                }

                ballMarkerView(geo: geo)
            }
            .contentShape(Rectangle())
            .gesture(isDragActive ? dragGesture(geo: geo) : nil)
        }
    }

    // ...
}
```

### 6.2 Drag Gesture Viselkedés

```
Drag gesture (.onChanged):
  1. activeDragPoint = CGPoint(x: loc.x / geo.width, y: loc.y / geo.height)  [normált]
  2. localPosition = activeDragPoint  [kör azonnal követi az ujjat]
  3. A kör pozíciója: CGPoint(x: activeDragPoint.x * geo.width, y: activeDragPoint.y * geo.height)

Drag gesture (.onEnded):
  1. onPositionCommitted(activeDragPoint)  [hívó: vm.postManualBallPosition]
  2. activeDragPoint = nil
  3. isDragActive = false (a parent View állítja vissza)

Boundary clamping:
  x = min(max(loc.x, 0), geo.size.width)  / geo.size.width
  y = min(max(loc.y, 0), geo.size.height) / geo.size.height
  → Kör nem lép ki a frame-ből.

Minimális drag distance: 0 (pont tap is pozíciót állít — szándékos: felhasználóbarát)
```

### 6.3 Kör pozíció forrás

A kör megjelenítésekor a pozíciós hierarhia:
1. `activeDragPoint` — ha drag aktív (live követés)
2. `localPosition` — ha a VM optimista update küldött (API válasz előtt)
3. `state`-ből: `BallDetectionOut.ballX / ballY` — ha `.loaded`
4. Középpont (0.5, 0.5) — ha `.notFound` (szürke placeholder)

---

## 7. `EventLabelDetailView` — Integráció

### 7.1 Új `@State` változók

```swift
// AN-3B2C-1 — ball detection
@State private var isBallDragActive:    Bool        = false
@State private var localBallPosition:   CGPoint?    = nil   // optimistic, cleared on API response
@State private var showBallCorrectionErrorToast: Bool = false
```

### 7.2 `loopPreview` ZStack bővítése

A jelenlegi `loopPreview` `ZStack`-je (meglévő tartalom megtartásával):

```swift
ZStack {
    Color.black
    AVPlayerLayerView(player: previewSession.player)
        .disabled(true)
    
    // Phase 2A — meglévő
    if let keypoints = currentPoseKeypoints {
        PoseSnapshotOverlayView(keypoints: keypoints)
    }
    
    // AN-3B2C-1 — ball detection overlay (ÚJ)
    if let eventId = currentDraft?.deviceEventId {
        let bdState = vm.ballDetections[eventId] ?? .notFetched
        BallOverlayView(
            state:              bdState,
            isDragActive:       isBallDragActive,
            localPosition:      $localBallPosition,
            onPositionCommitted: { point in
                Task {
                    do {
                        try await vm.postManualBallPosition(
                            videoId: vm.videoId,
                            eventId: eventId,
                            x: point.x, y: point.y
                        )
                        isBallDragActive  = false
                        localBallPosition = nil
                    } catch {
                        showBallCorrectionErrorToast = true
                        isBallDragActive  = false
                        localBallPosition = nil
                    }
                }
            }
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    
    // Meglévő play/pause controls — felett, tehát utoljára a ZStack-ben
    if !previewSession.isLoading, !previewSession.hasError {
        // ... (változatlan)
    }
}
```

### 7.3 Ball Detection szekció a `scrollableZoneBody` tetején

A `scrollableZoneBody` `VStack`-jébe, a zóna-picker előtt:

```swift
private var scrollableZoneBody: some View {
    ScrollView {
        VStack(spacing: 0) {
            // AN-3B2C-1 — ball detection szekció (ÚJ)
            if let eventId = currentDraft?.deviceEventId {
                ballDetectionSection(eventId: eventId)
                Divider()
            }
            
            // Meglévő tartalom
            if showTaxonomyFallback {
                taxonomyFallbackContent
            } else {
                emojiPickerContent
            }
        }
    }
}
```

### 7.4 `ballDetectionSection` — Action Row + Status Caption

```
┌─────────────────────────────────────────────────────┐
│  [kék tömör kör] Manuálisan jelölve  ← ha manual   │
│  [sárga kör] Labda azonosítva · 0.87 ← ha automatic│
│  [szürke kör] Azonosítás folyamatban… ← ha notFound│
│  [piros x] Labda nem azonosítható    ← ha no_ball  │
│                                                     │
│  [Pozíció korrekció]     [Nincs labda]              │
└─────────────────────────────────────────────────────┘
```

```swift
@ViewBuilder
private func ballDetectionSection(eventId: UUID) -> some View {
    let state = vm.ballDetections[eventId] ?? .notFetched

    VStack(spacing: 8) {
        // Status caption
        ballStatusCaption(state: state)

        // Action row — csak akkor látható, ha nincs drag aktív
        if !isBallDragActive {
            HStack(spacing: 12) {
                // "Pozíció korrekció" gomb — drag mode bekapcsol
                Button {
                    localBallPosition = nil
                    isBallDragActive  = true
                } label: {
                    Label("Pozíció korrekció", systemImage: "hand.point.up")
                        .font(.subheadline)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(Color(.systemGray6))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .disabled(state == .featureDisabled || state == .fetching)
                .accessibilityLabel("Labda pozíció kézi korrekció")

                // "Nincs labda" / "Labda volt" toggle
                let isNoBall: Bool = {
                    if case .loaded(let d) = state { return d.noBallDetected }
                    return false
                }()

                // "Nincs labda" visszavonás: ha van auto_ball_x → visszaáll arra;
                // ha nincs → drag mode nyílik (manual-first, nincs auto referencia)
                let autoX: Double? = {
                    if case .loaded(let d) = state { return d.autoBallX }
                    return nil
                }()
                let autoY: Double? = {
                    if case .loaded(let d) = state { return d.autoBallY }
                    return nil
                }()

                Button {
                    Task {
                        if isNoBall {
                            if let ax = autoX, let ay = autoY {
                                // Visszaállítás az eredeti automatikus pozícióra
                                try? await vm.postManualBallPosition(
                                    videoId: vm.videoId, eventId: eventId, x: ax, y: ay
                                )
                            } else {
                                // Nincs auto referencia — drag mode
                                isBallDragActive = true
                            }
                        } else {
                            try? await vm.markNoBall(videoId: vm.videoId, eventId: eventId)
                        }
                    }
                } label: {
                    Label(
                        isNoBall ? "Labda volt" : "Nincs labda",
                        systemImage: isNoBall ? "checkmark.circle" : "xmark.circle"
                    )
                    .font(.subheadline)
                    .foregroundColor(isNoBall ? .green : .red)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(Color(.systemGray6))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .disabled(state == .featureDisabled || state == .fetching)
                .accessibilityLabel(isNoBall ? "Labda volt — visszavonás" : "Nincs labda megjelölése")
            }
            .padding(.horizontal, 16)
        } else {
            // Drag aktív állapotban: "Húzd a kört" hint + Mégsem
            HStack {
                Text("Húzd a labdát a helyes pozícióra")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Button("Mégsem") {
                    isBallDragActive  = false
                    localBallPosition = nil
                }
                .font(.caption)
                .foregroundColor(.accentColor)
            }
            .padding(.horizontal, 16)
        }
    }
    .padding(.vertical, 10)
    .background(Color(.systemBackground))
}
```

### 7.5 `onAppear` bővítése

```swift
// Meglévő onAppear logic után (setUpQueue, loadPreviewForCurrentDraft)
.onAppear {
    setUpQueue()
    // AN-3B2C-1: ball detection fetch az aktuális event-hez
    if let draft = currentDraft {
        Task {
            await vm.fetchBallDetection(videoId: vm.videoId, eventId: draft.deviceEventId)
        }
    }
}
.onChange(of: currentIndex) { _ in
    loadPreviewForCurrentDraft()
    loadFormState()
    // AN-3B2C-1: polling törlése + új fetch a következő event-hez
    vm.cancelBallDetectionPolling()
    if let draft = currentDraft {
        Task {
            await vm.fetchBallDetection(videoId: vm.videoId, eventId: draft.deviceEventId)
        }
    }
}
```

### 7.6 Error Toast

```swift
// A body-ban, a .alert után:
.overlay(
    Group {
        if showBallCorrectionErrorToast {
            VStack {
                Spacer()
                Text("Pozíció mentése sikertelen. Próbáld újra.")
                    .font(.caption)
                    .foregroundColor(.white)
                    .padding(10)
                    .background(Color.red.opacity(0.85))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .padding(.horizontal, 16)
                    .padding(.bottom, 100)
                    .onAppear {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                            showBallCorrectionErrorToast = false
                        }
                    }
            }
        }
    }
)
```

---

## 8. `EventTimelineView` — Badge Integráció

A timeline pinekre kis ikon kerül a `BallDetectionState` alapján. A `pins(width:)` function-t kell bővíteni.

**Jelenlegi pin**: 10pt `Circle()`, szín a sync statusból.
**Kibővített pin**: a labda badge a pin mellett egy kis szuperszkript-szerű ikon (6pt), vagy a pin maga kapja a labda-badge overlay-t.

**Ajánlott megvalósítás**: a pin tetejére egy 6pt szaggatott kör overlay (ha van ball detection adat), hogy ne zavarjon a jelenlegi 10pt szín-kódolással:

```swift
private func pins(width: CGFloat) -> some View {
    ForEach(events.filter { $0.syncStatus != .deleted && !$0.deletedLocally }) { draft in
        let x = xPosition(ms: draft.timestampMs, trackWidth: width)
        ZStack {
            Circle()
                .fill(pinColor(for: draft.syncStatus))
                .frame(width: 10, height: 10)
            // AN-3B2C-1: labda badge (6pt, jobb felső sarok)
            if let badge = ballBadge(for: draft.deviceEventId) {
                badge
                    .offset(x: 5, y: -5)
            }
        }
        .shadow(color: .black.opacity(0.25), radius: 1)
        .offset(x: x - 5)
        .onTapGesture { onTap(draft.deviceEventId) }
        .accessibilityLabel(timelineAccessibilityLabel(for: draft))
    }
}

@ViewBuilder
private func ballBadge(for eventId: UUID) -> some View? {
    guard let state = ballDetectionStates?[eventId] else { return nil }
    switch state {
    case .loaded(let d) where d.noBallDetected:
        Image(systemName: "xmark.circle.fill")
            .font(.system(size: 6, weight: .bold))
            .foregroundColor(.red)
    case .loaded(let d) where (d.confidence ?? 0) >= 0.80:
        Image(systemName: "circle.fill")
            .font(.system(size: 6))
            .foregroundColor(.green)
    case .loaded:
        Image(systemName: "circle.fill")
            .font(.system(size: 6))
            .foregroundColor(.yellow)
    case .notFound, .fetching:
        Image(systemName: "circle.dashed")
            .font(.system(size: 6))
            .foregroundColor(.gray)
    default:
        nil
    }
}
```

`EventTimelineView`-nak kapnia kell egy `ballDetectionStates: [UUID: BallDetectionState]?` paramétert (optional, backward compatible).

---

## 9. Ground-Truth Provenance — A Rendszer Háromirányú Megkülönböztetése

Ez az AN-3B2C-1 legfontosabb adatminőségi aspektusa.

### 9.1 Megkülönböztető mezők

| Megkülönböztetés | `detection_source` | `no_ball_detected` | `excluded_from_training` |
|---|---|---|---|
| **Automatikus detekció** | `"automatic"` | `false` | quality-függő (low confidence → `true`) |
| **Manuális pozíciókorrekció** | `"manual"` | `false` | mindig `true` (service hardcodes) |
| **"Nincs labda" jelölés** | `"manual"` | `true` | mindig `true` |

### 9.2 Adatfolyam

```
Automatikus pipeline (Celery):
  onnx_ball_detector → JugglingBallDetection(detection_source="automatic", no_ball_detected=False/True, ...)

Felhasználói korrekció (iOS → POST /ball-detection):
  BallDetectionManualRequest(ball_x=0.42, ball_y=0.61, no_ball_detected=False)
  → JugglingBallDetection(detection_source="manual", no_ball_detected=False, excluded_from_training=True)

Felhasználói "nincs labda" (iOS → POST /ball-detection):
  BallDetectionManualRequest(ball_x=None, ball_y=None, no_ball_detected=True)
  → JugglingBallDetection(detection_source="manual", no_ball_detected=True, excluded_from_training=True)
```

### 9.3 Felhasználás modell-validációhoz és minőségméréshez

**Hamis pozitívok mérése**:
```sql
SELECT COUNT(*) FROM juggling_ball_detections
WHERE detection_source = 'manual'
  AND no_ball_detected = TRUE;
-- Ez az összes event, ahol a modell labdát talált, de a felhasználó jelölte, hogy nincs.
```

**Modell pontosság (pozíció-hiba)**:
```sql
-- Csak az esetekben, ahol auto → manual override történt,
-- ÉS megmaradnak az eredeti auto koordináták (opció A szerint: auto_ball_x / auto_ball_y)
SELECT
    id,
    SQRT(POWER(auto_ball_x - ball_x, 2) + POWER(auto_ball_y - ball_y, 2)) AS position_error
FROM juggling_ball_detections
WHERE detection_source = 'manual'
  AND no_ball_detected = FALSE
  AND auto_ball_x IS NOT NULL;
```

**Ground-truth inventory**:
```sql
SELECT
    detection_source,
    no_ball_detected,
    COUNT(*) AS count,
    AVG(confidence) FILTER (WHERE NOT no_ball_detected) AS avg_confidence
FROM juggling_ball_detections
GROUP BY detection_source, no_ball_detected;
```

### 9.4 `excluded_from_training` logikája

| Típus | Érték | Indok |
|---|---|---|
| Automatikus (high confidence) | `false` — training set | Modell-generált adat, bevonható a következő iteráció tanítójába |
| Automatikus (low confidence) | `true` — kizárva | Megbízhatatlan — nem javasolt tanítópontnak |
| Manuális korrekció | `true` — kizárva a *training set*-ből | Validációs adat: az auto-val összehasonlítható, de egyelőre nem tanításra |
| "Nincs labda" jelölés | `true` — kizárva | Negatív minta — külön kezelés kellhet a tanítóban |

**Jövőbeli folyamat** (nem Phase 2C scope): emberi reviewer jóváhagyás után az `excluded_from_training` `false`-ra állítható a validált manuális korrekciókra.

---

## 10. iOS Build Hatás

### 10.1 Új fájlok (5 db)

| Fájl | Hely | Méret (becsült) |
|---|---|---|
| `BallOverlayView.swift` | `Juggling/Annotation/Screen/` | ~120 sor |
| `BallDetectionAPIClientTests.swift` | `LFAEducationCenterTests/Juggling/` | ~80 sor |
| `BallDetectionVMTests.swift` | `LFAEducationCenterTests/Juggling/` | ~120 sor |
| `BallOverlayViewTests.swift` | `LFAEducationCenterTests/Juggling/` | ~80 sor |
| `BallDetectionTimelineTests.swift` | `LFAEducationCenterTests/Juggling/` | ~60 sor |

### 10.2 Módosított fájlok (5 db)

| Fájl | Változtatás |
|---|---|
| `JugglingAnnotationAPIClient.swift` | +`BallDetectionOut`, +`BallDetectionManualRequest`, +`BallDetectionState`; protocol + client bővítés |
| `JugglingAnnotationViewModel.swift` | +`ballDetections` dict, +5 metódus, +polling infra |
| `EventLabelDetailView.swift` | +`BallOverlayView` ZStack integrálás, +`ballDetectionSection`, +`onAppear` bővítés, +toast overlay |
| `EventTimelineView.swift` | +`ballDetectionStates` paraméter, +`ballBadge()` |
| `ios/LFAEducationCenter.xcodeproj/project.pbxproj` | `BallOverlayView.swift` hozzáadása mindkét targethez |

### 10.3 Backend fájlok (2 db — kötelező)

| Fájl | Változtatás |
|---|---|
| `app/schemas/juggling.py` | `BallDetectionManualRequest`: `ball_x/ball_y` → Optional, +`no_ball_detected`, +validator |
| `app/services/juggling/ball_detection_service.py` | `upsert_manual_detection`: `no_ball_detected = req.no_ball_detected` |

### 10.4 Backend fájlok — Opció A (auto koordináta megőrzés, KÖTELEZŐ)

| Fájl | Változtatás |
|---|---|
| `alembic/versions/2026_06_17_ball_detection_auto_coords.py` | `auto_ball_x FLOAT`, `auto_ball_y FLOAT` nullable oszlopok a `juggling_ball_detections` táblán |
| `app/models/juggling.py` | `JugglingBallDetection`: +`auto_ball_x`, +`auto_ball_y` Column(Float, nullable=True) |
| `app/services/juggling/ball_detection_service.py` | `upsert_manual_detection`: auto→manual átmenetnél menti az eredeti koordinátákat (ld. 1.3 szakasz) |
| `app/schemas/juggling.py` | `BallDetectionOut`: +`auto_ball_x: Optional[float]`, +`auto_ball_y: Optional[float]` |

### 10.5 Nincs szükség

- Új iOS framework-re
- Xcode verzióváltásra (marad 26)
- CoreData / Core ML módosítására
- Info.plist változásra
- Kamera/mikrofon engedélyekre (drag gesture, nem kamera)

---

## 11. iOS 14 Kompatibilitási Ellenőrzőlista

| Elem | Felhasznált API | iOS 14 OK? |
|---|---|---|
| `BallOverlayView` drag | `DragGesture(minimumDistance: 0)` | ✅ iOS 13+ |
| `BallOverlayView` stroke | `Circle().strokeBorder()` | ✅ iOS 13+ |
| `BallOverlayView` kör renderelés | `Circle().fill()`, `Path` | ✅ iOS 13+ |
| Fetch trigger | `.onAppear + Task {}` | ✅ (Phase 2A mintájára) |
| Polling sleep | `dispatchSleep(seconds:)` — `DispatchQueue.asyncAfter` wrapper | ✅ iOS 14+ (nem `Task.sleep`) |
| Toast overlay | `.overlay(Group { if condition { ... } })` | ✅ iOS 14+ |
| Accessibility | `Image(systemName:)`, `.accessibilityLabel()` | ✅ |

**Polling sleep implementáció**: `dispatchSleep(seconds:)` — `withCheckedContinuation` + `DispatchQueue.main.asyncAfter` — iOS 14 kompatibilis. A konkrét kód a 5.3 szakaszban van. `Task.sleep(nanoseconds:)` **nem használandó**.

---

## 12. Tesztterv

**Összesen**: 28 új iOS teszt + 2 backend (BDT) teszt.

### 12.1 Backend tesztek (BDT-NB-*)

| ID | Mit tesztel |
|---|---|
| BDT-NB-01 | `POST /ball-detection` `{no_ball_detected: true}` → 200/201; `no_ball_detected=True` a DB-ben |
| BDT-NB-02 | `POST /ball-detection` `{no_ball_detected: false, ball_x: null}` → 422 (validator) |

### 12.2 API Client tesztek (BD-AC-*)

| ID | Fájl | Mit tesztel |
|---|---|---|
| BD-AC-01 | `BallDetectionAPIClientTests` | `fetchBallDetection` 200 → `BallDetectionOut` helyes dekódolása (minden mező) |
| BD-AC-02 | | `fetchBallDetection` 404 → `AnnotationAPIError.permanent(code: 404)` |
| BD-AC-03 | | `fetchBallDetection` 503 → `AnnotationAPIError.permanent(code: 503)` |
| BD-AC-04 | | `postBallDetection` (manual pozíció) 201 → visszaadja `BallDetectionOut` |
| BD-AC-05 | | `postBallDetection` (`no_ball_detected=true`) 200 → `noBallDetected=true` a válaszban |
| BD-AC-06 | | `postBallDetection` hálózati hiba → `AnnotationAPIError.retryable` |

### 12.3 ViewModel tesztek (BD-VM-*)

| ID | Fájl | Mit tesztel |
|---|---|---|
| BD-VM-01 | `BallDetectionVMTests` | `fetchBallDetection` siker → `ballDetections[eventId] == .loaded(out)` |
| BD-VM-02 | | `fetchBallDetection` 404 → state `.notFound`; polling task létrejön |
| BD-VM-03 | | Polling leáll, ha `.loaded` érkezik |
| BD-VM-04 | | Polling leáll 5 kísérlet után; state marad `.notFound` |
| BD-VM-05 | | `postManualBallPosition` → optimistic update ELŐBB, API hívás UTÁNA |
| BD-VM-06 | | `postManualBallPosition` hálózati hiba → state visszaáll az előző értékre |
| BD-VM-07 | | `markNoBall` → POST `no_ball_detected=true`; state `.loaded(noBallDetected=true)` |
| BD-VM-08 | | `cancelBallDetectionPolling` event-váltáskor → polling task cancellálódik |
| BD-VM-09 | | `fetchBallDetection` mock client esetén → state `.notFound`, nem crash |
| BD-VM-10 | | `fetchBallDetection` 503 → state `.featureDisabled`; polling NEM indul |

### 12.4 Overlay View tesztek (BD-OV-*)

| ID | Fájl | Mit tesztel |
|---|---|---|
| BD-OV-01 | `BallOverlayViewTests` | `detection_source="automatic"`, confidence 0.87 → sárga tömör kör renderelési logika |
| BD-OV-02 | | confidence 0.65 → narancs kör |
| BD-OV-03 | | confidence 0.30 → piros szaggatott kör |
| BD-OV-04 | | `detection_source="manual"`, confidence bármilyen → kék tömör kör |
| BD-OV-05 | | `no_ball_detected=true` → nincs kör; szöveges label látható |
| BD-OV-06 | | state `.notFound` → szürke dashed placeholder kör |
| BD-OV-07 | | `isDragActive=true` → kék border visible; drag hint text visible |
| BD-OV-08 | | Drag end → `onPositionCommitted` meghívódik normált koordinátával |

### 12.5 Timeline badge tesztek (BD-TL-*)

| ID | Fájl | Mit tesztel |
|---|---|---|
| BD-TL-01 | `BallDetectionTimelineTests` | high confidence (≥0.80) → zöld `circle.fill` badge |
| BD-TL-02 | | low confidence (<0.80) → sárga `circle.fill` badge |
| BD-TL-03 | | `no_ball_detected=true` → piros `xmark.circle.fill` badge |
| BD-TL-04 | | state `.notFound` → szürke `circle.dashed` badge |
| BD-TL-05 | | `ballDetectionStates=nil` → nincs badge (backward compatible) |

---

## 13. Lezárt Döntések

| # | Kérdés | Döntés | Hatása a tervben |
|---|---|---|---|
| 1 | `no_ball_detected` backend gap | **Kötelező javítás ebben a PR-ben** — schema + service | 1.2 szakasz; `BallDetectionManualRequest` bővítve, service propagálja |
| 2 | Automatikus koordináta megőrzése | **Opció A** — `auto_ball_x / auto_ball_y` nullable oszlopok | 1.3 szakasz; migráció + service + `BallDetectionOut` bővítve; 10.4 kötelező |
| 3 | iOS 14 polling | **`DispatchQueue.asyncAfter`** — nem `Task.sleep(nanoseconds:)` | 5.2–5.3 szakasz; `dispatchSleep(seconds:)` helper; 11. szakasz frissítve |
| 4 | Drag gesture hatókör | **Teljes preview area** drag-aktív módban | 6.2 szakasz; `contentShape(Rectangle())` + `minimumDistance: 0` |
| 5 | "Nincs labda" visszavonás | Ha van `auto_ball_x` → visszaáll arra; ha nincs → drag mode | 7.4 szakasz; `autoX / autoY` check a "Labda volt" gombban |
| 6 | Ball detection szekció helye | **Scrollolható** — `scrollableZoneBody` teteje; secondary UX | 7.3–7.4 szakasz; nem fixed strip |

---

## 14. Nem Módosítandó Fájlok (hatóköri kizárás)

Ugyanazok, mint az AN-3B2C audit 6. szakaszában definiálva:
`football_skill_service.py`, `segment_reward_service.py`, `virtual_training_metrics.py`, `tournament_participation_service.py`, `juggling_analysis_task.py`, `onnx_ball_detector.py`, `frame_extractor.py`.

---

**Minden döntés lezárult (13. szakasz). Implementáció megkezdhető — kizárólag explicit jóváhagyás után.**
