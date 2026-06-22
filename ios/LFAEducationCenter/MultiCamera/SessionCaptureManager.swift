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

    func prepare(sessionUUID: String, deviceId: Int) {
        guard state == .configuring, !isTornDown else { return }
        self.sessionUUID = sessionUUID
        self.deviceId = deviceId

        captureQueue.async { [weak self] in
            guard let self else { return }
            self.captureSession.beginConfiguration()
            self.captureSession.sessionPreset = .high

            guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
                  let videoInput = try? AVCaptureDeviceInput(device: camera),
                  self.captureSession.canAddInput(videoInput) else {
                DispatchQueue.main.async { self.state = .failed("Kamera nem elérhető") }
                self.captureSession.commitConfiguration()
                return
            }
            self.captureSession.addInput(videoInput)

            guard let mic = AVCaptureDevice.default(for: .audio),
                  let audioInput = try? AVCaptureDeviceInput(device: mic),
                  self.captureSession.canAddInput(audioInput) else {
                DispatchQueue.main.async { self.state = .failed("Audio input nem elérhető") }
                self.captureSession.commitConfiguration()
                return
            }
            self.captureSession.addInput(audioInput)

            guard self.captureSession.canAddOutput(self.movieOutput) else {
                DispatchQueue.main.async { self.state = .failed("Movie output nem elérhető") }
                self.captureSession.commitConfiguration()
                return
            }
            self.captureSession.addOutput(self.movieOutput)

            if let conn = self.movieOutput.connection(with: .video), conn.isVideoOrientationSupported {
                conn.videoOrientation = .portrait
            }

            self.captureSession.commitConfiguration()
            self.captureSession.startRunning()

            DispatchQueue.main.async {
                self.registerObservers()
                self.state = .ready
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
            state = .capturing
        }
    }

    nonisolated func fileOutput(_ output: AVCaptureFileOutput, didFinishRecordingTo outputFileURL: URL,
                                from connections: [AVCaptureConnection], error: Error?) {
        Task { @MainActor in
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
