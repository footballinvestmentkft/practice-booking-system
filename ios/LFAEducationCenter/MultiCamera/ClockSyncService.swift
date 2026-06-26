import Foundation
import os
import QuartzCore

// MARK: — DTO

struct ServerTimeDTO: Codable, Sendable {
    let serverTimeUtc: String
    let serverEpochMs: Int
    let precision: String
    let source: String

    enum CodingKeys: String, CodingKey {
        case serverTimeUtc  = "server_time_utc"
        case serverEpochMs  = "server_epoch_ms"
        case precision
        case source
    }
}

// MARK: — API Client protocol

protocol SystemTimeAPIClient: Sendable {
    func fetchServerTime() async throws -> ServerTimeDTO
}

// MARK: — Live implementation

// Public endpoint — no auth token. Uses .reloadIgnoringLocalCacheData so the
// server's Cache-Control: no-store is enforced regardless of URLSession config.
struct LiveSystemTimeAPIClient: SystemTimeAPIClient {
    func fetchServerTime() async throws -> ServerTimeDTO {
        guard let url = URL(string: APIConfig.baseURL + "/api/v1/system/time") else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 5.0

        #if DEBUG
        let activeSession = APIClient._testURLSession ?? URLSession.shared
        #else
        let activeSession = URLSession.shared
        #endif

        let (data, response): (Data, URLResponse) = try await withCheckedThrowingContinuation { continuation in
            activeSession.dataTask(with: request) { data, response, error in
                if let error = error {
                    continuation.resume(throwing: error)
                } else if let data = data, let response = response {
                    continuation.resume(returning: (data, response))
                } else {
                    continuation.resume(throwing: URLError(.unknown))
                }
            }.resume()
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.networkError(URLError(.badServerResponse))
        }
        guard (200...299).contains(http.statusCode) else {
            throw APIError.httpError(statusCode: http.statusCode, detail: nil)
        }
        return try JSONDecoder().decode(ServerTimeDTO.self, from: data)
    }
}

// MARK: — Result types

struct ClockSyncResult: Sendable, Equatable {
    let estimatedServerAtSampleMs: Double
    let clockOffsetMs: Double
    let rttMs: Double
    let sampledAtMono: Double
}

// MARK: — Errors

enum ClockSyncError: Error, Equatable {
    case noSamplesSucceeded
}

// MARK: — Service

private let logger = Logger(subsystem: "com.lfa.multicamera", category: "ClockSync")

private struct ClockSample {
    let estimatedServerAtSampleMs: Double
    let clockOffsetMs: Double
    let rttMs: Double
    let sampledAtMono: Double
}

actor ClockSyncService {

    private let apiClient: SystemTimeAPIClient
    private let wallClockMs: @Sendable () -> Double
    private let monotonicClock: @Sendable () -> Double
    private let sampleCount: Int

    private(set) var lastResult: ClockSyncResult?

    init(
        apiClient: SystemTimeAPIClient = LiveSystemTimeAPIClient(),
        wallClockMs: @escaping @Sendable () -> Double = { Date().timeIntervalSince1970 * 1000.0 },
        monotonicClock: @escaping @Sendable () -> Double = { CACurrentMediaTime() },
        sampleCount: Int = 3
    ) {
        self.apiClient     = apiClient
        self.wallClockMs   = wallClockMs
        self.monotonicClock = monotonicClock
        self.sampleCount   = sampleCount
    }

    // MARK: — Sync

    func sync() async throws -> ClockSyncResult {
        guard sampleCount > 0 else { throw ClockSyncError.noSamplesSucceeded }
        var samples: [ClockSample] = []

        for i in 1...sampleCount {
            do {
                let sample = try await takeSample()
                logger.debug("sample \(i)/\(self.sampleCount): rtt=\(sample.rttMs, format: .fixed(precision: 1))ms offset=\(sample.clockOffsetMs, format: .fixed(precision: 1))ms")
                samples.append(sample)
            } catch {
                logger.warning("sample \(i)/\(self.sampleCount) failed: \(error.localizedDescription)")
            }
        }

        guard let best = samples.min(by: { $0.rttMs < $1.rttMs }) else {
            logger.error("sync failed: all \(self.sampleCount) samples errored (lastResult preserved)")
            throw ClockSyncError.noSamplesSucceeded
        }

        let bestIdx = (samples.firstIndex(where: { $0.sampledAtMono == best.sampledAtMono }) ?? 0) + 1
        logger.debug("selected: min-RTT sample \(bestIdx) (\(best.rttMs, format: .fixed(precision: 1))ms), estimatedServer=\(best.estimatedServerAtSampleMs, format: .fixed(precision: 0))ms")

        let result = ClockSyncResult(
            estimatedServerAtSampleMs: best.estimatedServerAtSampleMs,
            clockOffsetMs: best.clockOffsetMs,
            rttMs: best.rttMs,
            sampledAtMono: best.sampledAtMono
        )
        lastResult = result
        return result
    }

    // MARK: — Adjusted server time

    // Returns estimated current server time in epoch ms, extrapolated from the
    // last successful sync using monotonic elapsed time.
    // nil if no successful sync has occurred.
    var adjustedServerTimeMs: Double? {
        guard let r = lastResult else { return nil }
        let elapsed = (monotonicClock() - r.sampledAtMono) * 1000.0
        return r.estimatedServerAtSampleMs + elapsed
    }

    // MARK: — Private

    private func takeSample() async throws -> ClockSample {
        let t0Mono   = monotonicClock()

        let dto = try await apiClient.fetchServerTime()

        let t1Mono   = monotonicClock()
        let t1WallMs = wallClockMs()

        let rttMs                    = (t1Mono - t0Mono) * 1000.0
        let estimatedServerAtSampleMs = Double(dto.serverEpochMs) + rttMs / 2.0
        let clockOffsetMs            = estimatedServerAtSampleMs - t1WallMs

        return ClockSample(
            estimatedServerAtSampleMs: estimatedServerAtSampleMs,
            clockOffsetMs: clockOffsetMs,
            rttMs: rttMs,
            sampledAtMono: t1Mono
        )
    }
}
