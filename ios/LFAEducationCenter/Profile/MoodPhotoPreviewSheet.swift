import SwiftUI

// Full-size preview for a mood photo slot.
// Presented as fullScreenCover(item:) from MoodPhotosView — avoids the iOS 14/15
// SwiftUI bug where a .sheet inside a .fullScreenCover can dismiss the parent.
//
// displayUrl priority:
//   showOriginal == true  → slot.originalUrl
//   showOriginal == false → slot.processedPngUrl ?? slot.originalUrl (default: processed)
//
// Closures (all non-dismissing — the parent MoodPhotosView must never close):
//   onRetry   — POST /remove-bg, then dismiss this preview
//   onDelete  — DELETE /slot, then dismiss this preview
//   onReplace — dismiss this preview; parent opens PhotoPicker via pendingReplaceSlot
struct MoodPhotoPreviewSheet: View {

    @Environment(\.presentationMode) private var presentationMode

    let slot:      MoodPhotoSlotData
    let onRetry:   () -> Void
    let onDelete:  () -> Void
    let onReplace: () -> Void  // parent handles picker open after dismiss

    @State private var showOriginal = false

    // MARK: — Derived state

    private var hasBothVersions: Bool {
        slot.processedPngUrl != nil && slot.originalUrl != nil
    }

    private var displayUrl: String? {
        if showOriginal { return slot.originalUrl }
        return slot.processedPngUrl ?? slot.originalUrl
    }

    private var canRetry: Bool {
        slot.status == "failed" || slot.status == "uploaded"
    }

    // MARK: — Body

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: Theme.Spacing.lg) {
                    imageSection
                    if hasBothVersions { versionToggle }
                    statusBadge
                    actionButtons
                    Spacer(minLength: Theme.Spacing.xl)
                }
                .padding(Theme.Spacing.md)
            }
            .background(Color(UIColor.systemBackground).ignoresSafeArea())
            .navigationTitle(slot.label)
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
                }
            }
        }
        .navigationViewStyle(.stack)
    }

    // MARK: — Image section

    private var imageSection: some View {
        ZStack {
            RoundedRectangle(cornerRadius: Theme.Radius.md)
                .fill(Theme.Color.muted.opacity(0.06))
                .frame(maxWidth: .infinity)
                .frame(height: 340)

            if let url = displayUrl {
                PreviewImageLoader(urlPath: url)
                    .scaledToFit()
                    .frame(maxWidth: .infinity)
                    .frame(height: 340)
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.md))
            } else {
                Image(systemName: "photo.fill")
                    .font(.system(size: 52))
                    .foregroundColor(Theme.Color.muted.opacity(0.25))
            }
        }
    }

    // MARK: — Version toggle (Original / Processed)

    private var versionToggle: some View {
        HStack(spacing: 0) {
            versionButton(label: "Processed", isActive: !showOriginal) { showOriginal = false }
            versionButton(label: "Original",  isActive: showOriginal)  { showOriginal = true }
        }
        .background(Theme.Color.muted.opacity(0.08))
        .cornerRadius(Theme.Radius.sm)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.sm)
                .stroke(Theme.Color.muted.opacity(0.15), lineWidth: 1)
        )
    }

    private func versionButton(label: String, isActive: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(isActive ? .white : Theme.Color.muted)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(isActive ? Theme.Color.primary : Color.clear)
                .cornerRadius(Theme.Radius.sm)
        }
    }

    // MARK: — Status badge

    private var statusBadge: some View {
        let info = statusInfo
        return HStack(spacing: Theme.Spacing.xs) {
            if slot.status == "processing" {
                ProgressView().scaleEffect(0.75)
            } else {
                Image(systemName: info.icon)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(info.color)
            }
            Text(info.text)
                .font(.subheadline.weight(.semibold))
                .foregroundColor(info.color)
        }
        .padding(.vertical, Theme.Spacing.sm)
        .padding(.horizontal, Theme.Spacing.md)
        .frame(maxWidth: .infinity)
        .background(info.color.opacity(0.10))
        .cornerRadius(Theme.Radius.sm)
    }

    private var statusInfo: (icon: String, color: Color, text: String) {
        switch slot.status {
        case "ready":
            let label = showOriginal ? "Showing original (before removal)" : "Background removed"
            return ("checkmark.circle.fill", Color(red: 0.18, green: 0.80, blue: 0.44), label)
        case "processing":
            return ("clock.fill", Theme.Color.muted, "Processing…")
        case "failed":
            return ("exclamationmark.circle.fill", Theme.Color.error, "Background removal failed")
        default:
            return ("photo.fill", Theme.Color.muted, "Original only")
        }
    }

    // MARK: — Action buttons

    private var actionButtons: some View {
        VStack(spacing: Theme.Spacing.sm) {
            if canRetry {
                actionRow(
                    icon: "wand.and.stars",
                    label: "Retry Background Removal",
                    color: Theme.Color.secondary
                ) {
                    onRetry()
                }
            }
            actionRow(
                icon: "arrow.triangle.2.circlepath",
                label: "Replace Photo",
                color: Theme.Color.primary
            ) {
                onReplace()
            }
            actionRow(
                icon: "trash",
                label: "Delete Photo",
                color: Theme.Color.error
            ) {
                onDelete()
            }
        }
    }

    private func actionRow(icon: String, label: String, color: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: Theme.Spacing.xs) {
                Image(systemName: icon)
                    .font(.system(size: 15, weight: .semibold))
                Text(label)
                    .font(.subheadline.weight(.semibold))
                Spacer()
            }
            .foregroundColor(color)
            .padding(.vertical, 12)
            .padding(.horizontal, Theme.Spacing.md)
            .background(color.opacity(0.08))
            .cornerRadius(Theme.Radius.md)
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Radius.md)
                    .stroke(color.opacity(0.18), lineWidth: 1)
            )
        }
    }
}

// MARK: — Full-size image loader

// Loads a server-relative path using the configured base URL.
// Same pattern as MoodPhotoThumbnail but renders at full resolution
// (caller applies .scaledToFit for layout).
private struct PreviewImageLoader: View {
    let urlPath: String
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let img = image {
                Image(uiImage: img).resizable()
            } else {
                Rectangle()
                    .fill(Theme.Color.muted.opacity(0.08))
                    .overlay(ProgressView())
            }
        }
        .onAppear { loadImage() }
        .onChange(of: urlPath) { _ in
            image = nil
            loadImage()
        }
    }

    private func loadImage() {
        guard image == nil,
              let url = URL(string: APIConfig.baseURL + urlPath) else { return }
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data = data, let img = UIImage(data: data) else { return }
            DispatchQueue.main.async { self.image = img }
        }.resume()
    }
}
