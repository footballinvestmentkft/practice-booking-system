import Foundation
import UIKit

// MARK: — Load state

enum JugglingListState {
    case idle
    case loading
    case loaded([JugglingVideoItem])
    case empty
    case error(String)
}

// MARK: — ViewModel

// Drives JugglingVideoListView.
// All state mutations run on @MainActor.
// Network calls suspend the actor cleanly via async/await.
@MainActor
final class JugglingVideoListViewModel: ObservableObject {

    @Published private(set) var listState: JugglingListState = .idle
    @Published var thumbnails: [String: UIImage] = [:]    // videoId → UIImage
    @Published var showPlayerFor: JugglingVideoItem? = nil

    private var fetchingThumbnailIds: Set<String> = []

    // MARK: — List fetch

    func load(using authManager: AuthManager) async {
        guard case .idle = listState else { return }
        await fetchList(using: authManager)
    }

    func reload(using authManager: AuthManager) async {
        listState = .idle
        thumbnails = [:]
        fetchingThumbnailIds = []
        await fetchList(using: authManager)
    }

    private func fetchList(using authManager: AuthManager) async {
        listState = .loading
        do {
            let response: JugglingVideoListResponse = try await authManager.authenticatedGet(
                path: "/api/v1/users/me/juggling/videos?limit=50&offset=0"
            )
            listState = response.videos.isEmpty ? .empty : .loaded(response.videos)
        } catch APIError.httpError(503, _) {
            listState = .error("Juggling feature is not available.")
        } catch APIError.unauthorized {
            listState = .error("Session expired. Please log in again.")
        } catch APIError.networkError {
            listState = .error("No connection. Check your network and try again.")
        } catch {
            listState = .error("Could not load videos. Please try again.")
        }
    }

    // MARK: — Thumbnail fetch

    // Called from each VideoRow's .onAppear — idempotent, no duplicate requests.
    func fetchThumbnailIfNeeded(for video: JugglingVideoItem, using authManager: AuthManager) {
        guard video.hasThumbnail,
              thumbnails[video.videoId] == nil,
              !fetchingThumbnailIds.contains(video.videoId) else { return }
        fetchingThumbnailIds.insert(video.videoId)
        Task {
            await fetchThumbnail(videoId: video.videoId, using: authManager)
        }
    }

    private func fetchThumbnail(videoId: String, using authManager: AuthManager) async {
        defer { fetchingThumbnailIds.remove(videoId) }
        guard let data = try? await authManager.authenticatedFetchData(
            path: "/api/v1/users/me/juggling/videos/\(videoId)/thumbnail"
        ), let image = UIImage(data: data) else {
            return   // 404 / 409 / network error — placeholder shown in view
        }
        thumbnails[videoId] = image
    }

    // MARK: — Player trigger

    func playVideo(_ video: JugglingVideoItem) {
        guard video.isPlayable else { return }
        showPlayerFor = video
    }
}
