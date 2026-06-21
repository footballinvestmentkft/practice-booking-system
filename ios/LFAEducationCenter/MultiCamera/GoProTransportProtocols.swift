import Foundation
import CoreBluetooth

// MARK: — BLE Transport Protocol

protocol GoProBLETransport: AnyObject {
    var delegate: GoProBLETransportDelegate? { get set }
    var bluetoothState: CBManagerState { get }

    func startScan()
    func stopScan()
    func connect(peripheral: GoProPeripheralInfo)
    func disconnect()
    func discoverServices()
    func subscribeNotifications()
    func writeCommand(_ data: Data)
    func readCharacteristic(_ uuid: CBUUID)
}

protocol GoProBLETransportDelegate: AnyObject {
    func bleTransportDidUpdateState(_ state: CBManagerState)
    func bleTransportDidDiscover(_ peripheral: GoProPeripheralInfo)
    func bleTransportDidConnect()
    func bleTransportDidFailToConnect(error: Error?)
    func bleTransportDidDisconnect(error: Error?)
    func bleTransportDidDiscoverServices()
    func bleTransportDidFailServiceDiscovery(missing: String)
    func bleTransportDidSubscribeNotifications()
    func bleTransportDidReceiveCommandResponse(_ data: Data)
    func bleTransportDidReceiveQueryResponse(_ data: Data)
    func bleTransportDidReadCharacteristic(_ uuid: CBUUID, value: Data?)
}

struct GoProPeripheralInfo: Equatable {
    let identifier: UUID
    let name: String?
    let rssi: Int
}

// MARK: — HTTP Transport Protocol

protocol GoProHTTPTransport: AnyObject {
    func get(path: String, timeout: TimeInterval) async throws -> Data
    func isReachable(timeout: TimeInterval) async -> Bool
}

enum GoProHTTPError: Error, Equatable {
    case unreachable
    case timeout
    case httpError(statusCode: Int)
    case decodingError
    case cancelled
}

// MARK: — Wi-Fi Transport Protocol

protocol GoProWiFiTransport: AnyObject {
    func joinAccessPoint(ssid: String, password: String) async throws
    func isConnectedToGoProAP() -> Bool
}

enum GoProWiFiError: Error, Equatable {
    case configurationFailed
    case userDenied
    case alreadyAssociated
    case timeout
    case unavailable
}
