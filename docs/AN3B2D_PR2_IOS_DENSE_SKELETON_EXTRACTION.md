# AN-3B2D PR-2: iOS Dense Skeleton Extraction

**Dátum:** 2026-06-17  
**Branch:** `feat/an3b2d-2-dense-skeleton` (from `main`)  
**Státusz:** Terv — jóváhagyásra vár  
**Előfeltétel:** Nincs (független a PR-1-től; egymás után mergelhetők bármilyen sorrendben)  
**Deployment target:** iOS 15.0  
**Nem módosított fájlok:** `PoseSnapshotService.swift`, `PoseSnapshotOverlayView.swift`, `BallVideoOverlayView.swift`

---

## 1. Scope összefoglalás

| Elem | Leírás |
|------|--------|
| Frame extraction | `DensePoseExtractor` — AVAssetReader streaming pipeline |
| Vision inference | VNDetectHumanBodyPoseRequest per sampled frame (10 FPS) |
| Data model | `DensePoseFrame` — timestamp + keypoints + synthetic feet |
| Cache | In-memory + temp disk (per videoId) |
| Overlay | `ContinuousSkeletonOverlayView` — playhead-synced, interpolated |
| Synthetic feet | Ankle-based extension, szaggatott vonal, degradált confidence |
| Progress UI | Processing banner a videó felett |
| Tesztek | 18 iOS unit tesztek (DPSE-01..DPSE-18) |

---

## 2. AVAssetReader Pipeline

### 2.1 Miért nem AVAssetImageGenerator

A meglévő `PoseSnapshotService.extractFrame()` `AVAssetImageGenerator`-t használ. Ez jó **1-2 frame-re**, de **600+ frame-re** nem hatékony:

| Szempont | AVAssetImageGenerator | AVAssetReader |
|----------|----------------------|---------------|
| Seek overhead | Minden frame-re seek (random access) | Szekvenciális olvasás — natívan streaming |
| Memory | CGImage-eket generál (heavy) | CVPixelBuffer — lightweight, nem allokál új memóriát |
| Vision input | CGImage → VNImageRequestHandler | CVPixelBuffer → VNImageRequestHandler (közvetlenül!) |
| Batch perf | ~50ms / frame (seek + decode + copy) | ~5-8ms / frame (sequential decode) |
| 600 frame-re | ~30 sec | ~4-5 sec |

### 2.2 DensePoseExtractor

**Fájl:** `ios/LFAEducationCenter/Juggling/Annotation/DensePoseExtractor.swift` (ÚJ)

```swift
// DensePoseExtractor — streaming Vision body pose extraction from a video asset.
//
// Uses AVAssetReader for efficient sequential frame access.
// Processes every Nth frame (default: every 3rd at 30fps → ~10 FPS).
// Results are delivered incrementally via a callback.
//
// Thread model:
//   - Extraction runs on a background queue (not MainActor)
//   - Progress/completion callbacks dispatched to MainActor
//   - VNImageRequestHandler runs on the same background queue (no actor hop)
//
// Cancellation:
//   - cancel() stops the AVAssetReader and sets isCancelled
//   - Safe to call from any thread

enum DensePoseExtractorError: Error {
    case cannotCreateReader
    case noVideoTrack
    case readerFailed(String)
}

final class DensePoseExtractor {

    struct Config {
        let samplingFPS: Double       // target extraction rate (default: 10)
        let confidenceThreshold: Float // joint filter (default: 0.3)
        let syntheticFootEnabled: Bool // add estimated foot points (default: true)
        let syntheticFootExtension: Double // fraction of shin length (default: 0.25)
    }

    static let defaultConfig = Config(
        samplingFPS: 10,
        confidenceThreshold: 0.3,
        syntheticFootEnabled: true,
        syntheticFootExtension: 0.25
    )

    // State
    private(set) var isRunning = false
    private(set) var isCancelled = false
    private(set) var progress: Double = 0.0  // 0.0..1.0
    private var reader: AVAssetReader?
    private let queue = DispatchQueue(label: "com.lfa.densePose", qos: .utility)

    // Results
    private(set) var frames: [DensePoseFrame] = []

    func extract(
        from asset: AVAsset,
        config: Config = defaultConfig,
        onProgress: @escaping @MainActor (Double) -> Void,
        onFrame: @escaping @MainActor (DensePoseFrame) -> Void,
        onComplete: @escaping @MainActor (Result<[DensePoseFrame], Error>) -> Void
    ) { ... }

    func cancel() { ... }
}
```

### 2.3 Extraction logika (részletes)

```swift
func extract(...) {
    guard !isRunning else { return }
    isRunning = true
    isCancelled = false
    frames = []

    queue.async { [weak self] in
        guard let self = self else { return }

        // 1. AVAssetReader setup
        guard let reader = try? AVAssetReader(asset: asset) else {
            self.complete(with: .failure(.cannotCreateReader), onComplete)
            return
        }
        self.reader = reader

        guard let videoTrack = asset.tracks(withMediaType: .video).first else {
            self.complete(with: .failure(.noVideoTrack), onComplete)
            return
        }

        let outputSettings: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ]
        let output = AVAssetReaderTrackOutput(track: videoTrack, outputSettings: outputSettings)
        output.alwaysCopiesSampleData = false  // zero-copy where possible
        reader.add(output)

        guard reader.startReading() else {
            self.complete(with: .failure(.readerFailed(reader.error?.localizedDescription ?? "unknown")), onComplete)
            return
        }

        // 2. Compute frame skip interval
        let fps = videoTrack.nominalFrameRate  // e.g. 30.0
        let skipInterval = max(1, Int(round(Double(fps) / config.samplingFPS)))
        let durationMs = Int(CMTimeGetSeconds(asset.duration) * 1000)

        // 3. Frame loop
        var frameIndex = 0
        let poseRequest = VNDetectHumanBodyPoseRequest()

        while let sampleBuffer = output.copyNextSampleBuffer() {
            if self.isCancelled { break }

            // Skip frames for sampling rate
            if frameIndex % skipInterval != 0 {
                frameIndex += 1
                continue
            }

            let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
            let timestampMs = Int(CMTimeGetSeconds(presentationTime) * 1000)

            guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
                frameIndex += 1
                continue
            }

            // 4. Vision pose detection
            let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: .up)
            let denseFrame = self.runPoseDetection(
                handler: handler,
                request: poseRequest,
                timestampMs: timestampMs,
                config: config
            )

            self.frames.append(denseFrame)

            // 5. Progress + incremental callback
            let pct = Double(timestampMs) / Double(max(durationMs, 1))
            DispatchQueue.main.async { [pct, denseFrame] in
                onProgress(min(pct, 1.0))
                onFrame(denseFrame)
            }

            frameIndex += 1
        }

        // 6. Complete
        self.isRunning = false
        if self.isCancelled {
            self.complete(with: .success(self.frames), onComplete)
        } else if reader.status == .completed {
            self.complete(with: .success(self.frames), onComplete)
        } else {
            self.complete(with: .failure(.readerFailed(reader.error?.localizedDescription ?? "unknown")), onComplete)
        }
    }
}
```

### 2.4 Vision detection per frame

```swift
private func runPoseDetection(
    handler: VNImageRequestHandler,
    request: VNDetectHumanBodyPoseRequest,
    timestampMs: Int,
    config: Config
) -> DensePoseFrame {
    do {
        try handler.perform([request])
    } catch {
        return DensePoseFrame(timestampMs: timestampMs, keypoints: .empty(), confidence: nil, syntheticFeet: nil)
    }

    guard let observation = request.results?.first else {
        return DensePoseFrame(timestampMs: timestampMs, keypoints: .empty(), confidence: nil, syntheticFeet: nil)
    }

    // Reuse PoseSnapshotService's joint mapping logic
    let allPoints: [VNHumanBodyPoseObservation.JointName: VNRecognizedPoint]
    do {
        allPoints = try observation.recognizedPoints(.all)
    } catch {
        return DensePoseFrame(timestampMs: timestampMs, keypoints: .empty(), confidence: Float(observation.confidence), syntheticFeet: nil)
    }

    let landmarks: [BodyLandmarkDTO] = allPoints.compactMap { (key, point) in
        guard point.confidence >= config.confidenceThreshold else { return nil }
        let jsonName = DensePoseExtractor.jointNameMap[key.rawValue.rawValue] ?? key.rawValue.rawValue
        return BodyLandmarkDTO(
            name:       jsonName,
            x:          Double(point.location.x),
            y:          Double(1.0 - point.location.y),
            confidence: Double(point.confidence)
        )
    }

    let keypoints = PoseKeypointsDTO(schemaVersion: "1", body: landmarks, leftHand: [], rightHand: [])

    // Synthetic feet
    let syntheticFeet: SyntheticFeetDTO?
    if config.syntheticFootEnabled {
        syntheticFeet = Self.estimateFeet(
            from: landmarks,
            extension: config.syntheticFootExtension
        )
    } else {
        syntheticFeet = nil
    }

    return DensePoseFrame(
        timestampMs: timestampMs,
        keypoints: keypoints,
        confidence: Float(observation.confidence),
        syntheticFeet: syntheticFeet
    )
}
```

### 2.5 Joint name mapping

A `DensePoseExtractor` ugyanazt a joint mapping-et használja mint a `PoseSnapshotService`:

```swift
// Duplicated from PoseSnapshotService.jointNameMap — intentionally not shared
// because PoseSnapshotService is the event-snapshot path (unchanged).
// Keeps the two modules independent.
static let jointNameMap: [String: String] = {
    var m: [String: String] = [:]
    m["nose"] = "nose"; m["leftEye"] = "left_eye"; m["rightEye"] = "right_eye"
    m["leftEar"] = "left_ear"; m["rightEar"] = "right_ear"
    m["neck1"] = "neck"
    m["leftShoulder1"] = "left_shoulder"; m["rightShoulder1"] = "right_shoulder"
    m["leftElbow1"] = "left_elbow"; m["rightElbow1"] = "right_elbow"
    m["leftWrist1"] = "left_wrist"; m["rightWrist1"] = "right_wrist"
    m["root"] = "root"
    m["leftHip1"] = "left_hip"; m["rightHip1"] = "right_hip"
    m["leftKnee1"] = "left_knee"; m["rightKnee1"] = "right_knee"
    m["leftAnkle1"] = "left_ankle"; m["rightAnkle1"] = "right_ankle"
    return m
}()
```

---

## 3. Skeleton Trajectory Model

### 3.1 DensePoseFrame

**Fájl:** `ios/LFAEducationCenter/Juggling/Annotation/DensePoseDTO.swift` (ÚJ)

```swift
struct DensePoseFrame: Equatable {
    let timestampMs:    Int
    let keypoints:      PoseKeypointsDTO
    let confidence:     Float?
    let syntheticFeet:  SyntheticFeetDTO?
}

struct SyntheticFeetDTO: Equatable {
    let leftFoot:   SyntheticFootPoint?
    let rightFoot:  SyntheticFootPoint?
}

struct SyntheticFootPoint: Equatable {
    let x:          Double    // normalized [0,1]
    let y:          Double    // normalized [0,1]
    let confidence: Double    // ankle confidence × 0.7 (degraded)
    let ankleX:     Double    // ankle source point (for rendering the dashed line from ankle to foot)
    let ankleY:     Double
}
```

### 3.2 Reuse of PoseKeypointsDTO

A `DensePoseFrame.keypoints` a **meglévő** `PoseKeypointsDTO`-t használja. Ez azt jelenti:
- A `ContinuousSkeletonOverlayView` ugyanazt a bone/joint rendering logikát tudja használni mint a `PoseSnapshotOverlayView`
- Nem kell új DTO
- A `body: [BodyLandmarkDTO]` tömb a 19 Vision joint-ot tartalmazza (confidence > 0.3 felett)

---

## 4. Synthetic Foot Representation

### 4.1 Algoritmus

Apple Vision 2D body pose **NEM ad** foot/toe landmark-ot. A legalsó pont: `left_ankle`, `right_ankle`.

```swift
static func estimateFeet(
    from landmarks: [BodyLandmarkDTO],
    extension ext: Double
) -> SyntheticFeetDTO {
    let byName = Dictionary(uniqueKeysWithValues: landmarks.map { ($0.name, $0) })

    let left  = estimateOneFoot(knee: byName["left_knee"],  ankle: byName["left_ankle"],  ext: ext)
    let right = estimateOneFoot(knee: byName["right_knee"], ankle: byName["right_ankle"], ext: ext)

    return SyntheticFeetDTO(leftFoot: left, rightFoot: right)
}

private static func estimateOneFoot(
    knee: BodyLandmarkDTO?,
    ankle: BodyLandmarkDTO?,
    ext: Double
) -> SyntheticFootPoint? {
    guard let ankle = ankle else { return nil }
    guard let knee = knee else {
        // Ha nincs knee adat: a lábfej pont = boka pont + lefelé offset
        let footY = min(ankle.y + 0.03, 0.98)
        return SyntheticFootPoint(
            x: ankle.x, y: footY,
            confidence: ankle.confidence * 0.5,
            ankleX: ankle.x, ankleY: ankle.y
        )
    }

    // shin vector: ankle - knee
    let dx = ankle.x - knee.x
    let dy = ankle.y - knee.y

    // foot tip: ankle + shin_vector × extension factor
    var footX = ankle.x + dx * ext
    var footY = ankle.y + dy * ext

    // Clamp to image bounds
    footX = max(0.0, min(footX, 1.0))
    footY = max(0.0, min(footY, 0.98))

    return SyntheticFootPoint(
        x: footX, y: footY,
        confidence: ankle.confidence * 0.7,
        ankleX: ankle.x, ankleY: ankle.y
    )
}
```

### 4.2 Vizuális megjelenítés

A szintetikus lábfej **vizuálisan megkülönböztetett** a valódi landmark-októl:

| Elem | Valódi joint (Vision) | Szintetikus lábfej |
|------|----------------------|-------------------|
| Vonal stílus | Solid (2.5 pt cyan) | **Szaggatott** (2 pt, dash pattern [4, 4]) |
| Vonal szín | Cyan | Cyan, **csökkentett opacity** (0.6) |
| Pont méret | 10 pt fill | **8 pt fill** (kisebb) |
| Pont szegély | Dark 14 pt ring | Dark 12 pt ring |
| Pont szín | Confidence-based (yellow/orange/red) | Confidence-based, de mindig **degradált** |
| Label | Nincs | **"~" prefix** (DEBUG mode) |

### 4.3 Dokumentáció

A szintetikus lábfej tényét az overlay nézet kommentje dokumentálja:

```swift
// Synthetic foot estimation: Apple Vision 2D body pose does NOT provide
// foot/toe landmarks. The lowest available point is the ankle.
// Foot tip = ankle + (ankle - knee) × 0.25, confidence = ankle × 0.7.
// Rendered with dashed lines to visually distinguish from detected joints.
```

---

## 5. Overlay Renderer: ContinuousSkeletonOverlayView

**Fájl:** `ios/LFAEducationCenter/Juggling/Annotation/Screen/ContinuousSkeletonOverlayView.swift` (ÚJ)

### 5.1 API

```swift
struct ContinuousSkeletonOverlayView: View {
    let frame: DensePoseFrame?          // nil = no data for current time
    let showSyntheticFeet: Bool         // default: true

    var body: some View {
        GeometryReader { geo in
            if let frame = frame {
                ZStack {
                    // 1. Real bones (solid lines)
                    realBoneLayer(keypoints: frame.keypoints, w: geo.size.width, h: geo.size.height)

                    // 2. Synthetic foot lines (dashed)
                    if showSyntheticFeet, let feet = frame.syntheticFeet {
                        syntheticFootLayer(feet: feet, w: geo.size.width, h: geo.size.height)
                    }

                    // 3. Real joints (filled circles)
                    realJointLayer(keypoints: frame.keypoints, w: geo.size.width, h: geo.size.height)

                    // 4. Synthetic foot points (smaller, distinguishable)
                    if showSyntheticFeet, let feet = frame.syntheticFeet {
                        syntheticFootPointLayer(feet: feet, w: geo.size.width, h: geo.size.height)
                    }
                }
            }
        }
        .allowsHitTesting(false)
    }
}
```

### 5.2 Bone rendering

Ugyanaz a double-stroke logika mint a `PoseSnapshotOverlayView`:
- Outer: 5 pt dark halo
- Inner: 2.5 pt cyan

Bone connectivity: a meglévő 17 csont pár (PoseSnapshotOverlayView.bones).

### 5.3 Synthetic foot rendering

```swift
private func syntheticFootLayer(feet: SyntheticFeetDTO, w: CGFloat, h: CGFloat) -> some View {
    let segments = syntheticFootSegments(feet: feet, w: w, h: h)
    return ZStack {
        // Outer dark halo (dashed)
        Path { path in
            for (s, e) in segments { path.move(to: s); path.addLine(to: e) }
        }
        .stroke(
            Color.black.opacity(0.45),
            style: StrokeStyle(lineWidth: 4, dash: [4, 4])
        )

        // Inner cyan (dashed)
        Path { path in
            for (s, e) in segments { path.move(to: s); path.addLine(to: e) }
        }
        .stroke(
            Color.cyan.opacity(0.6),
            style: StrokeStyle(lineWidth: 2, dash: [4, 4])
        )
    }
}

static func syntheticFootSegments(
    feet: SyntheticFeetDTO, w: CGFloat, h: CGFloat
) -> [(CGPoint, CGPoint)] {
    var segments: [(CGPoint, CGPoint)] = []
    if let lf = feet.leftFoot {
        segments.append((
            CGPoint(x: lf.ankleX * w, y: lf.ankleY * h),
            CGPoint(x: lf.x * w, y: lf.y * h)
        ))
    }
    if let rf = feet.rightFoot {
        segments.append((
            CGPoint(x: rf.ankleX * w, y: rf.ankleY * h),
            CGPoint(x: rf.x * w, y: rf.y * h)
        ))
    }
    return segments
}
```

---

## 6. Progress UI

### 6.1 Processing banner

A videó overlay felett, a toggle gombok alatt:

```swift
// JugglingAnnotationScreen-ben, a ZStack-en belül:
if denseSkeletonProgress < 1.0 && denseSkeletonProgress > 0.0 {
    VStack {
        Spacer()
        HStack(spacing: 6) {
            ProgressView()
                .scaleEffect(0.7)
                .tint(.white)
            Text("Skeleton: \(Int(denseSkeletonProgress * 100))%")
                .font(.system(size: 11, weight: .medium).monospacedDigit())
                .foregroundColor(.white.opacity(0.85))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Color.black.opacity(0.55))
        .cornerRadius(6)
        .padding(.bottom, 8)
    }
}
```

### 6.2 State-ek

| Állapot | Progress | Overlay | Banner |
|---------|----------|---------|--------|
| Nem indult | 0.0 | Fallback: PoseSnapshotOverlayView (event-snapshot) | "Skeleton feldolgozás indítás..." |
| Folyamatban | 0.01-0.99 | Partial: eddig kész frame-ek mutathatók | "Skeleton: 42%" |
| Kész | 1.0 | Teljes ContinuousSkeletonOverlayView | Nincs banner |
| Hiba | — | Fallback: PoseSnapshotOverlayView | "Skeleton feldolgozás sikertelen" |

---

## 7. Cache / Storage

### 7.1 In-memory cache

```swift
// DensePoseCache — per-video in-memory skeleton trajectory cache.
//
// Keyed by videoId. Cleared when the screen is dismissed.
// Thread-safe: accessed from MainActor only (via ViewModel).

final class DensePoseCache {
    private var store: [String: [DensePoseFrame]] = [:]

    func get(_ videoId: String) -> [DensePoseFrame]? {
        store[videoId]
    }

    func set(_ videoId: String, frames: [DensePoseFrame]) {
        store[videoId] = frames
    }

    func append(_ videoId: String, frame: DensePoseFrame) {
        store[videoId, default: []].append(frame)
    }

    func clear(_ videoId: String) {
        store.removeValue(forKey: videoId)
    }

    func clearAll() {
        store.removeAll()
    }
}
```

### 7.2 Temp disk cache (opcionális — PR-2 scope-on kívül)

A disk cache a `FileManager.default.temporaryDirectory` alá kerülne, de ez **nem a PR-2 scope-ja**. Az in-memory cache elég az első verzióhoz:
- 60 sec videó: ~365 KB → elfogadható
- 5 perc videó: ~1.8 MB → elfogadható
- Screen dismiss → cache clear

### 7.3 Memória becslés

```
1 DensePoseFrame ≈ 19 joints × 32 byte + overhead ≈ 700 byte
600 frames (60 sec) × 700 byte ≈ 420 KB
3000 frames (5 perc) × 700 byte ≈ 2.1 MB
```

---

## 8. ViewModel integráció

### 8.1 Új @Published tulajdonságok

A `JugglingAnnotationViewModel`-be VAGY egy **önálló** ObservableObject (`DenseSkeletonViewModel`) — a kettő közül az önálló a jobb, mert a meglévő ViewModel 1083 sor.

**Döntés: Önálló `DenseSkeletonViewModel`**

```swift
// DenseSkeletonViewModel — manages dense pose extraction lifecycle.
//
// Owned by JugglingAnnotationScreen, NOT by JugglingAnnotationViewModel.
// Keeps the existing ViewModel untouched.

final class DenseSkeletonViewModel: ObservableObject {

    @Published private(set) var progress: Double = 0.0      // 0.0..1.0
    @Published private(set) var status: DenseSkeletonStatus = .idle
    @Published private(set) var frameCount: Int = 0

    enum DenseSkeletonStatus {
        case idle
        case extracting
        case complete
        case failed(String)
    }

    private let extractor = DensePoseExtractor()
    private let cache = DensePoseCache()
    private let videoId: String

    init(videoId: String) {
        self.videoId = videoId
    }

    // Start extraction. Safe to call multiple times — no-op if already running or complete.
    func startExtraction(asset: AVAsset) {
        guard status == .idle else { return }
        status = .extracting

        extractor.extract(
            from: asset,
            onProgress: { [weak self] pct in
                self?.progress = pct
            },
            onFrame: { [weak self] frame in
                guard let self = self else { return }
                self.cache.append(self.videoId, frame: frame)
                self.frameCount += 1
            },
            onComplete: { [weak self] result in
                guard let self = self else { return }
                switch result {
                case .success(let frames):
                    self.cache.set(self.videoId, frames: frames)
                    self.status = .complete
                    self.progress = 1.0
                case .failure(let error):
                    self.status = .failed(error.localizedDescription)
                }
            }
        )
    }

    // Find the closest DensePoseFrame to the given playhead timestamp.
    // Returns nil if no frame within 100ms.
    func frame(atMs ms: Int) -> DensePoseFrame? {
        guard let frames = cache.get(videoId), !frames.isEmpty else { return nil }

        // Binary search for closest frame
        var lo = 0, hi = frames.count - 1
        while lo < hi {
            let mid = (lo + hi) / 2
            if frames[mid].timestampMs < ms {
                lo = mid + 1
            } else {
                hi = mid
            }
        }

        // Check lo and lo-1 for closest
        let candidates = [lo > 0 ? lo - 1 : lo, lo].filter { $0 < frames.count }
        let best = candidates.min(by: { abs(frames[$0].timestampMs - ms) < abs(frames[$1].timestampMs - ms) })!
        let dist = abs(frames[best].timestampMs - ms)

        return dist <= 100 ? frames[best] : nil
    }

    // Interpolated frame between two adjacent DensePoseFrames.
    // Falls back to nearest frame if interpolation not possible.
    func interpolatedFrame(atMs ms: Int) -> DensePoseFrame? {
        guard let frames = cache.get(videoId), frames.count >= 2 else {
            return frame(atMs: ms)
        }

        // Find the two frames bracketing this timestamp
        var lo = 0, hi = frames.count - 1
        while lo < hi {
            let mid = (lo + hi) / 2
            if frames[mid].timestampMs < ms {
                lo = mid + 1
            } else {
                hi = mid
            }
        }

        // If exact match or at boundary: return direct
        if frames[lo].timestampMs == ms { return frames[lo] }
        if lo == 0 { return abs(frames[0].timestampMs - ms) <= 100 ? frames[0] : nil }

        let prev = frames[lo - 1]
        let next = frames[lo]

        // Both must be within reasonable range
        let gap = next.timestampMs - prev.timestampMs
        guard gap > 0, gap <= 200 else { return frame(atMs: ms) }

        let t = Double(ms - prev.timestampMs) / Double(gap)
        return Self.interpolate(prev: prev, next: next, t: t)
    }

    // Linear interpolation of body landmarks between two frames
    static func interpolate(prev: DensePoseFrame, next: DensePoseFrame, t: Double) -> DensePoseFrame {
        let prevByName = Dictionary(uniqueKeysWithValues: prev.keypoints.body.map { ($0.name, $0) })
        let nextByName = Dictionary(uniqueKeysWithValues: next.keypoints.body.map { ($0.name, $0) })

        let allNames = Set(prevByName.keys).union(nextByName.keys)
        let interpolatedBody: [BodyLandmarkDTO] = allNames.compactMap { name in
            guard let p = prevByName[name], let n = nextByName[name] else {
                // Joint only in one frame: use whichever exists
                return prevByName[name] ?? nextByName[name]
            }
            return BodyLandmarkDTO(
                name: name,
                x: p.x + (n.x - p.x) * t,
                y: p.y + (n.y - p.y) * t,
                confidence: p.confidence + (n.confidence - p.confidence) * t
            )
        }

        let keypoints = PoseKeypointsDTO(schemaVersion: "1", body: interpolatedBody, leftHand: [], rightHand: [])

        // Interpolate synthetic feet similarly
        let syntheticFeet: SyntheticFeetDTO?
        if let pf = prev.syntheticFeet, let nf = next.syntheticFeet {
            syntheticFeet = interpolateFeet(prev: pf, next: nf, t: t)
        } else {
            syntheticFeet = prev.syntheticFeet ?? next.syntheticFeet
        }

        return DensePoseFrame(
            timestampMs: prev.timestampMs + Int(Double(next.timestampMs - prev.timestampMs) * t),
            keypoints: keypoints,
            confidence: prev.confidence,
            syntheticFeet: syntheticFeet
        )
    }

    func cancel() {
        extractor.cancel()
    }

    deinit {
        extractor.cancel()
        cache.clear(videoId)
    }
}
```

### 8.2 JugglingAnnotationScreen integráció

```swift
// JugglingAnnotationScreen — ÚJ @StateObject + overlay csere

@StateObject private var denseSkeletonVM: DenseSkeletonViewModel

// init-ben:
_denseSkeletonVM = StateObject(wrappedValue: DenseSkeletonViewModel(videoId: videoId))

// onAppear / video load:
denseSkeletonVM.startExtraction(asset: asset)

// Overlay logika (a meglévő ±500ms snapshot-ot felváltja):
if showSkeletonOverlay {
    if denseSkeletonVM.status == .complete || denseSkeletonVM.frameCount > 0 {
        // Continuous overlay — playhead-synced
        ContinuousSkeletonOverlayView(
            frame: denseSkeletonVM.interpolatedFrame(atMs: playback.currentTimestampMs),
            showSyntheticFeet: true
        )
        .frame(width: renderSize.width, height: renderSize.height)
    } else if let snap = closestSnapshot(toMs: playback.currentTimestampMs) {
        // Fallback: event-snapshot overlay (PoseSnapshotOverlayView)
        PoseSnapshotOverlayView(keypoints: snap.keypoints)
            .frame(width: renderSize.width, height: renderSize.height)
            .allowsHitTesting(false)
    } else {
        skeletonStatusBanner
            .frame(width: renderSize.width, height: renderSize.height)
    }
}
```

**Prioritás:**
1. Ha van dense adat (akár partial) → `ContinuousSkeletonOverlayView`
2. Ha nincs dense adat → meglévő `PoseSnapshotOverlayView` (event-snapshot fallback)
3. Ha nincs semmi → status banner

---

## 9. iPhone Performance Test

### 9.1 Mérési terv

| Mérés | Hogyan | Elfogadási kritérium |
|-------|--------|---------------------|
| Extraction idő (30s videó) | `CFAbsoluteTimeGetCurrent()` start/end | < 15 sec |
| Extraction idő (60s videó) | Ugyanaz | < 30 sec |
| Extraction idő (5 perc videó) | Ugyanaz | < 150 sec |
| Vision inference / frame | Per-frame timing | < 50ms átlag |
| Memory peak | Xcode Instruments → Memory | < 50 MB spike |
| Battery drain (60s videó) | Battery level before/after | < 2% |
| UI responsiveness | Manually scrub slider during extraction | Nincs UI freeze |

### 9.2 Fallback ha túl lassú

Ha az iPhone modellje lassú (iPhone SE 2, iPhone 8):

```swift
// Config.samplingFPS adaptálható:
let adaptiveFPS: Double = {
    // iPhone 12+ → 10 FPS
    // iPhone 11 → 7 FPS
    // iPhone X/8 → 5 FPS
    // Ez a heurisztika a ProcessInfo.processInfo.processorCount alapján
    // vagy egyszerű device check alapján állítható
    if ProcessInfo.processInfo.processorCount >= 6 { return 10.0 }
    if ProcessInfo.processInfo.processorCount >= 4 { return 7.0 }
    return 5.0
}()
```

Első verzió: **fix 10 FPS**. Adaptív FPS = future enhancement, nem PR-2 scope.

---

## 10. iOS tesztek

**Fájl:** `ios/LFAEducationCenterTests/Juggling/DensePoseExtractionTests.swift` (ÚJ)

### 10.1 Synthetic foot tesztek

| Test ID | Leírás |
|---------|--------|
| DPSE-01 | `estimateFeet` — both knee+ankle present → foot point extended along shin vector |
| DPSE-02 | `estimateFeet` — ankle only (no knee) → foot point = ankle + 0.03 downward |
| DPSE-03 | `estimateFeet` — no ankle → nil foot point |
| DPSE-04 | `estimateFeet` — foot point clamped to y ≤ 0.98 |
| DPSE-05 | `estimateFeet` — confidence degraded: ankle_conf × 0.7 (with knee) |
| DPSE-06 | `estimateFeet` — confidence degraded: ankle_conf × 0.5 (without knee) |

### 10.2 Interpolation tesztek

| Test ID | Leírás |
|---------|--------|
| DPSE-07 | `interpolate` — t=0.0 → returns prev frame exactly |
| DPSE-08 | `interpolate` — t=1.0 → returns next frame exactly |
| DPSE-09 | `interpolate` — t=0.5 → midpoint of each landmark |
| DPSE-10 | `interpolate` — joint only in prev → returned as-is |
| DPSE-11 | `interpolate` — joint only in next → returned as-is |

### 10.3 Binary search / frame lookup tesztek

| Test ID | Leírás |
|---------|--------|
| DPSE-12 | `frame(atMs:)` — exact match → returns that frame |
| DPSE-13 | `frame(atMs:)` — 50ms off → returns nearest frame |
| DPSE-14 | `frame(atMs:)` — 150ms off → returns nil (beyond 100ms threshold) |
| DPSE-15 | `frame(atMs:)` — empty cache → returns nil |

### 10.4 Overlay rendering tesztek

| Test ID | Leírás |
|---------|--------|
| DPSE-16 | `syntheticFootSegments` — both feet → 2 segments |
| DPSE-17 | `syntheticFootSegments` — left only → 1 segment |
| DPSE-18 | `syntheticFootSegments` — no feet → 0 segments |

### 10.5 Joint color tesztek

A meglévő `SkeletonOverlayTests` (SK-OV-01..05) továbbra is PASS — a `PoseSnapshotOverlayView.jointColor` nem változik.

A `ContinuousSkeletonOverlayView` ugyanazt a color logic-ot használja → tesztelve az SK-OV tesztekkel implicitly.

---

## 11. Fájl lista

| Fájl | Akció |
|------|-------|
| `ios/.../Juggling/Annotation/DensePoseExtractor.swift` | ÚJ |
| `ios/.../Juggling/Annotation/DensePoseDTO.swift` | ÚJ |
| `ios/.../Juggling/Annotation/DensePoseCache.swift` | ÚJ |
| `ios/.../Juggling/Annotation/Screen/DenseSkeletonViewModel.swift` | ÚJ |
| `ios/.../Juggling/Annotation/Screen/ContinuousSkeletonOverlayView.swift` | ÚJ |
| `ios/.../Juggling/Annotation/Screen/JugglingAnnotationScreen.swift` | MÓDOSÍTVA (overlay prioritás + StateObject + progress banner) |
| `ios/.../LFAEducationCenter.xcodeproj/project.pbxproj` | MÓDOSÍTVA (fájl regisztrálás) |
| `ios/LFAEducationCenterTests/Juggling/DensePoseExtractionTests.swift` | ÚJ |

**NEM módosított fájlok (explicit):**

| Fájl | Státusz |
|------|---------|
| `PoseSnapshotService.swift` | TILOS (event-snapshot path megmarad) |
| `PoseSnapshotOverlayView.swift` | TILOS (fallback overlay) |
| `PoseSnapshotDTO.swift` | NEM módosul (importálva, nem módosítva) |
| `BallVideoOverlayView.swift` | TILOS (az a PR-3 scope) |
| `JugglingAnnotationViewModel.swift` | NEM módosul (az új ViewModel önálló) |

---

## 12. Szálmodell és életciklus

```
JugglingAnnotationScreen (MainActor)
    │
    ├─ @StateObject denseSkeletonVM
    │     │
    │     ├─ .startExtraction(asset:)  ← onAppear, when asset ready
    │     │     │
    │     │     └─ DensePoseExtractor.extract()
    │     │           │
    │     │           └─ DispatchQueue("com.lfa.densePose", qos: .utility)
    │     │                 │
    │     │                 ├─ AVAssetReader → CVPixelBuffer (sequential)
    │     │                 ├─ VNImageRequestHandler (per frame, same queue)
    │     │                 ├─ DensePoseFrame creation
    │     │                 └─ DispatchQueue.main.async { onProgress(), onFrame() }
    │     │
    │     ├─ .frame(atMs:) / .interpolatedFrame(atMs:)  ← per render cycle
    │     │
    │     └─ deinit → cancel() + cache.clear()
    │
    └─ ContinuousSkeletonOverlayView(frame:)  ← pure SwiftUI rendering
```

**Megszakítás:** Ha a felhasználó elhagyja a képernyőt mid-extraction, a `deinit` meghívja `cancel()`-t, ami az `AVAssetReader`-t leállítja.

---

## 13. Edge case-ek

| Eset | Kezelés |
|------|---------|
| Videó 0 másodperces | 0 frame extracted → status = `.complete`, overlay fallback |
| Vision nem talál embert | `keypoints = .empty()` → skeleton nem jelenik meg azon a frame-en |
| Több ember a videón | Vision az elsőt (highest confidence) adja vissza — `request.results?.first` |
| Videó corrupt mid-stream | AVAssetReader → `.failed` status → extractor `.failed`, fallback overlay |
| Memory pressure | Az OS törölheti a temp fájlokat, de az in-memory cache életben marad amíg a screen él |
| Videó rotation | A `ContinuousSkeletonOverlayView` a render frame-re van méretezve, ami már rotation-adjusted |

---

*Implementáció NEM kezdődhet el jóváhagyás nélkül.*

*A meglévő event-snapshot pipeline (PoseSnapshotService, PoseSnapshotOverlayView) változatlan marad — a ContinuousSkeletonOverlayView ráépül és prioritást kap, de fallback-ként az event-snapshot mindig elérhető.*
