import AVFoundation
import Combine
import UIKit

@MainActor
protocol CapturePreparable: AnyObject {
    func autoPrepare(sessionUUID: String, deviceId: Int) async
}

enum CaptureState: Equatable {
    case idle
    case requestingPermissions
    case configuring
    case ready
    case capturing
    case stopping
    case interrupted
    case completed(fileURL: URL)
    case failed(String)
    case tornDown
}

@MainActor
final class SessionCaptureManager: NSObject, ObservableObject {

    @Published private(set) var state: CaptureState = .idle
    @Published private(set) var outputFileURL: URL?
    @Published private(set) var lastValidation: CaptureFileValidation?
    /// Resolved at `prepare()` time by `CaptureFormatSelector` — .hd720 unless
    /// the device's camera can't satisfy 1280x720@30fps, in which case .sd360.
    /// nil until prepare() has run once.
    @Published private(set) var activeCaptureProfile: CaptureProfile?
    /// Device interface orientation ("portrait"/"landscape"/"unknown") captured live, on
    /// MainActor, at the moment startCapture() commits to recording — i.e. the ground-truth
    /// orientation the connection's videoOrientation should have been set to. Compared against
    /// the FILE's actual baked-in orientation (from the AVAsset preferredTransform, read back
    /// in CaptureMetadataDiagWriter) to catch a stale/hardcoded orientation regression
    /// (2026-07-01 flow audit — closes the loop the `.portrait` hardcode bug left open).
    @Published private(set) var orientationAtRecordingStart: String?

    private let permissionProvider: PermissionProvider
    private let fileStore: CaptureFileStore
    private let captureSession = AVCaptureSession()
    private let movieOutput = AVCaptureMovieFileOutput()
    private let captureQueue = DispatchQueue(label: "com.lfa.multicamera.capture", qos: .userInitiated)
    private var observers: [NSObjectProtocol] = []
    private var sessionUUID: String = ""
    private var deviceId: Int = 0
    private var isTornDown = false
    /// Whether the interruption began while a recording was in flight — decides
    /// whether interruption-end resumes to `.ready` or finalizes via stopCapture().
    private var wasCapturingWhenInterrupted = false
    /// Preview side-tap output currently attached to the shared session (see
    /// attachPreviewOutput). At most one at a time.
    private var attachedPreviewOutput: AVCaptureVideoDataOutput?

    var isCapturing: Bool { state == .capturing }
    var previewSession: AVCaptureSession { captureSession }

    var capturedFileDuration: TimeInterval? {
        guard case .completed = state, let url = outputFileURL else { return nil }
        return AVURLAsset(url: url).duration.seconds
    }

    init(
        permissionProvider: PermissionProvider = SystemPermissionProvider(),
        fileStore: CaptureFileStore = SystemCaptureFileStore()
    ) {
        self.permissionProvider = permissionProvider
        self.fileStore = fileStore
        super.init()
    }

    deinit {
        observers.forEach { NotificationCenter.default.removeObserver($0) }
    }

    // MARK: — Permissions (async, cancellation-safe)

    func requestPermissions() async {
        guard state == .idle, !isTornDown else { return }
        state = .requestingPermissions

        let camStatus = permissionProvider.cameraAuthorizationStatus()
        if camStatus == .denied || camStatus == .restricted {
            state = .failed("Kamera engedély szükséges — Beállítások → Adatvédelem → Kamera")
            return
        }
        if camStatus == .notDetermined {
            let granted = await permissionProvider.requestCameraAccess()
            guard !Task.isCancelled else { state = .idle; return }
            guard granted else { state = .failed("Kamera engedély szükséges"); return }
        }

        let micStatus = permissionProvider.microphoneAuthorizationStatus()
        if micStatus == .denied || micStatus == .restricted {
            state = .failed("Mikrofon engedély szükséges — Beállítások → Adatvédelem → Mikrofon")
            return
        }
        if micStatus == .notDetermined {
            let granted = await permissionProvider.requestMicrophoneAccess()
            guard !Task.isCancelled else { state = .idle; return }
            guard granted else { state = .failed("Mikrofon engedély szükséges"); return }
        }

        guard !Task.isCancelled else { state = .idle; return }
        state = .configuring
    }

    // MARK: — Configure

    private static let prepareTimeoutSeconds: Double = 15

    func prepare(sessionUUID: String, deviceId: Int) {
        guard state == .configuring, !isTornDown else { return }
        self.sessionUUID = sessionUUID
        self.deviceId = deviceId

        let prepareStarted = Date()
        var didComplete = false

        captureQueue.async { [weak self] in
            guard let self else { return }
            let log = { (msg: String) in MC1Log.notice("[SessionCapture] \(msg)") }
            log("prepare: captureQueue entered (thread: \(Thread.current))")
            assert(!Thread.isMainThread, "Capture configure must not run on main thread")

            log("prepare: beginConfiguration")
            self.captureSession.beginConfiguration()

            log("prepare: discovering rear camera...")
            let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
            log("prepare: rear camera = \(camera?.localizedName ?? "nil")")
            guard let camera else {
                self.captureSession.commitConfiguration()
                DispatchQueue.main.async { if !self.isTornDown { self.state = .failed("Rear kamera nem található") } }
                return
            }

            // Capture quality policy (docs/MEDIA_PIPELINE_PLAN.md): explicit
            // 1280x720@30fps primary, 640x360@30fps fallback — NOT `.high`
            // (device-default, unspecified resolution/fps).
            var resolvedProfile: CaptureProfile?
            if let selection = CaptureFormatSelector.selectRealFormat(for: camera) {
                do {
                    // .inputPriority tells AVCaptureSession to respect our explicit
                    // activeFormat instead of silently overriding it per sessionPreset.
                    self.captureSession.sessionPreset = .inputPriority
                    try camera.lockForConfiguration()
                    camera.activeFormat = selection.format
                    camera.activeVideoMinFrameDuration = CMTime(value: 1, timescale: 30)
                    camera.activeVideoMaxFrameDuration = CMTime(value: 1, timescale: 30)
                    camera.unlockForConfiguration()
                    resolvedProfile = selection.profile
                    log("prepare: capture profile = \(selection.profile.label)@30fps")
                } catch {
                    log("prepare: lockForConfiguration failed: \(error) — falling back to .high preset")
                    self.captureSession.sessionPreset = .high
                }
            } else {
                log("prepare: no 720p or 360p format available — falling back to .high preset")
                self.captureSession.sessionPreset = .high
            }
            DispatchQueue.main.async { self.activeCaptureProfile = resolvedProfile }

            var videoInput: AVCaptureDeviceInput?
            do {
                videoInput = try AVCaptureDeviceInput(device: camera)
                log("prepare: videoInput created")
            } catch {
                log("prepare: videoInput error: \(error)")
                self.captureSession.commitConfiguration()
                DispatchQueue.main.async { if !self.isTornDown { self.state = .failed("Video input hiba: \(error.localizedDescription)") } }
                return
            }

            guard let videoInput, self.captureSession.canAddInput(videoInput) else {
                log("prepare: canAddInput(video) = false")
                self.captureSession.commitConfiguration()
                DispatchQueue.main.async { if !self.isTornDown { self.state = .failed("Video input nem adható hozzá") } }
                return
            }
            self.captureSession.addInput(videoInput)
            log("prepare: video input added")

            log("prepare: discovering microphone...")
            let mic = AVCaptureDevice.default(for: .audio)
            log("prepare: mic = \(mic?.localizedName ?? "nil")")
            guard let mic else {
                self.captureSession.commitConfiguration()
                DispatchQueue.main.async { if !self.isTornDown { self.state = .failed("Mikrofon nem található") } }
                return
            }

            var audioInput: AVCaptureDeviceInput?
            do {
                audioInput = try AVCaptureDeviceInput(device: mic)
                log("prepare: audioInput created")
            } catch {
                log("prepare: audioInput error: \(error)")
                self.captureSession.commitConfiguration()
                DispatchQueue.main.async { if !self.isTornDown { self.state = .failed("Audio input hiba: \(error.localizedDescription)") } }
                return
            }

            guard let audioInput, self.captureSession.canAddInput(audioInput) else {
                log("prepare: canAddInput(audio) = false")
                self.captureSession.commitConfiguration()
                DispatchQueue.main.async { if !self.isTornDown { self.state = .failed("Audio input nem adható hozzá") } }
                return
            }
            self.captureSession.addInput(audioInput)
            log("prepare: audio input added")

            let canAddMovie = self.captureSession.canAddOutput(self.movieOutput)
            log("prepare: canAddOutput(movie) = \(canAddMovie)")
            guard canAddMovie else {
                self.captureSession.commitConfiguration()
                DispatchQueue.main.async { if !self.isTornDown { self.state = .failed("Movie output nem adható hozzá") } }
                return
            }
            self.captureSession.addOutput(self.movieOutput)
            log("prepare: movie output added")

            if let conn = self.movieOutput.connection(with: .video) {
                // Explicit H.264 — broader cross-device/decoder compatibility than
                // letting AVFoundation pick HEVC by device default; predictable for
                // the eventual upload pipeline (docs/MEDIA_PIPELINE_PLAN.md).
                self.movieOutput.setOutputSettings([AVVideoCodecKey: AVVideoCodecType.h264], for: conn)
                OrientationMapper.applyCurrentOrientation(to: conn)
                log("prepare: codec=h264, orientation=\(OrientationMapper.currentOrientationLabel)")
            }

            log("prepare: commitConfiguration")
            self.captureSession.commitConfiguration()

            log("prepare: startRunning() begin...")
            self.captureSession.startRunning()
            let isRunning = self.captureSession.isRunning
            log("prepare: startRunning() done, isRunning=\(isRunning)")

            didComplete = true
            DispatchQueue.main.async {
                guard !self.isTornDown else { return }
                if isRunning {
                    self.registerObservers()
                    self.state = .ready
                    log("prepare: state → ready")
                } else {
                    self.state = .failed("captureSession.isRunning = false startRunning() után")
                    log("prepare: state → failed (not running)")
                }
            }
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + Self.prepareTimeoutSeconds) { [weak self] in
            guard let self, !didComplete, !self.isTornDown else { return }
            if self.state == .configuring {
                self.state = .failed("Kamera inicializálási timeout (\(Int(Self.prepareTimeoutSeconds))s)")
                MC1Log.notice("[SessionCapture] prepare: TIMEOUT after \(Self.prepareTimeoutSeconds)s")
            }
        }
    }

    // MARK: — Capture

    func startCapture() {
        guard state == .ready, !isTornDown else { return }
        guard fileStore.availableStorageBytes() > 100_000_000 else {
            state = .failed("Nincs elég tárhely (min 100 MB)")
            return
        }
        do { try fileStore.ensureDirectoryExists() } catch {
            state = .failed("Könyvtár hiba: \(error.localizedDescription)")
            return
        }
        // Captured here (MainActor, synchronously) — NOT re-derived later from the file —
        // so it reflects the device's actual orientation at the moment recording commits.
        orientationAtRecordingStart = OrientationMapper.currentOrientationLabel
        let url = fileStore.outputURL(sessionUUID: sessionUUID, deviceId: deviceId)
        captureQueue.async { [weak self] in
            guard let self else { return }
            self.movieOutput.startRecording(to: url, recordingDelegate: self)
        }
    }

    func stopCapture() {
        guard state == .capturing || state == .interrupted, !isTornDown else { return }
        state = .stopping
        captureQueue.async { [weak self] in
            self?.movieOutput.stopRecording()
        }
    }

    // MARK: — Preview side-tap (single-session camera ownership)
    //
    // The recording session is the ONLY AVCaptureSession that may own the
    // camera on a capture device. The 2026-07-04 tricamera physical run failed
    // because CameraFramePublisher ran a SECOND AVCaptureSession on the same
    // back camera: iOS gave the camera to one session, silently interrupted the
    // other, and the player never reached confirmed_start. The preview frame
    // stream now taps THIS session via an added AVCaptureVideoDataOutput —
    // recording stays authoritative, preview is a side branch of the same
    // camera pipeline.

    /// Adds a preview AVCaptureVideoDataOutput to the shared session. The caller
    /// configures the output (settings + delegate) before attaching. Runs on the
    /// capture queue; completion is delivered on the main queue with whether the
    /// output was actually added.
    func attachPreviewOutput(_ output: AVCaptureVideoDataOutput,
                             completion: @escaping @MainActor (Bool) -> Void) {
        guard !isTornDown else {
            Task { @MainActor in completion(false) }
            return
        }
        guard attachedPreviewOutput == nil else {
            let alreadyAttached = attachedPreviewOutput === output
            MC1Log.notice("[SessionCapture] attachPreviewOutput: \(alreadyAttached ? "already attached" : "another preview output is attached") — skipping")
            Task { @MainActor in completion(alreadyAttached) }
            return
        }
        captureQueue.async { [weak self] in
            guard let self else { return }
            self.captureSession.beginConfiguration()
            let canAdd = self.captureSession.canAddOutput(output)
            if canAdd {
                self.captureSession.addOutput(output)
                OrientationMapper.applyCurrentOrientation(to: output.connection(with: .video))
            }
            self.captureSession.commitConfiguration()
            DispatchQueue.main.async {
                if canAdd { self.attachedPreviewOutput = output }
                MC1Log.notice("[SessionCapture] attachPreviewOutput: \(canAdd ? "attached" : "canAddOutput=false")")
                completion(canAdd)
            }
        }
    }

    /// Removes a previously attached preview output from the shared session.
    /// Safe to call when nothing is attached.
    func detachPreviewOutput() {
        guard let output = attachedPreviewOutput else { return }
        attachedPreviewOutput = nil
        captureQueue.async { [weak self] in
            guard let self else { return }
            self.captureSession.beginConfiguration()
            self.captureSession.removeOutput(output)
            self.captureSession.commitConfiguration()
            DispatchQueue.main.async {
                MC1Log.notice("[SessionCapture] detachPreviewOutput: removed")
            }
        }
    }

    // MARK: — Re-arm for next cycle (multi-cycle support)

    func rearmForNextCycle() {
        guard case .completed = state else { return }
        outputFileURL = nil
        lastValidation = nil
        state = .ready
    }

    // MARK: — Reset for reuse (MC1-AUTO scenario isolation)

    func resetForReuse() {
        removeObservers()
        captureQueue.async { [weak self] in
            guard let self else { return }
            if self.movieOutput.isRecording {
                self.movieOutput.stopRecording()
            }
            self.captureSession.stopRunning()
            for input in self.captureSession.inputs {
                self.captureSession.removeInput(input)
            }
            for output in self.captureSession.outputs {
                self.captureSession.removeOutput(output)
            }
        }
        outputFileURL = nil
        lastValidation = nil
        isTornDown = false
        // The loop above removed every output, including an attached preview
        // side-tap — forget it so the next attachPreviewOutput doesn't skip.
        attachedPreviewOutput = nil
        wasCapturingWhenInterrupted = false
        state = .idle
    }

    // MARK: — Teardown

    func teardown() {
        guard !isTornDown else { return }
        isTornDown = true
        removeObservers()
        captureQueue.async { [weak self] in
            self?.captureSession.stopRunning()
        }
        state = .tornDown
    }

    // MARK: — Observers

    private func registerObservers() {
        // A single orientation assignment at prepare() time is exactly the bug
        // this replaces — re-apply on every rotation so portrait↔landscape
        // mid-session (or simply starting in landscape) records correctly.
        UIDevice.current.beginGeneratingDeviceOrientationNotifications()
        observers.append(NotificationCenter.default.addObserver(
            forName: UIDevice.orientationDidChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.updateOrientation() })

        observers.append(NotificationCenter.default.addObserver(
            forName: .AVCaptureSessionWasInterrupted, object: captureSession, queue: .main
        ) { [weak self] _ in self?.handleInterruption() })

        observers.append(NotificationCenter.default.addObserver(
            forName: .AVCaptureSessionInterruptionEnded, object: captureSession, queue: .main
        ) { [weak self] _ in self?.handleInterruptionEnded() })

        observers.append(NotificationCenter.default.addObserver(
            forName: .AVCaptureSessionRuntimeError, object: captureSession, queue: .main
        ) { [weak self] note in
            let err = (note.userInfo?[AVCaptureSessionErrorKey] as? Error)?.localizedDescription ?? "unknown"
            self?.state = .failed("Runtime error: \(err)")
        })

        observers.append(NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main
        ) { [weak self] _ in
            if self?.state == .capturing { self?.stopCapture() }
        })

        observers.append(NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
        ) { [weak self] note in
            let type = (note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt)
                .flatMap(AVAudioSession.InterruptionType.init)
            if type == .began, self?.state == .capturing { self?.stopCapture() }
        })
    }

    private func removeObservers() {
        observers.forEach { NotificationCenter.default.removeObserver($0) }
        observers.removeAll()
        UIDevice.current.endGeneratingDeviceOrientationNotifications()
    }

    private func updateOrientation() {
        guard let conn = movieOutput.connection(with: .video) else { return }
        OrientationMapper.applyCurrentOrientation(to: conn)
    }

    // Internal (not private) — the interruption path is exercised directly by
    // SessionCaptureManagerTests; a real AVCaptureSessionWasInterrupted cannot
    // be triggered on the simulator (same seam as the fileOutput delegate calls).
    func handleInterruption() {
        switch state {
        case .capturing:
            wasCapturingWhenInterrupted = true
            state = .interrupted
        case .ready:
            // Camera lost while armed — e.g. another AVCaptureSession claimed the
            // device. Before this case existed, `.ready` silently survived the
            // interruption and the next startCapture() ran against a session with
            // no camera: movieOutput never reached didStartRecording, the player
            // never confirmed start, and the cycle hung with zero evidence
            // (2026-07-04 tricamera physical run RCA).
            wasCapturingWhenInterrupted = false
            state = .interrupted
            MC1Log.notice("[SessionCapture] INTERRUPTED while ready — camera lost (another session claimed the device?)")
        default:
            break
        }
    }

    func handleInterruptionEnded() {
        guard state == .interrupted else { return }
        if wasCapturingWhenInterrupted {
            stopCapture()
        } else {
            // Interrupted while merely armed — nothing was recording, so there is
            // nothing to finalize; the session is running again, re-arm.
            state = .ready
            MC1Log.notice("[SessionCapture] interruption ended — re-armed to ready")
        }
    }

    #if DEBUG
    /// Test-only: force a state that normally requires physical camera hardware
    /// to reach (`.ready`, `.capturing`). Interruption-path unit tests use this
    /// because prepare() cannot complete on the simulator.
    func forceStateForTesting(_ forced: CaptureState) {
        state = forced
    }
    #endif
}

// MARK: — CaptureController

extension SessionCaptureManager: CaptureController {
    var captureStatePublisher: AnyPublisher<CaptureState, Never> {
        $state.eraseToAnyPublisher()
    }
}

// MARK: — CapturePreparable

extension SessionCaptureManager: CapturePreparable {
    func autoPrepare(sessionUUID: String, deviceId: Int) async {
        guard state == .idle else { return }
        await requestPermissions()
        guard case .configuring = state else { return }
        prepare(sessionUUID: sessionUUID, deviceId: deviceId)
    }
}

// MARK: — AVCaptureFileOutputRecordingDelegate

extension SessionCaptureManager: AVCaptureFileOutputRecordingDelegate {

    nonisolated func fileOutput(_ output: AVCaptureFileOutput, didStartRecordingTo fileURL: URL,
                                from connections: [AVCaptureConnection]) {
        Task { @MainActor in
            guard !isTornDown, state != .tornDown else { return }
            state = .capturing
        }
    }

    nonisolated func fileOutput(_ output: AVCaptureFileOutput, didFinishRecordingTo outputFileURL: URL,
                                from connections: [AVCaptureConnection], error: Error?) {
        Task { @MainActor in
            guard !isTornDown else { return }
            if case .failed = state { return }
            if let error = error {
                state = .failed("Capture error: \(error.localizedDescription)")
                return
            }
            let validation = await fileStore.validateCaptureFile(url: outputFileURL)
            lastValidation = validation
            switch validation {
            case .valid:
                self.outputFileURL = outputFileURL
                state = .completed(fileURL: outputFileURL)
            case .invalid(let reason):
                state = .failed(reason)
            }
        }
    }
}

// MARK: — Capture metadata diagnostics (Capture Quality + Metadata block)
//
// Structured, file-based evidence — console capture (formerly print(), now
// MC1Log/os_log) has repeatedly proven unreliable on physical devices (see
// gopro_diag.json / gopro_stream_diag.json history). capture-info now
// writes the same fields it logs, to Documents/capture_metadata_diag.json,
// pulled by the regression script via the established devicectl
// appDataContainer copy pattern.
enum CaptureMetadataDiagWriter {
    static let fileName = "capture_metadata_diag.json"

    @MainActor
    static func write(from manager: SessionCaptureManager) {
        var diag: [String: Any] = [
            "timestamp": ISO8601DateFormatter().string(from: Date()),
            "state": "\(manager.state)",
            "outputFilePath": manager.outputFileURL?.path ?? NSNull(),
            "requestedProfile": manager.activeCaptureProfile?.label ?? NSNull(),
            "requestedFPS": manager.activeCaptureProfile?.targetFPS ?? NSNull(),
        ]
        if let url = manager.outputFileURL {
            let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
            diag["fileSizeBytes"] = size ?? 0
        }
        if case .valid(let duration, let resolution, let orientation, let hasAudio, _, let fps, let codec) = manager.lastValidation {
            diag["actualDurationSeconds"] = duration
            diag["actualResolution"] = "\(Int(resolution.width))x\(Int(resolution.height))"
            diag["actualOrientation"] = orientation
            diag["actualHasAudio"] = hasAudio
            diag["actualFPS"] = fps
            diag["actualCodec"] = codec

            // Orientation/aspect consistency assertion (2026-07-01 flow audit) — closes the
            // loop the `.portrait` hardcode bug left open: is the orientation ACTUALLY BAKED
            // INTO THE FILE (from the AVAsset preferredTransform) consistent with the DEVICE'S
            // OWN interface orientation, captured live at startCapture() time? A stale/hardcoded
            // orientation would silently diverge from this without ever failing to "record" —
            // the file would still be valid, just rotated wrong.
            let portraitOrientations: Set<String> = ["portrait", "portraitUpsideDown"]
            let landscapeOrientations: Set<String> = ["landscapeLeft", "landscapeRight"]
            let fileOrientationCoarse: String =
                portraitOrientations.contains(orientation) ? "portrait" :
                landscapeOrientations.contains(orientation) ? "landscape" : "unknown"
            let preparedLabel = manager.orientationAtRecordingStart ?? "unknown"
            let orientationConsistent = fileOrientationCoarse != "unknown"
                && preparedLabel != "unknown"
                && fileOrientationCoarse == preparedLabel
            diag["deviceOrientationAtRecordingStart"] = preparedLabel
            diag["fileOrientationCoarse"] = fileOrientationCoarse
            diag["orientationConsistent"] = orientationConsistent

            // Effective (post-rotation) display dimensions + aspect ratio. naturalSize is the
            // sensor's raw pixel dimensions (always landscape-shaped for this app's capture
            // profiles — see CaptureFormatSelector); the preferredTransform rotation is what a
            // player applies before display, so a portrait-oriented file's EFFECTIVE displayed
            // width/height are naturalSize's height/width swapped.
            let isPortraitFile = portraitOrientations.contains(orientation)
            let effectiveWidth  = isPortraitFile ? resolution.height : resolution.width
            let effectiveHeight = isPortraitFile ? resolution.width  : resolution.height
            diag["effectiveDisplayWidth"] = Int(effectiveWidth)
            diag["effectiveDisplayHeight"] = Int(effectiveHeight)
            if effectiveWidth > 0, effectiveHeight > 0 {
                func gcd(_ a: Int, _ b: Int) -> Int { b == 0 ? a : gcd(b, a % b) }
                let w = Int(effectiveWidth), h = Int(effectiveHeight)
                let d = gcd(w, h)
                diag["effectiveAspectRatio"] = d > 0 ? "\(w / d):\(h / d)" : NSNull()
            } else {
                diag["effectiveAspectRatio"] = NSNull()
            }
        } else if case .invalid(let reason) = manager.lastValidation {
            diag["validationError"] = reason
            diag["orientationConsistent"] = false
        }
        // Upload pipeline does not exist yet (docs/MEDIA_PIPELINE_PLAN.md) — explicit
        // placeholder so the field is never silently absent from the diag schema.
        diag["uploadStatus"] = "not_implemented"
        diag["backendMediaId"] = NSNull()

        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
              JSONSerialization.isValidJSONObject(diag),
              let data = try? JSONSerialization.data(withJSONObject: diag, options: [.prettyPrinted]) else { return }
        try? data.write(to: docs.appendingPathComponent(fileName), options: .atomic)
        MC1Log.notice("[CAPTURE-METADATA] wrote \(fileName): \(diag)")
    }
}
