import SwiftUI
import AVFoundation

// MARK: — JugglingAnnotationScreen

// Primary self-annotation screen. Owns the video player, timeline, event list,
// and contact picker. Replaces JugglingPlayerView for annotation-mode playback.
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
// Lifecycle:
//   .task { onAppear() }        — loads taxonomy, local session, starts download
//   .onDisappear { onDisappear() } — persists session, cancels in-flight download
//
// NOT in scope (AN-3C):
//   Finish flow, result/summary screen, navigation to next video.

struct JugglingAnnotationScreen: View {

    let video:       JugglingVideoItem
    let authManager: AuthManager   // passed explicitly because @StateObject init runs before EnvironmentObject

    @StateObject private var loader:  AnnotationVideoLoader
    @StateObject private var playback: PlaybackController
    @StateObject private var vm:      JugglingAnnotationViewModel

    @State private var showPicker        = false
    @State private var editingEventId:   UUID? = nil
    @State private var didCleanUp        = false   // guards double onDisappear calls

    @Environment(\.dismiss) private var dismiss

    // Explicit init: @StateObject values must be created before the view appears,
    // and EnvironmentObject is not available at init time.
    init(video: JugglingVideoItem, authManager: AuthManager) {
        self.video       = video
        self.authManager = authManager
        _loader  = StateObject(wrappedValue: AnnotationVideoLoader(authManager: authManager))
        _playback = StateObject(wrappedValue: PlaybackController())
        _vm      = StateObject(wrappedValue: JugglingAnnotationViewModel(
            userId:      authManager.currentUserId ?? 0,
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
                        onTap:  { editingEventId = $0 },
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
                        dismiss()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 16, weight: .medium))
                    }
                    .accessibilityLabel("Bezárás")
                }
            }
            .overlay(alignment: .bottom) { conflictOverlay }
            .sheet(isPresented: $showPicker) { pickerSheet }
            .sheet(item: $editingEventId) { id in detailSheet(for: id) }
            .task { await onAppear() }
            .onDisappear { onDisappear() }
            .onChange(of: loader.state, perform: { state in
                if case .ready(let url) = state {
                    let asset = AVAsset(url: url)
                    playback.loadAsset(asset)
                    if let avp = playback.avPlayer { avp.play() }
                }
            })
            .onChange(of: vm.pendingConflictId, perform: { _ in })  // re-render trigger
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
                ProgressView().tint(.white)
            case .downloading(let progress):
                if progress >= 0 {
                    VStack(spacing: 6) {
                        ProgressView(value: progress)
                            .progressViewStyle(.linear)
                            .tint(.white)
                            .frame(width: 180)
                        Text("\(Int(progress * 100))%")
                            .foregroundColor(.white.opacity(0.7))
                            .font(.caption.monospacedDigit())
                    }
                } else {
                    VStack(spacing: 6) {
                        ProgressView().tint(.white)
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

    @ViewBuilder
    private var eventList: some View {
        ZStack(alignment: .bottomTrailing) {
            if vm.activeEvents.isEmpty {
                emptyState
            } else {
                List {
                    ForEach(
                        vm.activeEvents.sorted { $0.timestampMs < $1.timestampMs }
                    ) { draft in
                        eventRow(draft)
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button("Törlés", role: .destructive) {
                                    vm.markDeleted(deviceEventId: draft.deviceEventId)
                                }
                                .accessibilityLabel("Esemény törlése")
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
        Button { editingEventId = draft.deviceEventId } label: {
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
            showPicker = true
        } label: {
            Image(systemName: "plus")
                .font(.title2.weight(.semibold))
                .foregroundColor(.white)
                .frame(width: 56, height: 56)
                .background(loaderReady ? Color.accentColor : Color.gray)
                .clipShape(Circle())
                .shadow(color: .black.opacity(0.25), radius: 4)
        }
        .padding(16)
        .disabled(!loaderReady)
        .accessibilityLabel("Új kontakt esemény")
        .accessibilityHint("Videó jelenlegi időpontjában rögzít egy kontakt eseményt")
    }

    // MARK: — Conflict overlay

    @ViewBuilder
    private var conflictOverlay: some View {
        if let conflictId = vm.pendingConflictId,
           let draft = vm.activeEvents.first(where: { $0.deviceEventId == conflictId }) {
            Color.black.opacity(0.45)
                .ignoresSafeArea()
                .overlay(alignment: .bottom) {
                    ConflictResolutionView(
                        draft:    draft,
                        taxonomy: vm.taxonomy,
                        onAcceptServer: {
                            vm.acceptServerVersion(deviceEventId: conflictId)
                        },
                        onKeepLocal: {
                            vm.keepLocalVersion(deviceEventId: conflictId)
                            Task { await vm.flushPending() }
                        }
                    )
                    .padding(.horizontal, 12)
                    .padding(.bottom, 8)
                }
                .accessibilityElement(children: .contain)
                .transition(.opacity)
        }
    }

    // MARK: — Sheets

    @ViewBuilder
    private var pickerSheet: some View {
        ContactPickerView(
            taxonomy:  vm.taxonomy,
            currentMs: playback.currentTimestampMs,
            onSave: { type, side, confidence, label, desc in
                vm.addEvent(
                    timestampMs:          playback.currentTimestampMs,
                    contactType:          type,
                    side:                 side,
                    annotationConfidence: confidence,
                    customLabel:          label,
                    customDescription:    desc
                )
                showPicker = false
                Task { await vm.flushPending() }
            },
            onCancel: { showPicker = false }
        )
    }

    @ViewBuilder
    private func detailSheet(for id: UUID) -> some View {
        if let draft = vm.activeEvents.first(where: { $0.deviceEventId == id }) {
            EventDetailView(
                draft:    draft,
                taxonomy: vm.taxonomy,
                onEdit: { type, side, confidence, label, desc in
                    vm.editEvent(
                        deviceEventId:        id,
                        contactType:          type,
                        side:                 side,
                        annotationConfidence: confidence,
                        customLabel:          label,
                        customDescription:    desc
                    )
                    Task { await vm.flushPending() }
                },
                onDelete: {
                    vm.markDeleted(deviceEventId: id)
                }
            )
        }
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

    private func typeLabel(for key: String) -> String {
        vm.taxonomy?.groups.flatMap { $0.contactTypes }.first { $0.key == key }?.labelHu ?? key
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
