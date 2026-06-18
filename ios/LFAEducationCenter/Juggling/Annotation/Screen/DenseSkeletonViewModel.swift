import AVFoundation
import Combine

// MARK: — DenseSkeletonViewModel (AN-3B2D-2)
//
// Manages dense pose extraction lifecycle for a single video.
// Owned by JugglingAnnotationScreen as a @StateObject.
// Keeps JugglingAnnotationViewModel (1083 lines) untouched.

enum DenseSkeletonStatus: Equatable {
    case idle
    case extracting
    case complete
    case failed(String)
}

final class DenseSkeletonViewModel: ObservableObject {

    @Published private(set) var progress: Double = 0.0
    @Published private(set) var status: DenseSkeletonStatus = .idle
    @Published private(set) var frameCount: Int = 0

    let videoId: String
    private let extractor = DensePoseExtractor()
    let cache: DensePoseCache

    init(videoId: String, cache: DensePoseCache = DensePoseCache()) {
        self.videoId = videoId
        self.cache = cache
    }

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

    // MARK: — Frame lookup (binary search, ≤100ms threshold)

    func frame(atMs ms: Int) -> DensePoseFrame? {
        guard let frames = cache.get(videoId), !frames.isEmpty else { return nil }

        let idx = Self.closestIndex(in: frames, toMs: ms)
        let dist = abs(frames[idx].timestampMs - ms)
        return dist <= 100 ? frames[idx] : nil
    }

    // MARK: — Interpolated frame lookup

    func interpolatedFrame(atMs ms: Int) -> DensePoseFrame? {
        guard let frames = cache.get(videoId), frames.count >= 2 else {
            return frame(atMs: ms)
        }

        let idx = Self.closestIndex(in: frames, toMs: ms)

        if frames[idx].timestampMs == ms { return frames[idx] }

        let prev: DensePoseFrame
        let next: DensePoseFrame
        if frames[idx].timestampMs < ms {
            guard idx + 1 < frames.count else { return abs(frames[idx].timestampMs - ms) <= 100 ? frames[idx] : nil }
            prev = frames[idx]
            next = frames[idx + 1]
        } else {
            guard idx > 0 else { return abs(frames[idx].timestampMs - ms) <= 100 ? frames[idx] : nil }
            prev = frames[idx - 1]
            next = frames[idx]
        }

        let gap = next.timestampMs - prev.timestampMs
        guard gap > 0, gap <= 200 else { return frame(atMs: ms) }

        let t = Double(ms - prev.timestampMs) / Double(gap)
        return Self.interpolate(prev: prev, next: next, t: t)
    }

    // MARK: — Binary search helper

    static func closestIndex(in frames: [DensePoseFrame], toMs ms: Int) -> Int {
        var lo = 0, hi = frames.count - 1
        while lo < hi {
            let mid = (lo + hi) / 2
            if frames[mid].timestampMs < ms {
                lo = mid + 1
            } else {
                hi = mid
            }
        }
        if lo > 0 && abs(frames[lo - 1].timestampMs - ms) < abs(frames[lo].timestampMs - ms) {
            return lo - 1
        }
        return lo
    }

    // MARK: — Linear interpolation

    static func interpolate(prev: DensePoseFrame, next: DensePoseFrame, t: Double) -> DensePoseFrame {
        let prevByName = Dictionary(uniqueKeysWithValues: prev.keypoints.body.map { ($0.name, $0) })
        let nextByName = Dictionary(uniqueKeysWithValues: next.keypoints.body.map { ($0.name, $0) })

        let allNames = Set(prevByName.keys).union(nextByName.keys)
        let interpolatedBody: [BodyLandmarkDTO] = allNames.sorted().compactMap { name in
            guard let p = prevByName[name], let n = nextByName[name] else {
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

        let syntheticFeet: SyntheticFeetDTO?
        if let pf = prev.syntheticFeet, let nf = next.syntheticFeet {
            syntheticFeet = Self.interpolateFeet(prev: pf, next: nf, t: t)
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

    private static func interpolateFeet(prev: SyntheticFeetDTO, next: SyntheticFeetDTO, t: Double) -> SyntheticFeetDTO {
        return SyntheticFeetDTO(
            leftFoot: interpolateOneFoot(prev: prev.leftFoot, next: next.leftFoot, t: t),
            rightFoot: interpolateOneFoot(prev: prev.rightFoot, next: next.rightFoot, t: t)
        )
    }

    private static func interpolateOneFoot(prev: SyntheticFootPoint?, next: SyntheticFootPoint?, t: Double) -> SyntheticFootPoint? {
        guard let p = prev, let n = next else { return prev ?? next }
        return SyntheticFootPoint(
            x: p.x + (n.x - p.x) * t,
            y: p.y + (n.y - p.y) * t,
            confidence: p.confidence + (n.confidence - p.confidence) * t,
            ankleX: p.ankleX + (n.ankleX - p.ankleX) * t,
            ankleY: p.ankleY + (n.ankleY - p.ankleY) * t
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
