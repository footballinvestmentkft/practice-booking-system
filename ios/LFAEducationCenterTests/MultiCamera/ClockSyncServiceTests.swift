import XCTest
@testable import LFAEducationCenter

// MARK: — FakeClock (simple mutable wall+mono pair)

final class FakeClock: @unchecked Sendable {
    var wallMs: Double
    var mono: Double

    init(wallMs: Double = 1_700_000_000_000.0, mono: Double = 100.0) {
        self.wallMs = wallMs
        self.mono   = mono
    }

    var asWallClock: @Sendable () -> Double { { [self] in self.wallMs } }
    var asMonotonic: @Sendable () -> Double { { [self] in self.mono } }
}

// MARK: — SequenceClock (steps through pre-defined value lists)

// Each call to wallMs() or mono() advances its own index.
// @unchecked Sendable because the closures capture self by reference;
// tests are single-threaded so this is safe.
final class SequenceClock: @unchecked Sendable {
    private let wallSequence: [Double]
    private let monoSequence: [Double]
    private var wallIdx = 0
    private var monoIdx = 0

    init(wallMs: [Double], mono: [Double]) {
        wallSequence = wallMs
        monoSequence = mono
    }

    var asWallClock: @Sendable () -> Double {
        { [self] in
            let v = self.wallSequence[min(self.wallIdx, self.wallSequence.count - 1)]
            self.wallIdx += 1
            return v
        }
    }

    var asMonotonic: @Sendable () -> Double {
        { [self] in
            let v = self.monoSequence[min(self.monoIdx, self.monoSequence.count - 1)]
            self.monoIdx += 1
            return v
        }
    }
}

// MARK: — FakeSystemTimeAPIClient

final class FakeSystemTimeAPIClient: SystemTimeAPIClient, @unchecked Sendable {
    private let responses: [Result<ServerTimeDTO, Error>]
    private(set) var callCount = 0

    init(responses: [Result<ServerTimeDTO, Error>]) {
        self.responses = responses
    }

    func fetchServerTime() async throws -> ServerTimeDTO {
        let idx = callCount
        callCount += 1
        let r = idx < responses.count ? responses[idx] : responses.last!
        switch r {
        case .success(let dto): return dto
        case .failure(let e):   throw e
        }
    }
}

// MARK: — Helpers

private func makeDTO(epochMs: Int) -> ServerTimeDTO {
    ServerTimeDTO(
        serverTimeUtc: "2023-11-15T10:00:00.000Z",
        serverEpochMs: epochMs,
        precision: "milliseconds",
        source: "backend_app_clock"
    )
}

private enum FakeError: Error { case network }

// MARK: — Tests

final class ClockSyncServiceTests: XCTestCase {

    // CS-01: sync() calls apiClient exactly sampleCount times
    func test_CS_01_callsAPIClientSampleCountTimes() async throws {
        let dto = makeDTO(epochMs: 1_700_000_000_000)
        let api = FakeSystemTimeAPIClient(responses: Array(repeating: .success(dto), count: 3))
        let clock = FakeClock()
        let service = ClockSyncService(
            apiClient: api,
            wallClockMs: clock.asWallClock,
            monotonicClock: clock.asMonotonic,
            sampleCount: 3
        )
        _ = try await service.sync()
        XCTAssertEqual(api.callCount, 3)
    }

    // CS-02: offset formula is mathematically correct
    // Verifies: estimatedServerAtSampleMs = serverEpochMs + rttMs/2
    //           clockOffsetMs = estimatedServerAtSampleMs - t1WallMs
    func test_CS_02_offsetCalculation() {
        let serverEpochMs = 1_000_010
        let rttMs         = 40.0
        let t1WallMs      = 1_000_040.0

        let estimated     = Double(serverEpochMs) + rttMs / 2.0
        let offset        = estimated - t1WallMs

        XCTAssertEqual(estimated, 1_000_030.0, accuracy: 0.1)
        XCTAssertEqual(offset,    -10.0,        accuracy: 0.1)
    }

    // CS-02b: end-to-end offset via service with controlled clocks
    func test_CS_02b_offsetViaService() async throws {
        let serverEpochMs = 1_700_000_000_050
        // takeSample() makes ONE wallClockMs() call: t1WallMs (after response).
        // rttMs = (100.100 - 100.0) * 1000 = 100ms
        // estimatedServerAtSampleMs = 1_700_000_000_050 + 50 = 1_700_000_000_100
        // t1WallMs = 1_700_000_000_100 → clockOffsetMs = 0
        let seqClock = SequenceClock(
            wallMs: [1_700_000_000_100.0],
            mono:   [100.0, 100.100, 100.200]
        )
        let api = FakeSystemTimeAPIClient(responses: [.success(makeDTO(epochMs: serverEpochMs))])
        let service = ClockSyncService(
            apiClient: api,
            wallClockMs: seqClock.asWallClock,
            monotonicClock: seqClock.asMonotonic,
            sampleCount: 1
        )
        let result = try await service.sync()
        XCTAssertEqual(result.rttMs, 100.0, accuracy: 0.1)
        XCTAssertEqual(result.estimatedServerAtSampleMs, Double(serverEpochMs) + 50.0, accuracy: 0.1)
        XCTAssertEqual(result.clockOffsetMs, 0.0, accuracy: 0.1)
    }

    // CS-03: minimum-RTT sample is selected
    func test_CS_03_minRTTSampleSelected() async throws {
        // RTTs: 80ms, 30ms (best), 60ms
        // takeSample() makes ONE wallClockMs() call per sample (t1WallMs).
        //   sample 1: t0Mono=100.0, t1Mono=100.080 → rtt=80ms; t1WallMs=80.0
        //             serverEpochMs=1_000_000_040, estimated=1_000_000_080
        //   sample 2: t0Mono=100.080, t1Mono=100.110 → rtt=30ms; t1WallMs=110.0
        //             serverEpochMs=1_000_000_095, estimated=1_000_000_110 ← min RTT
        //   sample 3: t0Mono=100.110, t1Mono=100.170 → rtt=60ms; t1WallMs=170.0
        //             serverEpochMs=1_000_000_140, estimated=1_000_000_170
        let seqClock = SequenceClock(
            wallMs: [80.0, 110.0, 170.0],
            mono:   [100.0, 100.080, 100.080, 100.110, 100.110, 100.170, 200.0]
        )
        let dtos = [
            makeDTO(epochMs: 1_000_000_040),
            makeDTO(epochMs: 1_000_000_095),
            makeDTO(epochMs: 1_000_000_140),
        ]
        let api = FakeSystemTimeAPIClient(responses: dtos.map { .success($0) })
        let service = ClockSyncService(
            apiClient: api,
            wallClockMs: seqClock.asWallClock,
            monotonicClock: seqClock.asMonotonic,
            sampleCount: 3
        )
        let result = try await service.sync()
        XCTAssertEqual(result.rttMs, 30.0, accuracy: 0.1, "Expected min-RTT sample (30ms) selected")
        XCTAssertEqual(result.estimatedServerAtSampleMs, 1_000_000_110.0, accuracy: 0.1)
    }

    // CS-04: adjustedServerTimeMs advances with elapsed monotonic time
    func test_CS_04_adjustedServerTimeMsAdvancesWithMonotonic() async throws {
        // takeSample() makes ONE wallClockMs() call (t1WallMs after response).
        // rttMs = (100.040 - 100.0) * 1000 = 40ms
        // estimatedServerAtSampleMs = 1_700_000_000_020 + 20 = 1_700_000_000_040
        // sampledAtMono = 100.040
        // 1 second later: mono = 101.040 → elapsed = 1000ms
        // adjustedServerTimeMs = 1_700_000_000_040 + 1000 = 1_700_000_001_040
        let seqClock = SequenceClock(
            wallMs: [1_700_000_000_040.0],      // t1WallMs only (1 wall call per sample)
            mono:   [100.0, 100.040, 101.040]   // t0Mono, t1Mono, adjustedServerTimeMs call
        )
        let api = FakeSystemTimeAPIClient(responses: [.success(makeDTO(epochMs: 1_700_000_000_020))])
        let service = ClockSyncService(
            apiClient: api,
            wallClockMs: seqClock.asWallClock,
            monotonicClock: seqClock.asMonotonic,
            sampleCount: 1
        )
        let result = try await service.sync()
        let adjusted = await service.adjustedServerTimeMs
        XCTAssertNotNil(adjusted)
        XCTAssertEqual(adjusted!, result.estimatedServerAtSampleMs + 1000.0, accuracy: 0.1)
    }

    // CS-05: adjustedServerTimeMs is nil before first sync
    func test_CS_05_adjustedNilBeforeSync() async {
        let api = FakeSystemTimeAPIClient(responses: [])
        let service = ClockSyncService(apiClient: api)
        let adjusted = await service.adjustedServerTimeMs
        XCTAssertNil(adjusted)
    }

    // CS-06: all samples fail → throws noSamplesSucceeded
    func test_CS_06_allSamplesFailThrows() async {
        let api = FakeSystemTimeAPIClient(responses: Array(repeating: .failure(FakeError.network), count: 3))
        let service = ClockSyncService(apiClient: api, sampleCount: 3)
        do {
            _ = try await service.sync()
            XCTFail("Expected ClockSyncError.noSamplesSucceeded")
        } catch ClockSyncError.noSamplesSucceeded {
            // expected
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    // CS-07: 2/3 samples fail, 1 succeeds → returns that sample's result
    func test_CS_07_partialFailureSucceeds() async throws {
        // Failed samples: only t0Mono called (fetch throws before t1Mono/t1WallMs).
        // sample 1 (fail): mono[0]=100.0
        // sample 2 (fail): mono[1]=100.0
        // sample 3 (ok):   mono[2]=100.0 (t0Mono), mono[3]=100.050 (t1Mono), wall[0]=50.0 (t1WallMs)
        // rttMs = (100.050 - 100.0) * 1000 = 50ms
        let seqClock = SequenceClock(
            wallMs: [50.0],
            mono:   [100.0, 100.0, 100.0, 100.050]
        )
        let api = FakeSystemTimeAPIClient(responses: [
            .failure(FakeError.network),
            .failure(FakeError.network),
            .success(makeDTO(epochMs: 1_000_000_025)),
        ])
        let service = ClockSyncService(
            apiClient: api,
            wallClockMs: seqClock.asWallClock,
            monotonicClock: seqClock.asMonotonic,
            sampleCount: 3
        )
        let result = try await service.sync()
        XCTAssertEqual(api.callCount, 3)
        XCTAssertEqual(result.rttMs, 50.0, accuracy: 0.1)
    }

    // CS-08: lastResult persists after successful sync
    func test_CS_08_lastResultPersistsAfterSync() async throws {
        let api = FakeSystemTimeAPIClient(responses: [.success(makeDTO(epochMs: 1_700_000_000_000))])
        let service = ClockSyncService(apiClient: api, sampleCount: 1)
        _ = try await service.sync()
        let last = await service.lastResult
        XCTAssertNotNil(last)
    }

    // CS-09: failed re-sync does not clear lastResult
    func test_CS_09_failedResyncPreservesLastResult() async throws {
        let api = FakeSystemTimeAPIClient(responses: [
            .success(makeDTO(epochMs: 1_700_000_000_000)),
            .failure(FakeError.network),
        ])
        let service = ClockSyncService(apiClient: api, sampleCount: 1)
        _ = try await service.sync()
        let firstResult = await service.lastResult

        do { _ = try await service.sync() } catch { }

        let secondResult = await service.lastResult
        XCTAssertNotNil(firstResult)
        XCTAssertEqual(
            firstResult?.estimatedServerAtSampleMs,
            secondResult?.estimatedServerAtSampleMs,
            "lastResult must not change after failed re-sync"
        )
    }

    // CS-10: sampleCount=1 makes exactly one API call
    func test_CS_10_sampleCount1() async throws {
        let api = FakeSystemTimeAPIClient(responses: [.success(makeDTO(epochMs: 1_700_000_000_000))])
        let service = ClockSyncService(apiClient: api, sampleCount: 1)
        _ = try await service.sync()
        XCTAssertEqual(api.callCount, 1)
    }

    // CS-11: wall clock jump after sync does not affect monotonic-based adjustedServerTimeMs
    func test_CS_11_wallClockJumpDoesNotAffectAdjusted() async throws {
        // rttMs = (100.040 - 100.0)*1000 = 40ms
        // estimated = 1_700_000_000_020 + 20 = 1_700_000_000_040, sampledAtMono=100.040
        // 500ms later (mono=100.540): adjusted = 1_700_000_000_040 + 500 = 1_700_000_000_540
        // Wall clock sequence: t1WallMs=1_700_000_000_040 (sync), then +10s jump (adjustedServerTimeMs call)
        // The adjustedServerTimeMs must NOT reflect the +10s jump.
        let seqClock = SequenceClock(
            wallMs: [1_700_000_000_040.0, 1_700_000_010_040.0],
            mono:   [100.0, 100.040, 100.540]
        )
        let api = FakeSystemTimeAPIClient(responses: [.success(makeDTO(epochMs: 1_700_000_000_020))])
        let service = ClockSyncService(
            apiClient: api,
            wallClockMs: seqClock.asWallClock,
            monotonicClock: seqClock.asMonotonic,
            sampleCount: 1
        )
        let result = try await service.sync()
        // adjustedServerTimeMs reads mono (100.540) and computes elapsed from sampledAtMono (100.040)
        let adjusted = await service.adjustedServerTimeMs
        XCTAssertNotNil(adjusted)
        let expectedAdjusted = result.estimatedServerAtSampleMs + 500.0
        XCTAssertEqual(adjusted!, expectedAdjusted, accuracy: 0.1,
                       "Wall clock jump must not affect monotonic-based server time estimate")
        XCTAssertNotEqual(adjusted!, 1_700_000_010_040.0 + 500.0,
                          "Must not reflect the wall clock jump (+10s)")
    }

    // CS-13: sampleCount=0 → ClockSyncError.noSamplesSucceeded, no crash
    func test_CS_13_sampleCountZeroThrows() async {
        let api = FakeSystemTimeAPIClient(responses: [])
        let service = ClockSyncService(apiClient: api, sampleCount: 0)
        do {
            _ = try await service.sync()
            XCTFail("Expected ClockSyncError.noSamplesSucceeded")
        } catch ClockSyncError.noSamplesSucceeded {
            // expected — guard fires before loop, no crash
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
        XCTAssertEqual(api.callCount, 0, "No API calls should be made when sampleCount=0")
    }

    // CS-12: rttMs=0 (identical mono values) — no NaN, no crash
    func test_CS_12_zeroRTTEdgeCase() async throws {
        let serverEpochMs = 1_700_000_000_000
        let seqClock = SequenceClock(
            wallMs: [Double(serverEpochMs), Double(serverEpochMs)],
            mono:   [100.0, 100.0, 100.0]
        )
        let api = FakeSystemTimeAPIClient(responses: [.success(makeDTO(epochMs: serverEpochMs))])
        let service = ClockSyncService(
            apiClient: api,
            wallClockMs: seqClock.asWallClock,
            monotonicClock: seqClock.asMonotonic,
            sampleCount: 1
        )
        let result = try await service.sync()
        XCTAssertEqual(result.rttMs, 0.0, accuracy: 0.001)
        XCTAssertEqual(result.estimatedServerAtSampleMs, Double(serverEpochMs), accuracy: 0.1)
        XCTAssertEqual(result.clockOffsetMs, 0.0, accuracy: 0.1)
        XCTAssertFalse(result.rttMs.isNaN)
        XCTAssertFalse(result.clockOffsetMs.isNaN)
    }
}
