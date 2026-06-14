import SwiftUI
import AVFoundation

// MARK: — StillFrameSession (AN-3B2A P2B-3)
//
// Owns one EventStillFrameGenerator whose cache survives for the full
// labeling sheet session (multiple events), and is cleared on sheet close.
// Held as @StateObject so it persists across view redraws.

@MainActor
private final class StillFrameSession: ObservableObject {
    let generator = EventStillFrameGenerator()
    var loadTask: Task<Void, Never>?

    func cancelLoad() {
        loadTask?.cancel()
        loadTask = nil
    }

    func clearAll() {
        cancelLoad()
        generator.clearCache()
    }
}

// MARK: — EventLabelDetailView (AN-3B2A P2B-1/P2B-3/P2B-4/P2B-5C)
//
// Step-by-step labeling / re-labeling flow.
//
// Opening modes:
//   startingEventId == nil  → first-session flow; queue = .labelPending + .localOnly
//   startingEventId != nil  → overview-initiated; queue = all editable states;
//                             initial position = startingEventId (fallback: index 0)
//
// P2B-4 default flow:
//   still frame → body-zone picker → filtered contact type chips
//   → confidence → "Mentés és tovább"
// "Egyéb / Lista nézet" falls through to the full taxonomy list.
// "Vissza az ábrához" from either detail view returns to the body picker.
//
// Callbacks (P2B-5C):
//   onBack  — returns to the overview; does NOT call exitLabelingMode()
//   onClose — closes the entire labeling flow; calls exitLabelingMode()
//
// Write path: vm.relabelEvent() routes .labelPending/.localOnly → labelEvent(),
//             .synced/.retryPending/.failedPermanent → editEvent().
// Blocked states (.syncing/.updating/.deleting/.conflicted/etc.) keep canSave=false.
// No backend sync or Finish flow is triggered here.

struct EventLabelDetailView: View {
    @ObservedObject var vm: JugglingAnnotationViewModel
    var videoURL:       URL?
    var startingEventId: UUID? = nil   // P2B-5C: nil = first session, non-nil = overview
    var onBack:  (() -> Void)? = nil   // P2B-5C: back to overview (no exitLabelingMode)
    var onClose: () -> Void            // closes entire labeling flow

    @StateObject private var frameSession = StillFrameSession()

    @State private var queue: [UUID] = []
    @State private var currentIndex: Int = 0

    @State private var selectedKey:       String? = nil
    @State private var selectedSide:      String? = nil
    @State private var confidence:        String  = "certain"
    @State private var customLabel:       String  = ""
    @State private var customDescription: String  = ""

    @State private var showSaveErrorAlert   = false
    // P2B-5D: true when startingEventId was supplied but not found in the queue
    // (e.g. event was deleted or in a permanently blocked state). Shows a safe
    // error view instead of silently jumping to another event.
    @State private var targetEventMissing   = false

    // P2B-3 — still frame state
    @State private var stillImage:     UIImage? = nil
    @State private var isLoadingFrame: Bool     = false

    // P2B-4 — body zone picker state
    @State private var selectedBodyZone:     BodyZone? = nil
    @State private var showTaxonomyFallback: Bool      = false

    // MARK: — Body

    var body: some View {
        NavigationView {
            Group {
                if targetEventMissing {
                    missingEventView
                } else if currentDraft != nil {
                    labelingView
                } else {
                    completionView
                }
            }
            .navigationTitle(navigationTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button { closeAll() } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 16, weight: .medium))
                    }
                    .accessibilityLabel("Bezárás")
                }
            }
            .alert(isPresented: $showSaveErrorAlert) {
                Alert(
                    title: Text("Mentési hiba"),
                    message: Text(vm.saveError ?? "A mentés sikertelen."),
                    dismissButton: .default(Text("OK")) { vm.clearSaveError() }
                )
            }
        }
        .navigationViewStyle(.stack)
        .onAppear { setUpQueue() }
        .onChange(of: currentIndex) { _ in loadFrameForCurrentDraft() }
        .onChange(of: selectedBodyZone) { zone in
            guard let zone = zone else { return }
            handleZoneSelection(zone)
        }
    }

    // MARK: — Top-level labeling layout

    @ViewBuilder
    private var labelingView: some View {
        VStack(spacing: 0) {
            stillFramePreview        // fixed — non-scrolling
            timestampRow             // fixed — non-scrolling
            Divider()
            if showTaxonomyFallback {
                taxonomyListView     // full list + "← Vissza az ábrához"
            } else if let zone = selectedBodyZone {
                zoneDetailView(zone) // filtered types + "← Vissza az ábrához"
            } else {
                bodyPickerView       // BodyZonePickerView + "Egyéb" button
            }
        }
    }

    // MARK: — Body zone picker screen

    private var bodyPickerView: some View {
        VStack(spacing: 0) {
            BodyZonePickerView(selectedZone: $selectedBodyZone, taxonomy: vm.taxonomy)
                .frame(maxWidth: .infinity)
                .frame(height: 220)
                .padding(.horizontal, 8)
                .padding(.vertical, 8)

            Divider()

            Button {
                showTaxonomyFallback = true
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "list.bullet")
                    Text("Egyéb / Lista nézet")
                }
                .font(.subheadline)
                .foregroundColor(.secondary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .accessibilityLabel("Lista nézet — összes kontakt típus")

            Spacer()
        }
    }

    // MARK: — Zone detail: filtered types for the selected zone

    @ViewBuilder
    private func zoneDetailView(_ zone: BodyZone) -> some View {
        List {
            Section {
                Button {
                    selectedBodyZone = nil
                } label: {
                    Label("Vissza az ábrához", systemImage: "chevron.left")
                        .foregroundColor(.accentColor)
                }
                .accessibilityLabel("Vissza a testrész-kiválasztóhoz")
            }

            if let doc = vm.taxonomy {
                let types = zone.contactTypes(in: doc)
                if !types.isEmpty {
                    Section(header: Text(zone.labelHu)) {
                        ForEach(types) { type in
                            typeRow(type)
                        }
                    }
                }
            }

            confidenceSection
            if needsCustomLabel        { customLabelSection }
            if needsCustomDescription  { customDescSection }
            navigationSection
        }
        .listStyle(.insetGrouped)
    }

    // MARK: — Taxonomy fallback: full list

    @ViewBuilder
    private var taxonomyListView: some View {
        List {
            Section {
                Button {
                    showTaxonomyFallback = false
                    selectedBodyZone = nil
                } label: {
                    Label("Vissza az ábrához", systemImage: "chevron.left")
                        .foregroundColor(.accentColor)
                }
                .accessibilityLabel("Vissza a testrész-kiválasztóhoz")
            }

            if let doc = vm.taxonomy {
                ForEach(doc.groups.sorted { $0.groupSortOrder < $1.groupSortOrder }) { group in
                    Section(header: groupHeader(group)) {
                        ForEach(group.contactTypes.sorted { $0.sortOrder < $1.sortOrder }) { type in
                            typeRow(type)
                        }
                    }
                }
            } else {
                Section {
                    Text("Taxonomy betöltése…")
                        .foregroundColor(.secondary)
                }
            }

            confidenceSection
            if needsCustomLabel        { customLabelSection }
            if needsCustomDescription  { customDescSection }
            navigationSection
        }
        .listStyle(.insetGrouped)
    }

    // MARK: — Still frame preview (P2B-3)

    private let stillFrameHeight: CGFloat = 180

    @ViewBuilder
    private var stillFramePreview: some View {
        ZStack {
            Color.black

            if let image = stillImage {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
            } else if isLoadingFrame {
                ProgressView()
                    .progressViewStyle(CircularProgressViewStyle(tint: .white))
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "photo.slash")
                        .font(.system(size: 28))
                        .foregroundColor(Color(.systemGray3))
                    Text("Előnézet nem elérhető")
                        .font(.caption)
                        .foregroundColor(Color(.systemGray3))
                }
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: stillFrameHeight)
        .clipped()
        .accessibilityHidden(true)
    }

    // MARK: — Timestamp row

    private var timestampRow: some View {
        HStack(spacing: 8) {
            Image(systemName: "clock")
                .foregroundColor(.secondary)
            Text(PlaybackControlBar.formatTimestamp(ms: currentDraft?.timestampMs ?? 0))
                .font(.headline.monospacedDigit())
            Spacer()
            statusBadge
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color(.systemBackground))
    }

    // P2B-5C: Expanded status badge for all reachable sync states.
    @ViewBuilder
    private var statusBadge: some View {
        let status = currentDraft?.syncStatus ?? .labelPending
        let (label, color) = statusBadgeStyle(for: status)
        Text(label)
            .font(.caption.weight(.semibold))
            .foregroundColor(.white)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color)
            .clipShape(Capsule())
    }

    private func statusBadgeStyle(for status: ContactEventSyncStatus) -> (String, Color) {
        switch status {
        case .unlabeled:           return ("Jelölésre vár",  Color(.systemGray))
        case .labelPending:        return ("Cimkézésre vár", Color(.systemGray))
        case .localOnly:           return ("Cimkézve",       Color.green)
        case .syncing:             return ("Szinkronizálás…", Color.orange)
        case .synced:              return ("Szinkronizálva", Color.blue)
        case .updating:            return ("Frissítés…",     Color.orange)
        case .deleting:            return ("Törlés…",        Color.orange)
        case .deleted:             return ("Törölve",        Color(.systemGray))
        case .failedPermanent:     return ("Hiba",           Color.red)
        case .retryPending:        return ("Újrapróbálás",   Color.orange)
        case .conflicted:          return ("Konfliktus",     Color.red)
        case .needsReconciliation: return ("Ellenőrzés",     Color.orange)
        }
    }

    // MARK: — Queue / current draft

    private var navigationTitle: String {
        guard !queue.isEmpty, currentIndex < queue.count else { return "Cimkézés kész" }
        return "Cimkézés (\(currentIndex + 1)/\(queue.count))"
    }

    private var currentDraft: ContactEventDraft? {
        guard currentIndex >= 0, currentIndex < queue.count else { return nil }
        let id = queue[currentIndex]
        return vm.activeEvents.first { $0.deviceEventId == id }
    }

    // P2B-5C: Queue filter depends on whether we were opened from the overview.
    //   startingEventId == nil → first-session: .labelPending + .localOnly only
    //   startingEventId != nil → overview access: all editable states
    // After building the queue, position to startingEventId (fallback: index 0).
    private func setUpQueue() {
        guard queue.isEmpty else { return }

        let isEditRevisit = startingEventId != nil
        queue = vm.activeEvents
            .filter { d in
                if isEditRevisit {
                    switch d.syncStatus {
                    case .labelPending, .localOnly, .synced, .retryPending, .failedPermanent:
                        return true
                    default:
                        return false
                    }
                } else {
                    return d.syncStatus == .labelPending || d.syncStatus == .localOnly
                }
            }
            .sorted { $0.timestampMs < $1.timestampMs }
            .map { $0.deviceEventId }

        // P2B-5D: When opened for a specific event, that event MUST be in the queue.
        // If it is not (deleted, blocked state, or never transitioned from .unlabeled),
        // show the missing-event safety screen instead of silently jumping elsewhere.
        if let startId = startingEventId {
            guard let idx = queue.firstIndex(of: startId) else {
                targetEventMissing = true
                return
            }
            currentIndex = idx
        } else {
            currentIndex = 0
        }

        loadFormState()
        loadFrameForCurrentDraft()
    }

    private func loadFormState() {
        guard let draft = currentDraft else { return }
        selectedKey       = draft.contactType
        selectedSide      = draft.side
        confidence        = draft.annotationConfidence
        customLabel       = draft.customLabel ?? ""
        customDescription = draft.customDescription ?? ""
        if selectedSide == nil, let type = currentType {
            selectedSide = Self.autoSide(for: type)
        }
        restoreBodyZone()
    }

    // Reverse-lookup: which body zone owns the current selectedKey?
    // Sets selectedBodyZone / showTaxonomyFallback for the picker routing.
    private func restoreBodyZone() {
        guard let key = selectedKey else {
            selectedBodyZone     = nil
            showTaxonomyFallback = false
            return
        }
        if let doc = vm.taxonomy {
            for zone in BodyZone.allCases {
                if zone.contactTypes(in: doc).contains(where: { $0.key == key }) {
                    selectedBodyZone     = zone
                    showTaxonomyFallback = false
                    return
                }
            }
        }
        // Key is "back", "custom_other", or taxonomy not yet loaded — use list
        selectedBodyZone     = nil
        showTaxonomyFallback = true
    }

    // Auto-select contact type when a zone has only one type, or preserve
    // existing key if it already belongs to the selected zone.
    private func handleZoneSelection(_ zone: BodyZone) {
        guard let doc = vm.taxonomy else { return }
        let types = zone.contactTypes(in: doc)
        guard !types.isEmpty else { return }

        if types.count == 1, let single = types.first {
            selectedKey  = single.key
            selectedSide = Self.autoSide(for: single)
        } else {
            // Multi-type zone: only clear if the current key is from a different zone
            if let key = selectedKey, !types.contains(where: { $0.key == key }) {
                selectedKey  = nil
                selectedSide = nil
            }
        }
    }

    // MARK: — Still frame loading (P2B-3)

    private func loadFrameForCurrentDraft() {
        frameSession.cancelLoad()
        stillImage     = nil
        isLoadingFrame = false

        guard let videoURL, let draft = currentDraft else { return }

        isLoadingFrame = true
        let asset   = AVAsset(url: videoURL)
        let ms      = draft.timestampMs
        let videoId = vm.videoId

        frameSession.loadTask = Task {
            let img = await frameSession.generator.image(for: asset, videoId: videoId, timestampMs: ms)
            guard !Task.isCancelled else { return }
            stillImage     = img
            isLoadingFrame = false
        }
    }

    // MARK: — Taxonomy row helpers

    @ViewBuilder
    private func groupHeader(_ group: TaxonomyGroup) -> some View {
        Label(group.groupLabelHu, systemImage: group.iosIcon ?? "circle")
            .accessibilityLabel(group.groupLabelHu)
    }

    @ViewBuilder
    private func typeRow(_ type: TaxonomyContactType) -> some View {
        let isSelected = selectedKey == type.key
        Button {
            toggleType(type)
        } label: {
            HStack(spacing: 12) {
                Image(systemName: type.iosIcon ?? "circle.fill")
                    .frame(width: 24)
                    .foregroundColor(isSelected ? .accentColor : .secondary)

                VStack(alignment: .leading, spacing: 2) {
                    Text(type.labelHu)
                        .foregroundColor(.primary)
                    Text(type.labelEn)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Spacer()

                if isSelected && type.sidePolicy == "explicit_required" {
                    sideToggleButtons
                        .padding(.trailing, 4)
                }

                if isSelected {
                    Image(systemName: "checkmark")
                        .foregroundColor(.accentColor)
                        .font(.caption.weight(.bold))
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .frame(minHeight: 52)
        .accessibilityLabel(accessibilityLabel(for: type))
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var sideToggleButtons: some View {
        HStack(spacing: 6) {
            sideButton(label: "B", value: "left",  accessLabel: "Bal")
            sideButton(label: "J", value: "right", accessLabel: "Jobb")
        }
    }

    private func sideButton(label: String, value: String, accessLabel: String) -> some View {
        Button(label) {
            selectedSide = (selectedSide == value) ? nil : value
        }
        .font(.caption.weight(.bold))
        .frame(width: 36, height: 36)
        .background(selectedSide == value ? Color.accentColor : Color(.systemGray5))
        .foregroundColor(selectedSide == value ? .white : .primary)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .accessibilityLabel(accessLabel)
        .accessibilityAddTraits(selectedSide == value ? .isSelected : [])
    }

    private var confidenceSection: some View {
        Section(header: Text("Bizonyosság")) {
            Picker("Bizonyosság", selection: $confidence) {
                Text("Biztos").tag("certain")
                Text("Valószínű").tag("probable")
                Text("Bizonytalan").tag("uncertain")
            }
            .pickerStyle(.segmented)
            .padding(.vertical, 4)
            .accessibilityLabel("Bizonyosság szint")
        }
    }

    private var customLabelSection: some View {
        Section(header: Text("Egyedi label (kötelező)")) {
            TextField("pl. belső csüd", text: $customLabel)
                .accessibilityLabel("Egyedi label szöveges mező")
        }
    }

    private var customDescSection: some View {
        Section(header: Text("Leírás")) {
            TextField("Rövid leírás (opcionális)", text: $customDescription)
                .accessibilityLabel("Leírás szöveges mező")
        }
    }

    // MARK: — Navigation row (Vissza event / Mentés és tovább)

    // P2B-5C: "Vissza" at index 0 calls onBack (overview) when available,
    // otherwise remains disabled (first-session behaviour unchanged).
    @ViewBuilder
    private var navigationSection: some View {
        Section {
            HStack(spacing: 12) {
                backButton
                Button(isLastInQueue ? "Mentés és befejezés" : "Mentés és tovább") {
                    saveAndAdvance()
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .foregroundColor(canSave ? .white : .secondary)
                .background(canSave ? Color.accentColor : Color(.systemGray5))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .disabled(!canSave)
                .accessibilityLabel(isLastInQueue ? "Mentés és befejezés" : "Mentés és a következő esemény")
            }
            .listRowInsets(EdgeInsets())
            .padding(.horizontal, 16)
            .padding(.vertical, 4)
        }
    }

    @ViewBuilder
    private var backButton: some View {
        if currentIndex > 0 {
            Button("Vissza") {
                goToPrevious()
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .foregroundColor(.accentColor)
            .accessibilityLabel("Előző esemény")
        } else if onBack != nil {
            // At first position and opened from overview: go back to the list.
            Button("← Áttekintő") {
                navigateBack()
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .foregroundColor(.accentColor)
            .accessibilityLabel("Vissza az áttekintőhöz")
        } else {
            // First-session: no back navigation available.
            Button("Vissza") { }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .foregroundColor(.secondary)
                .disabled(true)
                .accessibilityLabel("Előző esemény")
        }
    }

    private var isLastInQueue: Bool {
        currentIndex >= queue.count - 1
    }

    // MARK: — Completion view

    @ViewBuilder
    private var completionView: some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 48))
                .foregroundColor(.green)
            Text(queue.isEmpty ? "Nincs cimkézendő esemény" : "Minden esemény megcimkézve")
                .font(.headline)
            // P2B-5C: if opened from overview, return to it; otherwise close.
            Button(onBack != nil ? "Vissza az áttekintőhöz" : "Vissza a videóhoz") {
                navigateBack()
            }
            .font(.body.weight(.semibold))
            .foregroundColor(.white)
            .padding(.horizontal, 24)
            .padding(.vertical, 10)
            .background(Color.accentColor)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .accessibilityLabel(onBack != nil ? "Vissza az áttekintőhöz" : "Vissza a videóhoz")
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: — Missing event safety view (P2B-5D)
    //
    // Shown when startingEventId was supplied but not present in the built queue.
    // Does not fall back to another event. Offers only a safe back/close action.

    @ViewBuilder
    private var missingEventView: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 40))
                .foregroundColor(.orange)
            Text("Az esemény nem érhető el")
                .font(.headline)
            Text("Az esemény törlődött vagy jelenleg nem szerkeszthető.")
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Button(onBack != nil ? "Vissza az áttekintőhöz" : "Bezárás") {
                navigateBack()
            }
            .font(.body.weight(.semibold))
            .foregroundColor(.white)
            .padding(.horizontal, 24)
            .padding(.vertical, 10)
            .background(Color.accentColor)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: — Validation

    private var currentType: TaxonomyContactType? {
        guard let key = selectedKey else { return nil }
        return vm.taxonomy?.groups.flatMap { $0.contactTypes }.first { $0.key == key }
    }

    private var needsCustomLabel:       Bool { currentType?.requiresCustomLabel        == true }
    private var needsCustomDescription: Bool { currentType?.requiresCustomDescription   == true }

    // P2B-5C: Block save for in-flight / unresolvable sync states.
    // relabelEvent() would also return false for these, but blocking at canSave
    // keeps the button clearly disabled so the user is not confused.
    private var isBlocked: Bool {
        guard let status = currentDraft?.syncStatus else { return false }
        switch status {
        case .syncing, .updating, .deleting,
             .conflicted, .needsReconciliation,
             .deleted, .unlabeled:
            return true
        default:
            return false
        }
    }

    private var canSave: Bool {
        guard !isBlocked else { return false }
        guard selectedKey != nil else { return false }
        if currentType?.sidePolicy == "explicit_required" && selectedSide == nil { return false }
        if needsCustomLabel && customLabel.trimmingCharacters(in: .whitespaces).isEmpty { return false }
        return true
    }

    private static func autoSide(for type: TaxonomyContactType) -> String? {
        switch type.sidePolicy {
        case "fixed", "center": return type.side
        default:                return nil
        }
    }

    // MARK: — Interaction

    private func toggleType(_ type: TaxonomyContactType) {
        if selectedKey == type.key {
            selectedKey  = nil
            selectedSide = nil
        } else {
            selectedKey  = type.key
            selectedSide = Self.autoSide(for: type)
        }
    }

    // P2B-5C: Use relabelEvent() instead of labelEvent() so that already-synced
    // events (accessed from the overview) are correctly routed through editEvent().
    private func saveAndAdvance() {
        guard let draft = currentDraft, let key = selectedKey else { return }
        let label = customLabel.trimmingCharacters(in: .whitespaces)
        let desc  = customDescription.trimmingCharacters(in: .whitespaces)

        let ok = vm.relabelEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          key,
            side:                 selectedSide,
            annotationConfidence: confidence,
            customLabel:          label.isEmpty ? nil : label,
            customDescription:    desc.isEmpty  ? nil : desc
        )
        guard ok else {
            showSaveErrorAlert = true
            return
        }
        currentIndex += 1
        if currentIndex < queue.count { loadFormState() }
    }

    private func goToPrevious() {
        guard currentIndex > 0 else { return }
        currentIndex -= 1
        loadFormState()
    }

    // P2B-5C: navigateBack — returns to overview without exitLabelingMode.
    // Falls back to closeAll() when onBack is nil (first-session mode).
    private func navigateBack() {
        frameSession.cancelLoad()
        if let onBack = onBack {
            onBack()
        } else {
            closeAll()
        }
    }

    // Closes the entire labeling flow: clears cache, exits labeling mode, calls onClose.
    private func closeAll() {
        frameSession.clearAll()
        vm.exitLabelingMode()
        onClose()
    }

    private func accessibilityLabel(for type: TaxonomyContactType) -> String {
        var parts = [type.labelHu, type.labelEn]
        if type.sidePolicy == "explicit_required"  { parts.append("Oldal szükséges") }
        if type.requiresCustomLabel == true         { parts.append("Egyedi label szükséges") }
        return parts.joined(separator: ", ")
    }
}
