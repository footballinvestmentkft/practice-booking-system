import Foundation

// Decoded from GET /api/v1/specializations/progress/me.
// Backend wraps in { "success": true, "data": { "SPEC_CODE": { ... } } }.
// Inner data is an untyped Dict — all progress fields are Optional for resilience.
struct SpecializationProgressResponse: Decodable {
    let success: Bool?
    let data:    [String: SpecializationProgressData]?
}

struct SpecializationProgressData: Decodable {
    let currentLevel:           Int?
    let xp:                     Int?
    let sessionsCompleted:      Int?
    let projectsCompleted:      Int?
    let theoryHoursCompleted:   Int?
    let practiceHoursCompleted: Int?

    enum CodingKeys: String, CodingKey {
        case currentLevel           = "current_level"
        case xp
        case sessionsCompleted      = "sessions_completed"
        case projectsCompleted      = "projects_completed"
        case theoryHoursCompleted   = "theory_hours_completed"
        case practiceHoursCompleted = "practice_hours_completed"
    }
}
