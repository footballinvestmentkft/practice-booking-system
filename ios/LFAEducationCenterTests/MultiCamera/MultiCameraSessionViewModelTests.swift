import XCTest
@testable import LFAEducationCenter

// MARK: — Mock API Client

final class MockMultiCameraAPIClientP1: MultiCameraAPIClientProtocol, @unchecked Sendable {

    var registerDeviceCallCount = 0
    // Closure receives call count (1-based); throw to simulate failure.
    var registerDeviceHandler: (Int) throws -> SessionDeviceDTO = { _ in
        throw NSError(domain: "MockNetwork", code: -1,
                      userInfo: [NSLocalizedDescriptionKey: "network error"])
    }

    var updateDeviceStatusCallCount = 0
    var updateDeviceStatusRevisionUsed = 0
    var updateDeviceStatusHandler: () throws -> SessionDeviceDTO = {
        throw NSError(domain: "MockNetwork", code: -1, userInfo: nil)
    }

    var getSessionCallCount = 0
    var getSessionHandler: () throws -> MultiCameraSessionDTO = {
        throw NSError(domain: "MockNetwork", code: -1, userInfo: nil)
    }

    var transitionCallCount = 0
    var transitionHandler: () throws -> MultiCameraSessionDTO = {
        throw NSError(domain: "MockNetwork", code: -1, userInfo: nil)
    }

    func registerDevice(token: String, uuid: String, request: RegisterDeviceRequest) async throws -> SessionDeviceDTO {
        registerDeviceCallCount += 1
        return try registerDeviceHandler(registerDeviceCallCount)
    }

    func updateDeviceStatus(token: String, uuid: String, sessionDeviceId: Int,
                            targetStatus: MCDeviceStatus, deviceRevision: Int) async throws -> SessionDeviceDTO {
        updateDeviceStatusCallCount += 1
        updateDeviceStatusRevisionUsed = deviceRevision
        return try updateDeviceStatusHandler()
    }

    func getSession(token: String, uuid: String) async throws -> MultiCameraSessionDTO {
        getSessionCallCount += 1
        return try getSessionHandler()
    }

    func transitionSession(token: String, uuid: String, target: SessionStatus,
                           revision: Int) async throws -> MultiCameraSessionDTO {
        transitionCallCount += 1
        return try transitionHandler()
    }
}

// MARK: — DTO Factories

private func makeSDevice(
    id: Int = 1, sessionId: Int = 1, deviceId: Int = 100,
    participantId: Int? = nil, managedByDeviceId: Int? = nil,
    role: MCDeviceRole = .playerPrimary, status: MCDeviceStatus = .ready,
    revision: Int = 1, removedAt: String? = nil
) -> SessionDeviceDTO {
    SessionDeviceDTO(
        id: id, sessionId: sessionId, deviceId: deviceId,
        participantId: participantId, managedByDeviceId: managedByDeviceId,
        deviceRole: role, status: status, revision: revision,
        lastHeartbeat: nil, registeredAt: "2026-06-24T10:00:00Z",
        removedAt: removedAt
    )
}

private func makeParticipant(
    id: Int = 1, userId: Int = 42, role: ParticipantRole = .instructor
) -> SessionParticipantDTO {
    SessionParticipantDTO(
        id: id, sessionId: 1, userId: userId, role: role,
        revision: 1, joinedAt: "2026-06-24T10:00:00Z", leftAt: nil
    )
}

private func makeSSession(
    uuid: String = "test-uuid-0001",
    status: SessionStatus = .devicesReady,
    revision: Int = 3,
    participants: [SessionParticipantDTO] = [],
    devices: [SessionDeviceDTO] = [],
    scheduledStartAt: String? = nil
) -> MultiCameraSessionDTO {
    MultiCameraSessionDTO(
        id: 1, sessionUuid: uuid, status: status, createdByUserId: 42,
        maxParticipants: 2, maxDevices: 4, revision: revision,
        calibration: nil, scheduledStartAt: scheduledStartAt,
        createdAt: "2026-06-24T10:00:00Z",
        startedAt: nil, stoppedAt: nil, finalizedAt: nil, cancelledAt: nil,
        participants: participants, devices: devices, streams: []
    )
}

// MARK: — Tests

@MainActor
final class MultiCameraSessionViewModelTests: XCTestCase {

    private var mock: MockMultiCameraAPIClientP1!
    private var vm: MultiCameraSessionViewModel!

    override func setUp() async throws {
        try await super.setUp()
        mock = MockMultiCameraAPIClientP1()
        vm = MultiCameraSessionViewModel(
            authManager: AuthManager(),
            apiClient: mock,
            tokenProvider: { "test-token" },
            pollingIntervalSeconds: 9_999,
            heartbeatIntervalSeconds: 9_999
        )
    }

    override func tearDown() async throws {
        UserDefaults.standard.removeObject(forKey: "lfa_current_user_id")
        try await super.tearDown()
    }

    // MARK: — LVM-01: autoRegisterDevice succeeds on first attempt

    func test_LVM_01_registerSucceedsFirstAttempt() async throws {
        let device = makeSDevice(id: 54, status: .registered, revision: 7)
        mock.registerDeviceHandler = { _ in device }

        await vm.autoRegisterDevice(sessionUuid: "test-uuid", participantId: 1)

        XCTAssertEqual(vm.sessionDeviceId, 54, "sessionDeviceId must be set from response id")
        XCTAssertEqual(vm.sessionDeviceRevision, 7, "revision must come from response, not hardcoded")
        XCTAssertEqual(mock.registerDeviceCallCount, 1, "should make exactly one call on success")
        if case .error(let m) = vm.state { XCTFail("state must not be .error on success: \(m)") }
    }

    // MARK: — LVM-02: autoRegisterDevice fails on 1st, succeeds on 2nd

    func test_LVM_02_registerSucceedsOnSecondAttempt() async throws {
        let device = makeSDevice(id: 54, status: .registered, revision: 3)
        let netErr = NSError(domain: "MockNetwork", code: -1, userInfo: nil)
        mock.registerDeviceHandler = { callCount in
            if callCount == 1 { throw netErr }
            return device
        }

        await vm.autoRegisterDevice(sessionUuid: "test-uuid", participantId: 1)

        XCTAssertEqual(vm.sessionDeviceId, 54)
        XCTAssertEqual(vm.sessionDeviceRevision, 3)
        XCTAssertEqual(mock.registerDeviceCallCount, 2, "should retry once and succeed")
        if case .error(let m) = vm.state { XCTFail("state must not be .error on eventual success: \(m)") }
    }

    // MARK: — LVM-03: autoRegisterDevice fails all 3 → visible error, never silent

    func test_LVM_03_registerAllThreeAttemptsFail_visibleError() async throws {
        let netErr = NSError(domain: "MockNetwork", code: -1, userInfo: nil)
        mock.registerDeviceHandler = { _ in throw netErr }

        await vm.autoRegisterDevice(sessionUuid: "test-uuid", participantId: 1)

        XCTAssertNil(vm.sessionDeviceId, "sessionDeviceId must remain nil after total failure")
        XCTAssertEqual(mock.registerDeviceCallCount, 3, "must attempt exactly 3 times")
        guard case .error(let msg) = vm.state else {
            XCTFail("state must be .error after all 3 failures; was: \(vm.state)")
            return
        }
        XCTAssertFalse(msg.isEmpty, "error message must not be empty")
    }

    // MARK: — LVM-04: _patchDeviceReady uses actual revision from registration, not hardcoded 1

    func test_LVM_04_patchUsesRevisionFromRegistration_notHardcoded() async throws {
        // Step 1: Register with revision=7
        let regDevice = makeSDevice(id: 54, revision: 7)
        mock.registerDeviceHandler = { _ in regDevice }
        await vm.autoRegisterDevice(sessionUuid: "test-uuid", participantId: 1)
        XCTAssertEqual(vm.sessionDeviceRevision, 7, "revision must be 7 after registration")

        // Step 2: PATCH ready — the call must use revision=7
        let patchResult = makeSDevice(id: 54, status: .ready, revision: 8)
        mock.updateDeviceStatusHandler = { patchResult }
        vm._setLobbyStateForTesting(makeSSession())

        await vm._patchDeviceReady(token: "tok", sessionUuid: "test-uuid", sdId: 54)

        XCTAssertEqual(mock.updateDeviceStatusRevisionUsed, 7,
                       "PATCH must send revision=7 from registration; hardcoded 1 would fail this")
        XCTAssertEqual(vm.sessionDeviceRevision, 8,
                       "sessionDeviceRevision must update to response revision after PATCH")
    }

    // MARK: — LVM-05: _patchDeviceReady 409 is surfaced as error, never swallowed

    func test_LVM_05_patchDeviceReady_409NotSwallowed() async throws {
        let conflictErr = NSError(domain: "APIClient", code: 409,
                                  userInfo: [NSLocalizedDescriptionKey: "revision conflict"])
        mock.updateDeviceStatusHandler = { throw conflictErr }
        vm._setLobbyStateForTesting(makeSSession())

        await vm._patchDeviceReady(token: "tok", sessionUuid: "test-uuid", sdId: 54)

        guard case .error(let msg) = vm.state else {
            XCTFail("state must be .error after 409; was: \(vm.state)")
            return
        }
        XCTAssertFalse(msg.isEmpty, "error must not be empty on 409")
    }

    // MARK: — LVM-06: parseScheduledDate parses fractional seconds

    func test_LVM_06_parseScheduledDate_fractionalSeconds() {
        let iso = "2026-06-24T10:05:30.123456+00:00"
        let result = MultiCameraSessionViewModel.parseScheduledDate(iso)
        XCTAssertNotNil(result, "fractional ISO8601 must parse; plain formatter drops fractional seconds")
        XCTAssertGreaterThan(result!.timeIntervalSince1970, 0)
    }

    // MARK: — LVM-07: parseScheduledDate parses plain Internet date (no fractional seconds)

    func test_LVM_07_parseScheduledDate_noFractionalSeconds() {
        let iso = "2026-06-24T10:05:30+00:00"
        let result = MultiCameraSessionViewModel.parseScheduledDate(iso)
        XCTAssertNotNil(result, "plain ISO8601 without fractional seconds must also parse")
    }

    // MARK: — LVM-08: parseScheduledDate returns nil for invalid string (not silently wrong date)

    func test_LVM_08_parseScheduledDate_invalidString_returnsNil() {
        XCTAssertNil(MultiCameraSessionViewModel.parseScheduledDate("not-a-date"))
        XCTAssertNil(MultiCameraSessionViewModel.parseScheduledDate(""))
        XCTAssertNil(MultiCameraSessionViewModel.parseScheduledDate("2026-06-24"))
    }

    // MARK: — LVM-09: startCapture with registered device → blocks, names the device

    func test_LVM_09_startCapture_registeredDevice_blocksAndNamesDevice() async throws {
        UserDefaults.standard.set(42, forKey: "lfa_current_user_id")
        let instructor = makeParticipant(id: 1, userId: 42, role: .instructor)
        let ipadDev = makeSDevice(id: 10, role: .instructorPrimary, status: .ready)
        let iphoneDev = makeSDevice(id: 11, role: .playerPrimary, status: .registered)
        let session = makeSSession(
            status: .devicesReady, revision: 3,
            participants: [instructor], devices: [ipadDev, iphoneDev]
        )
        vm._setLobbyStateForTesting(session)
        vm.orchestrator.orchestrationState = .armed
        mock.getSessionHandler = { session }

        await vm.startCapture()

        XCTAssertEqual(mock.getSessionCallCount, 1, "must do exactly one fresh GET")
        XCTAssertEqual(mock.transitionCallCount, 0, "must not call transition when device not ready")
        XCTAssertNotNil(vm.deviceNotReadyMessage,
                        "deviceNotReadyMessage must be set to name the blocking device")
        let msg = vm.deviceNotReadyMessage!
        XCTAssertTrue(
            msg.contains("player_primary") || msg.contains("id=11"),
            "message must identify the blocking device: '\(msg)'"
        )
        XCTAssertTrue(msg.contains("registered"),
                      "message must include device's actual status: '\(msg)'")
    }

    // MARK: — LVM-10: startCapture with all devices ready → proceeds to transition

    func test_LVM_10_startCapture_allReady_proceedsToTransition() async throws {
        UserDefaults.standard.set(42, forKey: "lfa_current_user_id")
        let instructor = makeParticipant(id: 1, userId: 42, role: .instructor)
        let ipadDev = makeSDevice(id: 10, role: .instructorPrimary, status: .ready)
        let iphoneDev = makeSDevice(id: 11, role: .playerPrimary, status: .ready)
        let session = makeSSession(
            status: .devicesReady, revision: 3,
            participants: [instructor], devices: [ipadDev, iphoneDev]
        )
        vm._setLobbyStateForTesting(session)
        vm.orchestrator.orchestrationState = .armed
        mock.getSessionHandler = { session }

        let pending = makeSSession(
            status: .recordingPending, revision: 4,
            participants: [instructor], devices: [ipadDev, iphoneDev],
            scheduledStartAt: "2026-06-24T10:05:30.000+00:00"
        )
        mock.transitionHandler = { pending }

        await vm.startCapture()

        XCTAssertEqual(mock.getSessionCallCount, 1, "must do one fresh GET")
        XCTAssertEqual(mock.transitionCallCount, 1, "must call transitionSession when all ready")
        XCTAssertNil(vm.deviceNotReadyMessage, "deviceNotReadyMessage must be nil when all ready")
    }

    // MARK: — LVM-11: duplicateDeviceWarning with stale same-role entries

    func test_LVM_11_duplicateDeviceWarning_twoPrimaryPlayerDevices() {
        let p1 = makeSDevice(id: 10, role: .playerPrimary, status: .ready)
        let p2 = makeSDevice(id: 11, role: .playerPrimary, status: .registered)  // stale
        let instr = makeSDevice(id: 5, role: .instructorPrimary, status: .ready)
        let session = makeSSession(devices: [instr, p1, p2])

        let warning = vm.duplicateDeviceWarning(in: session)

        XCTAssertNotNil(warning, "must detect duplicate player_primary entries")
        XCTAssertTrue(warning!.contains("player_primary"),
                      "warning must name the duplicate role: '\(warning!)'")
    }

    func test_LVM_11b_duplicateDeviceWarning_noDuplicates_returnsNil() {
        let p1 = makeSDevice(id: 10, role: .playerPrimary, status: .ready)
        let instr = makeSDevice(id: 5, role: .instructorPrimary, status: .ready)
        let session = makeSSession(devices: [instr, p1])

        XCTAssertNil(vm.duplicateDeviceWarning(in: session),
                     "must return nil when no duplicates exist")
    }

    func test_LVM_11c_duplicateDeviceWarning_removedDeviceNotCounted() {
        let p1 = makeSDevice(id: 10, role: .playerPrimary, status: .ready)
        let p2 = makeSDevice(id: 11, role: .playerPrimary, status: .registered,
                             removedAt: "2026-06-24T09:00:00Z")
        let session = makeSSession(devices: [p1, p2])

        XCTAssertNil(vm.duplicateDeviceWarning(in: session),
                     "removed device must not count as duplicate")
    }

    // MARK: — LVM-12: instructorIdentityError catches missing/wrong user ID

    func test_LVM_12_instructorIdentityError_missingUserId() {
        UserDefaults.standard.removeObject(forKey: "lfa_current_user_id")
        let session = makeSSession()
        let err = vm.instructorIdentityError(for: session)
        XCTAssertNotNil(err, "must return error when lfa_current_user_id absent")
        XCTAssertTrue(
            err!.contains("lfa_current_user_id") || err!.contains("bejelentkezve"),
            "error must reference the missing key: '\(err!)'"
        )
    }

    func test_LVM_12b_instructorIdentityError_userNotInParticipants() {
        UserDefaults.standard.set(99, forKey: "lfa_current_user_id")
        let instructor = makeParticipant(id: 1, userId: 42, role: .instructor)
        let session = makeSSession(participants: [instructor])
        let err = vm.instructorIdentityError(for: session)
        XCTAssertNotNil(err, "must return error when user 99 is not in participant list")
        XCTAssertTrue(err!.contains("99"), "error must include the actual user ID: '\(err!)'")
    }

    func test_LVM_12c_instructorIdentityError_validInstructor_returnsNil() {
        UserDefaults.standard.set(42, forKey: "lfa_current_user_id")
        let instructor = makeParticipant(id: 1, userId: 42, role: .instructor)
        let session = makeSSession(participants: [instructor])
        XCTAssertNil(vm.instructorIdentityError(for: session),
                     "must return nil for a valid instructor")
    }

    // MARK: — LVM-13: startCapture blocked when orchestrator not armed

    func test_LVM_13_startCapture_blockedWhenOrchestratorNotArmed() async throws {
        UserDefaults.standard.set(42, forKey: "lfa_current_user_id")
        let instructor = makeParticipant(id: 1, userId: 42, role: .instructor)
        let dev = makeSDevice(id: 10, role: .instructorPrimary, status: .ready)
        let session = makeSSession(
            status: .devicesReady, revision: 3,
            participants: [instructor], devices: [dev]
        )
        vm._setLobbyStateForTesting(session)
        // orchestrator.orchestrationState is .idle (not .armed)

        await vm.startCapture()

        XCTAssertEqual(mock.getSessionCallCount, 0,
                       "must NOT do fresh GET when orchestrator not armed")
        XCTAssertEqual(mock.transitionCallCount, 0,
                       "must NOT transition when orchestrator not armed")
        XCTAssertNotNil(vm.deviceNotReadyMessage,
                        "must show message about camera initialization")
        XCTAssertTrue(vm.deviceNotReadyMessage!.contains("inicializálás"),
                      "message must mention initialization: '\(vm.deviceNotReadyMessage!)'")
    }
}
