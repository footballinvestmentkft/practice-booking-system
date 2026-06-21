import Foundation
import CoreBluetooth

@MainActor
final class GoProConnectionManager: ObservableObject {

    @Published private(set) var state: GoProConnectionState = .idle
    @Published private(set) var discoveredPeripherals: [GoProPeripheralInfo] = []
    @Published private(set) var diagnosticLog: [GoProDiagnosticEvent] = []
    @Published private(set) var cameraStatus: GoProCameraStatus?

    private let bleTransport: GoProBLETransport
    private let httpTransport: GoProHTTPTransport
    private let wifiTransport: GoProWiFiTransport

    private var timeoutTask: Task<Void, Never>?
    private var selectedPeripheral: GoProPeripheralInfo?
    private var wifiSSID: String?
    private var wifiPassword: String?
    private var manualWiFiSSID: String?
    private var isHTTPVerifyInProgress = false

    init(
        bleTransport: GoProBLETransport,
        httpTransport: GoProHTTPTransport,
        wifiTransport: GoProWiFiTransport
    ) {
        self.bleTransport = bleTransport
        self.httpTransport = httpTransport
        self.wifiTransport = wifiTransport
    }

    // MARK: — Public actions

    func startConnection() {
        guard state == .idle || state.isTerminal else { return }
        bleTransport.delegate = self
        let btState = bleTransport.bluetoothState
        switch btState {
        case .poweredOn:
            transition(to: .discovering(attempt: 1), trigger: "user_initiated")
            bleTransport.startScan()
            startTimeout(GoProSpec.discoveryTimeout, trigger: "discovery_timeout")
        case .unknown, .resetting:
            transition(to: .waitingForBluetooth, trigger: "bluetooth_initializing")
            startTimeout(GoProSpec.bluetoothInitTimeout, trigger: "bluetooth_init_timeout")
        case .poweredOff:
            transition(to: .bluetoothUnavailable(btState), trigger: "bluetooth_off")
        case .unauthorized:
            transition(to: .bluetoothUnavailable(btState), trigger: "bluetooth_unauthorized")
        case .unsupported:
            transition(to: .bluetoothUnavailable(btState), trigger: "bluetooth_unsupported")
        @unknown default:
            transition(to: .bluetoothUnavailable(btState), trigger: "bluetooth_unknown_cbstate")
        }
    }

    func selectPeripheral(_ peripheral: GoProPeripheralInfo) {
        guard case .discovering = state else { return }
        selectedPeripheral = peripheral
        bleTransport.stopScan()
        cancelTimeout()
        transition(to: .connecting, trigger: "peripheral_selected")
        bleTransport.connect(peripheral: peripheral)
        startTimeout(GoProSpec.connectTimeout, trigger: "connect_timeout")
    }

    func cancel() {
        cancelTimeout()
        manualWiFiSSID = nil
        isHTTPVerifyInProgress = false
        let prev = state
        if case .ready = prev {
            transition(to: .disconnecting, trigger: "user_cancel")
            bleTransport.disconnect()
        } else {
            bleTransport.stopScan()
            bleTransport.disconnect()
            transition(to: .failed(.cancelled), trigger: "user_cancel")
        }
    }

    func disconnect() {
        cancelTimeout()
        manualWiFiSSID = nil
        isHTTPVerifyInProgress = false
        transition(to: .disconnecting, trigger: "user_disconnect")
        bleTransport.disconnect()
    }

    func retry() {
        guard case .failed(let err) = state, err.isRecoverable else { return }
        transition(to: .idle, trigger: "retry")
        startConnection()
    }

    // MARK: — State transitions

    private func transition(to newState: GoProConnectionState, trigger: String) {
        let event = GoProDiagnosticEvent(
            timestamp: Date(),
            fromState: "\(state)",
            toState: "\(newState)",
            trigger: trigger,
            metadata: [:]
        )
        diagnosticLog.append(event)
        state = newState
    }

    // MARK: — Timeout

    private func startTimeout(_ interval: TimeInterval, trigger: String) {
        cancelTimeout()
        timeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            guard !Task.isCancelled else { return }
            await self?.handleTimeout(trigger: trigger)
        }
    }

    private func cancelTimeout() {
        timeoutTask?.cancel()
        timeoutTask = nil
    }

    private func handleTimeout(trigger: String) {
        switch state {
        case .waitingForBluetooth:
            transition(to: .bluetoothUnavailable(.unknown), trigger: trigger)
        case .discovering(let attempt):
            if attempt < GoProSpec.discoveryMaxRetries {
                transition(to: .discovering(attempt: attempt + 1), trigger: trigger)
                bleTransport.startScan()
                startTimeout(GoProSpec.discoveryTimeout, trigger: "discovery_timeout")
            } else {
                bleTransport.stopScan()
                transition(to: .failed(.discoveryTimeout), trigger: trigger)
            }
        case .connecting:
            bleTransport.disconnect()
            transition(to: .failed(.connectFailed("timeout")), trigger: trigger)
        case .discoveringServices:
            bleTransport.disconnect()
            transition(to: .failed(.serviceDiscoveryFailed), trigger: trigger)
        case .establishingControl:
            bleTransport.disconnect()
            transition(to: .failed(.controlEstablishmentFailed), trigger: trigger)
        case .enablingAccessPoint:
            transition(to: .failed(.apActivationFailed), trigger: trigger)
        case .verifyingHTTP(let attempt):
            if attempt < GoProSpec.httpVerifyMaxRetries {
                transition(to: .verifyingHTTP(attempt: attempt + 1), trigger: trigger)
                Task { await verifyHTTP() }
            } else {
                transition(to: .failed(.httpUnreachable), trigger: trigger)
            }
        default:
            break
        }
    }

    // MARK: — Internal flow

    private func proceedToServiceDiscovery() {
        transition(to: .discoveringServices, trigger: "ble_connected")
        bleTransport.discoverServices()
        startTimeout(GoProSpec.serviceDiscoveryTimeout, trigger: "service_discovery_timeout")
    }

    private func proceedToControlEstablishment() {
        cancelTimeout()
        transition(to: .establishingControl, trigger: "services_discovered")
        bleTransport.subscribeNotifications()
        startTimeout(GoProSpec.serviceDiscoveryTimeout, trigger: "control_timeout")
    }

    private func proceedToAPActivation() {
        cancelTimeout()
        transition(to: .connectedBLE, trigger: "control_established")
        transition(to: .enablingAccessPoint, trigger: "auto_proceed")
        let cmd = Data(GoProSpec.apModeOnCommand)
        bleTransport.writeCommand(cmd)
        bleTransport.readCharacteristic(GoProSpec.wifiSSIDCharUUID)
        bleTransport.readCharacteristic(GoProSpec.wifiPasswordCharUUID)
        startTimeout(GoProSpec.apActivationTimeout, trigger: "ap_timeout")
    }

    private func proceedToWiFiJoin() {
        cancelTimeout()
        guard let ssid = wifiSSID else {
            transition(to: .failed(.apActivationFailed), trigger: "no_wifi_creds")
            return
        }
        manualWiFiSSID = ssid
        transition(to: .awaitingManualWiFiJoin(ssid: ssid), trigger: "ap_activated")
    }

    func confirmManualWiFiJoined() {
        guard case .awaitingManualWiFiJoin = state else { return }
        startHTTPVerify(trigger: "manual_wifi_confirmed")
    }

    func onForeground() {
        guard case .awaitingManualWiFiJoin = state else { return }
        startHTTPVerify(trigger: "foreground_verify")
    }

    private func startHTTPVerify(trigger: String) {
        guard !isHTTPVerifyInProgress else { return }
        isHTTPVerifyInProgress = true
        transition(to: .verifyingHTTP(attempt: 1), trigger: trigger)
        Task { await verifyHTTPManual() }
    }

    private func verifyHTTPManual() async {
        let reachable = await httpTransport.isReachable(timeout: GoProSpec.httpReachabilityTimeout)
        isHTTPVerifyInProgress = false
        if reachable {
            await fetchCameraState()
        } else if let ssid = manualWiFiSSID {
            transition(to: .awaitingManualWiFiJoin(ssid: ssid), trigger: "http_not_ready")
        } else {
            transition(to: .failed(.httpUnreachable), trigger: "http_verify_no_context")
        }
    }

    private func verifyHTTP() async {
        startTimeout(GoProSpec.httpReachabilityTimeout, trigger: "http_timeout")
        let reachable = await httpTransport.isReachable(timeout: GoProSpec.httpReachabilityTimeout)
        guard reachable else { return } // timeout handler retries
        cancelTimeout()
        await fetchCameraState()
    }

    private func fetchCameraState() async {
        do {
            let data = try await httpTransport.get(
                path: GoProSpec.cameraStatePath,
                timeout: GoProSpec.commandTimeout
            )
            let status = try? JSONDecoder().decode(GoProCameraStatus.self, from: data)
            cameraStatus = status
            if let fw = status?.firmwareVersion {
                let supported = isFirmwareSupported(fw)
                if supported {
                    transition(to: .ready(firmware: fw), trigger: "http_verified")
                } else {
                    let req = "\(GoProSpec.minimumFirmwareMajor).\(GoProSpec.minimumFirmwareMinor)"
                    transition(to: .failed(.unsupportedFirmware(found: fw, required: req)), trigger: "firmware_check")
                }
            } else {
                transition(to: .ready(firmware: "unknown"), trigger: "http_verified_no_fw")
            }
        } catch {
            transition(to: .failed(.httpUnreachable), trigger: "state_fetch_failed")
        }
    }

    private func isFirmwareSupported(_ version: String) -> Bool {
        let parts = version.split(separator: ".").compactMap { Int($0) }
        guard parts.count >= 2 else { return false }
        if parts[0] > GoProSpec.minimumFirmwareMajor { return true }
        if parts[0] == GoProSpec.minimumFirmwareMajor && parts[1] >= GoProSpec.minimumFirmwareMinor { return true }
        return false
    }
}

// MARK: — BLE Transport Delegate

extension GoProConnectionManager: GoProBLETransportDelegate {
    nonisolated func bleTransportDidUpdateState(_ btState: CBManagerState) {
        Task { @MainActor in
            switch (state, btState) {
            case (.waitingForBluetooth, .poweredOn):
                cancelTimeout()
                transition(to: .discovering(attempt: 1), trigger: "bluetooth_ready")
                bleTransport.startScan()
                startTimeout(GoProSpec.discoveryTimeout, trigger: "discovery_timeout")
            case (.waitingForBluetooth, .poweredOff),
                 (.waitingForBluetooth, .unauthorized),
                 (.waitingForBluetooth, .unsupported):
                cancelTimeout()
                transition(to: .bluetoothUnavailable(btState), trigger: "bt_state_resolved")
            case (.waitingForBluetooth, _):
                break
            case (.discovering, _) where btState != .poweredOn:
                cancelTimeout()
                bleTransport.stopScan()
                transition(to: .bluetoothUnavailable(btState), trigger: "bt_state_change")
            default:
                break
            }
        }
    }

    nonisolated func bleTransportDidDiscover(_ peripheral: GoProPeripheralInfo) {
        Task { @MainActor in
            guard case .discovering = state else { return }
            if !discoveredPeripherals.contains(peripheral) {
                discoveredPeripherals.append(peripheral)
            }
            if discoveredPeripherals.count == 1 {
                selectPeripheral(peripheral)
            }
        }
    }

    nonisolated func bleTransportDidConnect() {
        Task { @MainActor in
            guard case .connecting = state else { return }
            proceedToServiceDiscovery()
        }
    }

    nonisolated func bleTransportDidFailToConnect(error: Error?) {
        Task { @MainActor in
            cancelTimeout()
            transition(to: .failed(.connectFailed(error?.localizedDescription ?? "unknown")), trigger: "connect_failed")
        }
    }

    nonisolated func bleTransportDidDisconnect(error: Error?) {
        Task { @MainActor in
            cancelTimeout()
            if case .disconnecting = state {
                transition(to: .idle, trigger: "disconnected_clean")
            } else if case .awaitingManualWiFiJoin = state {
                // Expected: user is in Settings joining Wi-Fi
            } else if case .verifyingHTTP = state {
                // HTTP verify in progress after manual Wi-Fi join
            } else if case .idle = state {
                // already idle
            } else {
                transition(to: .failed(.disconnectedUnexpectedly), trigger: "disconnected_unexpected")
            }
        }
    }

    nonisolated func bleTransportDidDiscoverServices() {
        Task { @MainActor in
            guard case .discoveringServices = state else { return }
            proceedToControlEstablishment()
        }
    }

    nonisolated func bleTransportDidFailServiceDiscovery(missing: String) {
        Task { @MainActor in
            cancelTimeout()
            bleTransport.disconnect()
            transition(to: .failed(.serviceDiscoveryFailed), trigger: "missing_service: \(missing)")
        }
    }

    nonisolated func bleTransportDidSubscribeNotifications() {
        Task { @MainActor in
            guard case .establishingControl = state else { return }
            proceedToAPActivation()
        }
    }

    nonisolated func bleTransportDidReceiveCommandResponse(_ data: Data) {
        Task { @MainActor in
            // AP mode confirmation or other command responses
            if case .enablingAccessPoint = state, wifiSSID != nil, wifiPassword != nil {
                proceedToWiFiJoin()
            }
        }
    }

    nonisolated func bleTransportDidReceiveQueryResponse(_ data: Data) {
        // Future: parse query responses
    }

    nonisolated func bleTransportDidReadCharacteristic(_ uuid: CBUUID, value: Data?) {
        Task { @MainActor in
            guard let data = value else { return }
            if uuid == GoProSpec.wifiSSIDCharUUID {
                wifiSSID = String(data: data, encoding: .utf8)
            } else if uuid == GoProSpec.wifiPasswordCharUUID {
                wifiPassword = String(data: data, encoding: .utf8)
            }
            if wifiSSID != nil && wifiPassword != nil, case .enablingAccessPoint = state {
                proceedToWiFiJoin()
            }
        }
    }
}

// MARK: — Camera Status DTO

struct GoProCameraStatus: Codable, Equatable {
    let firmwareVersion: String?
    let isRecording: Bool?
    let batteryLevel: Int?
    let sdCardSpaceRemaining: Int?

    enum CodingKeys: String, CodingKey {
        case firmwareVersion = "firmware_version"
        case isRecording = "is_recording"
        case batteryLevel = "battery_level"
        case sdCardSpaceRemaining = "sd_card_space_remaining"
    }
}
