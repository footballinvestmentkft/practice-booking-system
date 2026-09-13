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

// Canonical PC5 projection. Values come from the same server-side progression
// authority used by assessment and instructor skill commands.
enum PlayerProgressionAPI {
    static let statePath = "/api/v1/progression/player-state"
    static let historyPath = "/api/v1/progression/player-history"
}

struct PlayerProgressionState: Decodable {
    let playerUserId: Int
    let specializationId: String
    let xp: Int
    let level: Int
    let skills: [CanonicalPlayerSkill]
    let achievements: [CanonicalPlayerAchievement]

    enum CodingKeys: String, CodingKey {
        case playerUserId = "player_user_id"
        case specializationId = "specialization_id"
        case xp, level, skills, achievements
    }
}

struct CanonicalPlayerSkill: Decodable {
    let key: String
    let value: Double
    let lastOutcomeId: String

    enum CodingKeys: String, CodingKey {
        case key, value
        case lastOutcomeId = "last_outcome_id"
    }
}

struct CanonicalPlayerAchievement: Decodable {
    let key: String
    let badgeType: String
    let title: String
    let earnedAt: String

    enum CodingKeys: String, CodingKey {
        case key, title
        case badgeType = "badge_type"
        case earnedAt = "earned_at"
    }
}
