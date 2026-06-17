import SwiftUI

// MARK: — List view

struct JugglingVideoListView: View {
    @EnvironmentObject var authManager: AuthManager
    @StateObject private var viewModel = JugglingVideoListViewModel()

    // Swipe-to-delete UI state (I-2).
    // deleteCandidate holds the stable videoId captured at swipe time by
    // resolveDeleteCandidate(at:in:). It is never re-derived from an index later.
    @State private var deleteCandidate:       String? = nil
    @State private var showDeleteConfirmation = false
    @State private var showDeleteError        = false
    @State private var deleteErrorMessage:    String  = ""

    // Upload sheet coordinator (B3). Shared by the toolbar "+" button and
    // the empty-state CTA — both call openUploadSheet().
    @StateObject private var uploadCoordinator = JugglingVideoUploadCoordinator()

    var body: some View {
        NavigationView {
            listContent
                .navigationTitle("My Videos")
                .toolbar {
                    ToolbarItemGroup(placement: .navigationBarTrailing) {
                        if case .loaded = viewModel.listState {
                            Button {
                                Task { await viewModel.reload(using: authManager) }
                            } label: {
                                Image(systemName: "arrow.clockwise")
                            }
                        }
                        Button(action: openUploadSheet) {
                            Image(systemName: "plus")
                        }
                        .accessibilityLabel("Videó feltöltése")
                    }
                }
                // Alert is anchored to listContent (inside NavigationView) to avoid
                // competing with confirmationDialog which is on the outer NavigationView.
                .alert("Törlés sikertelen", isPresented: $showDeleteError) {
                    Button("OK", role: .cancel) { }
                } message: {
                    Text(deleteErrorMessage)
                }
        }
        .navigationViewStyle(.stack)
        .sheet(isPresented: $uploadCoordinator.showSheet, onDismiss: uploadCoordinator.handleDismiss) {
            if let uploadViewModel = uploadCoordinator.uploadViewModel {
                JugglingVideoUploadView(viewModel: uploadViewModel, isPresented: $uploadCoordinator.showSheet)
            }
        }
        .fullScreenCover(item: $viewModel.showPlayerFor) { video in
            if let userId = authManager.currentUserId, userId > 0 {
                JugglingAnnotationScreen(video: video, authManager: authManager, userId: userId)
            } else {
                AnnotationUserUnavailableView()
            }
        }
        // iOS 15+: confirmationDialog replaces deprecated actionSheet.
        // Anchored on the outer NavigationView — separate view from .alert above.
        .confirmationDialog(
            "Videófelvétel törlése",
            isPresented: $showDeleteConfirmation,
            titleVisibility: .visible
        ) {
            Button("Felvételek törlése", role: .destructive) {
                guard let videoId = deleteCandidate else { return }
                deleteCandidate = nil
                Task { await performDelete(videoId: videoId) }
            }
            Button("Mégse", role: .cancel) {
                deleteCandidate = nil
            }
        } message: {
            Text(
                "A videófájl és az előnézeti kép véglegesen törlődik, " +
                "így tárhely szabadul fel. Az elemzési eredmények, " +
                "címkék és statisztikák megmaradnak a profilodban."
            )
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
            // iOS 15+: swipeActions replaces onDelete for per-row destructive gestures.
            // UITableView appearance hides separators and clears the background.
            List {
                ForEach(videos) { video in
                    JugglingVideoRow(
                        video: video,
                        thumbnail: viewModel.thumbnails[video.videoId],
                        isDeleting: viewModel.deletingVideoIds.contains(video.videoId),
                        onPlay: { viewModel.playVideo(video) }
                    )
                    .contextMenu {
                        Button(role: .destructive) {
                            deleteCandidate = video.videoId
                            showDeleteConfirmation = true
                        } label: {
                            Label("Videó törlése", systemImage: "trash")
                        }
                    }
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button(role: .destructive) {
                            deleteCandidate = video.videoId
                            showDeleteConfirmation = true
                        } label: {
                            Label("Törlés", systemImage: "trash")
                        }
                    }
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(
                        top: Theme.Spacing.xs,
                        leading: Theme.Spacing.md,
                        bottom: Theme.Spacing.xs,
                        trailing: Theme.Spacing.md
                    ))
                    .onAppear {
                        viewModel.fetchThumbnailIfNeeded(for: video, using: authManager)
                    }
                }
            }
            .listStyle(PlainListStyle())
            .onAppear {
                // iOS 14: hide List separators and clear the UITableView background.
                // Restored in .onDisappear to limit impact on other screens.
                UITableView.appearance().separatorColor = .clear
                UITableView.appearance().backgroundColor = .clear
            }
            .onDisappear {
                UITableView.appearance().separatorColor = .opaqueSeparator
                UITableView.appearance().backgroundColor = .systemBackground
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
                Button(action: openUploadSheet) {
                    Text("Videó feltöltése")
                        .font(.body.weight(.semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, Theme.Spacing.lg)
                        .padding(.vertical, Theme.Spacing.sm)
                        .background(Theme.Color.primary)
                        .cornerRadius(Theme.Radius.md)
                }
                .padding(.top, Theme.Spacing.sm)
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

    // Calls the ViewModel delete, then shows an error alert if it failed.
    // videoId was captured at swipe time — never re-derived from a list index here.
    private func performDelete(videoId: String) async {
        await viewModel.deleteVideo(videoId: videoId)
        if let msg = viewModel.errorMessage {
            deleteErrorMessage = msg
            showDeleteError = true
        }
    }

    // MARK: — Upload sheet (B3)

    // Entry point for both the toolbar "+" button and the empty-state CTA.
    // Configures the coordinator with the current AuthManager (unavailable
    // at coordinator init) and delegates the open/guard logic to it.
    private func openUploadSheet() {
        uploadCoordinator.makeClient = { JugglingAnnotationAPIClient(authManager: authManager) }
        uploadCoordinator.onReload = {
            Task { await viewModel.reload(using: authManager) }
        }
        uploadCoordinator.open()
    }
}

// MARK: — Video row

private struct JugglingVideoRow: View {
    let video:      JugglingVideoItem
    let thumbnail:  UIImage?
    let isDeleting: Bool        // true while a DELETE for this video is in flight
    let onPlay:     () -> Void

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
        // iOS 14: .overlay(_ view:) — the iOS 15+ alignment: label form is not available.
        // Shows a spinner overlay while the storage-release DELETE is in flight.
        .overlay(
            Group {
                if isDeleting {
                    ZStack {
                        Theme.Color.surface.opacity(0.55)
                        ProgressView()
                    }
                    .cornerRadius(Theme.Radius.md)
                }
            }
        )
        .disabled(isDeleting)
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
        case "media_deleted": return "archivebox"
        case "processing":    return "clock"
        case "rejected":      return "xmark.circle"
        case "failed":        return "exclamationmark.circle"
        default:              return "photo"
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
        case "media_deleted":     return Theme.Color.muted
        default:                  return Theme.Color.muted
        }
    }
}

// MARK: — Annotation user-unavailable fallback

// Shown instead of JugglingAnnotationScreen when authManager.currentUserId is
// nil or non-positive. JugglingAnnotationViewModel can only be constructed
// with a valid, positive userId — presenting the screen without one would
// either crash (precondition) or, before this fix, silently scope local
// storage to userId 0.
private struct AnnotationUserUnavailableView: View {
    @Environment(\.presentationMode) private var presentationMode

    var body: some View {
        VStack(spacing: Theme.Spacing.md) {
            ProgressView()
            Text("A felhasználói profil betöltése folyamatban van.")
                .font(.subheadline)
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)
                .padding(.horizontal, Theme.Spacing.xl)
            Text("Ha ez a hibaüzenet nem tűnik el, lépj ki és jelentkezz be újra.")
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
                .padding(.horizontal, Theme.Spacing.xl)
            Button {
                presentationMode.wrappedValue.dismiss()
            } label: {
                Text("Bezárás")
                    .font(.body.weight(.semibold))
                    .foregroundColor(Theme.Color.primary)
            }
            .padding(.top, Theme.Spacing.sm)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
    }
}
