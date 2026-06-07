import Foundation

// Decoded from GET /api/v1/progression/skill-profile.
// Requires an active LFA_FOOTBALL_PLAYER license — returns 404 if not found.
// Typed Pydantic SkillProfileResponse on the backend — stable schema.
struct SkillProfile: Decodable {
    let userLicenseId:    Int
    let specialization:   String
    let averageLevel:     Double
    let totalAssessments: Int
    let totalTournaments: Int
    let skills:           [String: SkillData]

    struct SkillData: Decodable {
        let currentLevel:    Double
        let baseline:        Double
        let totalDelta:      Double
        let tier:            String    // "BEGINNER", "DEVELOPING", "INTERMEDIATE", etc.
        let tierEmoji:       String
        let assessmentCount: Int
        let tournamentCount: Int

        enum CodingKeys: String, CodingKey {
            case currentLevel    = "current_level"
            case baseline
            case totalDelta      = "total_delta"
            case tier
            case tierEmoji       = "tier_emoji"
            case assessmentCount = "assessment_count"
            case tournamentCount = "tournament_count"
        }
    }

    // Top skills sorted by currentLevel descending.
    func topSkills(limit: Int = 5) -> [(name: String, data: SkillData)] {
        skills
            .sorted { $0.value.currentLevel > $1.value.currentLevel }
            .prefix(limit)
            .map { (name: $0.key, data: $0.value) }
    }

    enum CodingKeys: String, CodingKey {
        case userLicenseId    = "user_license_id"
        case specialization
        case averageLevel     = "average_level"
        case totalAssessments = "total_assessments"
        case totalTournaments = "total_tournaments"
        case skills
    }
}
