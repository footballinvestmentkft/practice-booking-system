import Foundation

// MARK: — Response models (P5 list endpoint)

// One video item from GET /api/v1/users/me/juggling/videos.
//
// Privacy invariant: no raw path, no URL, no filesystem path ever included
// in this model — enforced structurally by the backend schema.
//
// has_thumbnail / has_media signal *expected* availability.
// The actual thumbnail/media endpoints perform disk checks and may return 404.
struct JugglingVideoItem: Codable, Identifiable {

    let videoId:                  String
    let status:                   String
    let transcodeStatus:          String?
    let qualityStatus:            String?
    let qualityScore:             Double?
    let createdAt:                String   // ISO8601 — formatted via displayDate
    let updatedAt:                String
    let durationSeconds:          Double?
    let processedResolution:      String?
    let processedFps:             Double?
    let processedFileSizeBytes:   Int?
    let hasThumbnail:             Bool
    let hasMedia:                 Bool
    let uploadSource:             String
    let sourceType:               String

    var id: String { videoId }

    enum CodingKeys: String, CodingKey {
        case videoId                  = "video_id"
        case status
        case transcodeStatus          = "transcode_status"
        case qualityStatus            = "quality_status"
        case qualityScore             = "quality_score"
        case createdAt                = "created_at"
        case updatedAt                = "updated_at"
        case durationSeconds          = "duration_seconds"
        case processedResolution      = "processed_resolution"
        case processedFps             = "processed_fps"
        case processedFileSizeBytes   = "processed_file_size_bytes"
        case hasThumbnail             = "has_thumbnail"
        case hasMedia                 = "has_media"
        case uploadSource             = "upload_source"
        case sourceType               = "source_type"
    }

    // MARK: — Display helpers

    var displayDate: String {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let isoBasic = ISO8601DateFormatter()
        let date = iso.date(from: createdAt) ?? isoBasic.date(from: createdAt)
        guard let d = date else { return createdAt }
        let df = DateFormatter()
        df.dateStyle = .medium
        df.timeStyle = .short
        return df.string(from: d)
    }

    var fileSizeDisplay: String? {
        guard let bytes = processedFileSizeBytes else { return nil }
        let mb = Double(bytes) / (1024.0 * 1024.0)
        return String(format: "%.1f MB", mb)
    }

    // true when processed_file_size_bytes > 200 MB
    var isLargeFile: Bool {
        guard let bytes = processedFileSizeBytes else { return false }
        return bytes > 200 * 1024 * 1024
    }

    var statusBadgeLabel: String {
        switch status {
        case "analyzed":       return "✓ Ready"
        case "processing":     return "⏳ Processing"
        case "rejected":       return "✗ Rejected"
        case "failed":         return "! Failed"
        case "uploaded":       return "↑ Uploaded"
        case "pending_upload": return "Pending"
        default:               return status.capitalized
        }
    }

    var isPlayable: Bool { hasMedia }
}

// MARK: — List response envelope

struct JugglingVideoListResponse: Codable {
    let videos: [JugglingVideoItem]
    let total:  Int
    let limit:  Int
    let offset: Int
}