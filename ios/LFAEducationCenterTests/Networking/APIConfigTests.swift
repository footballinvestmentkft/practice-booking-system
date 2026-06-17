import XCTest
@testable import LFAEducationCenter

// MARK: — APIConfigTests (fix/config environment-aware baseURL)
//
// Tests the APIConfig.resolve(rawURL:isReleaseBuild:) static function directly,
// without involving Bundle.main or the xcconfig file.
//
// APIConfig.baseURL itself is not tested here because it reads from the
// test host's Info.plist (which has API_BASE_URL = http://CHANGEME:8000 from
// Config.Debug.xcconfig). Network reachability is outside the scope of these tests.

final class APIConfigTests: XCTestCase {

    // MARK: — Valid URLs

    // AC_01: LAN IP is accepted in Debug mode.
    func test_AC_01_valid_lan_ip_debug_succeeds() throws {
        let url = try APIConfig.resolve(rawURL: "http://192.168.1.55:8000", isReleaseBuild: false)
        XCTAssertEqual(url, "http://192.168.1.55:8000")
    }

    // AC_02: localhost is accepted in Debug mode.
    func test_AC_02_valid_localhost_debug_succeeds() throws {
        let url = try APIConfig.resolve(rawURL: "http://localhost:8000", isReleaseBuild: false)
        XCTAssertEqual(url, "http://localhost:8000")
    }

    // AC_03: HTTPS production URL is accepted in both Debug and Release.
    func test_AC_03_valid_https_url_release_succeeds() throws {
        let url = try APIConfig.resolve(rawURL: "https://api.example.com", isReleaseBuild: true)
        XCTAssertEqual(url, "https://api.example.com")
    }

    // AC_04: HTTPS URL also accepted in Debug.
    func test_AC_04_valid_https_url_debug_succeeds() throws {
        let url = try APIConfig.resolve(rawURL: "https://staging.example.com/api", isReleaseBuild: false)
        XCTAssertEqual(url, "https://staging.example.com/api")
    }

    // MARK: — Missing / empty configuration

    // AC_05: nil rawURL throws notConfigured.
    func test_AC_05_nil_throws_notConfigured() {
        XCTAssertThrowsError(try APIConfig.resolve(rawURL: nil)) { error in
            guard case APIConfig.ConfigError.notConfigured = error else {
                return XCTFail("Expected .notConfigured, got \(error)")
            }
        }
    }

    // AC_06: empty string throws notConfigured.
    func test_AC_06_empty_string_throws_notConfigured() {
        XCTAssertThrowsError(try APIConfig.resolve(rawURL: "")) { error in
            guard case APIConfig.ConfigError.notConfigured = error else {
                return XCTFail("Expected .notConfigured, got \(error)")
            }
        }
    }

    // MARK: — Unexpanded xcconfig placeholder

    // AC_07: literal "$(API_BASE_URL)" throws unexpanded (xcconfig not wired).
    func test_AC_07_unexpanded_xcconfig_throws_unexpanded() {
        XCTAssertThrowsError(try APIConfig.resolve(rawURL: "$(API_BASE_URL)")) { error in
            guard case APIConfig.ConfigError.unexpanded = error else {
                return XCTFail("Expected .unexpanded, got \(error)")
            }
        }
    }

    // AC_08: any value starting with "$(" is treated as unexpanded.
    func test_AC_08_dollar_paren_prefix_throws_unexpanded() {
        XCTAssertThrowsError(try APIConfig.resolve(rawURL: "$(OTHER_VAR)")) { error in
            guard case APIConfig.ConfigError.unexpanded = error else {
                return XCTFail("Expected .unexpanded, got \(error)")
            }
        }
    }

    // MARK: — Invalid URL syntax / unsupported scheme

    // AC_09: schemeless string throws invalidURL.
    // URL(string:) accepts schemeless strings as relative paths, but resolve() requires http/https.
    func test_AC_09_schemeless_string_throws_invalidURL() {
        XCTAssertThrowsError(try APIConfig.resolve(rawURL: "not a url at all")) { error in
            guard case APIConfig.ConfigError.invalidURL = error else {
                return XCTFail("Expected .invalidURL, got \(error)")
            }
        }
    }

    // AC_10: unsupported scheme (ftp) throws invalidURL — only http/https are valid backend schemes.
    func test_AC_10_ftp_scheme_throws_invalidURL() {
        XCTAssertThrowsError(try APIConfig.resolve(rawURL: "ftp://example.com")) { error in
            guard case APIConfig.ConfigError.invalidURL = error else {
                return XCTFail("Expected .invalidURL, got \(error)")
            }
        }
    }

    // MARK: — Dev IP guard in Release build

    // AC_11: 192.168.x.x in Release build throws devIPInRelease.
    func test_AC_11_lan_192_in_release_throws_devIPInRelease() {
        XCTAssertThrowsError(
            try APIConfig.resolve(rawURL: "http://192.168.1.129:8000", isReleaseBuild: true)
        ) { error in
            guard case APIConfig.ConfigError.devIPInRelease = error else {
                return XCTFail("Expected .devIPInRelease, got \(error)")
            }
        }
    }

    // AC_12: 10.x.x.x in Release build throws devIPInRelease.
    func test_AC_12_lan_10_in_release_throws_devIPInRelease() {
        XCTAssertThrowsError(
            try APIConfig.resolve(rawURL: "http://10.0.0.5:8000", isReleaseBuild: true)
        ) { error in
            guard case APIConfig.ConfigError.devIPInRelease = error else {
                return XCTFail("Expected .devIPInRelease, got \(error)")
            }
        }
    }

    // AC_13: localhost in Release build throws devIPInRelease.
    func test_AC_13_localhost_in_release_throws_devIPInRelease() {
        XCTAssertThrowsError(
            try APIConfig.resolve(rawURL: "http://localhost:8000", isReleaseBuild: true)
        ) { error in
            guard case APIConfig.ConfigError.devIPInRelease = error else {
                return XCTFail("Expected .devIPInRelease, got \(error)")
            }
        }
    }

    // AC_14: 127.0.0.1 in Release build throws devIPInRelease.
    func test_AC_14_loopback_in_release_throws_devIPInRelease() {
        XCTAssertThrowsError(
            try APIConfig.resolve(rawURL: "http://127.0.0.1:8000", isReleaseBuild: true)
        ) { error in
            guard case APIConfig.ConfigError.devIPInRelease = error else {
                return XCTFail("Expected .devIPInRelease, got \(error)")
            }
        }
    }

    // AC_15: 172.x.x.x in Release build throws devIPInRelease.
    func test_AC_15_lan_172_in_release_throws_devIPInRelease() {
        XCTAssertThrowsError(
            try APIConfig.resolve(rawURL: "http://172.16.0.1:8000", isReleaseBuild: true)
        ) { error in
            guard case APIConfig.ConfigError.devIPInRelease = error else {
                return XCTFail("Expected .devIPInRelease, got \(error)")
            }
        }
    }

    // MARK: — Source-level guard: no hardcoded dev IP in APIConfig.swift

    // AC_16: the resolved baseURL does not contain the previously hardcoded LAN IP.
    func test_AC_16_baseURL_does_not_contain_old_hardcoded_ip() {
        // The old hardcoded value was "http://192.168.1.129:8000".
        // After this fix, APIConfig.swift must not contain that literal.
        // We assert at the string-resolution level: passing the old IP to resolve
        // in a simulated Release build must FAIL (not silently succeed).
        XCTAssertThrowsError(
            try APIConfig.resolve(rawURL: "http://192.168.1.129:8000", isReleaseBuild: true)
        )
    }

    // MARK: — Error descriptions are non-empty

    // AC_17: all ConfigError cases have non-empty descriptions.
    func test_AC_17_all_error_descriptions_are_non_empty() {
        let errors: [APIConfig.ConfigError] = [
            .notConfigured,
            .unexpanded("$(API_BASE_URL)"),
            .invalidURL("bad url"),
            .devIPInRelease("http://192.168.1.1:8000")
        ]
        for e in errors {
            XCTAssertFalse(e.description.isEmpty,
                           "\(e) must have a non-empty description")
            XCTAssertTrue(e.description.contains("[APIConfig]"),
                          "\(e).description should start with [APIConfig]")
        }
    }
}
