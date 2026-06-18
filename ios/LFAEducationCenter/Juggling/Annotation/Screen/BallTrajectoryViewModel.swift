import Foundation

// MARK: — BallTrajectoryViewModel (AN-3B2D-3)
//
// Manages dense ball trajectory lifecycle for a single video.
// Fetches trajectory from backend, polls during processing,
// provides playhead-synced point lookup and trail.

enum BallTrajectoryStatus: Equatable {
    case idle
    case loading
    case processing
    case complete
    case noData
    case featureDisabled
    case failed(String)
}

final class BallTrajectoryViewModel: ObservableObject {

    @Published private(set) var status: BallTrajectoryStatus = .idle
    @Published private(set) var points: [BallTrajectoryPointDTO] = []

    let videoId: String
    private var pollingTask: Task<Void, Never>?
    private let apiClient: JugglingAnnotationAPIClientProtocol?
    private let maxWindowMs = 60_000

    init(videoId: String, apiClient: JugglingAnnotationAPIClientProtocol? = nil) {
        self.videoId = videoId
        self.apiClient = apiClient
    }

    // MARK: — Fetch (chunked for long videos)

    @MainActor
    func fetchTrajectory(durationMs: Int? = nil) async {
        guard status == .idle || status == .loading else { return }
        status = .loading

        guard let client = apiClient as? JugglingAnnotationAPIClient else {
            status = .noData
            return
        }

        let totalMs = durationMs ?? maxWindowMs
        var allPoints: [BallTrajectoryPointDTO] = []
        var lastStatus = "pending"

        var fromMs = 0
        while fromMs < totalMs {
            let toMs = min(fromMs + maxWindowMs, totalMs)
            guard let response = await client.fetchBallTrajectory(
                videoId: videoId, fromMs: fromMs, toMs: toMs
            ) else {
                if allPoints.isEmpty {
                    status = fromMs == 0 ? .noData : .failed("Partial fetch failed")
                    return
                }
                break
            }

            lastStatus = response.status
            allPoints.append(contentsOf: response.points)
            fromMs = toMs + 1
        }

        points = allPoints.sorted(by: { $0.frameMs < $1.frameMs })

        switch lastStatus {
        case "complete":
            status = .complete
        case "processing", "pending":
            status = .processing
            startPolling(durationMs: totalMs)
        case "failed":
            status = .failed("Backend processing failed")
        default:
            status = points.isEmpty ? .noData : .complete
        }
    }

    // MARK: — Polling

    func startPolling(durationMs: Int? = nil) {
        guard pollingTask == nil else { return }
        let totalMs = durationMs ?? maxWindowMs
        pollingTask = Task { [weak self] in
            var cycles = 0
            while !Task.isCancelled, cycles < 60 {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard let self = self, !Task.isCancelled else { return }

                guard let client = self.apiClient as? JugglingAnnotationAPIClient else { return }
                guard let response = await client.fetchBallTrajectory(
                    videoId: self.videoId, fromMs: 0, toMs: min(totalMs, self.maxWindowMs)
                ) else {
                    cycles += 1
                    continue
                }

                await MainActor.run {
                    if response.status == "complete" || response.status == "failed" {
                        self.points = response.points.sorted(by: { $0.frameMs < $1.frameMs })
                        self.status = response.status == "complete" ? .complete : .failed("Backend failed")
                        self.stopPolling()
                    }
                }

                if response.status == "complete" || response.status == "failed" { return }
                cycles += 1
            }

            await MainActor.run { [weak self] in
                if self?.status == .processing {
                    self?.status = .failed("Polling timeout")
                }
            }
        }
    }

    func stopPolling() {
        pollingTask?.cancel()
        pollingTask = nil
    }

    // MARK: — Point lookup (binary search, ≤100ms threshold)

    func point(atMs ms: Int) -> BallTrajectoryPointDTO? {
        guard !points.isEmpty else { return nil }

        let idx = closestIndex(toMs: ms)
        let pt = points[idx]
        guard abs(pt.frameMs - ms) <= 100 else { return nil }
        guard pt.trackingState != "lost" else { return nil }
        return pt
    }

    // MARK: — Trail (last N visible points before ms)

    func trail(beforeMs ms: Int, count: Int = 10) -> [BallTrajectoryPointDTO] {
        guard !points.isEmpty else { return [] }

        let idx = closestIndex(toMs: ms)
        var result: [BallTrajectoryPointDTO] = []
        var i = idx
        while i >= 0 && result.count < count {
            let pt = points[i]
            if pt.frameMs < ms, pt.trackingState != "lost", pt.ballX != nil, pt.ballY != nil {
                result.append(pt)
            }
            i -= 1
        }
        return result
    }

    // MARK: — Manual seed (optimistic update)

    @MainActor
    func postManualSeed(frameMs: Int, ballX: Double, ballY: Double) async {
        let seedPoint = BallTrajectoryPointDTO(
            frameMs: frameMs, ballX: ballX, ballY: ballY,
            confidence: nil, isManual: true, trackingState: "manual_seed"
        )

        if let existingIdx = points.firstIndex(where: { $0.frameMs == frameMs }) {
            points[existingIdx] = seedPoint
        } else {
            points.append(seedPoint)
            points.sort(by: { $0.frameMs < $1.frameMs })
        }

        guard let client = apiClient as? JugglingAnnotationAPIClient else { return }
        _ = await client.postManualBallSeed(
            videoId: videoId, frameMs: frameMs, ballX: ballX, ballY: ballY
        )
    }

    // MARK: — Binary search helper

    private func closestIndex(toMs ms: Int) -> Int {
        var lo = 0, hi = points.count - 1
        while lo < hi {
            let mid = (lo + hi) / 2
            if points[mid].frameMs < ms { lo = mid + 1 } else { hi = mid }
        }
        if lo > 0 && abs(points[lo - 1].frameMs - ms) < abs(points[lo].frameMs - ms) {
            return lo - 1
        }
        return lo
    }

    func cancel() { stopPolling() }
    deinit { stopPolling() }
}
