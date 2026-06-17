import Foundation

// MARK: — APIConfig
//
// Single source of truth for the backend base URL.
//
// The URL is resolved at app launch from the Info.plist key "APIBaseURL",
// which is populated by the xcconfig build setting API_BASE_URL:
//
//   Debug:   LFAEducationCenter/Config/Config.Debug.xcconfig
//            Set API_BASE_URL to your Mac's current LAN IP before running on a physical device.
//            Example: API_BASE_URL = http://192.168.1.129:8000
//            Simulator: API_BASE_URL = http://localhost:8000
//
//   Release: LFAEducationCenter/Config/Config.Release.xcconfig
//            Set API_BASE_URL to the production HTTPS URL before archiving.
//
// Error behaviour:
//   - Missing / empty / unexpanded value → fatalError at app launch (programmer error)
//   - Invalid URL syntax → fatalError at app launch
//   - LAN IP / localhost in a Release build → fatalError at app launch
//
// All errors are loud and immediate — no silent timeout or fallback to a hardcoded IP.

enum APIConfig {

    // Resolved once at first access. fatalError on misconfiguration.
    static let baseURL: String = {
        let raw = Bundle.main.object(forInfoDictionaryKey: "APIBaseURL") as? String
        do {
            let resolved = try resolve(rawURL: raw)
            #if DEBUG
            print("[APIConfig] ▶ baseURL=\(resolved)")
            #endif
            return resolved
        } catch let e as ConfigError {
            fatalError(e.description)
        } catch {
            fatalError("[APIConfig] Unexpected error: \(error)")
        }
    }()

    static let v1:           String = baseURL + "/api/v1"
    static let verifyBaseURL: String = baseURL

    // MARK: — Errors (public for tests)

    enum ConfigError: Error, CustomStringConvertible {
        case notConfigured
        case unexpanded(String)
        case invalidURL(String)
        case devIPInRelease(String)

        var description: String {
            switch self {
            case .notConfigured:
                return """
                [APIConfig] API_BASE_URL is not configured.
                Set API_BASE_URL in LFAEducationCenter/Config/Config.Debug.xcconfig \
                (Debug) or Config.Release.xcconfig (Release) and make sure the xcconfig \
                file is assigned to the build configuration in Xcode project settings.
                """
            case .unexpanded(let raw):
                return """
                [APIConfig] API_BASE_URL was not expanded by Xcode — found literal '\(raw)'.
                Verify that Config.Debug.xcconfig / Config.Release.xcconfig is set as the \
                base configuration for the active build configuration in the project editor.
                """
            case .invalidURL(let raw):
                return "[APIConfig] API_BASE_URL '\(raw)' is not a valid URL."
            case .devIPInRelease(let raw):
                return """
                [APIConfig] Release build must not use a development/LAN IP or localhost.
                Found: '\(raw)'. Set API_BASE_URL in Config.Release.xcconfig to a \
                production HTTPS URL before archiving.
                """
            }
        }
    }

    // MARK: — Resolution logic (internal for unit tests)
    //
    // `isReleaseBuild` defaults to true in Release, false in Debug.
    // Tests can override it to exercise the Release path without a real Release build.

    #if DEBUG
    static let _isReleaseBuild = false
    #else
    static let _isReleaseBuild = true
    #endif

    static func resolve(
        rawURL:         String?,
        isReleaseBuild: Bool = APIConfig._isReleaseBuild
    ) throws -> String {
        // 1. Must be present and non-empty.
        guard let raw = rawURL, !raw.isEmpty else {
            throw ConfigError.notConfigured
        }

        // 2. Must not be an unexpanded xcconfig placeholder.
        guard !raw.hasPrefix("$(") else {
            throw ConfigError.unexpanded(raw)
        }

        // 3. Must be a syntactically valid URL with an http or https scheme.
        // Note: URL(string:) is permissive — it accepts schemeless strings as relative paths.
        // Explicit scheme check ensures only absolute backend URLs are accepted.
        guard let parsed = URL(string: raw),
              let scheme = parsed.scheme,
              ["http", "https"].contains(scheme.lowercased()) else {
            throw ConfigError.invalidURL(raw)
        }

        // 4. Release builds must not use a development/LAN IP or localhost.
        if isReleaseBuild {
            let lower = raw.lowercased()
            let devPrefixes = [
                "http://192.", "http://10.", "http://172.",
                "http://localhost", "http://127."
            ]
            if devPrefixes.contains(where: { lower.hasPrefix($0) }) {
                throw ConfigError.devIPInRelease(raw)
            }
        }

        return raw
    }
}
