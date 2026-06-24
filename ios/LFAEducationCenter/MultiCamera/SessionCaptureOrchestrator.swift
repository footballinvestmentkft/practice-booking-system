import Foundation
import AVFoundation
import os.log

private let orchLog = Logger(subsystem: "com.lovas-zoltan.lfa-education-center", category: "Orchestration")

enum OrchestrationState: Equatable {
    case idle
    case arming
    case armed
    case scheduled(fireAt: Date)
    case starting
    case capturing
    case stopping
    case completed(fileURL: URL)
    case failed(String)

    static func == (lhs: OrchestrationState, rhs: OrchestrationState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.arming, .arming), (.armed, .armed),
             (.starting, .starting), (.capturing, .capturing), (.stopping, .stopping):
            return true
        case (.scheduled(let a), .scheduled(let b)): return a == b
        case (.completed(let a), .completed(let b)): return a == b
        case (.failed(let a), .failed(let b)): return a == b
        default: return false
        }
    }
}

@MainActor
final class SessionCaptureOrchestrator: ObservableObject {

    @Published var orchestrationState: OrchestrationState = .idle
    @Published private(set) var clockQuality: ClockSyncQuality = .degradedMissingServerDate
    @Published private(set) var streamId: Int?
    @Published private(set) var lastDriftMs: Int?

    var captureSessionForPreview: AVCaptureSession? {
        captureManager?.captureSessionForPreview
    }

    private var captureManager: SessionCaptureManager?
    private var scheduledTimer: Cancellable?
    private var sessionUUID: String = ""
    private var deviceId: Int = 0
    private var streamCreateInFlight = false
    private var isTornDown = false
    private let timerProvider: OrchestrationTimerProvider
    private let clock: ScheduledCaptureClockManager

    #if DEBUG
    private(set) var cycleIndex: Int = 0
    private var measuredScheduledStartAt: Date = Date()
    private var measuredLocalFireAt: Date = Date()
    var deviceType: String = "unknown"
    #endif

    private let captureManagerFactory: @MainActor () -> SessionCaptureManager

    init(timerProvider: OrchestrationTimerProvider = SystemOrchestrationTimer(),
         clock: ScheduledCaptureClockManager? = nil,
         captureManagerFactory: @MainActor @escaping () -> SessionCaptureManager = { SessionCaptureManager() }) {
        self.timerProvider = timerProvider
        self.clock = clock ?? ScheduledCaptureClockManager()
        self.captureManagerFactory = captureManagerFactory
    }

    // MARK: — Clock update (forwarded from polling)

    func updateClock(requestDuration: TimeInterval, serverDateHeader: Date?) {
        clock.updateFromPolling(requestDuration: requestDuration, serverDateHeader: serverDateHeader)
    }

    // MARK: — Arm

    func armCapture(sessionUUID: String, deviceId: Int) async {
        guard orchestrationState == .idle, !isTornDown else {
            orchLog.warning("armCapture skipped — state=\(String(describing: self.orchestrationState)) isTornDown=\(self.isTornDown)")
            return
        }
        self.sessionUUID = sessionUUID
        self.deviceId = deviceId
        orchestrationState = .arming
        orchLog.info("armCapture: started sessionUUID=\(sessionUUID) deviceId=\(deviceId)")

        let mgr = captureManagerFactory()
        self.captureManager = mgr
        await mgr.requestPermissions()
        guard mgr.state == .configuring else {
            orchestrationState = .failed("Permission denied")
            return
        }
        mgr.prepare(sessionUUID: sessionUUID, deviceId: deviceId)

        for _ in 0..<30 {
            try? await Task.sleep(nanoseconds: 500_000_000)
            if mgr.state == .ready { break }
            if case .failed = mgr.state { break }
        }

        if mgr.state == .ready {
            orchestrationState = .armed
            orchLog.info("armCapture: ARMED ✓ sessionUUID=\(sessionUUID)")
        } else {
            orchestrationState = .failed("Capture prepare failed: \(mgr.state)")
            orchLog.error("armCapture: FAILED — captureManager.state=\(String(describing: mgr.state))")
        }
    }

    // MARK: — Schedule

    func scheduleStart(serverScheduledAt: Date) {
        if case .scheduled = orchestrationState { return }
        if case .capturing = orchestrationState { return }
        if case .stopping = orchestrationState { return }
        if case .completed = orchestrationState { return }
        guard orchestrationState == .armed, !isTornDown else {
            orchLog.error("scheduleStart rejected — state=\(String(describing: self.orchestrationState)) (must be .armed)")
            if orchestrationState != .armed {
                orchestrationState = .failed("Nem armed állapotban érkezett schedule")
            }
            return
        }
        let localFire = clock.localFireDate(for: serverScheduledAt)
        let delay = localFire.timeIntervalSinceNow
        orchLog.info("scheduleStart: serverAt=\(serverScheduledAt) localFire=\(localFire) delayMs=\(Int(delay*1000))")
        if delay < -2.0 {
            orchLog.error("scheduleStart: expired by \(Int(-delay))s — failing")
            orchestrationState = .failed("Schedule lejárt (\(Int(-delay))s késés)")
            return
        }
        orchestrationState = .scheduled(fireAt: localFire)
        #if DEBUG
        measuredScheduledStartAt = serverScheduledAt
        measuredLocalFireAt = localFire
        #endif
        clockQuality = clock.currentOffset.quality
        scheduledTimer = timerProvider.scheduleTimer(fireAt: localFire) { [weak self] in
            Task { @MainActor in
                self?.fireScheduledCapture()
            }
        }
    }

    func cancelSchedule() {
        scheduledTimer?.cancel()
        scheduledTimer = nil
        if case .scheduled = orchestrationState { orchestrationState = .armed }
    }

    // MARK: — Capture

    private func fireScheduledCapture() {
        orchLog.info("fireScheduledCapture: called — orchState=\(String(describing: self.orchestrationState))")
        guard case .scheduled = orchestrationState, !isTornDown else {
            orchLog.warning("fireScheduledCapture: guard failed — orchState=\(String(describing: self.orchestrationState))")
            return
        }
        guard let mgr = captureManager, mgr.state == .ready else {
            orchLog.error("fireScheduledCapture: captureManager not ready — mgrState=\(String(describing: self.captureManager?.state))")
            orchestrationState = .failed("Capture manager not ready at fire time")
            return
        }
        orchLog.info("fireScheduledCapture: FIRING — starting capture")
        orchestrationState = .starting
        // Snapshot interface orientation at fire time; held fixed for the full recording.
        let captureOrientation = CaptureOrientationHelper.currentAVCaptureOrientation()
        #if DEBUG
        cycleIndex += 1
        let driftCtx = DriftMeasurementContext(
            sessionUUID: sessionUUID,
            cycleIndex: cycleIndex,
            deviceId: deviceId,
            deviceType: deviceType,
            scheduledStartAt: measuredScheduledStartAt,
            localFireAt: measuredLocalFireAt,
            serverOffsetEstimateSeconds: clock.currentOffset.offsetSeconds,
            clockQuality: clock.currentOffset.quality.rawValue,
            captureOrientation: captureOrientation.name
        )
        mgr.startCapture(captureOrientation: captureOrientation, driftContext: driftCtx)
        #else
        mgr.startCapture(captureOrientation: captureOrientation)
        #endif

        Task {
            for _ in 0..<20 {
                try? await Task.sleep(nanoseconds: 100_000_000)
                if mgr.state == .capturing {
                    orchestrationState = .capturing
                    lastDriftMs = nil
                    return
                }
                if case .failed = mgr.state {
                    orchestrationState = .failed("Capture start failed")
                    return
                }
            }
            if orchestrationState == .starting {
                orchestrationState = .failed("Capture start timeout")
            }
        }
    }

    func stopCapture() {
        guard orchestrationState == .capturing || orchestrationState == .starting, !isTornDown else { return }
        orchestrationState = .stopping
        captureManager?.stopCapture()

        Task {
            for _ in 0..<100 {
                try? await Task.sleep(nanoseconds: 100_000_000)
                if let mgr = captureManager {
                    if case .completed(let url) = mgr.state {
                        orchestrationState = .completed(fileURL: url)
                        return
                    }
                    if case .failed = mgr.state {
                        orchestrationState = .failed("Capture stop failed")
                        return
                    }
                }
            }
            if orchestrationState == .stopping {
                orchestrationState = .failed("Capture stop timeout")
            }
        }
    }

    // MARK: — Stream create (deduplicated)

    func ensureStreamCreated(token: String, uuid: String, sdId: Int, preset: [String: AnyCodable]) async {
        guard streamId == nil, !streamCreateInFlight else { return }
        streamCreateInFlight = true
        do {
            let stream = try await MultiCameraAPIClient.createCaptureStream(
                token: token, uuid: uuid, sessionDeviceId: sdId,
                streamType: .video, presetJson: preset
            )
            streamId = stream.id
        } catch { }
        streamCreateInFlight = false
    }

    // MARK: — Teardown

    func teardown() {
        guard !isTornDown else { return }
        isTornDown = true
        scheduledTimer?.cancel()
        captureManager?.teardown()
        captureManager = nil
    }

    // MARK: — Retry

    func resetForRetry() {
        scheduledTimer?.cancel()
        captureManager?.teardown()
        captureManager = nil
        streamId = nil
        streamCreateInFlight = false
        orchestrationState = .idle
    }
}
