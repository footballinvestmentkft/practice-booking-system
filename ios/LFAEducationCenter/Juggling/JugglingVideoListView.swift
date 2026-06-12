import SwiftUI

// MARK: — List view

struct JugglingVideoListView: View {
    @EnvironmentObject var authManager: AuthManager
    @StateObject private var viewModel = JugglingVideoListViewModel()

    var body: some View {
        NavigationView {
            listContent
                .navigationTitle("My Videos")
                .toolbar {
                    ToolbarItem(placement: .navigationBarTrailing) {
                        if case .loaded = viewModel.listState {
                            Button {
                                Task { await viewModel.reload(using: authManager) }
                            } label: {
                                Image(systemName: "arrow.clockwise")
                            }
                        }
                    }
                }
        }
        .navigationViewStyle(.stack)
        .fullScreenCover(item: $viewModel.showPlayerFor) { video in
            JugglingPlayerView(video: video)
                .environmentObject(authManager)
        }
    }

    @ViewBuilder
    private var listContent: some View {
        switch viewModel.listState {
        case .idle:
            Color.clear
                .onAppear { Task { await viewModel.load(using: authManager) } }

        case .loading:
            VStack(spacing: Theme.Spacing.md) {
                ProgressView()
                Text("Loading your videos…")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
            }

        case .loaded(let videos):
            ScrollView {
                LazyVStack(spacing: Theme.Spacing.sm) {
                    ForEach(videos) { video in
                        JugglingVideoRow(
                            video: video,
                            thumbnail: viewModel.thumbnails[video.videoId],
                            onPlay: { viewModel.playVideo(video) }
                        )
                        .onAppear {
                            viewModel.fetchThumbnailIfNeeded(for: video, using: authManager)
                        }
                        .padding(.horizontal, Theme.Spacing.md)
                    }
                }
                .padding(.vertical, Theme.Spacing.sm)
            }

        case .empty:
            VStack(spacing: Theme.Spacing.md) {
                Image(systemName: "video.slash")
                    .font(.system(size: 48))
                    .foregroundColor(Theme.Color.muted)
                Text("No videos yet")
                    .font(.title3.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                Text("Upload your first juggling video to see it here.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Spacing.xl)
            }

        case .error(let msg):
            VStack(spacing: Theme.Spacing.md) {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 48))
                    .foregroundColor(Theme.Color.error)
                Text(msg)
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Spacing.xl)
                Button {
                    Task { await viewModel.reload(using: authManager) }
                } label: {
                    Text("Try Again")
                        .font(.body.weight(.semibold))
                        .foregroundColor(Theme.Color.primary)
                }
            }
        }
    }
}

// MARK: — Video row

private struct JugglingVideoRow: View {
    let video:     JugglingVideoItem
    let thumbnail: UIImage?
    let onPlay:    () -> Void

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            thumbnailView
                .frame(width: 72, height: 54)
                .cornerRadius(Theme.Radius.sm)

            VStack(alignment: .leading, spacing: 4) {
                Text(video.displayDate)
                    .font(.subheadline.weight(.medium))
                    .foregroundColor(Theme.Color.onSurface)
                    .lineLimit(1)

                statusBadge

                if let size = video.fileSizeDisplay {
                    Text(size)
                        .font(.caption)
                        .foregroundColor(Theme.Color.muted)
                }
            }

            Spacer()

            if video.isPlayable {
                Button(action: onPlay) {
                    Image(systemName: "play.circle.fill")
                        .font(.system(size: 32))
                        .foregroundColor(Theme.Color.primary)
                }
                .accessibilityLabel("Play video")
            } else {
                Image(systemName: "play.circle")
                    .font(.system(size: 32))
                    .foregroundColor(Theme.Color.muted)
                    .accessibilityLabel("Video not yet playable")
            }
        }
        .padding(Theme.Spacing.sm)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }

    @ViewBuilder
    private var thumbnailView: some View {
        if let img = thumbnail {
            Image(uiImage: img)
                .resizable()
                .scaledToFill()
        } else {
            ZStack {
                Color(UIColor.systemGray5)
                Image(systemName: thumbnailPlaceholderIcon)
                    .font(.system(size: 20))
                    .foregroundColor(Theme.Color.muted)
            }
        }
    }

    private var thumbnailPlaceholderIcon: String {
        switch video.status {
        case "processing": return "clock"
        case "rejected":   return "xmark.circle"
        case "failed":     return "exclamationmark.circle"
        default:           return "photo"
        }
    }

    private var statusBadge: some View {
        Text(video.statusBadgeLabel)
            .font(.caption.weight(.semibold))
            .foregroundColor(badgeColor)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(badgeColor.opacity(0.12))
            .cornerRadius(4)
    }

    private var badgeColor: SwiftUI.Color {
        switch video.status {
        case "analyzed":          return Theme.Color.primary
        case "processing":        return Theme.Color.secondary
        case "rejected", "failed": return Theme.Color.error
        default:                  return Theme.Color.muted
        }
    }
}
