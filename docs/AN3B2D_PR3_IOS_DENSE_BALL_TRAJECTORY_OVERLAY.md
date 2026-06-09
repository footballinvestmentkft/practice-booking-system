# AN-3B2D PR-3: iOS Dense Ball Trajectory Overlay

**Dátum:** 2026-06-18  
**Branch:** `feat/an3b2d-3-ball-trajectory-overlay` (from PR-2 branch merged with main)  
**Előfeltétel:** PR-1 merged to main + `BALL_TRAJECTORY_ENABLED=true` + migration applied  
**Deployment target:** iOS 15.0

---

## 1. Scope

| Elem | Leírás |
|------|--------|
| API client | GET `/ball-trajectory` + POST `/manual-seed` hívások |
| ViewModel | `BallTrajectoryViewModel` — trajectory fetch, polling, manual seed |
| Overlay | `BallTrajectoryOverlayView` — playhead-synced marker + trail |
| Integration | JugglingAnnotationScreen: continuous ball > event-snapshot fallback |
| Status UI | Processing / lost / no-data / failed bannerek |

---

## 2. API Client Extension

**Fájl:** `JugglingAnnotationAPIClient.swift` — 2 új metódus

```swift
// GET /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory?from_ms=X&to_ms=Y
func fetchBallTrajectory(videoId: String, fromMs: Int, toMs: Int) async -> BallTrajectoryResponse?

// POST /api/v1/users/me/juggling/videos/{videoId}/ball-trajectory/manual-seed
func postManualBallSeed(videoId: String, frameMs: Int, ballX: Double, ballY: Double) async -> Bool
```

**Response DTO-k:** `BallTrajectoryDTO.swift` (ÚJ fájl)

```swift
struct BallTrajectoryPointDTO: Decodable, Equatable {
    let frameMs: Int
    let ballX: Double?
    let ballY: Double?
    let confidence: Double?
    let isManual: Bool
    let trackingState: String      // detected / predicted / lost / manual_seed
}

struct BallTrajectoryResponse: Decodable {
    let status: String              // pending / processing / complete / failed
    let points: [BallTrajectoryPointDTO]
}
```

---

## 3. BallTrajectoryViewModel

**Fájl:** `BallTrajectoryViewModel.swift` (ÚJ — önálló ObservableObject, nem a fő VM-ben)

```swift
final class BallTrajectoryViewModel: ObservableObject {

    @Published private(set) var status: BallTrajectoryStatus = .idle
    @Published private(set) var points: [BallTrajectoryPointDTO] = []
    @Published private(set) var isPolling = false

    enum BallTrajectoryStatus: Equatable {
        case idle               // nem kértük le még
        case loading            // első fetch folyamatban
        case processing(Int)    // backend task fut, progress %
        case complete           // trajectory kész, points tele
        case noData             // 404 — backend nem dolgozta fel
        case featureDisabled    // 503 — flag OFF
        case failed(String)     // hálózati/szerver hiba
    }

    let videoId: String
    private var pollingTask: Task<Void, Never>?

    func fetchTrajectory(fromMs: Int, toMs: Int) async { ... }
    func startPolling(windowMs: Int) { ... }
    func stopPolling() { ... }
    func postManualSeed(frameMs: Int, ballX: Double, ballY: Double) async { ... }

    // Binary search: closest point to playhead
    func point(atMs ms: Int) -> BallTrajectoryPointDTO? { ... }
    // Trail: last N points before current ms
    func trail(beforeMs ms: Int, count: Int = 10) -> [BallTrajectoryPointDTO] { ... }
}
```

**Polling logika:**
- `status == .processing` → fetch every 3s (Celery task running)
- `status == .complete` → stop polling
- `status == .noData` / `.featureDisabled` → stop, show banner
- `status == .failed` → stop, show error banner

---

## 4. BallTrajectoryOverlayView

**Fájl:** `BallTrajectoryOverlayView.swift` (ÚJ — NEM módosítja BallVideoOverlayView.swift-et)

```swift
struct BallTrajectoryOverlayView: View {
    let currentPoint: BallTrajectoryPointDTO?   // legközelebbi pont a playhead-hez
    let trail: [BallTrajectoryPointDTO]          // utolsó 10 pont
    let trackingLost: Bool                       // nincs pont ±100ms-ben

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height

            // 1. Trail: halvány körök, csökkenő opacity
            ForEach(trail.indices, id: \.self) { i in
                if let bx = trail[i].ballX, let by = trail[i].ballY {
                    Circle()
                        .fill(trailColor(for: trail[i]).opacity(1.0 - Double(i) * 0.09))
                        .frame(width: max(6 - CGFloat(i) * 0.4, 2),
                               height: max(6 - CGFloat(i) * 0.4, 2))
                        .position(x: bx * w, y: by * h)
                }
            }

            // 2. Current marker: nagyobb, színkódolt kör
            if let pt = currentPoint, let bx = pt.ballX, let by = pt.ballY {
                ZStack {
                    Circle()
                        .strokeBorder(markerColor(for: pt), lineWidth: 2.5)
                        .background(Circle().fill(markerColor(for: pt).opacity(0.20)))
                        .frame(width: 28, height: 28)
                    if let conf = pt.confidence {
                        Text("\(Int(conf * 100))%")
                            .font(.system(size: 8, weight: .semibold).monospacedDigit())
                            .foregroundColor(.white)
                            .padding(.horizontal, 3).padding(.vertical, 1)
                            .background(Color.black.opacity(0.55))
                            .cornerRadius(3)
                            .offset(x: 20, y: -16)
                    }
                }
                .position(x: bx * w, y: by * h)
            }
        }
        .allowsHitTesting(false)
    }
}
```

**Szín kódolás:**
| Állapot | Szín |
|---------|------|
| `manual_seed` | Blue |
| `detected`, confidence ≥ 0.80 | Green |
| `detected`, confidence ≥ 0.50 | Yellow |
| `detected`, confidence < 0.50 | Orange |
| `predicted` | Orange, szaggatott szegély |
| `lost` | Nem renderelődik |

---

## 5. Screen integráció

**JugglingAnnotationScreen.swift módosítások:**

```swift
// Új @StateObject
@StateObject private var ballTrajectoryVM: BallTrajectoryViewModel

// Init-ben:
_ballTrajectoryVM = StateObject(wrappedValue: BallTrajectoryViewModel(videoId: video.videoId))

// Video load után:
ballTrajectoryVM.fetchTrajectory(fromMs: 0, toMs: 60000)
ballTrajectoryVM.startPolling(windowMs: 60000)

// Ball overlay prioritás (showBallOverlay block-on belül):
if showBallOverlay {
    if isBallSelecting {
        // ... manual tap-to-mark (meglévő)
    } else if ballTrajectoryVM.status == .complete,
              let pt = ballTrajectoryVM.point(atMs: playback.currentTimestampMs) {
        // Dense ball trajectory overlay
        BallTrajectoryOverlayView(
            currentPoint: pt,
            trail: ballTrajectoryVM.trail(beforeMs: playback.currentTimestampMs),
            trackingLost: false
        )
        .frame(width: renderSize.width, height: renderSize.height)
    } else if ballTrajectoryVM.status == .complete {
        // Dense trajectory exists but no point at current time → tracking lost
        BallTrajectoryOverlayView(currentPoint: nil, trail: [], trackingLost: true)
            .frame(width: renderSize.width, height: renderSize.height)
    } else if let bd = closestBallDetection(toMs: playback.currentTimestampMs) {
        // Fallback: event-snapshot overlay (meglévő)
        BallVideoOverlayView(detection: bd)
            .frame(width: renderSize.width, height: renderSize.height)
    } else {
        ballOverlayStatusBanner  // meglévő
            .frame(width: renderSize.width, height: renderSize.height)
    }
}
```

**Processing banner:**
```swift
if case .processing(let pct) = ballTrajectoryVM.status {
    HStack(spacing: 6) {
        ProgressView().scaleEffect(0.7).tint(.white)
        Text("Labda: \(pct)%")
            .font(.system(size: 11, weight: .medium).monospacedDigit())
    }
    // ... same styling as skeleton progress banner
}
```

---

## 6. Manual Seed Flow

A meglévő `handleBallSelection` kiegészítése:

```swift
// Ha dense trajectory aktív: POST /manual-seed a trajectory endpontra is
if ballTrajectoryVM.status == .complete {
    Task {
        await ballTrajectoryVM.postManualSeed(
            frameMs: playback.currentTimestampMs,
            ballX: x, ballY: y
        )
    }
}
```

A manuális seed pont azonnal megjelenik a trajectory overlay-ben (optimistic update).

---

## 7. Status Bannerek

| BallTrajectoryStatus | Banner szöveg | Akció |
|---------------------|---------------|-------|
| `.idle` / `.loading` | "Labda pálya betöltés…" | Spinner |
| `.processing(pct)` | "Labda feldolgozás: XX%" | Spinner + progress |
| `.complete` + tracking lost | "Labda elveszett — koppints a labdára" | Tap-to-seed |
| `.noData` | "Labda pálya nem elérhető" | Fallback event-snapshot |
| `.featureDisabled` | "Labda detektálás nem elérhető" | — |
| `.failed(msg)` | "Labda pálya hiba" | Retry button |

---

## 8. Commit bontás

| # | Commit | Scope |
|---|--------|-------|
| C1 | `BallTrajectoryDTO + API client methods` | DTOs + fetchBallTrajectory + postManualBallSeed |
| C2 | `BallTrajectoryViewModel` | Fetch, polling, binary search, trail, manual seed |
| C3 | `BallTrajectoryOverlayView` | Marker + trail rendering + colour coding |
| C4 | `Screen integration + status banners` | Overlay priority, processing banner, cancel, manual seed hook |
| C5 | `Tests` | VM polling, overlay rendering, binary search, colour logic |

---

## 9. Tesztek

| Test ID | Leírás |
|---------|--------|
| BTR-01 | `BallTrajectoryViewModel.point(atMs:)` — exact match |
| BTR-02 | `BallTrajectoryViewModel.point(atMs:)` — 50ms off → nearest |
| BTR-03 | `BallTrajectoryViewModel.point(atMs:)` — no data → nil |
| BTR-04 | `BallTrajectoryViewModel.trail(beforeMs:count:)` — returns last 10 |
| BTR-05 | `BallTrajectoryViewModel.trail(beforeMs:count:)` — empty when no data |
| BTR-06 | `BallTrajectoryOverlayView` marker colour: manual → blue |
| BTR-07 | `BallTrajectoryOverlayView` marker colour: high conf → green |
| BTR-08 | `BallTrajectoryOverlayView` marker colour: low conf → orange |
| BTR-09 | Status: `.noData` → fallback event-snapshot |
| BTR-10 | Status: `.featureDisabled` → banner text correct |

---

## 10. iPhone QA terv

| Teszt | Elvárt |
|-------|--------|
| Q1: Skeleton + ball egyszerre | Mindkét overlay látszik, toggle-ök működnek |
| Q2: Ball trail | Halvány pontsor a labda mögött |
| Q3: Ball tracking lost → tap-to-seed | Banner + koppintás → manuális seed → marker megjelenik |
| Q4: Processing banner | "Labda: XX%" amíg a backend dolgozik |
| Q5: Seek test | Videó közepére seek → ball marker azonnal frissül |
| Q6: No trajectory data | "Labda pálya nem elérhető" banner + event-snapshot fallback |

---

## 11. Nem módosított fájlok

| Fájl | Státusz |
|------|---------|
| `BallVideoOverlayView.swift` | NEM módosul (fallback overlay marad) |
| `BallOverlayView.swift` | NEM módosul |
| `juggling_analysis_task.py` | NEM módosul |
| `onnx_ball_detector.py` | NEM módosul |

---

*Implementáció NEM kezdődhet el jóváhagyás nélkül.*

*A meglévő event-snapshot ball overlay (BallVideoOverlayView) változatlan marad — a BallTrajectoryOverlayView ráépül és prioritást kap, de fallback-ként az event-snapshot mindig elérhető.*
