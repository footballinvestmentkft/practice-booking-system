import SwiftUI
import AVFoundation

// MARK: — EventLabelDetailView (AN-3B2A P2B-1/P2B-3/P2B-4/P2B-5C/P2C-1/SILO-2)
//
// Step-by-step labeling / re-labeling flow.
//
// Opening modes (P2C-FLOW-3):
//   startingEventId == nil          → sequential; queue = all .labelPending
//   startingEventId with .labelPending target → sequential; positioned at target
//   startingEventId with .localOnly/.synced/.retryPending/.failedPermanent → singleEdit;
//       queue = [startId]; save → navigateBack()
//   Blocked states → targetEventMissing safety view
//
// SILO-2 layout (replaces 3-branch picker / zone-detail / taxonomy-list approach):
//   1. loopPreview        — fixed; adaptive height (240pt normal / 200pt small screen)
//   2. timestampRow       — fixed
//   3. scrollableZoneBody — ScrollView: EmojiBodyZonePickerView OR taxonomy fallback,
//                           plus custom label/description fields
//   4. pinnedBottomBar    — always visible; confidence segmented picker + back + save
//
// Callbacks (P2B-5C):
//   onBack  — returns to the overview; does NOT call exitLabelingMode()
//   onClose — closes the entire labeling flow; calls exitLabelingMode()
//
// Write path: vm.relabelEvent() routes .labelPending/.localOnly → labelEvent(),
//             .synced/.retryPending/.failedPermanent → editEvent().
// Blocked states (.syncing/.updating/.deleting/.conflicted/etc.) keep canSave=false.

struct EventLabelDetailView: View {
    @ObservedObject var vm: JugglingAnnotationViewModel
    var videoURL:       URL?
    var startingEventId: UUID? = nil   // P2B-5C: nil = first session, non-nil = overview
    var onBack:  (() -> Void)? = nil   // P2B-5C: back to overview (no exitLabelingMode)
    var onClose: () -> Void            // closes entire labeling flow

    @StateObject private var previewSession = EventPreviewSession()

    @State private var queue: [UUID] = []
    @State private var currentIndex: Int = 0

    @State private var selectedKey:       String? = nil
    @State private var selectedSide:      String? = nil
    @State private var confidence:        String  = "certain"
    @State private var customLabel:       String  = ""
    @State private var customDescription: String  = ""

    @State private var showSaveErrorAlert   = false
    // P2B-5D: true when startingEventId was supplied but not found in the queue.
    @State private var targetEventMissing   = false

    // P2B-4 — body zone picker state (used by EmojiBodyZonePickerView)
    @State private var selectedBodyZone:     BodyZone? = nil
    @State private var showTaxonomyFallback: Bool      = false

    // P2C-FLOW-1/3 — labeling mode + double-save guard.
    enum LabelingDetailMode: Equatable {
        case sequential   // .labelPending events only, auto-advance through queue
        case singleEdit   // .localOnly/.synced/.retryPending/.failedPermanent — single event, save → back
    }
    @State private var mode:     LabelingDetailMode = .sequential
    @State private var isSaving: Bool               = false

    // AN-3B2C-1 — ball detection section error toast (nil = no error shown).
    @State private var ballDetectionError: String? = nil

    // MARK: — Body

    var body: some View {
        NavigationView {
            Group {
                if targetEventMissing {
                    missingEventView
                } else if currentDraft != nil {
                    labelingView
                } else if mode == .singleEdit {
                    missingEventView
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
        .onDisappear { previewSession.stop() }
        .onChange(of: currentIndex) { _ in
            loadPreviewForCurrentDraft()
            ballDetectionError = nil
            fetchBallDetectionForCurrent()
        }
    }

    // MARK: — Top-level labeling layout (SILO-2)
    //
    //   loopPreview (fixed, adaptive height)
    //   timestampRow (fixed)
    //   ─────────────────────────────────
    //   scrollableZoneBody (flexible)
    //   ─────────────────────────────────
    //   pinnedBottomBar (fixed)

    @ViewBuilder
    private var labelingView: some View {
        VStack(spacing: 0) {
            loopPreview
            timestampRow
            Divider()
            scrollableZoneBody
            Divider()
            pinnedBottomBar
        }
    }

    // MARK: — Scrollable zone body

    private var scrollableZoneBody: some View {
        ScrollView {
            VStack(spacing: 0) {
                if showTaxonomyFallback {
                    taxonomyFallbackContent
                } else {
                    emojiPickerContent
                }
                // AN-3B2C-1: ball detection secondary section (always shown when event is synced).
                if currentDraft?.serverEventId != nil {
                    Divider().padding(.top, 4)
                    ballDetectionSection
                }
            }
        }
    }

    // MARK: — Emoji picker section (default)

    private var emojiPickerContent: some View {
        VStack(spacing: 0) {
            EmojiBodyZonePickerView(
                selectedZone: $selectedBodyZone,
                selectedKey:  $selectedKey,
                selectedSide: $selectedSide,
                taxonomy:     vm.taxonomy,
                onZoneSelected: { zone in handleZoneSelection(zone) }
            )

            Divider()
                .padding(.horizontal, 16)
                .padding(.vertical, 2)

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

            if needsCustomLabel        { customLabelField }
            if needsCustomDescription  { customDescField }
        }
    }

    // MARK: — Taxonomy fallback (Egyéb / Lista nézet)

    @ViewBuilder
    private var taxonomyFallbackContent: some View {
        VStack(spacing: 0) {
            Button {
                showTaxonomyFallback = false
                selectedBodyZone     = nil
            } label: {
                Label("Vissza az ábrához", systemImage: "chevron.left")
                    .foregroundColor(.accentColor)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(Color(.systemBackground))
            .accessibilityLabel("Vissza a testrész-kiválasztóhoz")

            Divider()

            if let doc = vm.taxonomy {
                ForEach(doc.groups.sorted { $0.groupSortOrder < $1.groupSortOrder }) { group in
                    groupSectionHeader(group)
                    ForEach(group.contactTypes.sorted { $0.sortOrder < $1.sortOrder }) { type in
                        typeRow(type)
                        Divider()
                            .padding(.leading, 16)
                    }
                }
            } else {
                Text("Taxonomy betöltése…")
                    .foregroundColor(.secondary)
                    .padding(16)
            }

            if needsCustomLabel        { customLabelField }
            if needsCustomDescription  { customDescField }
        }
        .background(Color(.systemBackground))
    }

    @ViewBuilder
    private func groupSectionHeader(_ group: TaxonomyGroup) -> some View {
        HStack(spacing: 6) {
            Image(systemName: group.iosIcon ?? "circle")
                .font(.caption)
                .foregroundColor(.secondary)
            Text(group.groupLabelHu)
                .font(.footnote.weight(.semibold))
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .padding(.bottom, 4)
        .background(Color(.systemGroupedBackground))
        .accessibilityLabel(group.groupLabelHu)
    }

    // MARK: — Standalone custom fields (replaces Section-based versions)

    @ViewBuilder
    private var customLabelField: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Egyedi label (kötelező)")
                .font(.caption.weight(.semibold))
                .foregroundColor(.secondary)
                .padding(.horizontal, 16)
            TextField("pl. belső csüd", text: $customLabel)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal, 16)
        }
        .padding(.top, 12)
        .padding(.bottom, 4)
        .background(Color(.systemBackground))
        .accessibilityLabel("Egyedi label szöveges mező")
    }

    @ViewBuilder
    private var customDescField: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Leírás")
                .font(.caption.weight(.semibold))
                .foregroundColor(.secondary)
                .padding(.horizontal, 16)
            TextField("Rövid leírás (opcionális)", text: $customDescription)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal, 16)
        }
        .padding(.top, 4)
        .padding(.bottom, 12)
        .background(Color(.systemBackground))
        .accessibilityLabel("Leírás szöveges mező")
    }

    // MARK: — Pinned bottom bar (SILO-2)
    //
    // Always visible below the scrollable body. Contains:
    //   • Confidence segmented picker
    //   • Back button (← Áttekintő / Vissza / disabled)
    //   • Save button (Mentés / Mentés és következő / Mentés és befejezés)

    private var pinnedBottomBar: some View {
        VStack(spacing: 8) {
            Picker("Bizonyosság", selection: $confidence) {
                Text("Biztos").tag("certain")
                Text("Valószínű").tag("probable")
                Text("Bizonytalan").tag("uncertain")
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .accessibilityLabel("Bizonyosság szint")

            HStack(spacing: 12) {
                backButton
                Button(saveButtonLabel) {
                    saveAndAdvance()
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .foregroundColor(canSave ? .white : .secondary)
                .background(canSave ? Color.accentColor : Color(.systemGray5))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .disabled(!canSave)
                .accessibilityLabel(saveButtonLabel)
            }
            .padding(.horizontal, 16)
        }
        .padding(.vertical, 10)
        .background(Color(.systemBackground))
        .shadow(color: Color.black.opacity(0.06), radius: 4, x: 0, y: -2)
    }

    // MARK: — Loop preview (P2C-1, SILO-2: adaptive height)

    // SILO-2: adaptive preview height — 240pt for standard iPhones (screen height > 667pt),
    // 200pt for compact (iPhone SE 2nd gen: 667pt and below). Keeps preview primary on all
    // screen sizes without crowding the zone picker on small devices.
    static func previewHeight(for screenHeight: CGFloat) -> CGFloat {
        screenHeight <= 667 ? 200 : 240
    }
    private var adaptivePreviewHeight: CGFloat {
        Self.previewHeight(for: UIScreen.main.bounds.height)
    }

    @ViewBuilder
    private var loopPreview: some View {
        ZStack {
            Color.black
            AVPlayerLayerView(player: previewSession.player)
                .disabled(true)

            if previewSession.isLoading {
                ProgressView()
                    .progressViewStyle(CircularProgressViewStyle(tint: .white))
            } else if previewSession.hasError {
                VStack(spacing: 8) {
                    Image(systemName: "photo.slash")
                        .font(.system(size: 28))
                        .foregroundColor(Color(.systemGray3))
                    Text("Előnézet nem elérhető")
                        .font(.caption)
                        .foregroundColor(Color(.systemGray3))
                }
            }

            // AN-3B2C-1: ball position overlay (rendered above video, below controls).
            if let serverEventId = currentDraft?.serverEventId,
               case .loaded(let detection) = vm.ballDetections[serverEventId] {
                BallOverlayView(
                    detection:    detection,
                    isDragEnabled: ballDragEnabled(detection: detection)
                ) { nx, ny in
                    ballDetectionError = nil
                    Task {
                        do {
                            try await vm.postManualBallPosition(
                                videoId: vm.videoId,
                                eventId: serverEventId,
                                x: nx, y: ny
                            )
                        } catch {
                            ballDetectionError = error.localizedDescription
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .allowsHitTesting(ballDragEnabled(detection: detection))
            }

            if !previewSession.isLoading, !previewSession.hasError {
                HStack(spacing: 20) {
                    Button {
                        previewSession.togglePlayPause()
                    } label: {
                        Image(systemName: previewSession.isPlaying ? "pause.fill" : "play.fill")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundColor(.white)
                            .frame(width: 44, height: 44)
                            .background(Color.black.opacity(0.5))
                            .clipShape(Circle())
                    }
                    .accessibilityLabel(previewSession.isPlaying ? "Szünet" : "Lejátszás")

                    Button {
                        previewSession.replay()
                    } label: {
                        Image(systemName: "backward.end.fill")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundColor(.white)
                            .frame(width: 44, height: 44)
                            .background(Color.black.opacity(0.5))
                            .clipShape(Circle())
                    }
                    .accessibilityLabel("Újrajátszás")
                }
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: adaptivePreviewHeight)
        .clipped()
        .accessibilityHidden(true)
    }

    // Ball is draggable only when position is known and not "no ball".
    private func ballDragEnabled(detection: BallDetectionOut) -> Bool {
        !detection.noBallDetected && detection.ballX != nil && detection.ballY != nil
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
        switch mode {
        case .singleEdit:
            return "Szerkesztés"
        case .sequential:
            guard !queue.isEmpty, currentIndex < queue.count else { return "Cimkézés kész" }
            return "Cimkézés (\(currentIndex + 1)/\(queue.count))"
        }
    }

    private var currentDraft: ContactEventDraft? {
        guard currentIndex >= 0, currentIndex < queue.count else { return nil }
        let id = queue[currentIndex]
        return vm.activeEvents.first { $0.deviceEventId == id }
    }

    private func setUpQueue() {
        guard queue.isEmpty else { return }

        if let startId = startingEventId {
            guard let draft = vm.activeEvents.first(where: { $0.deviceEventId == startId }) else {
                targetEventMissing = true
                return
            }
            mode = Self.detectMode(for: startId, syncStatus: draft.syncStatus)

            switch mode {
            case .sequential:
                queue = Self.sequentialQueueIds(from: vm.activeEvents)
                guard let idx = queue.firstIndex(of: startId) else {
                    targetEventMissing = true
                    return
                }
                currentIndex = idx

            case .singleEdit:
                queue = [startId]
                currentIndex = 0
            }
        } else {
            mode = .sequential
            queue = Self.sequentialQueueIds(from: vm.activeEvents)
            currentIndex = 0
        }

        loadFormState()
        loadPreviewForCurrentDraft()
        fetchBallDetectionForCurrent()
    }

    private func loadFormState() {
        guard let draft = currentDraft else { return }
        // Explicit reset before loading — prevents stale state from a previous draft
        // bleeding into the current one when navigating between events.
        selectedKey          = nil
        selectedSide         = nil
        selectedBodyZone     = nil
        showTaxonomyFallback = false
        customLabel          = ""
        customDescription    = ""
        confidence           = "certain"
        // Load from draft
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

    // P2C-FLOW-1: internal for unit tests.
    static func sequentialQueueIds(from events: [ContactEventDraft]) -> [UUID] {
        events
            .filter { $0.syncStatus == .labelPending }
            .sorted { $0.timestampMs < $1.timestampMs }
            .map { $0.deviceEventId }
    }

    // P2C-FLOW-3: internal for unit tests.
    static func detectMode(for startingEventId: UUID?,
                           syncStatus: ContactEventSyncStatus?) -> LabelingDetailMode {
        guard startingEventId != nil else { return .sequential }
        switch syncStatus {
        case .localOnly, .synced, .retryPending, .failedPermanent:
            return .singleEdit
        default:
            return .sequential
        }
    }

    // P2C-FLOW-3: mode-aware save button label.
    private var saveButtonLabel: String {
        switch mode {
        case .singleEdit:  return "Mentés"
        case .sequential:  return isLastInQueue ? "Mentés és befejezés" : "Mentés és következő"
        }
    }

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
        selectedBodyZone     = nil
        showTaxonomyFallback = true
    }

    private func handleZoneSelection(_ zone: BodyZone) {
        guard let doc = vm.taxonomy else { return }
        let types = zone.contactTypes(in: doc)
        guard !types.isEmpty else { return }

        if types.count == 1, let single = types.first {
            selectedKey  = single.key
            selectedSide = Self.autoSide(for: single)
        } else {
            if let key = selectedKey, !types.contains(where: { $0.key == key }) {
                selectedKey  = nil
                selectedSide = nil
            }
        }
    }

    // MARK: — Ball Detection Section (AN-3B2C-1)

    @ViewBuilder
    private var ballDetectionSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Labda detektálás")
                .font(.subheadline.weight(.semibold))
                .padding(.horizontal, 16)
                .padding(.top, 12)

            Group {
                if let serverEventId = currentDraft?.serverEventId {
                    let state = vm.ballDetections[serverEventId] ?? .notFetched
                    switch state {
                    case .notFetched:
                        Text("Betöltés…")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    case .fetching:
                        HStack(spacing: 8) {
                            ProgressView().progressViewStyle(CircularProgressViewStyle())
                            Text("Elemzés lekérése…")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    case .featureDisabled:
                        Text("Labda detektálás nem érhető el")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    case .notFound:
                        HStack(spacing: 8) {
                            ProgressView().progressViewStyle(CircularProgressViewStyle())
                            Text("Elemzés folyamatban…")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    case .networkError(let msg):
                        Text("Hálózati hiba: \(msg)")
                            .font(.caption)
                            .foregroundColor(.red)
                    case .loaded(let detection):
                        ballDetectionLoadedRow(detection: detection, serverEventId: serverEventId)
                    }
                }
            }
            .padding(.horizontal, 16)

            if let err = ballDetectionError {
                Text(err)
                    .font(.caption)
                    .foregroundColor(.red)
                    .padding(.horizontal, 16)
            }
        }
        .padding(.bottom, 12)
    }

    @ViewBuilder
    private func ballDetectionLoadedRow(detection: BallDetectionOut, serverEventId: UUID) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if detection.noBallDetected {
                Label("Nincs labda jelezve", systemImage: "xmark.circle")
                    .font(.caption)
                    .foregroundColor(.secondary)
                // Revert button
                Button {
                    ballDetectionError = nil
                    Task {
                        do {
                            if let ax = detection.autoBallX, let ay = detection.autoBallY {
                                try await vm.postManualBallPosition(
                                    videoId: vm.videoId, eventId: serverEventId, x: ax, y: ay
                                )
                            }
                            // No auto coords: can't revert automatically; user must drag on preview.
                        } catch {
                            ballDetectionError = error.localizedDescription
                        }
                    }
                } label: {
                    Label(
                        detection.autoBallX != nil ? "Labda volt — visszaállítás" : "Nincs visszaállítható pozíció",
                        systemImage: "arrow.uturn.backward"
                    )
                    .font(.caption)
                }
                .disabled(detection.autoBallX == nil)
                .foregroundColor(detection.autoBallX != nil ? .accentColor : .secondary)
            } else {
                if let conf = detection.confidence {
                    let pct = Int(conf * 100)
                    let src = detection.detectionSource == "manual" ? "manuális" : "auto"
                    Label("Pozíció: \(src), \(pct)% konfidencia", systemImage: "target")
                        .font(.caption)
                        .foregroundColor(.secondary)
                } else {
                    Label("Pozíció: manuális", systemImage: "target")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                // "No ball" button
                Button {
                    ballDetectionError = nil
                    Task {
                        do {
                            try await vm.markNoBall(videoId: vm.videoId, eventId: serverEventId)
                        } catch {
                            ballDetectionError = error.localizedDescription
                        }
                    }
                } label: {
                    Label("Nincs labda ezen a képkockán", systemImage: "xmark.circle")
                        .font(.caption)
                }
                .foregroundColor(.secondary)
            }
        }
    }

    private func fetchBallDetectionForCurrent() {
        guard let serverEventId = currentDraft?.serverEventId else { return }
        Task { await vm.fetchBallDetection(videoId: vm.videoId, eventId: serverEventId) }
    }

    private func loadPreviewForCurrentDraft() {
        guard let videoURL, let draft = currentDraft else {
            previewSession.stop()
            return
        }
        previewSession.restart(url: videoURL, timestampMs: draft.timestampMs)
    }

    // MARK: — Taxonomy row helpers (used in taxonomyFallbackContent)

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
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
        }
        .buttonStyle(.plain)
        .frame(minHeight: 52)
        .background(Color(.systemBackground))
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

    // MARK: — Navigation helpers

    @ViewBuilder
    private var backButton: some View {
        if currentIndex > 0 {
            Button("Vissza") {
                goToPrevious()
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .foregroundColor(.accentColor)
            .accessibilityLabel("Előző esemény")
        } else if onBack != nil {
            Button("← Áttekintő") {
                navigateBack()
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .foregroundColor(.accentColor)
            .accessibilityLabel("Vissza az áttekintőhöz")
        } else {
            Button("Vissza") { }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
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
        guard !isSaving else { return false }
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

    private func saveAndAdvance() {
        guard canSave else { return }
        isSaving = true

        guard let draft = currentDraft, let key = selectedKey else {
            isSaving = false
            return
        }
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
            isSaving = false
            showSaveErrorAlert = true
            return
        }

        switch mode {
        case .sequential:
            currentIndex += 1
            if currentIndex < queue.count { loadFormState() }
            isSaving = false
        case .singleEdit:
            isSaving = false
            navigateBack()
        }
    }

    private func goToPrevious() {
        guard currentIndex > 0 else { return }
        currentIndex -= 1
        loadFormState()
    }

    private func navigateBack() {
        previewSession.stop()
        if let onBack = onBack {
            onBack()
        } else {
            closeAll()
        }
    }

    private func closeAll() {
        previewSession.stop()
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
