import AVFoundation
import UIKit

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

    private let permissionProvider: PermissionProvider
    private let fileStore: CaptureFileStore
    private let captureSession = AVCaptureSession()
    private let movieOutput = AVCaptureMovieFileOutput()
    private let captureQueue = DispatchQueue(label: "com.lfa.multicamera.capture", qos: .userInitiated)
    private var observers: [NSObjectProtocol] = []
    private var sessionUUID: String = ""
    private var deviceId: Int = 0
    private var isTornDown = false

    var isCapturing: Bool { state == .capturing }

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
            let log = { (msg: String) in print("[SessionCapture] \(msg)") }
            log("prepare: captureQueue entered (thread: \(Thread.current))")
            assert(!Thread.isMainThread, "Capture configure must not run on main thread")

            log("prepare: beginConfiguration")
            self.captureSession.beginConfiguration()

            log("prepare: sessionPreset = .high")
            self.captureSession.sessionPreset = .high

            log("prepare: discovering rear camera...")
            let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
            log("prepare: rear camera = \(camera?.localizedName ?? "nil")")
            guard let camera else {
                self.captureSession.commitConfiguration()
                DispatchQueue.main.async { if !self.isTornDown { self.state = .failed("Rear kamera nem található") } }
                return
            }

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

            if let conn = self.movieOutput.connection(with: .video), conn.isVideoOrientationSupported {
                conn.videoOrientation = .portrait
                log("prepare: orientation set to portrait")
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
                print("[SessionCapture] prepare: TIMEOUT after \(Self.prepareTimeoutSeconds)s")
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
    }

    private func handleInterruption() {
        guard state == .capturing else { return }
        state = .interrupted
    }

    private func handleInterruptionEnded() {
        if state == .interrupted { stopCapture() }
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
