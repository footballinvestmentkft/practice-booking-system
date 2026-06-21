import XCTest
import Combine
import CoreBluetooth
@testable import LFAEducationCenter

// MARK: — Mock Transports

@MainActor
final class MockGoProBLETransport: @preconcurrency GoProBLETransport {
    weak var delegate: GoProBLETransportDelegate?
    var bluetoothState: CBManagerState = .poweredOn

    var scanStarted = false
    var scanStartCount = 0
    var scanStopped = false
    var connectCalled = false
    var disconnectCalled = false
    var discoverServicesCalled = false
    var subscribeNotificationsCalled = false
    var lastWrittenCommand: Data?
    var lastReadCharUUID: CBUUID?

    nonisolated func startScan() { Task { @MainActor in self.scanStartCount += 1; self.scanStarted = true } }
    nonisolated func stopScan() { Task { @MainActor in self.scanStopped = true } }
    nonisolated func connect(peripheral: GoProPeripheralInfo) { Task { @MainActor in self.connectCalled = true } }
    nonisolated func disconnect() { Task { @MainActor in self.disconnectCalled = true } }
    nonisolated func discoverServices() { Task { @MainActor in self.discoverServicesCalled = true } }
    nonisolated func subscribeNotifications() { Task { @MainActor in self.subscribeNotificationsCalled = true } }
    nonisolated func writeCommand(_ data: Data) { Task { @MainActor in self.lastWrittenCommand = data } }
    nonisolated func readCharacteristic(_ uuid: CBUUID) { Task { @MainActor in self.lastReadCharUUID = uuid } }

    func simulateBTStateUpdate(_ state: CBManagerState) {
        delegate?.bleTransportDidUpdateState(state)
    }
    func simulateDiscover(name: String = "GoPro 1234") {
        let info = GoProPeripheralInfo(identifier: UUID(), name: name, rssi: -50)
        delegate?.bleTransportDidDiscover(info)
    }
    func simulateConnect() { delegate?.bleTransportDidConnect() }
    func simulateFailToConnect() { delegate?.bleTransportDidFailToConnect(error: nil) }
    func simulateDisconnect(error: Error? = nil) { delegate?.bleTransportDidDisconnect(error: error) }
    func simulateServicesDiscovered() { delegate?.bleTransportDidDiscoverServices() }
    func simulateServiceDiscoveryFailed(missing: String) { delegate?.bleTransportDidFailServiceDiscovery(missing: missing) }
    func simulateNotificationsSubscribed() { delegate?.bleTransportDidSubscribeNotifications() }
    func simulateCommandResponse(_ data: Data = Data([0x02, 0x17, 0x00])) {
        delegate?.bleTransportDidReceiveCommandResponse(data)
    }
    func simulateCharRead(uuid: CBUUID, value: Data?) {
        delegate?.bleTransportDidReadCharacteristic(uuid, value: value)
    }
}

@MainActor
final class MockGoProHTTPTransport: @preconcurrency GoProHTTPTransport {
    var isReachableResult = true
    var getResult: Result<Data, Error> = .success(Data())
    var getCallCount = 0
    var isReachableCallCount = 0

    nonisolated func get(path: String, timeout: TimeInterval) async throws -> Data {
        await MainActor.run { getCallCount += 1 }
        return try await MainActor.run { try getResult.get() }
    }

    nonisolated func isReachable(timeout: TimeInterval) async -> Bool {
        await MainActor.run { isReachableCallCount += 1 }
        return await MainActor.run { isReachableResult }
    }
}

@MainActor
final class MockGoProWiFiTransport: @preconcurrency GoProWiFiTransport {
    var joinResult: Result<Void, Error> = .success(())
    var isConnected = false
    var joinCallCount = 0

    nonisolated func joinAccessPoint(ssid: String, password: String) async throws {
        await MainActor.run { joinCallCount += 1 }
        try await MainActor.run { try joinResult.get() }
    }

    nonisolated func isConnectedToGoProAP() -> Bool { return false }
}

// MARK: — Deterministic State Await Helper

@MainActor
extension GoProConnectionStateMachineTests {

    func awaitState(
        _ expected: GoProConnectionState,
        on manager: GoProConnectionManager,
        timeout: TimeInterval = 2.0,
        file: StaticString = #file,
        line: UInt = #line
    ) async {
        if manager.state == expected { return }

        let expectation = XCTestExpectation(description: "Await state: \(expected)")
        var cancellable: AnyCancellable?
        cancellable = manager.$state
            .dropFirst()
            .sink { state in
                if state == expected {
                    expectation.fulfill()
                    cancellable?.cancel()
                }
            }

        let result = await XCTWaiter().fulfillment(of: [expectation], timeout: timeout)
        if result != .completed {
            XCTFail(
                "Timeout waiting for state \(expected). Current: \(manager.state). " +
                "Log: \(manager.diagnosticLog.suffix(5).map { $0.trigger })",
                file: file, line: line
            )
        }
        cancellable?.cancel()
    }

    func awaitStatePredicate(
        _ predicate: @escaping (GoProConnectionState) -> Bool,
        on manager: GoProConnectionManager,
        timeout: TimeInterval = 2.0,
        label: String = "predicate",
        file: StaticString = #file,
        line: UInt = #line
    ) async {
        if predicate(manager.state) { return }

        let expectation = XCTestExpectation(description: "Await \(label)")
        var cancellable: AnyCancellable?
        cancellable = manager.$state
            .dropFirst()
            .sink { state in
                if predicate(state) {
                    expectation.fulfill()
                    cancellable?.cancel()
                }
            }

        let result = await XCTWaiter().fulfillment(of: [expectation], timeout: timeout)
        if result != .completed {
            XCTFail("Timeout: \(label). Current: \(manager.state)", file: file, line: line)
        }
        cancellable?.cancel()
    }
}

// MARK: — Tests

@MainActor
final class GoProConnectionStateMachineTests: XCTestCase {

    private func makeManager() -> (GoProConnectionManager, MockGoProBLETransport, MockGoProHTTPTransport, MockGoProWiFiTransport) {
        let ble = MockGoProBLETransport()
        let http = MockGoProHTTPTransport()
        let wifi = MockGoProWiFiTransport()
        let mgr = GoProConnectionManager(bleTransport: ble, httpTransport: http, wifiTransport: wifi)
        return (mgr, ble, http, wifi)
    }

    // SM-01: Initial state is idle
    func test_SM_01_initialStateIdle() {
        let (mgr, _, _, _) = makeManager()
        XCTAssertEqual(mgr.state, .idle)
    }

    // SM-02: Start → discovering
    func test_SM_02_startConnection_discovering() async {
        let (mgr, _, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false },
                                  on: mgr, label: "discovering")
    }

    // SM-03: Bluetooth off → bluetoothUnavailable
    func test_SM_03_bluetoothOff() async {
        let (mgr, ble, _, _) = makeManager()
        ble.bluetoothState = .poweredOff
        mgr.startConnection()
        await awaitStatePredicate({ if case .bluetoothUnavailable = $0 { return true }; return false },
                                  on: mgr, label: "bluetoothUnavailable")
    }

    // SM-04: Discovery → peripheral → connecting
    func test_SM_04_peripheralDiscovered_connecting() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false },
                                  on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
    }

    // SM-05: Connect → discoveringServices
    func test_SM_05_connectSuccess_discoveringServices() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
    }

    // SM-06: Connect failure → failed
    func test_SM_06_connectFailure_failed() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateFailToConnect()
        await awaitStatePredicate({ if case .failed(.connectFailed) = $0 { return true }; return false },
                                  on: mgr, label: "failed.connectFailed")
    }

    // SM-07: Services → establishingControl
    func test_SM_07_servicesDiscovered_establishingControl() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
    }

    // SM-08: Notifications → enablingAP
    func test_SM_08_notificationsSubscribed_enablingAP() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
    }

    // SM-09: Cancel → failed(.cancelled)
    func test_SM_09_cancelDuringDiscovery() async {
        let (mgr, _, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        mgr.cancel()
        XCTAssertEqual(mgr.state, .failed(.cancelled))
    }

    // SM-10: Disconnect from ready → idle
    func test_SM_10_disconnectFromReady() async {
        let (mgr, ble, http, _) = makeManager()
        let statusJSON = """
        {"firmware_version":"2.30","is_recording":false,"battery_level":85}
        """.data(using: .utf8)!
        http.getResult = .success(statusJSON)
        http.isReachableResult = true

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        mgr.confirmManualWiFiJoined()
        await awaitStatePredicate({ if case .ready = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "ready")

        mgr.disconnect()
        await awaitState(.disconnecting, on: mgr)
        ble.simulateDisconnect()
        await awaitState(.idle, on: mgr)
    }

    // SM-11: Unexpected disconnect → failed
    func test_SM_11_unexpectedDisconnect() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateDisconnect(error: NSError(domain: "test", code: 1))
        await awaitState(.failed(.disconnectedUnexpectedly), on: mgr)
    }

    // SM-12: Retry after recoverable failure
    func test_SM_12_retryAfterFailure() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateFailToConnect()
        await awaitStatePredicate({ if case .failed = $0 { return true }; return false }, on: mgr, label: "failed")
        mgr.retry()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "re-discovering")
    }

    // SM-13: Non-recoverable → retry blocked
    func test_SM_13_nonRecoverableNoRetry() async {
        let (mgr, ble, _, _) = makeManager()
        ble.bluetoothState = .poweredOff
        mgr.startConnection()
        await awaitStatePredicate({ if case .bluetoothUnavailable = $0 { return true }; return false }, on: mgr, label: "btOff")
        mgr.retry()
        // Still in bluetoothUnavailable
        if case .bluetoothUnavailable = mgr.state { } else {
            XCTFail("Expected bluetoothUnavailable after retry, got \(mgr.state)")
        }
    }

    // SM-14: Unsupported firmware → failed
    func test_SM_14_unsupportedFirmware() async {
        let (mgr, ble, http, _) = makeManager()
        let statusJSON = """
        {"firmware_version":"1.50","is_recording":false,"battery_level":85}
        """.data(using: .utf8)!
        http.getResult = .success(statusJSON)
        http.isReachableResult = true

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        mgr.confirmManualWiFiJoined()
        await awaitStatePredicate({ if case .failed(.unsupportedFirmware) = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "unsupportedFirmware")
    }

    // SM-15: Diagnostic log records
    func test_SM_15_diagnosticLogRecords() async {
        let (mgr, _, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        XCTAssertGreaterThan(mgr.diagnosticLog.count, 0)
        XCTAssertEqual(mgr.diagnosticLog.first?.trigger, "user_initiated")
    }

    // SM-16: Start blocked from active state
    func test_SM_16_startBlockedFromActiveState() async {
        let (mgr, _, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        mgr.startConnection() // should be no-op
        if case .discovering(let attempt) = mgr.state {
            XCTAssertEqual(attempt, 1)
        } else {
            XCTFail("State should still be discovering")
        }
    }

    // SM-17: AP credentials → awaitingManualWiFiJoin with SSID
    func test_SM_17_manualWiFiJoinState() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({
            if case .awaitingManualWiFiJoin(let ssid) = $0 { return ssid == "GP12345" }; return false
        }, on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
    }

    // SM-18: Stale callback ignored
    func test_SM_18_staleCallbackIgnored() async {
        let (mgr, ble, _, _) = makeManager()
        ble.delegate = mgr
        // Call connect callback while in idle → should be ignored
        ble.simulateConnect()
        // Give time for any spurious Task to run
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(mgr.state, .idle)
    }

    // SM-19: Camera status populated on ready
    func test_SM_19_cameraStatusOnReady() async {
        let (mgr, ble, http, _) = makeManager()
        let statusJSON = """
        {"firmware_version":"2.30","is_recording":false,"battery_level":72,"sd_card_space_remaining":4096}
        """.data(using: .utf8)!
        http.getResult = .success(statusJSON)
        http.isReachableResult = true

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        mgr.confirmManualWiFiJoined()
        await awaitStatePredicate({ if case .ready = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "ready")
        XCTAssertEqual(mgr.cameraStatus?.batteryLevel, 72)
        XCTAssertEqual(mgr.cameraStatus?.firmwareVersion, "2.30")
    }

    // SM-20: Full flow happy path
    func test_SM_20_fullFlowHappyPath() async {
        let (mgr, ble, http, _) = makeManager()
        let statusJSON = """
        {"firmware_version":"2.30","is_recording":false,"battery_level":90}
        """.data(using: .utf8)!
        http.getResult = .success(statusJSON)
        http.isReachableResult = true

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        mgr.confirmManualWiFiJoined()
        await awaitStatePredicate({ if case .ready = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "ready")
        if case .ready(let fw) = mgr.state {
            XCTAssertEqual(fw, "2.30")
        } else {
            XCTFail("Expected .ready, got \(mgr.state)")
        }
    }

    // SM-21: .unknown → .poweredOn triggers exactly one scan
    func test_SM_21_unknownToPoweredOn_singleScan() async {
        let (mgr, ble, _, _) = makeManager()
        ble.bluetoothState = .unknown
        mgr.startConnection()
        await awaitState(.waitingForBluetooth, on: mgr)
        XCTAssertEqual(ble.scanStartCount, 0)
        ble.simulateBTStateUpdate(.poweredOn)
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false },
                                  on: mgr, label: "discovering")
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(ble.scanStartCount, 1)
    }

    // SM-22: .unknown does not generate terminal error
    func test_SM_22_unknownIsNotTerminal() async {
        let (mgr, ble, _, _) = makeManager()
        ble.bluetoothState = .unknown
        mgr.startConnection()
        await awaitState(.waitingForBluetooth, on: mgr)
        XCTAssertFalse(mgr.state.isTerminal)
        if case .bluetoothUnavailable = mgr.state { XCTFail("Should not be bluetoothUnavailable") }
        if case .failed = mgr.state { XCTFail("Should not be failed") }
    }

    // SM-23: .poweredOff → explicit retry after BT turned on
    func test_SM_23_poweredOffRecovery() async {
        let (mgr, ble, _, _) = makeManager()
        ble.bluetoothState = .poweredOff
        mgr.startConnection()
        await awaitStatePredicate({ if case .bluetoothUnavailable = $0 { return true }; return false },
                                  on: mgr, label: "btOff")
        // BT turned on, but no auto-recovery — user must tap Connect again
        ble.simulateBTStateUpdate(.poweredOn)
        try? await Task.sleep(nanoseconds: 50_000_000)
        if case .bluetoothUnavailable = mgr.state { } else {
            XCTFail("Expected bluetoothUnavailable (no auto-recovery), got \(mgr.state)")
        }
        // Explicit retry via startConnection
        ble.bluetoothState = .poweredOn
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false },
                                  on: mgr, label: "discovering after retry")
    }

    // SM-24: .unauthorized → no scan, no auto-recovery
    func test_SM_24_unauthorizedNoScan() async {
        let (mgr, ble, _, _) = makeManager()
        ble.bluetoothState = .unauthorized
        mgr.startConnection()
        await awaitStatePredicate({ if case .bluetoothUnavailable = $0 { return true }; return false },
                                  on: mgr, label: "btUnauthorized")
        XCTAssertEqual(ble.scanStartCount, 0)
        ble.simulateBTStateUpdate(.poweredOn)
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(ble.scanStartCount, 0)
    }

    // SM-25: Multiple Connect taps during waitingForBluetooth — no parallel scans
    func test_SM_25_duplicateConnectBlocked() async {
        let (mgr, ble, _, _) = makeManager()
        ble.bluetoothState = .unknown
        mgr.startConnection()
        await awaitState(.waitingForBluetooth, on: mgr)
        mgr.startConnection()
        XCTAssertEqual(mgr.state, .waitingForBluetooth)
        ble.simulateBTStateUpdate(.poweredOn)
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false },
                                  on: mgr, label: "discovering")
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(ble.scanStartCount, 1)
    }

    // SM-26: Cancel during waitingForBluetooth prevents later auto-scan
    func test_SM_26_cancelDuringWaiting() async {
        let (mgr, ble, _, _) = makeManager()
        ble.bluetoothState = .unknown
        mgr.startConnection()
        await awaitState(.waitingForBluetooth, on: mgr)
        mgr.cancel()
        XCTAssertEqual(mgr.state, .failed(.cancelled))
        ble.simulateBTStateUpdate(.poweredOn)
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(mgr.state, .failed(.cancelled))
        XCTAssertEqual(ble.scanStartCount, 0)
    }

    // SM-27: Both services discovered → proceeds to establishingControl
    func test_SM_27_dualServiceDiscovery() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
    }

    // SM-28: SSID UUID is B5F90002 (not B5F90003)
    func test_SM_28_correctSSIDCharUUID() {
        XCTAssertEqual(
            GoProSpec.wifiSSIDCharUUID,
            CBUUID(string: "B5F90002-AA8D-11E3-9046-0002A5D5C51B")
        )
    }

    // SM-29: Password UUID is B5F90003 (not B5F90004)
    func test_SM_29_correctPasswordCharUUID() {
        XCTAssertEqual(
            GoProSpec.wifiPasswordCharUUID,
            CBUUID(string: "B5F90003-AA8D-11E3-9046-0002A5D5C51B")
        )
    }

    // SM-30: Missing WiFi AP service → explicit failure
    func test_SM_30_missingServiceExplicitFailure() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServiceDiscoveryFailed(missing: "WiFiAP(B5F90001)")
        await awaitStatePredicate({ if case .failed(.serviceDiscoveryFailed) = $0 { return true }; return false },
                                  on: mgr, label: "serviceDiscoveryFailed")
        let trigger = mgr.diagnosticLog.last?.trigger ?? ""
        XCTAssertTrue(trigger.contains("WiFiAP"), "Trigger should name missing service: \(trigger)")
    }

    // SM-31: Missing characteristic → explicit failure
    func test_SM_31_missingCharExplicitFailure() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServiceDiscoveryFailed(missing: "SSID(B5F90002)")
        await awaitStatePredicate({ if case .failed(.serviceDiscoveryFailed) = $0 { return true }; return false },
                                  on: mgr, label: "serviceDiscoveryFailed")
        let trigger = mgr.diagnosticLog.last?.trigger ?? ""
        XCTAssertTrue(trigger.contains("SSID"), "Trigger should name missing char: \(trigger)")
    }

    // SM-32: Credentials arrive in either order (password first, then SSID)
    func test_SM_32_credentialsEitherOrder() async {
        let (mgr, ble, http, _) = makeManager()
        let statusJSON = """
        {"firmware_version":"2.30","is_recording":false,"battery_level":85}
        """.data(using: .utf8)!
        http.getResult = .success(statusJSON)
        http.isReachableResult = true

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        // Password first, then SSID (reversed order)
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        mgr.confirmManualWiFiJoined()
        await awaitStatePredicate({ if case .ready = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "ready")
    }

    // SM-33: Command response + credential reads race — proceedToWiFiJoin fires only once
    func test_SM_33_commandAndCredentialRace() async {
        let (mgr, ble, http, _) = makeManager()
        let statusJSON = """
        {"firmware_version":"2.30","is_recording":false,"battery_level":85}
        """.data(using: .utf8)!
        http.getResult = .success(statusJSON)
        http.isReachableResult = true

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        // Both credentials arrive, then command response (race scenario)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        ble.simulateCommandResponse()
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        mgr.confirmManualWiFiJoined()
        await awaitStatePredicate({ if case .ready = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "ready")
    }

    // SM-34: BLE disconnect during awaitingManualWiFiJoin preserves state
    func test_SM_34_bleDisconnectPreservesManualWiFiState() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        ble.simulateDisconnect()
        try? await Task.sleep(nanoseconds: 50_000_000)
        if case .awaitingManualWiFiJoin(let ssid) = mgr.state {
            XCTAssertEqual(ssid, "GP12345")
        } else {
            XCTFail("Expected awaitingManualWiFiJoin, got \(mgr.state)")
        }
    }

    // SM-35: foreground triggers exactly one HTTP verify
    func test_SM_35_foregroundTriggersOneVerify() async {
        let (mgr, ble, http, _) = makeManager()
        let statusJSON = """
        {"firmware_version":"2.30","is_recording":false,"battery_level":85}
        """.data(using: .utf8)!
        http.getResult = .success(statusJSON)
        http.isReachableResult = true

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        ble.simulateDisconnect()
        try? await Task.sleep(nanoseconds: 50_000_000)
        let countBefore = http.getCallCount
        mgr.onForeground()
        mgr.onForeground()
        await awaitStatePredicate({ if case .ready = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "ready")
        XCTAssertEqual(http.getCallCount - countBefore, 1, "Exactly one fetchCameraState call")
    }

    // SM-36: HTTP success after foreground → ready
    func test_SM_36_foregroundHTTPSuccessReady() async {
        let (mgr, ble, http, _) = makeManager()
        let statusJSON = """
        {"firmware_version":"2.30","is_recording":false,"battery_level":90}
        """.data(using: .utf8)!
        http.getResult = .success(statusJSON)
        http.isReachableResult = true

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        mgr.onForeground()
        await awaitStatePredicate({ if case .ready = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "ready")
        if case .ready(let fw) = mgr.state {
            XCTAssertEqual(fw, "2.30")
        }
    }

    // SM-37: HTTP failure → back to awaitingManualWiFiJoin (retryable)
    func test_SM_37_httpFailureRetryable() async {
        let (mgr, ble, http, _) = makeManager()
        http.isReachableResult = false

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        mgr.confirmManualWiFiJoined()
        await awaitStatePredicate({
            if case .awaitingManualWiFiJoin(let ssid) = $0 { return ssid == "GP12345" }; return false
        }, on: mgr, timeout: 3.0, label: "back to awaitingManualWiFiJoin")
        let trigger = mgr.diagnosticLog.last?.trigger ?? ""
        XCTAssertEqual(trigger, "http_not_ready")
    }

    // SM-38: BLE disconnect in background doesn't trigger discovery
    func test_SM_38_bleDisconnectNoBgDiscovery() async {
        let (mgr, ble, _, _) = makeManager()
        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        ble.scanStartCount = 0
        ble.simulateDisconnect()
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(ble.scanStartCount, 0)
    }

    // SM-39: multiple foreground events don't create parallel requests
    func test_SM_39_noParallelForegroundRequests() async {
        let (mgr, ble, http, _) = makeManager()
        http.isReachableResult = false

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        let countBefore = http.isReachableCallCount
        mgr.onForeground()
        mgr.onForeground()
        mgr.onForeground()
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "back after fail")
        XCTAssertEqual(http.isReachableCallCount - countBefore, 1, "Only one HTTP check despite 3 foreground events")
    }

    // SM-40: Cancel clears pending manual Wi-Fi context
    func test_SM_40_cancelClearsManualContext() async {
        let (mgr, ble, http, _) = makeManager()
        http.isReachableResult = true
        let statusJSON = """
        {"firmware_version":"2.30","is_recording":false,"battery_level":85}
        """.data(using: .utf8)!
        http.getResult = .success(statusJSON)

        mgr.startConnection()
        await awaitStatePredicate({ if case .discovering = $0 { return true }; return false }, on: mgr, label: "discovering")
        ble.simulateDiscover()
        await awaitState(.connecting, on: mgr)
        ble.simulateConnect()
        await awaitState(.discoveringServices, on: mgr)
        ble.simulateServicesDiscovered()
        await awaitState(.establishingControl, on: mgr)
        ble.simulateNotificationsSubscribed()
        await awaitState(.enablingAccessPoint, on: mgr)
        ble.simulateCharRead(uuid: GoProSpec.wifiSSIDCharUUID, value: "GP12345".data(using: .utf8))
        ble.simulateCharRead(uuid: GoProSpec.wifiPasswordCharUUID, value: "pass123".data(using: .utf8))
        await awaitStatePredicate({ if case .awaitingManualWiFiJoin = $0 { return true }; return false },
                                  on: mgr, timeout: 3.0, label: "awaitingManualWiFiJoin")
        mgr.cancel()
        XCTAssertEqual(mgr.state, .failed(.cancelled))
        mgr.onForeground()
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(mgr.state, .failed(.cancelled))
    }
}
