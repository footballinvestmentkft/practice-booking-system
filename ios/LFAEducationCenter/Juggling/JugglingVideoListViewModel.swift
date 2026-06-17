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
    // Per-video delete loading state. Non-empty while a DELETE is in flight.
    @Published private(set) var deletingVideoIds: Set<String> = []
    // Set on delete failure; nil on success. Consumers may display and clear this.
    @Published private(set) var errorMessage: String? = nil

    private var fetchingThumbnailIds: Set<String> = []
    // Stored on first load so deleteVideo can create a real API client in production.
    private var storedAuthManager: AuthManager?
    // Injected in tests via the test-only init; nil in production.
    private let injectableDeleteClient: JugglingAnnotationAPIClientProtocol?

    // MARK: — Init

    // Production: SwiftUI @StateObject uses this no-arg init.
    init() { injectableDeleteClient = nil }

    // Test-only: inject a mock delete client without requiring network or AuthManager.
    init(deleteClient: JugglingAnnotationAPIClientProtocol) {
        injectableDeleteClient = deleteClient
    }

    // MARK: — List fetch

    func load(using authManager: AuthManager) async {
        storedAuthManager = authManager
        guard case .idle = listState else { return }
        await fetchList(using: authManager)
    }

    func reload(using authManager: AuthManager) async {
        storedAuthManager = authManager
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
            return   // 404 / 409 / 410 / network error — placeholder shown in view
        }
        thumbnails[videoId] = image
    }

    // MARK: — Player trigger

    func playVideo(_ video: JugglingVideoItem) {
        guard video.isPlayable else { return }
        showPlayerFor = video
    }

    // MARK: — Video delete (B-2 I-1)
    //
    // Marks the video as media_deleted on the server (storage release).
    // 204 and 410 are both treated as success: the list item stays (archive row)
    // but status → media_deleted, hasThumbnail / hasMedia → false, thumbnail evicted.
    //
    // Duplicate-call guard: if a DELETE is already in flight for this videoId, the
    // second call returns immediately. The guard is per-video so deleting video A
    // never blocks deletion of video B.

    func deleteVideo(videoId: String) async {
        guard !deletingVideoIds.contains(videoId) else { return }
        deletingVideoIds.insert(videoId)
        defer { deletingVideoIds.remove(videoId) }

        errorMessage = nil

        let client: JugglingAnnotationAPIClientProtocol
        if let injected = injectableDeleteClient {
            client = injected
        } else if let auth = storedAuthManager {
            client = JugglingAnnotationAPIClient(authManager: auth)
        } else {
            errorMessage = "Not authenticated."
            return
        }

        do {
            try await client.deleteVideo(videoId: videoId)
            applyDeleteSuccess(videoId: videoId)
        } catch let err as VideoDeleteError {
            errorMessage = err.errorDescription
        } catch {
            errorMessage = "Could not delete video. Please try again."
        }
    }

    // MARK: — Swipe-to-delete resolution (I-2)
    //
    // Converts an IndexSet from .onDelete into a stable videoId captured at
    // swipe time. Returns nil (no-op) for:
    //   • already media_deleted — archive row, nothing left to remove
    //   • a delete already in flight for this video — duplicate guard
    //   • index out of bounds — defensive guard

    func resolveDeleteCandidate(at indexSet: IndexSet, in videos: [JugglingVideoItem]) -> String? {
        guard let index = indexSet.first, index < videos.count else { return nil }
        let video = videos[index]
        guard video.status != "media_deleted" else { return nil }
        guard !deletingVideoIds.contains(video.videoId) else { return nil }
        return video.videoId
    }

    // MARK: — Test support (internal — accessible via @testable import in unit tests)

    func setLoadedForTests(_ items: [JugglingVideoItem]) {
        listState = .loaded(items)
    }

    func simulateInFlightDelete(videoId: String) {
        deletingVideoIds.insert(videoId)
    }

    // MARK: — Private helpers

    private func applyDeleteSuccess(videoId: String) {
        thumbnails[videoId] = nil

        guard case .loaded(let items) = listState else { return }
        let remaining = items.filter { $0.videoId != videoId }
        listState = remaining.isEmpty ? .empty : .loaded(remaining)
    }
}
