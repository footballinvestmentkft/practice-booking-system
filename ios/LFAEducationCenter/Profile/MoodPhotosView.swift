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
        // PhotoPicker sheet
        .sheet(isPresented: Binding(
            get: { showingPickerForSlot != nil },
            set: { if !$0 { showingPickerForSlot = nil } }
        )) {
            ProfilePhotoPicker { image in
                pickerImage = image
                showingPickerForSlot = nil
            }
        }
        // Upload when pickerImage arrives
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
                        onReset:     { Task { await vm.resetProcessing(slot: slot.slot, using: authManager) } }
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
        lastPickedSlot      = slot
        showingPickerForSlot = slot
    }
}
