import Foundation

// MARK: — DensePoseCache (AN-3B2D-2)
//
// Per-video in-memory skeleton trajectory cache.
// Keyed by videoId. Cleared when the screen is dismissed.
// Accessed from MainActor only (via ViewModel).

final class DensePoseCache {

    private var store: [String: [DensePoseFrame]] = [:]

    func get(_ videoId: String) -> [DensePoseFrame]? {
        store[videoId]
    }

    func set(_ videoId: String, frames: [DensePoseFrame]) {
        store[videoId] = frames
    }

    func append(_ videoId: String, frame: DensePoseFrame) {
        store[videoId, default: []].append(frame)
    }

    func clear(_ videoId: String) {
        store.removeValue(forKey: videoId)
    }

    func clearAll() {
        store.removeAll()
    }
}
