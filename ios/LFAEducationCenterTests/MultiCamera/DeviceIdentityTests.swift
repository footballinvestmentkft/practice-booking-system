import XCTest
@testable import LFAEducationCenter

final class DeviceIdentityTests: XCTestCase {

    override func setUp() {
        super.setUp()
        #if DEBUG
        DeviceIdentity.resetForTesting()
        #endif
    }

    override func tearDown() {
        #if DEBUG
        DeviceIdentity.resetForTesting()
        #endif
        super.tearDown()
    }

    // DI-01: First call generates a UUID and stores it
    func test_DI_01_firstCallGeneratesUUID() {
        let uuid = DeviceIdentity.stableDeviceUUID()
        XCTAssertFalse(uuid.isEmpty, "UUID must not be empty")
    }

    // DI-02: Second call returns the same UUID
    func test_DI_02_secondCallReturnsSameUUID() {
        let first = DeviceIdentity.stableDeviceUUID()
        let second = DeviceIdentity.stableDeviceUUID()
        XCTAssertEqual(first, second, "Stable UUID must be identical on repeated calls")
    }

    // DI-03: logout/clearAll does NOT clear device UUID
    func test_DI_03_logoutClearAllPreservesDeviceUUID() {
        let before = DeviceIdentity.stableDeviceUUID()
        KeychainService.clearAll()
        let after = DeviceIdentity.stableDeviceUUID()
        XCTAssertEqual(before, after, "Device UUID must survive KeychainService.clearAll()")
    }

    // DI-04: UUID format is valid RFC 4122
    func test_DI_04_uuidFormatValid() {
        let uuidString = DeviceIdentity.stableDeviceUUID()
        XCTAssertNotNil(UUID(uuidString: uuidString), "UUID must be parseable as Foundation.UUID")
    }

    // DI-05: logSafeIdentifier does not expose the full UUID
    func test_DI_05_logSafeIdentifierIsTruncated() {
        let full = DeviceIdentity.stableDeviceUUID()
        let safe = DeviceIdentity.logSafeIdentifier()
        XCTAssertTrue(safe.hasPrefix("..."), "Log-safe identifier must start with ...")
        XCTAssertTrue(safe.count < full.count, "Log-safe identifier must be shorter than full UUID")
        let suffix = String(full.suffix(8))
        XCTAssertTrue(safe.contains(suffix), "Log-safe identifier must contain the last 8 chars of the UUID")
    }

    #if DEBUG
    // DI-06: resetForTesting produces a new UUID
    func test_DI_06_resetForTestingProducesNewUUID() {
        let first = DeviceIdentity.stableDeviceUUID()
        DeviceIdentity.resetForTesting()
        let second = DeviceIdentity.stableDeviceUUID()
        XCTAssertNotEqual(first, second, "Reset must generate a fresh UUID")
    }
    #endif
}
