import SwiftUI

// Slot-key → emoji mapping. iOS-only — no backend change needed.
// Keys match backend _SLOT_META exactly (mood_photos.py).
private let _moodEmoji: [String: String] = [
    "mood_intro_neutral":     "😐",
    "mood_happy_smile":       "😄",
    "mood_celebration":       "🎉",
    "mood_sad_disappointed":  "😢",
    "mood_angry_competitive": "😠",
    "mood_surprised_shocked": "😮",
    "mood_focused_ready":     "🎯",
    "mood_confident":         "💪",
    "mood_proud":             "🏆",
]

// Async image loader — loads a relative URL path using the configured base URL.
// Private to this file; mirrors ProfileURLPhotoView in ProfileView.swift.
private struct MoodPhotoThumbnail: View {
    let urlPath: String
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let img = image {
                Image(uiImage: img).resizable().scaledToFill()
            } else {
                Rectangle()
                    .fill(Theme.Color.muted.opacity(0.08))
                    .overlay(ProgressView().scaleEffect(0.6))
            }
        }
        .onAppear { loadImage() }
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

// Per-slot card for the Mood Photos screen.
// Shows thumbnail (if uploaded), label, upload / delete / BG removal controls,
// and processing / ready / failed state indicators.
// Thumbnail is tappable when a photo exists — opens MoodPhotoPreviewSheet.
struct MoodPhotoSlotCard: View {

    let slot:         MoodPhotoSlotData
    let isUploading:  Bool          // this slot is currently uploading
    let onUpload:     () -> Void    // triggers PhotoPicker
    let onDelete:     () -> Void
    let onRemoveBg:   () -> Void
    let onReset:      () -> Void    // reset stuck processing
    let onPreview:    () -> Void    // tap thumbnail → MoodPhotoPreviewSheet

    private static let successGreen = Color(red: 0.18, green: 0.80, blue: 0.44)

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            thumbnail
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 5) {
                    if let emoji = _moodEmoji[slot.slot] {
                        Text(emoji).font(.system(size: 15))
                    }
                    Text(slot.label)
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(Theme.Color.onSurface)
                }
                statusLine
            }
            Spacer()
            actionButtons
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }

    // MARK: — Thumbnail

    @ViewBuilder
    private var thumbnail: some View {
        if isUploading {
            RoundedRectangle(cornerRadius: Theme.Radius.sm)
                .fill(Theme.Color.primary.opacity(0.08))
                .frame(width: 56, height: 56)
                .overlay(ProgressView().scaleEffect(0.7))
        } else if let url = slot.processedPngUrl ?? slot.originalUrl {
            Button(action: onPreview) {
                MoodPhotoThumbnail(urlPath: url)
                    .frame(width: 56, height: 56)
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.sm))
                    .overlay(
                        RoundedRectangle(cornerRadius: Theme.Radius.sm)
                            .stroke(Theme.Color.primary.opacity(0.25), lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)
        } else {
            Button(action: onUpload) {
                RoundedRectangle(cornerRadius: Theme.Radius.sm)
                    .fill(Theme.Color.primary.opacity(0.07))
                    .frame(width: 56, height: 56)
                    .overlay(
                        Image(systemName: "camera.fill")
                            .font(.system(size: 18))
                            .foregroundColor(Theme.Color.primary.opacity(0.6))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: Theme.Radius.sm)
                            .stroke(Theme.Color.primary.opacity(0.15), lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: — Status line

    @ViewBuilder
    private var statusLine: some View {
        switch slot.status {
        case "uploaded":
            Text("Uploaded")
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
        case "processing":
            if slot.processingTimedOut {
                HStack(spacing: 4) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundColor(Theme.Color.warning)
                    Text("Timed out — tap Reset")
                        .font(.caption)
                        .foregroundColor(Theme.Color.warning)
                }
            } else {
                HStack(spacing: 4) {
                    ProgressView().scaleEffect(0.6)
                    Text("Processing…")
                        .font(.caption)
                        .foregroundColor(Theme.Color.muted)
                }
            }
        case "ready":
            HStack(spacing: 4) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.caption)
                    .foregroundColor(Self.successGreen)
                Text("Background removed")
                    .font(.caption)
                    .foregroundColor(Self.successGreen)
            }
        case "failed":
            HStack(spacing: 4) {
                Image(systemName: "exclamationmark.circle.fill")
                    .font(.caption)
                    .foregroundColor(Theme.Color.error)
                Text("BG removal failed — tap Retry")
                    .font(.caption)
                    .foregroundColor(Theme.Color.error)
            }
        default:
            Text("No photo yet")
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
        }
    }

    // MARK: — Action buttons

    @ViewBuilder
    private var actionButtons: some View {
        if isUploading {
            EmptyView()
        } else if slot.hasPhoto {
            HStack(spacing: 8) {
                if slot.status == "processing" && slot.processingTimedOut {
                    iconButton(systemName: "arrow.counterclockwise", color: Theme.Color.warning, action: onReset)
                } else if slot.status == "uploaded" || slot.status == "failed" {
                    iconButton(systemName: "wand.and.stars", color: Theme.Color.secondary, action: onRemoveBg)
                }
                iconButton(systemName: "trash", color: Theme.Color.error, action: onDelete)
            }
        } else {
            iconButton(systemName: "plus.circle.fill", color: Theme.Color.primary, action: onUpload)
        }
    }

    private func iconButton(systemName: String, color: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 20))
                .foregroundColor(color)
        }
    }
}

// MARK: — Coming Soon card (Phase-B)

struct MoodPhotoSlotComingSoonCard: View {
    let label: String

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            RoundedRectangle(cornerRadius: Theme.Radius.sm)
                .fill(Theme.Color.muted.opacity(0.10))
                .frame(width: 56, height: 56)
                .overlay(
                    Image(systemName: "camera.fill")
                        .font(.system(size: 20))
                        .foregroundColor(Theme.Color.muted.opacity(0.4))
                )
            Text(label)
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.muted)
            Spacer()
            Text("Coming Soon")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.secondary)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(Theme.Color.secondary.opacity(0.10))
                .cornerRadius(4)
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
        .opacity(0.65)
    }
}
