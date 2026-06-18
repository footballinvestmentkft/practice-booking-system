import SwiftUI
import AVFoundation

// MARK: — JugglingAnnotationScreen

// Phase 1 (AN-3B2A) — Event Marking Mode only.
// Tap the FAB to mark a contact event at the current playhead position.
// Swipe left on a row to delete. Tap a timeline pin or row to seek.
// Phase 2 picker / detail / conflict UI: AN-3B2B.
//
// Mixed-orientation layout:
//   videoAreaHeight(in:) computes the video container height from
//   playback.videoNaturalSize + preferredTransform (set by loadAsset):
//     - Landscape (width > height): naturalHeight = containerWidth / aspect
//       → uncapped, typically ~221 pt for 16:9 on a 393 pt screen
//     - Portrait  (height > width): naturalHeight capped at geo.height * 0.50
//       → leaves ≥ 50 % for PlaybackControlBar + EventTimelineView + EventList
//   AVPlayerLayerView uses .resizeAspect in all cases: black pillarbox/letterbox
//   fills any gap between the video and the container boundary.
//
// NOT in scope (AN-3C):
//   Finish flow, result/summary screen, navigation to next video.

struct JugglingAnnotationScreen: View {

    let video:       JugglingVideoItem
    let authManager: AuthManager   // passed explicitly because @StateObject init runs before EnvironmentObject

    @StateObject private var loader:  AnnotationVideoLoader
    @StateObject private var playback: PlaybackController
    @StateObject private var vm:      JugglingAnnotationViewModel

    @State private var didCleanUp  = false   // guards double onDisappear/save calls
    @State private var fabPressed  = false   // brief scale-down on successful mark

    // AN-3B2A P1 — close confirmation (X with active events) and save-error alert.
    @State private var showCloseConfirm   = false
    @State private var showSaveErrorAlert = false

    // AN-3B2A P2 — labeling flow presentation.
    @State private var showLabeling    = false
    // AN-3B2A P2B-3 — local video URL set once loader reaches .ready; passed to
    // EventLabelDetailView so the still-frame generator can open the same file.
    @State private var loaderVideoURL: URL? = nil

    // Phase 2A: pose snapshot — keyed by deviceEventId.
    // Keypoints are captured immediately at FAB tap (correct video frame).
    // Upload is deferred until the event is synced and has a serverEventId.
    private struct CapturedPose {
        let keypoints:           PoseKeypointsDTO
        let capturedAtMs:        Int
        let imageWidthPx:        Int?
        let imageHeightPx:       Int?
        let inferenceConfidence: Double?
    }
    @State private var pendingPoseSnapshots: [UUID: CapturedPose] = [:]
    @State private var poseSnapshots:        [PoseSnapshotOut]    = []
    @State private var showSkeletonOverlay   = false
    // AN-3B2D-2: continuous skeleton extraction (separate ViewModel)
    @StateObject private var denseSkeletonVM: DenseSkeletonViewModel
    // AN-3B2D-3: dense ball trajectory overlay (separate ViewModel)
    @StateObject private var ballTrajectoryVM: BallTrajectoryViewModel
    // AN-3B2C-1: ball detection overlay on main video (event-level granularity, ±500ms window)
    @State private var showBallOverlay        = false
    @State private var isBallSelecting        = false
    @State private var ballSelectionDragPoint: CGPoint? = nil
    // AN-3B2B1: ball feedback mode (mutually exclusive with isBallSelecting)
    @StateObject private var feedbackVM: BallFeedbackViewModel
    @State private var isFeedbackMode         = false
    @State private var isFeedbackCorrecting   = false

    // Phase 2A patch: retroactive pose generation for pre-existing events.
    // isGeneratingPoses gates the banner spinner; poseGenProgress drives the
    // "N / total kész" label; poseGenCompleted + poseGenResultFailed drive the
    // retry-hint text after a partial-failure run.
    @State private var isGeneratingPoses     = false
    @State private var poseGenProgressDone   = 0
    @State private var poseGenProgressTotal  = 0
    @State private var poseGenResultOk       = 0
    @State private var poseGenResultFailed   = 0
    @State private var poseGenCompleted      = false

    @Environment(\.presentationMode) private var presentationMode
    #if DEBUG
    @Environment(\.scenePhase) private var scenePhase
    @State private var showDebugOverlay = false
    #endif

    // Explicit init: @StateObject values must be created before the view appears,
    // and EnvironmentObject is not available at init time.
    //
    // userId is required and must be a valid, positive id — callers (e.g.
    // JugglingVideoListView) must guard on authManager.currentUserId before
    // presenting this screen. JugglingAnnotationViewModel's init enforces this
    // with a precondition; there is no `?? 0` fallback here by design.
    // UserDefaults key for per-video rotation — survives list-cache staleness between open/close.
    private static func rotationKey(_ videoId: String) -> String { "juggling_rotation_\(videoId)" }

    static func cachedRotation(for video: JugglingVideoItem) -> Int {
        let local = UserDefaults.standard.integer(forKey: rotationKey(video.videoId))
        // integer(forKey:) returns 0 for missing keys, and 0 is a valid rotation —
        // use object(forKey:) to distinguish "not set" from "set to 0".
        if UserDefaults.standard.object(forKey: rotationKey(video.videoId)) != nil,
           [0, 90, 180, 270].contains(local) {
            return local
        }
        return video.userRotationDegrees ?? 0
    }

    init(video: JugglingVideoItem, authManager: AuthManager, userId: Int) {
        self.video       = video
        self.authManager = authManager
        _loader  = StateObject(wrappedValue: AnnotationVideoLoader(authManager: authManager))
        _playback = StateObject(wrappedValue: PlaybackController(
            initialRotation: JugglingAnnotationScreen.cachedRotation(for: video)
        ))
        _vm      = StateObject(wrappedValue: JugglingAnnotationViewModel(
            userId:      userId,
            videoId:     video.videoId,
            authManager: authManager
        ))
        _denseSkeletonVM = StateObject(wrappedValue: DenseSkeletonViewModel(videoId: video.videoId))
        let sharedAPIClient = JugglingAnnotationAPIClient(authManager: authManager)
        _ballTrajectoryVM = StateObject(wrappedValue: BallTrajectoryViewModel(
            videoId: video.videoId,
            apiClient: sharedAPIClient
        ))
        _feedbackVM = StateObject(wrappedValue: BallFeedbackViewModel(
            videoId: video.videoId,
            apiClient: sharedAPIClient
        ))
        #if DEBUG
        AnnotationDiagnosticsLog.log("Screen init — userId=\(userId) videoId=\(video.videoId)")
        #endif
    }

    var body: some View {
        NavigationView {
            GeometryReader { geo in
                VStack(spacing: 0) {
                    videoArea(in: geo)
                        .accessibilityLabel("Video")

                    if isFeedbackMode {
                        BallFeedbackPanel(
                            vm:        feedbackVM,
                            onConfirm: { Task { await feedbackVM.submitFeedback(decision: "confirm") } },
                            onNoBall:  { Task { await feedbackVM.submitFeedback(decision: "no_ball") } },
                            onCorrect: { isFeedbackCorrecting = true },
                            onSkip:    { feedbackVM.skip() },
                            onClose:   { isFeedbackMode = false; isFeedbackCorrecting = false }
                        )
                    }

                    PlaybackControlBar(controller: playback, isEnabled: loaderReady)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)

                    statusBar
                        .padding(.horizontal, 12)
                        .padding(.bottom, 4)

                    if !syncedEventsNeedingPose.isEmpty || isGeneratingPoses {
                        generatePosesBanner
                    }

                    EventTimelineView(
                        events:              vm.activeEvents,
                        duration:            playback.duration,
                        currentMs:           playback.currentTimestampMs,
                        onTap:  { id in
                            if let draft = vm.activeEvents.first(where: { $0.deviceEventId == id }) {
                                playback.seek(toTimestampMs: draft.timestampMs)
                            }
                        },
                        onSeek: { playback.seek(toTimestampMs: $0) },
                        ballDetectionStates: vm.ballDetections
                    )
                    .padding(.bottom, 4)

                    eventList

                    if vm.showLabelingCTA {
                        labelingCTA
                            .padding(.horizontal, 12)
                            .padding(.bottom, 4)
                    }

                    saveAndCloseButton
                        .padding(.horizontal, 12)
                        .padding(.bottom, 8)
                }
                .background(Color(.systemBackground))
            }
            .navigationTitle(video.displayDate)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        if vm.activeEvents.isEmpty {
                            performClose()
                        } else {
                            showCloseConfirm = true
                        }
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 16, weight: .medium))
                    }
                    .accessibilityLabel("Bezárás")
                }
                #if DEBUG
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        showDebugOverlay = true
                    } label: {
                        Image(systemName: "ladybug")
                    }
                    .accessibilityLabel("Diagnosztika (DEBUG)")
                }
                #endif
            }
            .onAppear {
                Task { await onAppear() }
                // Fetch pose snapshots concurrently with the video load so the
                // banner and figure.walk icons reflect server state immediately,
                // even if the loader.state → .ready Task is cancelled mid-download.
                // The onChange(of: loader.state) fetch below is kept as defense-in-depth.
                Task { poseSnapshots = await vm.fetchPoseSnapshots() }
            }
            .onDisappear { onDisappear() }
            .actionSheet(isPresented: $showCloseConfirm) {
                ActionSheet(
                    title: Text("Bezárod a képernyőt?"),
                    message: Text("Az események automatikusan mentve vannak, és később folytathatók."),
                    buttons: [
                        .default(Text("Bezárás")) { performClose() },
                        .cancel(Text("Mégsem"))
                    ]
                )
            }
            .alert(isPresented: $showSaveErrorAlert) {
                Alert(
                    title: Text("Mentési hiba"),
                    message: Text(vm.saveError ?? "A mentés sikertelen."),
                    dismissButton: .default(Text("OK")) { vm.clearSaveError() }
                )
            }
            // P2B-5E: labeling flow now opens via the overview, not directly into detail.
            // onDismiss ensures exitLabelingMode() runs however the sheet is dismissed
            // (X button, drag-to-dismiss, or programmatic showLabeling = false).
            // flushPending() uploads all .localOnly events that were just labeled;
            // fires in a detached Task so it does not block the dismiss animation.
            .sheet(isPresented: $showLabeling, onDismiss: {
                vm.exitLabelingMode()
                Task {
                    await vm.flushPending()
                    await vm.bulkFetchBallDetections()
                }
            }) {
                LabelingOverviewView(
                    vm:       vm,
                    videoURL: loaderVideoURL,
                    onClose:  { showLabeling = false }
                )
            }
            .onChange(of: loader.state, perform: { state in
                if case .ready(let url) = state {
                    loaderVideoURL = url                    // AN-3B2A P2B-3
                    let asset = AVAsset(url: url)
                    playback.loadAsset(asset)
                    if let avp = playback.avPlayer { avp.play() }
                    Task { poseSnapshots = await vm.fetchPoseSnapshots() }
                    // AN-3B2D-2: start continuous skeleton extraction
                    denseSkeletonVM.startExtraction(asset: asset)
                    // AN-3B2D-3: fetch dense ball trajectory
                    let durMs = Int(CMTimeGetSeconds(asset.duration) * 1000)
                    Task { await ballTrajectoryVM.fetchTrajectory(durationMs: durMs > 0 ? durMs : nil) }
                }
            })
            .onChange(of: vm.activeEvents) { events in
                // Remove pending pose entries for events that were deleted.
                let activeIds = Set(events.map { $0.deviceEventId })
                for id in pendingPoseSnapshots.keys where !activeIds.contains(id) {
                    pendingPoseSnapshots.removeValue(forKey: id)
                }
                // Upload pending pose when an event reaches .synced and has a serverEventId.
                for draft in events {
                    guard draft.syncStatus == .synced,
                          let serverEventId = draft.serverEventId,
                          let captured = pendingPoseSnapshots[draft.deviceEventId] else { continue }
                    pendingPoseSnapshots.removeValue(forKey: draft.deviceEventId)
                    let eid = serverEventId
                    let req = PoseSnapshotUploadRequest(
                        keypoints:           captured.keypoints,
                        modelVersion:        "apple_vision_v1",
                        captureSource:       "ios_realtime",
                        capturedAtMs:        captured.capturedAtMs,
                        imageWidthPx:        captured.imageWidthPx,
                        imageHeightPx:       captured.imageHeightPx,
                        inferenceConfidence: captured.inferenceConfidence
                    )
                    Task {
                        await vm.uploadPendingPoseSnapshot(serverEventId: eid, request: req)
                        poseSnapshots = await vm.fetchPoseSnapshots()
                    }
                }
                Task { await vm.bulkFetchBallDetections() }
            }
            #if DEBUG
            .onChange(of: scenePhase) { newPhase in
                switch newPhase {
                case .active:
                    AnnotationDiagnosticsLog.log("scenePhase → active (foreground) — userId=\(vm.userId) videoId=\(video.videoId)")
                case .background:
                    AnnotationDiagnosticsLog.log("scenePhase → background — userId=\(vm.userId) videoId=\(video.videoId)")
                case .inactive:
                    AnnotationDiagnosticsLog.log("scenePhase → inactive — userId=\(vm.userId) videoId=\(video.videoId)")
                @unknown default:
                    break
                }
            }
            .sheet(isPresented: $showDebugOverlay) {
                AnnotationDebugOverlay(vm: vm, authManager: authManager, videoId: video.videoId)
            }
            #endif
        }
        .navigationViewStyle(.stack)
        .onChange(of: playback.userRotation) { degrees in
            UserDefaults.standard.set(degrees, forKey: JugglingAnnotationScreen.rotationKey(video.videoId))
            Task { await vm.patchRotation(degrees: degrees) }
        }
        // AN-3B2B1: mutual exclusion — isBallSelecting and isFeedbackMode cannot coexist
        .onChange(of: isBallSelecting) { selecting in
            if selecting {
                isFeedbackMode = false
                isFeedbackCorrecting = false
            }
        }
        .onChange(of: isFeedbackMode) { feedbackOn in
            if feedbackOn {
                isBallSelecting = false
                ballSelectionDragPoint = nil
            } else {
                isFeedbackCorrecting = false
            }
        }
    }

    // MARK: — Video area

    private var loaderReady: Bool {
        if case .ready = loader.state { return true }
        return false
    }

    private func videoAreaHeight(in geo: GeometryProxy) -> CGFloat {
        let width  = geo.size.width
        let height = geo.size.height
        guard let size = playback.videoNaturalSize, size.width > 0 else {
            return width * (9.0 / 16.0)   // fallback: assume 16:9
        }
        let aspect       = size.width / size.height
        let naturalHeight = width / aspect
        let isPortrait   = size.height > size.width
        return isPortrait ? min(naturalHeight, height * 0.50) : naturalHeight
    }

    @ViewBuilder
    private func videoArea(in geo: GeometryProxy) -> some View {
        let containerW = geo.size.width
        let containerH = videoAreaHeight(in: geo)
        let videoSize  = playback.videoNaturalSize ?? CGSize(width: 16, height: 9)
        let renderSize = PlaybackController.computeVideoRenderSize(
            videoSize:    videoSize,
            container:    CGSize(width: containerW, height: containerH),
            userRotation: playback.userRotation
        )

        ZStack {
            Color.black
            if let avp = playback.avPlayer, loaderReady {
                AVPlayerLayerView(player: avp)
                    .frame(width: renderSize.width, height: renderSize.height)
                    .rotationEffect(.degrees(Double(playback.userRotation)))
                    .animation(.easeInOut(duration: 0.25), value: playback.userRotation)

                // AN-3B2D-2: continuous > event-snapshot > nothing
                if showSkeletonOverlay {
                    if denseSkeletonVM.status == .complete || denseSkeletonVM.frameCount > 0 {
                        ContinuousSkeletonOverlayView(
                            frame: denseSkeletonVM.interpolatedFrame(atMs: playback.currentTimestampMs),
                            showSyntheticFeet: true
                        )
                        .frame(width: renderSize.width, height: renderSize.height)
                    } else if let snap = closestSnapshot(toMs: playback.currentTimestampMs) {
                        PoseSnapshotOverlayView(keypoints: snap.keypoints)
                            .frame(width: renderSize.width, height: renderSize.height)
                            .allowsHitTesting(false)
                    }
                }

                // Ball detection overlay — three states:
                //   1. isBallSelecting: interactive crosshair + tap-to-mark gesture
                //   2. auto detection found: read-only BallVideoOverlayView
                //   3. no detection: status banner with "Megjelölöm" correction button
                // Ball overlay — priority: manual tap > trajectory > event-snapshot > banner
                if showBallOverlay {
                    if isBallSelecting {
                        ballSelectionOverlay
                            .frame(width: renderSize.width, height: renderSize.height)
                            .contentShape(Rectangle())
                            .gesture(
                                DragGesture(minimumDistance: 0)
                                    .onChanged { v in ballSelectionDragPoint = v.location }
                                    .onEnded { v in
                                        let np = CGPoint(
                                            x: v.location.x / renderSize.width,
                                            y: v.location.y / renderSize.height
                                        )
                                        handleBallSelection(normalizedPoint: np)
                                    }
                            )
                    } else if ballTrajectoryVM.status == .complete {
                        BallTrajectoryOverlayView(
                            currentPoint: ballTrajectoryVM.point(atMs: playback.currentTimestampMs),
                            trail: ballTrajectoryVM.trail(beforeMs: playback.currentTimestampMs),
                            trackingLost: ballTrajectoryVM.point(atMs: playback.currentTimestampMs) == nil
                        )
                        .frame(width: renderSize.width, height: renderSize.height)
                    } else if let bd = closestBallDetection(toMs: playback.currentTimestampMs) {
                        BallVideoOverlayView(detection: bd)
                            .frame(width: renderSize.width, height: renderSize.height)
                    } else {
                        ballOverlayStatusBanner
                            .frame(width: renderSize.width, height: renderSize.height)
                    }
                }

                // Ball trajectory processing banner
                if ballTrajectoryVM.status == .processing {
                    VStack {
                        Spacer()
                        HStack(spacing: 6) {
                            ProgressView()
                                .scaleEffect(0.7)
                                .tint(.white)
                            Text("Labda: feldolgozás...")
                                .font(.system(size: 11, weight: .medium))
                                .foregroundColor(.white.opacity(0.85))
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Color.black.opacity(0.55))
                        .cornerRadius(6)
                        .padding(.bottom, 28)
                    }
                    .frame(width: renderSize.width, height: renderSize.height)
                    .allowsHitTesting(false)
                }

                // Dense skeleton progress banner
                if denseSkeletonVM.progress > 0, denseSkeletonVM.progress < 1.0 {
                    VStack {
                        Spacer()
                        HStack(spacing: 6) {
                            ProgressView()
                                .scaleEffect(0.7)
                                .tint(.white)
                            Text("Skeleton: \(Int(denseSkeletonVM.progress * 100))%")
                                .font(.system(size: 11, weight: .medium).monospacedDigit())
                                .foregroundColor(.white.opacity(0.85))
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Color.black.opacity(0.55))
                        .cornerRadius(6)
                        .padding(.bottom, 8)
                    }
                    .frame(width: renderSize.width, height: renderSize.height)
                    .allowsHitTesting(false)
                }

                // AN-3B2B1: feedback correction tap overlay (above all other overlays)
                if isFeedbackMode && isFeedbackCorrecting {
                    BallFeedbackOverlayView(item: feedbackVM.currentItem, isCorrecting: true)
                        .frame(width: renderSize.width, height: renderSize.height)
                        .contentShape(Rectangle())
                        .gesture(
                            DragGesture(minimumDistance: 0)
                                .onEnded { v in
                                    let np = CGPoint(
                                        x: max(0, min(1, v.location.x / renderSize.width)),
                                        y: max(0, min(1, v.location.y / renderSize.height))
                                    )
                                    isFeedbackCorrecting = false
                                    Task {
                                        await feedbackVM.submitFeedback(
                                            decision: "corrected",
                                            correctedX: np.x,
                                            correctedY: np.y,
                                            correctionMethod: "tap"
                                        )
                                    }
                                }
                        )
                } else if isFeedbackMode, let item = feedbackVM.currentItem {
                    // Reference circle when not correcting
                    BallFeedbackOverlayView(item: item, isCorrecting: false)
                        .frame(width: renderSize.width, height: renderSize.height)
                }

                // Overlay toggle controls — skeleton + ball + feedback
                VStack {
                    HStack {
                        Spacer()
                        HStack(spacing: 4) {
                            overlayToggleButton(
                                icon:        showBallOverlay ? "viewfinder.circle.fill" : "viewfinder.circle",
                                isOn:        showBallOverlay,
                                accessLabel: showBallOverlay ? "Labda overlay elrejtése" : "Labda overlay megjelenítése"
                            ) {
                                showBallOverlay.toggle()
                                if !showBallOverlay { isBallSelecting = false; ballSelectionDragPoint = nil }
                            }

                            overlayToggleButton(
                                icon:        showSkeletonOverlay ? "figure.walk.circle.fill" : "figure.walk.circle",
                                isOn:        showSkeletonOverlay,
                                accessLabel: showSkeletonOverlay ? "Csontváz elrejtése" : "Csontváz megjelenítése"
                            ) { showSkeletonOverlay.toggle() }

                            // AN-3B2B1: feedback toggle (D2 — mutually exclusive with isBallSelecting)
                            overlayToggleButton(
                                icon:        isFeedbackMode ? "hand.thumbsup.circle.fill" : "hand.thumbsup.circle",
                                isOn:        isFeedbackMode,
                                accessLabel: isFeedbackMode ? "Visszajelzés mód kikapcsolása" : "Visszajelzés mód bekapcsolása"
                            ) {
                                if !isFeedbackMode {
                                    isBallSelecting = false
                                    ballSelectionDragPoint = nil
                                    isFeedbackCorrecting = false
                                    Task { await feedbackVM.loadQueue() }
                                } else {
                                    isFeedbackCorrecting = false
                                }
                                isFeedbackMode.toggle()
                            }
                        }
                        .padding(8)
                    }
                    Spacer()
                }
            } else {
                loaderPlaceholder
            }
        }
        .frame(width: containerW, height: containerH)
        .clipped()
    }

    @ViewBuilder
    private var loaderPlaceholder: some View {
        VStack(spacing: 12) {
            switch loader.state {
            case .idle:
                ProgressView().accentColor(.white)
            case .downloading(let progress):
                if progress >= 0 {
                    VStack(spacing: 6) {
                        ProgressView(value: progress)
                            .progressViewStyle(.linear)
                            .accentColor(.white)
                            .frame(width: 180)
                        Text("\(Int(progress * 100))%")
                            .foregroundColor(.white.opacity(0.7))
                            .font(.caption.monospacedDigit())
                    }
                } else {
                    VStack(spacing: 6) {
                        ProgressView().accentColor(.white)
                        Text("Letöltés…")
                            .foregroundColor(.white.opacity(0.7))
                            .font(.caption)
                    }
                }
            case .failed(let error):
                VStack(spacing: 10) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(.system(size: 28))
                        .foregroundColor(.orange)
                    Text(loaderErrorMessage(error))
                        .foregroundColor(.white.opacity(0.8))
                        .font(.caption)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                    Button("Újra") {
                        loader.reset()
                        Task { await loader.load(videoId: video.videoId, userId: vm.userId) }
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundColor(.white)
                    .padding(.horizontal, 16).padding(.vertical, 8)
                    .background(Color.white.opacity(0.2))
                    .clipShape(Capsule())
                    .accessibilityLabel("Videó újra letöltése")
                }
            case .ready:
                EmptyView()
            }
        }
        .accessibilityElement(children: .contain)
    }

    // MARK: — Event list

    private var sortedEvents: [ContactEventDraft] {
        vm.activeEvents.sorted { $0.timestampMs < $1.timestampMs }
    }

    @ViewBuilder
    private var eventList: some View {
        ZStack(alignment: .bottomTrailing) {
            if vm.activeEvents.isEmpty {
                emptyState
            } else {
                List {
                    ForEach(sortedEvents) { draft in
                        eventRow(draft)
                    }
                    .onDelete { indexSet in
                        let events = sortedEvents
                        for i in indexSet {
                            vm.markDeleted(deviceEventId: events[i].deviceEventId)
                        }
                    }
                }
                .listStyle(.plain)
            }

            fabButton
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "plus.circle.dashed")
                .font(.system(size: 40))
                .foregroundColor(.secondary)
            Text("Nincs esemény")
                .font(.headline)
                .foregroundColor(.secondary)
            Text("Nyomj + gombra a jelenlegi időpontban egy kontakt esemény rögzítéséhez.")
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityLabel("Nincsenek annotált események. Nyomj a plusz gombra egy új hozzáadásához.")
    }

    @ViewBuilder
    private func eventRow(_ draft: ContactEventDraft) -> some View {
        Button {
            playback.seek(toTimestampMs: draft.timestampMs)
        } label: {
            HStack(spacing: 12) {
                Circle()
                    .fill(EventTimelineView.pinColor(for: draft.syncStatus))
                    .frame(width: 8, height: 8)

                VStack(alignment: .leading, spacing: 2) {
                    Text(PlaybackControlBar.formatTimestamp(ms: draft.timestampMs))
                        .font(.caption2.monospacedDigit())
                        .foregroundColor(.secondary)
                    Text(typeLabel(for: draft.contactType))
                        .font(.body)
                }

                Spacer()

                if let serverEventId = draft.serverEventId,
                   poseSnapshots.contains(where: { $0.contactEventId == serverEventId }) {
                    Image(systemName: "figure.walk.circle")
                        .font(.caption)
                        .foregroundColor(.blue)
                }
                if let side = draft.side { sideTag(side) }
                syncIcon(for: draft)
            }
            .contentShape(Rectangle())
            .padding(.vertical, 4)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(rowAccessibilityLabel(draft))
    }

    @ViewBuilder
    private func sideTag(_ side: String) -> some View {
        Text(side == "left" ? "B" : side == "right" ? "J" : side.prefix(1).uppercased())
            .font(.caption.weight(.semibold))
            .foregroundColor(.secondary)
            .frame(width: 20)
    }

    @ViewBuilder
    private func syncIcon(for draft: ContactEventDraft) -> some View {
        switch draft.syncStatus {
        case .unlabeled, .labelPending:
            EmptyView()
        case .synced:
            Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
        case .localOnly, .retryPending:
            Image(systemName: "arrow.up.circle").foregroundColor(.orange)
        case .syncing, .updating, .deleting:
            ProgressView().scaleEffect(0.65)
        case .conflicted, .failedPermanent:
            Image(systemName: "exclamationmark.circle.fill").foregroundColor(.red)
        case .needsReconciliation:
            Image(systemName: "questionmark.circle").foregroundColor(.yellow)
        case .deleted:
            EmptyView()
        }
    }

    private var fabButton: some View {
        Button {
            guard loaderReady else { return }
            let currentMs = playback.currentTimestampMs
            if let draft = vm.markTimestamp(ms: currentMs) {
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                withAnimation(.spring(response: 0.15, dampingFraction: 0.6)) { fabPressed = true }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
                    withAnimation(.spring(response: 0.15, dampingFraction: 0.6)) { fabPressed = false }
                }
                if let videoURL = loaderVideoURL {
                    let deviceId = draft.deviceEventId
                    Task { await capturePose(at: currentMs, deviceEventId: deviceId, videoURL: videoURL) }
                }
            }
        } label: {
            Image(systemName: "plus")
                .font(.title2.weight(.semibold))
                .foregroundColor(.white)
                .frame(width: 56, height: 56)
                .background(loaderReady ? Color.accentColor : Color.gray)
                .clipShape(Circle())
                .shadow(color: .black.opacity(0.25), radius: 4)
                .scaleEffect(fabPressed ? 0.88 : 1.0)
        }
        .padding(16)
        .disabled(!loaderReady)
        .accessibilityLabel("Kontakt jelölése")
        .accessibilityHint("Rögzíti a kontakt eseményt a videó jelenlegi időpontján")
    }

    // MARK: — Status bar (AN-3B2A P1)

    private var eventCountLabel: String {
        let n = vm.activeEvents.count
        return n == 1 ? "1 esemény jelölve" : "\(n) esemény jelölve"
    }

    @ViewBuilder
    private var statusBar: some View {
        HStack {
            Text(eventCountLabel)
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
            saveStatusLabel
        }
    }

    @ViewBuilder
    private var saveStatusLabel: some View {
        switch vm.saveStatus {
        case .saving:
            HStack(spacing: 4) {
                ProgressView().scaleEffect(0.7)
                Text("Mentés folyamatban…")
            }
            .font(.caption)
            .foregroundColor(.secondary)
        case .saved:
            HStack(spacing: 4) {
                Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
                Text("Automatikusan mentve")
            }
            .font(.caption)
            .foregroundColor(.secondary)
        case .failed:
            HStack(spacing: 4) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.red)
                Text("Mentési hiba")
            }
            .font(.caption)
            .foregroundColor(.red)
        case .idle:
            EmptyView()
        }
    }

    // MARK: — Labeling CTA (AN-3B2A P2 / P2B-5E)

    // Button text is vm.labelingCTAText — updates reactively as session state changes.
    // Tapping always pauses the main player first.
    //
    // .unlabeled CTA state: enterLabelingMode() batch-transitions all .unlabeled →
    //   .labelPending so the overview immediately shows "Folytatás" cards. Opens only
    //   when screenMode becomes .labeling (guards session == nil edge case).
    //
    // All other CTA states: opens LabelingOverviewView directly — the overview
    //   handles per-event routing via handleOpenEvent(id:).
    private var labelingCTA: some View {
        Button {
            playback.pause()
            if vm.labelingCTAState == .unlabeled {
                vm.enterLabelingMode()
                guard vm.screenMode == .labeling else { return }
            }
            showLabeling = true
        } label: {
            Text(vm.labelingCTAText)
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .foregroundColor(.white)
                .background(Color.accentColor)
                .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .accessibilityLabel(vm.labelingCTAText)
        .accessibilityHint("Megnyitja a címkézési áttekintőt")
    }

    // MARK: — Save and close (AN-3B2A P1)

    private var saveAndCloseButton: some View {
        Button {
            performClose()
        } label: {
            Text("Mentés és bezárás")
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .foregroundColor(vm.isSaving ? .secondary : Color.accentColor)
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(vm.isSaving ? Color.secondary : Color.accentColor, lineWidth: 1)
                )
        }
        .disabled(vm.isSaving)
        .accessibilityLabel("Mentés és bezárás")
        .accessibilityHint("Elmenti a jelölt eseményeket és visszatér a videólistára")
    }

    // MARK: — Lifecycle

    // Phase 2A: captures a Vision body pose for the given video timestamp and
    // stores it in pendingPoseSnapshots. Called from the FAB tap; runs async so
    // it never delays the contact event creation feedback.
    private func capturePose(at timestampMs: Int, deviceEventId: UUID, videoURL: URL) async {
        let asset = AVAsset(url: videoURL)
        guard let (cgImage, imageSize) = await PoseSnapshotService.extractFrame(
            from: asset, atMs: timestampMs
        ) else { return }

        let (keypoints, confidence) = await Task.detached(priority: .utility) {
            PoseSnapshotService.runPoseDetection(on: cgImage)
        }.value

        pendingPoseSnapshots[deviceEventId] = CapturedPose(
            keypoints:           keypoints,
            capturedAtMs:        timestampMs,
            imageWidthPx:        Int(imageSize.width),
            imageHeightPx:       Int(imageSize.height),
            inferenceConfidence: confidence.map { Double($0) }
        )
    }

    private func onAppear() async {
        await vm.onAppear()
        await loader.load(videoId: video.videoId, userId: vm.userId)
        await vm.bulkFetchBallDetections()
    }

    private func onDisappear() {
        guard !didCleanUp else { return }
        didCleanUp = true
        vm.onDisappear()
        loader.cancel()
        playback.pause()
        denseSkeletonVM.cancel()
        ballTrajectoryVM.cancel()
    }

    // Explicit save-then-close path for the X button and "Mentés és
    // bezárás" CTA. Performs vm.saveNow() and only dismisses on success —
    // a failed save leaves the screen open with showSaveErrorAlert set, so
    // the user never silently loses data. Shares the didCleanUp guard with
    // the SwiftUI .onDisappear fallback to avoid a double save.
    //
    // flushPending() runs before dismiss so any .localOnly events reach the
    // backend before the screen disappears. dismiss() is called inside the
    // Task so it runs only after the flush completes.
    private func performClose() {
        guard !didCleanUp else { return }
        let ok = vm.saveNow()
        guard ok else {
            showSaveErrorAlert = true
            return
        }
        didCleanUp = true
        loader.cancel()
        playback.pause()
        Task {
            await vm.flushPending()
            presentationMode.wrappedValue.dismiss()
        }
    }

    // MARK: — Retroactive pose generation (Phase 2A patch)

    // Events that are synced with the server but have no pose snapshot yet.
    // Recomputed whenever poseSnapshots or activeEvents changes.
    private var syncedEventsNeedingPose: [ContactEventDraft] {
        let coveredIds = Set(poseSnapshots.map(\.contactEventId))
        return vm.activeEvents.filter {
            $0.syncStatus == .synced &&
            $0.serverEventId != nil &&
            !coveredIds.contains($0.serverEventId!)
        }
    }

    @ViewBuilder
    private var generatePosesBanner: some View {
        HStack(spacing: 8) {
            if isGeneratingPoses {
                ProgressView().scaleEffect(0.75)
                Text("\(poseGenProgressDone) / \(poseGenProgressTotal) pose snapshot kész…")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
            } else {
                Image(systemName: "figure.walk.circle")
                    .font(.caption)
                    .foregroundColor(.secondary)
                if poseGenCompleted && poseGenResultFailed > 0 {
                    Text("\(poseGenResultFailed) sikertelen · Próbáld újra")
                        .font(.caption)
                        .foregroundColor(.orange)
                } else {
                    Text("\(syncedEventsNeedingPose.count) eseményhez hiányzik pose snapshot")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Button("Generál") {
                    Task { await generateAllPoses() }
                }
                .font(.caption.weight(.semibold))
                .disabled(!loaderReady || isGeneratingPoses)
                .accessibilityLabel("Pose snapshot generálása az összes eseményhez")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color(.secondarySystemBackground))
    }

    // Iterates every synced event without a pose snapshot, extracts the video
    // frame at each event's timestamp, runs Apple Vision body pose detection,
    // and uploads via the existing POST /pose-snapshot endpoint.
    // Uses captureSource "ios_retroactive" to distinguish from real-time FAB taps.
    // Non-throwing: frame-extraction or upload failures increment the failed counter
    // but do not abort the remaining events.
    private func generateAllPoses() async {
        guard loaderReady, let videoURL = loaderVideoURL else { return }
        let targets = syncedEventsNeedingPose
        guard !targets.isEmpty else { return }

        isGeneratingPoses    = true
        poseGenProgressDone  = 0
        poseGenProgressTotal = targets.count
        poseGenResultOk      = 0
        poseGenResultFailed  = 0
        poseGenCompleted     = false

        let asset = AVAsset(url: videoURL)
        var ok     = 0
        var failed = 0

        for (idx, draft) in targets.enumerated() {
            defer { poseGenProgressDone = idx + 1 }

            guard let serverEventId = draft.serverEventId else {
                failed += 1
                continue
            }
            guard let (cgImage, imageSize) = await PoseSnapshotService.extractFrame(
                from: asset, atMs: draft.timestampMs
            ) else {
                failed += 1
                continue
            }
            let (keypoints, confidence) = await Task.detached(priority: .utility) {
                PoseSnapshotService.runPoseDetection(on: cgImage)
            }.value
            let req = PoseSnapshotUploadRequest(
                keypoints:           keypoints,
                modelVersion:        "apple_vision_v1",
                captureSource:       "ios_retroactive",
                capturedAtMs:        draft.timestampMs,
                imageWidthPx:        Int(imageSize.width),
                imageHeightPx:       Int(imageSize.height),
                inferenceConfidence: confidence.map { Double($0) }
            )
            await vm.uploadPendingPoseSnapshot(serverEventId: serverEventId, request: req)
            ok += 1
        }

        poseSnapshots       = await vm.fetchPoseSnapshots()
        poseGenResultOk     = ok
        poseGenResultFailed = failed
        poseGenCompleted    = true
        isGeneratingPoses   = false
    }

    // MARK: — Display helpers

    // Returns the pose snapshot whose timestamp is closest to the given playhead
    // position, provided it falls within a ±500 ms window of the playhead.
    private func closestSnapshot(toMs ms: Int) -> PoseSnapshotOut? {
        guard !poseSnapshots.isEmpty else { return nil }
        let best = poseSnapshots.min(by: { abs($0.timestampMs - ms) < abs($1.timestampMs - ms) })!
        return abs(best.timestampMs - ms) <= 500 ? best : nil
    }

    private func closestBallDetection(toMs ms: Int) -> BallDetectionOut? {
        vm.activeEvents
            .compactMap { draft -> (distance: Int, detection: BallDetectionOut)? in
                guard let serverId = draft.serverEventId,
                      case .loaded(let d) = vm.ballDetections[serverId],
                      !d.noBallDetected,
                      d.ballX != nil, d.ballY != nil else { return nil }
                let dist = abs(draft.timestampMs - ms)
                guard dist <= 500 else { return nil }
                return (dist, d)
            }
            .min(by: { $0.distance < $1.distance })
            .map(\.detection)
    }

    @ViewBuilder
    private func overlayToggleButton(
        icon:        String,
        isOn:        Bool,
        accessLabel: String,
        action:      @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundColor(.white)
                .padding(8)
                .background(Color.black.opacity(isOn ? 0.65 : 0.45))
                .clipShape(Circle())
        }
        .accessibilityLabel(accessLabel)
    }

    // MARK: — Skeleton status helpers (AN-3B2C-2)

    static func skeletonStatusText(snapshotsEmpty: Bool, hasNearby: Bool) -> String {
        if snapshotsEmpty { return "Nincs skeleton adat ehhez a videóhoz" }
        if !hasNearby     { return "Nincs skeleton adat ehhez az időponthoz" }
        return ""
    }

    private var skeletonStatusBanner: some View {
        let text = JugglingAnnotationScreen.skeletonStatusText(
            snapshotsEmpty: poseSnapshots.isEmpty,
            hasNearby:      closestSnapshot(toMs: playback.currentTimestampMs) != nil
        )
        return VStack {
            Spacer()
            Text(text)
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(.white.opacity(0.85))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(Color.black.opacity(0.60))
                .cornerRadius(6)
                .padding(.bottom, 60)
        }
        .allowsHitTesting(false)
    }

    // MARK: — Ball overlay helpers (AN-3B2C-1)

    private var ballOverlayStatusBanner: some View {
        VStack {
            Spacer()
            VStack(spacing: 8) {
                Text(ballOverlayStatusText)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(.white.opacity(0.85))
                if nearestEventForBallSelection() != nil {
                    Button("Megjelölöm") { isBallSelecting = true }
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(Color.black)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 7)
                        .background(Color.yellow)
                        .cornerRadius(8)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color.black.opacity(0.62))
            .cornerRadius(10)
            .padding(.bottom, 10)
        }
        .allowsHitTesting(true)
    }

    private var ballOverlayStatusText: String {
        if vm.activeEvents.isEmpty {
            return "Rögzíts kontakt eseményt a labda jelöléséhez"
        }
        let syncedCount = vm.activeEvents.filter { $0.serverEventId != nil }.count
        if syncedCount == 0 {
            return "Labda detektálás szinkronizálás után lesz elérhető"
        }
        if vm.ballDetections.isEmpty {
            return "Labda detektálás betöltés alatt…"
        }
        let hasFetching = vm.ballDetections.values.contains {
            if case .fetching = $0 { return true }; return false
        }
        if hasFetching { return "Labda detektálás betöltés alatt…" }
        let hasFeatureDisabled = vm.ballDetections.values.contains {
            if case .featureDisabled = $0 { return true }; return false
        }
        if hasFeatureDisabled { return "Labda detektálás nem elérhető" }
        if nearestEventForBallSelection() == nil { return "Nincs esemény ±2s ablakban" }
        return "Nincs auto-detektálás — jelöld meg manuálisan"
    }

    @ViewBuilder
    private var ballSelectionOverlay: some View {
        ZStack {
            Color.black.opacity(0.01)
            if let pt = ballSelectionDragPoint {
                ZStack {
                    Circle()
                        .strokeBorder(Color.yellow, lineWidth: 2.5)
                        .frame(width: 36, height: 36)
                    Circle()
                        .fill(Color.yellow.opacity(0.18))
                        .frame(width: 36, height: 36)
                }
                .position(pt)
                .allowsHitTesting(false)
            }
            VStack {
                Text("Koppints a labda helyére")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(.white)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Color.black.opacity(0.68))
                    .cornerRadius(7)
                    .padding(.top, 10)
                Spacer()
                Button("Mégsem") {
                    isBallSelecting = false
                    ballSelectionDragPoint = nil
                }
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(.white)
                .padding(.horizontal, 18)
                .padding(.vertical, 7)
                .background(Color.black.opacity(0.55))
                .cornerRadius(8)
                .padding(.bottom, 10)
            }
        }
        .allowsHitTesting(true)
    }

    private func nearestEventForBallSelection() -> ContactEventDraft? {
        let ms = playback.currentTimestampMs
        guard let nearest = vm.activeEvents
            .filter({ $0.serverEventId != nil })
            .min(by: { abs($0.timestampMs - ms) < abs($1.timestampMs - ms) }),
              abs(nearest.timestampMs - ms) <= 2000
        else { return nil }
        return nearest
    }

    private func handleBallSelection(normalizedPoint np: CGPoint) {
        guard let draft = nearestEventForBallSelection(),
              let serverId = draft.serverEventId else {
            isBallSelecting = false
            ballSelectionDragPoint = nil
            return
        }
        isBallSelecting = false
        ballSelectionDragPoint = nil
        let x = Double(min(max(np.x, 0), 1))
        let y = Double(min(max(np.y, 0), 1))
        Task {
            try? await vm.postManualBallPosition(videoId: vm.videoId, eventId: serverId, x: x, y: y)
            // AN-3B2D-3: also seed the dense trajectory if available
            if ballTrajectoryVM.status == .complete {
                await ballTrajectoryVM.postManualSeed(
                    frameMs: playback.currentTimestampMs, ballX: x, ballY: y
                )
            }
        }
    }

    private func typeLabel(for key: String?) -> String {
        guard let key = key else { return "—" }
        return vm.taxonomy?.groups.flatMap { $0.contactTypes }.first { $0.key == key }?.labelHu ?? key
    }

    private func loaderErrorMessage(_ error: AnnotationVideoLoader.LoadError) -> String {
        switch error {
        case .diskSpaceInsufficient(let bytes):
            return "Nincs elég tárhely (\(bytes / 1_048_576) MB szabad)."
        case .unauthorized:
            return "Lejárt munkamenet. Lépj be újra."
        case .httpError(let code):
            return "Szerver hiba (\(code)). Próbáld újra."
        case .networkError:
            return "Hálózati hiba. Ellenőrizd a kapcsolatot."
        case .cancelled:
            return "Letöltés megszakítva."
        }
    }

    private func rowAccessibilityLabel(_ draft: ContactEventDraft) -> String {
        let time = PlaybackControlBar.formatTimestamp(ms: draft.timestampMs)
        let type = typeLabel(for: draft.contactType)
        let side = draft.side.map { ", \($0 == "left" ? "bal" : "jobb")" } ?? ""
        return "\(time), \(type)\(side)"
    }
}
