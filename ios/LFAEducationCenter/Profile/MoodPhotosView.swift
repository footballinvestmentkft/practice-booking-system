import SwiftUI

// Mood Photos — upload up to 6 Phase-A mood expression photos.
//
// Presented as fullScreenCover from ProfileView.
// Phase-A (6 slots): tappable — upload / delete / BG removal
// Phase-B (3 slots): Coming Soon — not tappable, not completion-blocking
//
// Completion: phase_a_complete == true (all 6 Phase-A slots have original_url)
// On dismiss: onDismiss() → dashboardVM.reload() → ProfileCompletionScore +10
struct MoodPhotosView: View {

    @EnvironmentObject private var authManager: AuthManager
    @Environment(\.presentationMode) private var presentationMode

    let onDismiss: () -> Void

    @StateObject private var vm = MoodPhotosViewModel()

    // PhotoPicker state
    @State private var showingPickerForSlot: String? = nil
    @State private var pickerImage: UIImage? = nil

    // Source selection dialog + camera
    @State private var showingSourceDialog: Bool    = false
    @State private var pendingUploadSlot:   String? = nil
    @State private var showingCamera:       Bool    = false

    // Preview sheet state — uses fullScreenCover(item:) to avoid the iOS 14/15
    // nested-sheet bug that can dismiss this MoodPhotosView when a .sheet closes.
    @State private var previewSlot: MoodPhotoSlotData? = nil
    // When Replace is tapped in the preview, the picker opens after the
    // preview dismisses — pendingReplaceSlot carries the slot name across.
    @State private var pendingReplaceSlot: String? = nil

    var body: some View {
        NavigationView {
            Group {
                switch vm.loadState {
                case .idle, .loading:
                    loadingView
                case .loaded:
                    loadedView
                case .error(let msg):
                    errorView(msg)
                }
            }
            .navigationTitle("Mood Photos")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        onDismiss()
                        presentationMode.wrappedValue.dismiss()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Theme.Color.onSurface)
                    }
                }
            }
        }
        .navigationViewStyle(.stack)
        .onAppear { Task { await vm.load(using: authManager) } }
        // PhotoPicker sheet.
        // SwiftUI controls dismissal — the callback sets showingPickerForSlot=nil,
        // which makes the binding return false and triggers a SwiftUI-native sheet
        // close. This avoids the iOS 14/15 cascade bug where UIKit-side picker.dismiss()
        // also closed the parent MoodPhotosView fullScreenCover.
        .sheet(isPresented: Binding(
            get: { showingPickerForSlot != nil },
            set: { if !$0 { showingPickerForSlot = nil } }
        )) {
            ProfilePhotoPicker { imageOrNil in
                showingPickerForSlot = nil  // SwiftUI-safe: drives sheet close via binding
                if let image = imageOrNil {
                    pickerImage = image     // triggers onChange → upload
                }
                // imageOrNil == nil: user cancelled — showingPickerForSlot=nil closes sheet,
                // no upload triggered, MoodPhotosView stays open.
            }
        }
        // Upload when pickerImage arrives.
        // showingPickerForSlot may already be nil at this point (set in callback above);
        // lastPickedSlot provides the slot name as fallback.
        .onChange(of: pickerImage) { image in
            guard let image, let slot = showingPickerForSlot ?? lastPickedSlot else { return }
            lastPickedSlot = nil
            Task { await vm.upload(image: image, slot: slot, using: authManager) }
            pickerImage = nil
        }
        // Upload error alert (iOS 14-compatible)
        .alert(isPresented: Binding(
            get: { vm.uploadError != nil },
            set: { if !$0 { vm.uploadError = nil } }
        )) {
            Alert(
                title: Text("Upload Error"),
                message: Text(vm.uploadError ?? ""),
                dismissButton: .default(Text("OK")) { vm.uploadError = nil }
            )
        }
        // Add Photo source selection — shown for both placeholder tap and plus tap.
        // Uses actionSheet (iOS 14-compatible). Take Photo button only included when
        // camera hardware is available — hidden on Simulator.
        .actionSheet(isPresented: $showingSourceDialog) {
            ActionSheet(
                title: Text("Add Photo"),
                buttons: {
                    var buttons: [ActionSheet.Button] = []
                    if UIImagePickerController.isSourceTypeAvailable(.camera) {
                        buttons.append(.default(Text("Take Photo")) {
                            showingCamera = true
                        })
                    }
                    buttons.append(.default(Text("Choose from Library")) {
                        if let slot = pendingUploadSlot {
                            lastPickedSlot       = slot
                            showingPickerForSlot = slot
                        }
                    })
                    buttons.append(.cancel {
                        pendingUploadSlot = nil
                    })
                    return buttons
                }()
            )
        }
        // Camera sheet — same dismiss strategy as ProfilePhotoPicker sheet:
        // callback sets showingCamera = false, SwiftUI drives the close, no UIKit cascade.
        .sheet(isPresented: $showingCamera) {
            CameraImagePicker { imageOrNil in
                showingCamera = false          // SwiftUI-safe: drives sheet close via binding
                if let image = imageOrNil {
                    lastPickedSlot = pendingUploadSlot
                    pickerImage    = image     // triggers onChange → upload
                }
                pendingUploadSlot = nil
            }
        }
        // Mood photo preview — fullScreenCover(item:) avoids the nested .sheet
        // bug that can dismiss this view when the inner sheet closes.
        .fullScreenCover(item: $previewSlot, onDismiss: {
            // Replace action: open PhotoPicker after the preview has fully dismissed.
            if let slot = pendingReplaceSlot {
                pendingReplaceSlot = nil
                beginUpload(slot: slot)
            }
        }) { slotToPreview in
            MoodPhotoPreviewSheet(
                slot:     slotToPreview,
                onRetry: {
                    Task { await vm.triggerBgRemoval(slot: slotToPreview.slot, using: authManager) }
                    previewSlot = nil
                },
                onDelete: {
                    Task { await vm.delete(slot: slotToPreview.slot, using: authManager) }
                    previewSlot = nil
                },
                onReplace: {
                    pendingReplaceSlot = slotToPreview.slot
                    previewSlot = nil
                }
            )
            .environmentObject(authManager)
        }
    }

    // Tracks which slot was waiting for the picker (needed after sheet dismissal)
    @State private var lastPickedSlot: String? = nil

    // MARK: — Loading

    private var loadingView: some View {
        VStack { Spacer(); ProgressView("Loading…").foregroundColor(Theme.Color.muted); Spacer() }
    }

    // MARK: — Error

    private func errorView(_ message: String) -> some View {
        VStack(spacing: Theme.Spacing.lg) {
            Spacer()
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 44))
                .foregroundColor(Theme.Color.warning)
            Text(message)
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
                .padding(.horizontal, Theme.Spacing.xl)
            Button("Retry") { Task { await vm.reload(using: authManager) } }
                .font(.body.weight(.semibold))
                .foregroundColor(Theme.Color.primary)
            Spacer()
        }
    }

    // MARK: — Loaded

    private var loadedView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
                subtitleSection
                phaseASection
                phaseBSection
                Spacer(minLength: Theme.Spacing.xl)
            }
            .padding(Theme.Spacing.md)
        }
    }

    private var subtitleSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Upload a photo for each mood. These will appear on your player card based on match results.")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .fixedSize(horizontal: false, vertical: true)
            Text("\(vm.phaseACount)/6 uploaded")
                .font(.caption.weight(.semibold))
                .foregroundColor(vm.phaseAComplete ? Color(red: 0.18, green: 0.80, blue: 0.44) : Theme.Color.primary)
                .padding(.top, 2)
        }
    }

    // MARK: — Phase-A section

    private var phaseASection: some View {
        let phaseA = vm.slots.filter { $0.phase == "A" }
        return VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            sectionHeader("MATCH MOODS")
            VStack(spacing: Theme.Spacing.sm) {
                ForEach(phaseA) { slot in
                    MoodPhotoSlotCard(
                        slot:        slot,
                        isUploading: vm.uploadingSlot == slot.slot,
                        onUpload:    { beginUpload(slot: slot.slot) },
                        onDelete:    { Task { await vm.delete(slot: slot.slot, using: authManager) } },
                        onRemoveBg:  { Task { await vm.triggerBgRemoval(slot: slot.slot, using: authManager) } },
                        onReset:     { Task { await vm.resetProcessing(slot: slot.slot, using: authManager) } },
                        onPreview:   { previewSlot = slot }
                    )
                }
            }
        }
    }

    // MARK: — Phase-B section

    private var phaseBSection: some View {
        let phaseB = vm.slots.filter { $0.phase == "B" }
        return VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            sectionHeader("MORE MOODS")
            VStack(spacing: Theme.Spacing.sm) {
                ForEach(phaseB) { slot in
                    MoodPhotoSlotComingSoonCard(label: slot.label)
                }
            }
        }
    }

    // MARK: — Helpers

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 10, weight: .semibold))
            .foregroundColor(Theme.Color.muted)
            .kerning(0.8)
    }

    private func beginUpload(slot: String) {
        pendingUploadSlot   = slot
        showingSourceDialog = true
    }
}
