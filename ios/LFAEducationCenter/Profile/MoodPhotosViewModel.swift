import Foundation
import UIKit

// MARK: — Response models

struct MoodPhotoSlotData: Decodable, Identifiable {
    var id: String { slot }
    let slot:               String
    let label:              String
    let phase:              String
    let status:             String?
    let originalUrl:        String?
    let processedPngUrl:    String?
    let processingTimedOut: Bool

    var hasPhoto: Bool { originalUrl != nil }

    enum CodingKeys: String, CodingKey {
        case slot, label, phase, status
        case originalUrl        = "original_url"
        case processedPngUrl    = "processed_png_url"
        case processingTimedOut = "processing_timed_out"
    }
}

private struct MoodPhotosListResponse: Decodable {
    let slots:                [MoodPhotoSlotData]
    let phaseAUploadedCount:  Int
    let phaseAComplete:       Bool

    enum CodingKeys: String, CodingKey {
        case slots
        case phaseAUploadedCount = "phase_a_uploaded_count"
        case phaseAComplete      = "phase_a_complete"
    }
}

// Empty body for void-body POSTs (remove-bg)
private struct _EmptyBody: Encodable {}

// MARK: — State

enum MoodPhotosLoadState { case idle, loading, loaded, error(String) }
enum SlotUploadState     { case idle, uploading, error(String) }

// MARK: — ViewModel

// Manages the 6 Phase-A + 3 Phase-B mood photo slots.
// GET  /api/v1/lfa-player/mood-photos         — load / reload all slots
// POST /api/v1/lfa-player/mood-photos/{slot}/upload   — upload one slot
// DELETE /api/v1/lfa-player/mood-photos/{slot}        — delete one slot
// POST /api/v1/lfa-player/mood-photos/{slot}/remove-bg — trigger BG removal
// GET  /api/v1/lfa-player/mood-photos/{slot}/status   — poll processing status
@MainActor
final class MoodPhotosViewModel: ObservableObject {

    @Published private(set) var slots:           [MoodPhotoSlotData] = []
    @Published private(set) var phaseACount:     Int  = 0
    @Published private(set) var phaseAComplete:  Bool = false
    @Published private(set) var loadState:       MoodPhotosLoadState = .idle
    @Published var uploadingSlot: String? = nil
    @Published var uploadError:   String? = nil

    // Polling timer handle — one per processing slot
    private var pollTasks: [String: Task<Void, Never>] = [:]

    // MARK: — Load

    func load(using authManager: AuthManager) async {
        guard case .idle = loadState else { return }
        loadState = .loading
        await fetch(using: authManager)
    }

    func reload(using authManager: AuthManager) async {
        loadState = .idle
        await fetch(using: authManager)
    }

    private func fetch(using authManager: AuthManager) async {
        loadState = .loading
        do {
            let response: MoodPhotosListResponse = try await authManager.authenticatedGet(
                path: "/api/v1/lfa-player/mood-photos"
            )
            slots          = response.slots
            phaseACount    = response.phaseAUploadedCount
            phaseAComplete = response.phaseAComplete
            loadState      = .loaded
            startPollingForProcessingSlots(using: authManager)
        } catch {
            loadState = .error("Could not load mood photos. Check your connection.")
        }
    }

    // MARK: — Upload

    func upload(image: UIImage, slot: String, using authManager: AuthManager) async {
        guard let jpegData = image.jpegData(compressionQuality: 0.85) else {
            uploadError = "Could not process the selected image."
            return
        }
        if jpegData.count > 5 * 1024 * 1024 {
            uploadError = "Photo too large (max 5 MB)."
            return
        }
        uploadError   = nil
        uploadingSlot = slot

        do {
            let updated: MoodPhotoSlotData = try await authManager.authenticatedMultipartPost(
                path:      "/api/v1/lfa-player/mood-photos/\(slot)/upload",
                imageData: jpegData,
                mimeType:  "image/jpeg",
                fieldName: "photo"
            )
            updateSlot(updated)
            recalcPhaseA()
            if updated.status == "processing" {
                startPoll(slot: slot, using: authManager)
            }
        } catch APIError.httpError(let code, let detail) {
            uploadError = detail ?? "Upload failed (error \(code))."
        } catch {
            uploadError = "Network error. Check your connection."
        }
        uploadingSlot = nil
    }

    // MARK: — Delete

    func delete(slot: String, using authManager: AuthManager) async {
        do {
            try await authManager.authenticatedDeleteNoContent(
                path: "/api/v1/lfa-player/mood-photos/\(slot)"
            )
            stopPoll(slot: slot)
            clearSlot(slot)
            recalcPhaseA()
        } catch {
            // Non-fatal — the slot UI stays as-is
        }
    }

    // MARK: — Remove BG

    func triggerBgRemoval(slot: String, using authManager: AuthManager) async {
        do {
            let updated: MoodPhotoSlotData = try await authManager.authenticatedPost(
                path: "/api/v1/lfa-player/mood-photos/\(slot)/remove-bg",
                body: _EmptyBody()
            )
            updateSlot(updated)
            if updated.status == "processing" {
                startPoll(slot: slot, using: authManager)
            }
        } catch {
            // Non-fatal
        }
    }

    // MARK: — Reset stuck processing

    func resetProcessing(slot: String, using authManager: AuthManager) async {
        do {
            let updated: MoodPhotoSlotData = try await authManager.authenticatedPost(
                path: "/api/v1/lfa-player/mood-photos/\(slot)/remove-bg",
                body: _EmptyBody()
            )
            stopPoll(slot: slot)
            updateSlot(updated)
        } catch {
            // Non-fatal
        }
    }

    // MARK: — Polling

    private func startPollingForProcessingSlots(using authManager: AuthManager) {
        for slot in slots where slot.status == "processing" {
            startPoll(slot: slot.slot, using: authManager)
        }
    }

    private func startPoll(slot: String, using authManager: AuthManager) {
        guard pollTasks[slot] == nil else { return }
        pollTasks[slot] = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000) // 3s
                guard let self else { return }
                await self.pollSlot(slot: slot, using: authManager)
                if let current = self.slots.first(where: { $0.slot == slot }),
                   current.status != "processing" {
                    self.stopPoll(slot: slot)
                    return
                }
            }
        }
    }

    private func stopPoll(slot: String) {
        pollTasks[slot]?.cancel()
        pollTasks[slot] = nil
    }

    private func pollSlot(slot: String, using authManager: AuthManager) async {
        struct StatusResponse: Decodable {
            let status:             String
            let processedPngUrl:    String?
            let updatedAt:          String?
            let processingTimedOut: Bool
            enum CodingKeys: String, CodingKey {
                case status
                case processedPngUrl    = "processed_png_url"
                case updatedAt          = "updated_at"
                case processingTimedOut = "processing_timed_out"
            }
        }
        guard let resp: StatusResponse = try? await authManager.authenticatedGet(
            path: "/api/v1/lfa-player/mood-photos/\(slot)/status"
        ) else { return }

        if let idx = slots.firstIndex(where: { $0.slot == slot }) {
            let old = slots[idx]
            slots[idx] = MoodPhotoSlotData(
                slot:               old.slot,
                label:              old.label,
                phase:              old.phase,
                status:             resp.status,
                originalUrl:        old.originalUrl,
                processedPngUrl:    resp.processedPngUrl,
                processingTimedOut: resp.processingTimedOut
            )
        }
    }

    // MARK: — Helpers

    private func updateSlot(_ updated: MoodPhotoSlotData) {
        if let idx = slots.firstIndex(where: { $0.slot == updated.slot }) {
            slots[idx] = updated
        }
    }

    private func clearSlot(_ slot: String) {
        if let idx = slots.firstIndex(where: { $0.slot == slot }) {
            let old = slots[idx]
            slots[idx] = MoodPhotoSlotData(
                slot: old.slot, label: old.label, phase: old.phase,
                status: nil, originalUrl: nil, processedPngUrl: nil,
                processingTimedOut: false
            )
        }
    }

    private func recalcPhaseA() {
        let phaseA = ["mood_intro_neutral","mood_happy_smile","mood_celebration",
                      "mood_sad_disappointed","mood_angry_competitive","mood_surprised_shocked"]
        phaseACount    = slots.filter { phaseA.contains($0.slot) && $0.hasPhoto }.count
        phaseAComplete = phaseACount == 6
    }
}
