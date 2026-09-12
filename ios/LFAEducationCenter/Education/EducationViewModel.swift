import Foundation

struct EducationTrackSummary: Decodable, Identifiable, Equatable {
    let id: String
    let stableKey: String
    let code: String
    let name: String
    let defaultLocale: String
    let releaseId: String

    enum CodingKeys: String, CodingKey {
        case id, code, name
        case stableKey = "stable_key"
        case defaultLocale = "default_locale"
        case releaseId = "release_id"
    }
}

struct EducationAssessmentVariant: Decodable, Equatable {
    let quizId: Int
    let locale: String
    enum CodingKeys: String, CodingKey { case quizId = "quiz_id"; case locale }
}

struct EducationLessonAssessment: Decodable, Identifiable, Equatable {
    let id: String
    let stableKey: String
    let deliveryMode: String
    let purpose: String
    let difficulty: String?
    let variants: [EducationAssessmentVariant]
    enum CodingKeys: String, CodingKey {
        case id, purpose, difficulty, variants
        case stableKey = "stable_key"
        case deliveryMode = "delivery_mode"
    }
}

indirect enum EducationJSONValue: Decodable, Equatable {
    case string(String), number(Double), boolean(Bool)
    case object([String: EducationJSONValue]), array([EducationJSONValue]), null

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let decoded = try? value.decode(Bool.self) { self = .boolean(decoded) }
        else if let decoded = try? value.decode(Double.self) { self = .number(decoded) }
        else if let decoded = try? value.decode(String.self) { self = .string(decoded) }
        else if let decoded = try? value.decode([String: EducationJSONValue].self) { self = .object(decoded) }
        else if let decoded = try? value.decode([EducationJSONValue].self) { self = .array(decoded) }
        else { throw DecodingError.dataCorruptedError(in: value, debugDescription: "Unsupported education payload") }
    }
}

struct EducationComponent: Decodable, Identifiable, Equatable {
    let id: String
    let stableKey: String
    let type: String
    let name: String
    let payload: [String: EducationJSONValue]
    enum CodingKeys: String, CodingKey { case id, type, name, payload; case stableKey = "stable_key" }
}

struct EducationLesson: Decodable, Identifiable, Equatable {
    let id: String
    let stableKey: String
    let title: String
    let topicKey: String?
    let components: [EducationComponent]
    let assessments: [EducationLessonAssessment]
    enum CodingKeys: String, CodingKey {
        case id, title, components, assessments
        case stableKey = "stable_key"
        case topicKey = "topic_key"
    }
}

struct EducationModule: Decodable, Identifiable, Equatable {
    let id: String
    let stableKey: String
    let name: String
    let lessons: [EducationLesson]
    enum CodingKeys: String, CodingKey { case id, name, lessons; case stableKey = "stable_key" }
}

struct EducationCurriculum: Decodable, Equatable {
    let programId: String
    let trackId: String
    let releaseId: String
    let releaseVersion: Int
    let locale: String
    let modules: [EducationModule]
    enum CodingKeys: String, CodingKey {
        case locale, modules
        case programId = "program_id"
        case trackId = "track_id"
        case releaseId = "release_id"
        case releaseVersion = "release_version"
    }
}

enum PlayerEducationAPI {
    static let tracksPath = "/api/v1/education/programs/LFA_FOOTBALL_PLAYER/tracks"
    static func curriculumPath(trackId: String, locale: String) -> String {
        "/api/v1/education/tracks/\(trackId)/curriculum?locale=\(locale)"
    }
}

// Education Center data layer.
//
// Endpoint mapping:
//   /api/v1/specializations/me            → status  (network error = fatal, other errors = empty state)
//   /api/v1/specializations/              → availableSpecs (non-fatal, public catalog)
//   /api/v1/specializations/progress/me  → progressData (non-fatal, silent-fail OK)
//   /api/v1/lfa-player/licenses/me        → lfaLicense (non-fatal, 404 = not onboarded)
//   /api/v1/progression/skill-profile     → skillProfile (non-fatal, 404 = no license)
//
// Error handling policy:
//   networkError  → .error (connection error shown)
//   unauthorized  → .idle  (AuthManager triggers logout)
//   httpError 4xx/5xx, decodingError on status → status = nil, continue to .loaded (empty state)
//   httpError/decodingError on non-fatal calls → try? swallows, field stays nil
//
// /api/v1/progression/progress is intentionally excluded: returns mock/hardcoded data.
@MainActor
final class EducationViewModel: ObservableObject {

    enum LoadState: Equatable {
        case idle
        case loading
        case loaded
        case error(String)

        static func == (lhs: LoadState, rhs: LoadState) -> Bool {
            switch (lhs, rhs) {
            case (.idle, .idle), (.loading, .loading), (.loaded, .loaded): return true
            case (.error(let a), .error(let b)):                           return a == b
            default:                                                        return false
            }
        }
    }

    @Published private(set) var loadState:      LoadState                             = .idle
    @Published private(set) var status:         SpecializationStatus?                 = nil
    @Published private(set) var availableSpecs: [SpecializationInfo]                  = []
    @Published private(set) var progressData:   [String: SpecializationProgressData]  = [:]
    @Published private(set) var lfaLicense:     LFAPlayerLicense?                     = nil
    @Published private(set) var skillProfile:   SkillProfile?                          = nil
    @Published private(set) var educationTracks: [EducationTrackSummary]               = []
    @Published private(set) var curriculum: EducationCurriculum?                       = nil

    // MARK: — Load (initial, guarded)

    func load(using authManager: AuthManager) async {
        guard case .idle = loadState else { return }
        await fetchData(using: authManager)
    }

    // MARK: — Reload (manual retry)

    func reload(using authManager: AuthManager) async {
        loadState      = .idle
        status         = nil
        availableSpecs = []
        progressData   = [:]
        lfaLicense     = nil
        skillProfile   = nil
        educationTracks = []
        curriculum = nil
        await fetchData(using: authManager)
    }

    // MARK: — Reset (called on logout)

    func reset() {
        loadState      = .idle
        status         = nil
        availableSpecs = []
        progressData   = [:]
        lfaLicense     = nil
        skillProfile   = nil
        educationTracks = []
        curriculum = nil
    }

    // MARK: — Private

    private func fetchData(using authManager: AuthManager) async {
        loadState = .loading

        // 1. Specialization status — precise per-error-type handling.
        //    networkError → fatal (connection error). Other errors → empty state (status = nil).
        //    This prevents misleading "connection error" when backend returns 4xx or schema drifts.
        do {
            status = try await authManager.authenticatedGet(path: "/api/v1/specializations/me")
        } catch APIError.unauthorized {
            loadState = .idle
            return
        } catch APIError.networkError(_) {
            loadState = .error("Could not reach Education Center. Check your connection.")
            return
        } catch {
            // HTTP 4xx/5xx or decodingError — not a connectivity problem.
            // Show empty/onboarding state rather than a misleading connection error.
            status = nil
        }

        // 2. Available specializations catalog — non-fatal (empty list if endpoint fails)
        availableSpecs = (try? await authManager.authenticatedGet(
            path: "/api/v1/specializations/"
        )) ?? []

        // 3. Specialization progress — non-fatal (silent-fail on empty dict or decode error)
        let progressResp: SpecializationProgressResponse? = try? await authManager.authenticatedGet(
            path: "/api/v1/specializations/progress/me"
        )
        progressData = progressResp?.data ?? [:]

        // 4. LFA Player license — non-fatal (404 = user not yet onboarded)
        lfaLicense = try? await authManager.authenticatedGet(
            path: "/api/v1/lfa-player/licenses/me"
        )

        // 5. Skill profile — non-fatal (404 = no active LFA license yet)
        skillProfile = try? await authManager.authenticatedGet(
            path: "/api/v1/progression/skill-profile"
        )

        // 6. Canonical Player education. Draft releases are filtered server-side.
        if lfaLicense != nil {
            educationTracks = (try? await authManager.authenticatedGet(
                path: PlayerEducationAPI.tracksPath
            )) ?? []
            if let first = educationTracks.first {
                curriculum = try? await authManager.authenticatedGet(
                    path: PlayerEducationAPI.curriculumPath(trackId: first.id, locale: "en")
                )
            }
        }

        loadState = .loaded
    }
}
