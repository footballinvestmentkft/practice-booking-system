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

    @State private var didCleanUp  = false   // guards double onDisappear calls
    @State private var fabPressed  = false   // brief scale-down on successful mark

    @Environment(\.presentationMode) private var presentationMode

    // Explicit init: @StateObject values must be created before the view appears,
    // and EnvironmentObject is not available at init time.
    //
    // userId is required and must be a valid, positive id — callers (e.g.
    // JugglingVideoListView) must guard on authManager.currentUserId before
    // presenting this screen. JugglingAnnotationViewModel's init enforces this
    // with a precondition; there is no `?? 0` fallback here by design.
    init(video: JugglingVideoItem, authManager: AuthManager, userId: Int) {
        self.video       = video
        self.authManager = authManager
        _loader  = StateObject(wrappedValue: AnnotationVideoLoader(authManager: authManager))
        _playback = StateObject(wrappedValue: PlaybackController())
        _vm      = StateObject(wrappedValue: JugglingAnnotationViewModel(
            userId:      userId,
            videoId:     video.videoId,
            authManager: authManager
        ))
    }

    var body: some View {
        NavigationView {
            GeometryReader { geo in
                VStack(spacing: 0) {
                    videoArea(in: geo)
                        .accessibilityLabel("Video")

                    PlaybackControlBar(controller: playback, isEnabled: loaderReady)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)

                    EventTimelineView(
                        events:    vm.activeEvents,
                        duration:  playback.duration,
                        currentMs: playback.currentTimestampMs,
                        onTap:  { id in
                            if let draft = vm.activeEvents.first(where: { $0.deviceEventId == id }) {
                                playback.seek(toTimestampMs: draft.timestampMs)
                            }
                        },
                        onSeek: { playback.seek(toTimestampMs: $0) }
                    )
                    .padding(.bottom, 4)

                    eventList
                }
                .background(Color(.systemBackground))
            }
            .navigationTitle(video.displayDate)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        onDisappear()
                        presentationMode.wrappedValue.dismiss()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 16, weight: .medium))
                    }
                    .accessibilityLabel("Bezárás")
                }
            }
            .onAppear { Task { await onAppear() } }
            .onDisappear { onDisappear() }
            .onChange(of: loader.state, perform: { state in
                if case .ready(let url) = state {
                    let asset = AVAsset(url: url)
                    playback.loadAsset(asset)
                    if let avp = playback.avPlayer { avp.play() }
                }
            })
        }
        .navigationViewStyle(.stack)
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
        ZStack {
            Color.black
            if let avp = playback.avPlayer, loaderReady {
                AVPlayerLayerView(player: avp)
            } else {
                loaderPlaceholder
            }
        }
        .frame(width: geo.size.width, height: videoAreaHeight(in: geo))
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
            if vm.markTimestamp(ms: playback.currentTimestampMs) != nil {
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                withAnimation(.spring(response: 0.15, dampingFraction: 0.6)) { fabPressed = true }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
                    withAnimation(.spring(response: 0.15, dampingFraction: 0.6)) { fabPressed = false }
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

    // MARK: — Lifecycle

    private func onAppear() async {
        await vm.onAppear()
        await loader.load(videoId: video.videoId, userId: vm.userId)
    }

    private func onDisappear() {
        guard !didCleanUp else { return }
        didCleanUp = true
        vm.onDisappear()
        loader.cancel()
        playback.pause()
    }

    // MARK: — Display helpers

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
