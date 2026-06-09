import SwiftUI

// Full-screen photo upload flow.
//
// State machine (PhotoUploadState):
//   idle      → picker sheet → preview(image) → uploading → success
//                                                          → error(msg) → retry → preview
//
// On success: calls onSuccess() which triggers dashboardVM.reload() in the caller.
// BG removal runs server-side; MVP treats "uploaded" and "processing" both as success.
// Picker is opened automatically on appear when state == .idle.
struct ProfilePhotoUploadView: View {

    @EnvironmentObject private var authManager: AuthManager
    @Environment(\.presentationMode) private var presentationMode

    let onSuccess: () -> Void

    @StateObject private var vm             = ProfilePhotoUploadViewModel()
    @State       private var isShowingPicker = false

    var body: some View {
        NavigationView {
            VStack(spacing: Theme.Spacing.lg) {
                Spacer(minLength: Theme.Spacing.xl)
                photoArea
                actionArea
                Spacer()
            }
            .padding(.horizontal, Theme.Spacing.lg)
            .background(Color(UIColor.systemBackground).ignoresSafeArea())
            .navigationTitle("Profile Photo")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        presentationMode.wrappedValue.dismiss()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Theme.Color.onSurface)
                    }
                    .disabled(isUploading)
                }
            }
            .sheet(isPresented: $isShowingPicker) {
                ProfilePhotoPicker { imageOrNil in
                    isShowingPicker = false   // SwiftUI-safe dismissal, no UIKit cascade
                    if let image = imageOrNil {
                        vm.selectImage(image)
                    }
                }
            }
        }
        .navigationViewStyle(.stack)
        .onAppear {
            if case .idle = vm.state { isShowingPicker = true }
        }
        .onChange(of: successTriggered) { triggered in
            if triggered {
                onSuccess()
                presentationMode.wrappedValue.dismiss()
            }
        }
    }

    // MARK: — Photo area

    @ViewBuilder
    private var photoArea: some View {
        switch vm.state {
        case .idle:
            placeholderCircle

        case .preview(let image):
            Image(uiImage: image)
                .resizable()
                .scaledToFill()
                .frame(width: 140, height: 140)
                .clipShape(Circle())
                .overlay(Circle().stroke(Theme.Color.primary.opacity(0.4), lineWidth: 2))

        case .uploading:
            ZStack {
                Circle()
                    .fill(Theme.Color.muted.opacity(0.08))
                    .frame(width: 140, height: 140)
                ProgressView()
                    .scaleEffect(1.4)
                    .accentColor(Theme.Color.primary)
            }

        case .success:
            ZStack {
                Circle()
                    .fill(Color(red: 0.18, green: 0.80, blue: 0.44).opacity(0.12))
                    .frame(width: 140, height: 140)
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 56))
                    .foregroundColor(Color(red: 0.18, green: 0.80, blue: 0.44))
            }

        case .error:
            placeholderCircle
        }
    }

    private var placeholderCircle: some View {
        Circle()
            .fill(Theme.Color.muted.opacity(0.08))
            .frame(width: 140, height: 140)
            .overlay(
                Image(systemName: "person.fill")
                    .font(.system(size: 56))
                    .foregroundColor(Theme.Color.muted.opacity(0.3))
            )
    }

    // MARK: — Action area

    @ViewBuilder
    private var actionArea: some View {
        switch vm.state {
        case .idle:
            choosePhotoButton

        case .preview:
            VStack(spacing: Theme.Spacing.md) {
                uploadButton
                choosePhotoButton
            }

        case .uploading:
            Text("Uploading…")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)

        case .success:
            Text("Photo uploaded successfully.")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Color(red: 0.18, green: 0.80, blue: 0.44))
                .multilineTextAlignment(.center)

        case .error(let message):
            VStack(spacing: Theme.Spacing.md) {
                Text(message)
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.error)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                choosePhotoButton
            }
        }
    }

    private var uploadButton: some View {
        Button {
            Task { await vm.upload(using: authManager) }
        } label: {
            Text("Upload Photo")
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 48)
                .background(Theme.Color.primary)
                .foregroundColor(.white)
                .cornerRadius(Theme.Radius.sm)
        }
        .disabled(isUploading)
    }

    private var choosePhotoButton: some View {
        Button {
            isShowingPicker = true
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "photo.on.rectangle")
                    .font(.system(size: 15))
                Text(hasPreview ? "Choose Different Photo" : "Choose Photo")
                    .font(.body.weight(.semibold))
            }
            .frame(maxWidth: .infinity)
            .frame(height: 48)
            .background(Theme.Color.primary.opacity(0.10))
            .foregroundColor(Theme.Color.primary)
            .cornerRadius(Theme.Radius.sm)
        }
        .disabled(isUploading)
    }

    // MARK: — Helpers

    private var isUploading: Bool {
        if case .uploading = vm.state { return true }
        return false
    }

    private var hasPreview: Bool {
        if case .preview = vm.state { return true }
        return false
    }

    private var successTriggered: Bool {
        if case .success = vm.state { return true }
        return false
    }
}
