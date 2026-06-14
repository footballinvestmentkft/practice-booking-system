import SwiftUI
import AVFoundation

// MARK: — ThumbnailSession (AN-3B2A P2B-5B)

@MainActor
private final class ThumbnailSession: ObservableObject {
    let generator = EventStillFrameGenerator(maxCacheSize: 20)
    var loadTasks: [UUID: Task<Void, Never>] = [:]

    func clearAll() {
        loadTasks.values.forEach { $0.cancel() }
        loadTasks = [:]
        generator.clearCache()
    }
}

// MARK: — LabelingOverviewView (AN-3B2A P2B-5B / P2B-5D)
//
// Presents either:
//   • the scrollable event card list (selectedEventId == nil)
//   • EventLabelDetailView for the selected event (selectedEventId != nil)
//
// Card CTA routing (P2B-5D):
//   .unlabeled           → markEventForLabeling() (single-event only) → then open
//   .labelPending / .localOnly / .synced / .retryPending / .failedPermanent → open directly
//   blocked states       → do nothing (no detail opens)
//
// "Következő cimkézetlen" finds the earliest unfinished event via vm.nextUnlabeledId
// and routes it through the same handleOpenEvent() path.
//
// onBack (from detail) → dismisses detail, returns to the card list.
// onClose             → clears thumbs, propagates to the caller (closes the sheet).
// No fallback to another event if startingEventId is not found — safety state shown instead.
// No backend sync, no Finish flow.

struct LabelingOverviewView: View {

    @ObservedObject var vm: JugglingAnnotationViewModel
    var videoURL: URL?
    var onClose: () -> Void

    @StateObject private var thumbSession = ThumbnailSession()
    @State private var thumbnails:       [UUID: UIImage] = [:]
    @State private var loadingIds:       Set<UUID>       = []
    @State private var selectedEventId:  UUID?           = nil  // P2B-5D: routing state

    // MARK: — Body

    var body: some View {
        if let eventId = selectedEventId {
            // Detail view replaces the overview entirely when an event is selected.
            EventLabelDetailView(
                vm:              vm,
                videoURL:        videoURL,
                startingEventId: eventId,
                onBack: {
                    // Return to the overview — do NOT call exitLabelingMode().
                    selectedEventId = nil
                },
                onClose: {
                    // Close the entire labeling flow.
                    selectedEventId = nil
                    thumbSession.clearAll()
                    onClose()
                }
            )
        } else {
            overviewContent
        }
    }

    // MARK: — Overview navigation

    private var overviewContent: some View {
        NavigationView {
            VStack(spacing: 0) {
                progressSection
                Divider()
                eventScrollView
                Divider()
                bottomCTA
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Cimkézés — \(vm.activeEvents.count) esemény")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        thumbSession.clearAll()
                        onClose()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 16, weight: .medium))
                    }
                    .accessibilityLabel("Bezárás")
                }
            }
        }
        .navigationViewStyle(.stack)
    }

    // MARK: — Progress section

    private var progressSection: some View {
        let total = vm.activeEvents.count
        let done  = vm.labeledCount
        let frac  = total > 0 ? Double(done) / Double(total) : 0.0

        return VStack(spacing: 4) {
            ProgressView(value: frac)
                .progressViewStyle(.linear)
                .padding(.horizontal, 16)
            Text("\(done) / \(total) kész")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(.vertical, 10)
        .background(Color(.systemBackground))
    }

    // MARK: — Event list

    private var sortedEvents: [ContactEventDraft] {
        vm.activeEvents.sorted { $0.timestampMs < $1.timestampMs }
    }

    @ViewBuilder
    private var eventScrollView: some View {
        if vm.activeEvents.isEmpty {
            emptyState
        } else {
            List {
                ForEach(sortedEvents) { draft in
                    EventOverviewCard(
                        draft:              draft,
                        taxonomy:           vm.taxonomy,
                        thumbnail:          thumbnails[draft.deviceEventId],
                        isLoadingThumbnail: loadingIds.contains(draft.deviceEventId)
                    ) {
                        handleOpenEvent(id: draft.deviceEventId)
                    }
                    .onAppear { loadThumbnail(for: draft) }
                    .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
                    .listRowBackground(Color(.systemBackground))
                }
            }
            .listStyle(.plain)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "clock.badge.questionmark")
                .font(.system(size: 40))
                .foregroundColor(.secondary)
            Text("Nincs jelölt esemény")
                .font(.headline)
                .foregroundColor(.secondary)
            Text("Nyomj a + gombra az annotációs képernyőn egy kontakt jelöléséhez.")
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: — Bottom CTA

    @ViewBuilder
    private var bottomCTA: some View {
        if let nextId = vm.nextUnlabeledId {
            Button {
                handleOpenEvent(id: nextId)
            } label: {
                Text("Következő cimkézetlen")
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .foregroundColor(.white)
                    .background(Color.accentColor)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Color(.systemBackground))
            .accessibilityLabel("Következő megcimkézetlen esemény megnyitása")
        } else if !vm.activeEvents.isEmpty {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                Text("Minden esemény megcimkézve")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(.green)
            }
            .padding(.vertical, 14)
            .frame(maxWidth: .infinity)
            .background(Color(.systemBackground))
        }
    }

    // MARK: — Event routing (P2B-5D)

    // Opens EventLabelDetailView for exactly the requested event.
    //
    // .unlabeled: calls markEventForLabeling() to transition only this event
    //   to .labelPending before opening — does NOT touch other unlabeled events.
    // .labelPending / .localOnly / .synced / .retryPending / .failedPermanent:
    //   opens directly with selectedEventId = id.
    // Blocked (.syncing / .updating / .deleting / .conflicted /
    //          .needsReconciliation / .deleted): no-op, detail stays closed.
    private func handleOpenEvent(id: UUID) {
        guard let draft = vm.activeEvents.first(where: { $0.deviceEventId == id }) else { return }

        switch draft.syncStatus {
        case .unlabeled:
            guard vm.markEventForLabeling(deviceEventId: id) else { return }
            selectedEventId = id
        case .labelPending, .localOnly, .synced, .retryPending, .failedPermanent:
            selectedEventId = id
        case .syncing, .updating, .deleting, .conflicted, .needsReconciliation, .deleted:
            return
        }
    }

    // MARK: — Thumbnail loading (lazy, on card appear)

    private func loadThumbnail(for draft: ContactEventDraft) {
        guard let videoURL else { return }
        let id = draft.deviceEventId
        guard thumbnails[id] == nil, !loadingIds.contains(id) else { return }

        loadingIds.insert(id)
        let asset   = AVAsset(url: videoURL)
        let ms      = draft.timestampMs
        let videoId = vm.videoId

        let task = Task {
            let img = await thumbSession.generator.image(for: asset, videoId: videoId, timestampMs: ms)
            guard !Task.isCancelled else { return }
            thumbnails[id] = img
            loadingIds.remove(id)
        }
        thumbSession.loadTasks[id] = task
    }
}

// MARK: — EventOverviewCard

private struct EventOverviewCard: View {

    let draft:              ContactEventDraft
    let taxonomy:           TaxonomyDocument?
    let thumbnail:          UIImage?
    let isLoadingThumbnail: Bool
    let onCTA:              () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            thumbnailView
            infoStack
            Spacer(minLength: 4)
            ctaButton
        }
        .frame(minHeight: 72)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(cardAccessibilityLabel)
    }

    // MARK: — Thumbnail (80×60 pt)

    private var thumbnailView: some View {
        ZStack {
            Color.black

            if let img = thumbnail {
                Image(uiImage: img)
                    .resizable()
                    .scaledToFill()
            } else if isLoadingThumbnail {
                ProgressView()
                    .progressViewStyle(CircularProgressViewStyle(tint: .white))
                    .scaleEffect(0.75)
            } else {
                Image(systemName: "photo.slash")
                    .foregroundColor(Color(.systemGray3))
                    .font(.system(size: 16))
            }
        }
        .frame(width: 80, height: 60)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(
            RoundedRectangle(cornerRadius: 6)
                .stroke(Color(.systemGray5), lineWidth: 1)
        )
        .accessibilityHidden(true)
    }

    // MARK: — Info stack

    private var infoStack: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Image(systemName: "clock")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Text(PlaybackControlBar.formatTimestamp(ms: draft.timestampMs))
                    .font(.caption2.monospacedDigit())
                    .foregroundColor(.secondary)
            }

            Text(typeLabel)
                .font(.subheadline)
                .foregroundColor(draft.contactType != nil ? .primary : Color(.tertiaryLabel))
                .lineLimit(1)

            HStack(spacing: 4) {
                Circle()
                    .fill(EventTimelineView.pinColor(for: draft.syncStatus))
                    .frame(width: 6, height: 6)
                Text(statusLabel)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                if let side = draft.side {
                    Text("·")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    Text(side == "left" ? "Bal" : side == "right" ? "Jobb" : side)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }
        }
    }

    private var typeLabel: String {
        guard let key = draft.contactType else { return "Nincs cimkézve" }
        return taxonomy?.groups
            .flatMap { $0.contactTypes }
            .first { $0.key == key }?
            .labelHu ?? key
    }

    private var statusLabel: String {
        switch draft.syncStatus {
        case .unlabeled:           return "Nem jelölt"
        case .labelPending:        return "Folyamatban"
        case .localOnly:           return "Helyi"
        case .syncing:             return "Szinkronizálás…"
        case .synced:              return "Szinkronizálva"
        case .updating:            return "Frissítés…"
        case .deleting:            return "Törlés…"
        case .deleted:             return "Törölve"
        case .failedPermanent:     return "Hiba"
        case .retryPending:        return "Újrapróbálás"
        case .conflicted:          return "Konfliktus"
        case .needsReconciliation: return "Ellenőrzés szükséges"
        }
    }

    // MARK: — CTA button

    @ViewBuilder
    private var ctaButton: some View {
        let (label, enabled) = ctaConfig
        if case .inFlight = ctaState {
            ProgressView()
                .progressViewStyle(CircularProgressViewStyle(tint: Color(.systemGray3)))
                .scaleEffect(0.75)
                .frame(width: 56, height: 32)
        } else {
            Button(label) {
                if enabled { onCTA() }
            }
            .font(.caption.weight(.semibold))
            .foregroundColor(enabled ? .accentColor : Color(.systemGray3))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .frame(minWidth: 56)
            .background(Color(.systemGray6))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .disabled(!enabled)
            .accessibilityLabel("\(label), \(typeLabel)")
        }
    }

    private enum CTAState { case action, inFlight }
    private var ctaState: CTAState {
        switch draft.syncStatus {
        case .syncing, .updating, .deleting: return .inFlight
        default:                             return .action
        }
    }

    private var ctaConfig: (label: String, enabled: Bool) {
        switch draft.syncStatus {
        case .unlabeled:           return ("Cimkézés",    true)
        case .labelPending:        return ("Folytatás",   true)
        case .localOnly:           return ("Szerkesztés", true)
        case .synced:              return ("Szerkesztés", true)
        case .retryPending:        return ("Szerkesztés", true)
        case .failedPermanent:     return ("Újra",        true)
        case .needsReconciliation: return ("Szerkesztés", true)
        case .conflicted:          return ("Feloldás",    false) // AN-3C scope
        case .syncing, .updating, .deleting, .deleted:
                                   return ("…",           false)
        }
    }

    // MARK: — Accessibility

    private var cardAccessibilityLabel: String {
        let time = PlaybackControlBar.formatTimestamp(ms: draft.timestampMs)
        return "\(time), \(typeLabel), \(statusLabel)"
    }
}
