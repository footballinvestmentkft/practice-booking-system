import Foundation
import CoreBluetooth

final class CoreBluetoothBLETransport: NSObject, GoProBLETransport {

    weak var delegate: GoProBLETransportDelegate?

    var bluetoothState: CBManagerState { centralManager.state }

    // Eager: CBCentralManager resolves BT state at init, not first use.
    private let centralManager: CBCentralManager
    private let bleQueue: DispatchQueue

    override init() {
        let queue = DispatchQueue(label: "com.lfa.gopro.ble", qos: .userInitiated)
        bleQueue = queue
        centralManager = CBCentralManager(delegate: nil, queue: queue, options: [
            CBCentralManagerOptionShowPowerAlertKey: true
        ])
        super.init()
        centralManager.delegate = self
    }
    private var discoveredPeripheral: CBPeripheral?
    private var connectedPeripheral: CBPeripheral?

    private var commandChar: CBCharacteristic?
    private var queryChar: CBCharacteristic?
    private var settingsChar: CBCharacteristic?

    private var pendingNotificationCount = 0
    private let requiredNotificationChars: Set<CBUUID> = [
        GoProSpec.commandResponseCharUUID,
        GoProSpec.queryResponseCharUUID,
        GoProSpec.settingsResponseCharUUID,
    ]
    private var subscribedChars: Set<CBUUID> = []

    func startScan() {
        centralManager.scanForPeripherals(
            withServices: [GoProSpec.advertisedServiceUUID],
            options: [CBCentralManagerScanOptionAllowDuplicatesKey: false]
        )
    }

    func stopScan() {
        centralManager.stopScan()
    }

    func connect(peripheral: GoProPeripheralInfo) {
        guard let cb = discoveredPeripherals[peripheral.identifier] else { return }
        discoveredPeripheral = cb
        centralManager.connect(cb, options: nil)
    }

    func disconnect() {
        if let p = connectedPeripheral ?? discoveredPeripheral {
            centralManager.cancelPeripheralConnection(p)
        }
        connectedPeripheral = nil
        discoveredPeripheral = nil
    }

    func discoverServices() {
        controlServiceReady = false
        wifiAPServiceReady = false
        connectedPeripheral?.discoverServices([
            GoProSpec.controlServiceUUID,
            GoProSpec.wifiAPServiceUUID
        ])
    }

    func subscribeNotifications() {
        guard let p = connectedPeripheral else { return }
        subscribedChars = []
        for char in allCharacteristics where requiredNotificationChars.contains(char.uuid) {
            p.setNotifyValue(true, for: char)
        }
    }

    func writeCommand(_ data: Data) {
        guard let p = connectedPeripheral, let cmd = commandChar else { return }
        p.writeValue(data, for: cmd, type: .withResponse)
    }

    func readCharacteristic(_ uuid: CBUUID) {
        guard let p = connectedPeripheral else { return }
        if let char = allCharacteristics.first(where: { $0.uuid == uuid }) {
            p.readValue(for: char)
        }
    }

    // MARK: — Internal storage

    private var controlServiceReady = false
    private var wifiAPServiceReady = false
    private var discoveredPeripherals: [UUID: CBPeripheral] = [:]
    private var allCharacteristics: [CBCharacteristic] = []
}

// MARK: — CBCentralManagerDelegate

extension CoreBluetoothBLETransport: CBCentralManagerDelegate {

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        delegate?.bleTransportDidUpdateState(central.state)
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        discoveredPeripherals[peripheral.identifier] = peripheral
        let info = GoProPeripheralInfo(
            identifier: peripheral.identifier,
            name: peripheral.name,
            rssi: RSSI.intValue
        )
        delegate?.bleTransportDidDiscover(info)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        connectedPeripheral = peripheral
        peripheral.delegate = self
        delegate?.bleTransportDidConnect()
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        delegate?.bleTransportDidFailToConnect(error: error)
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        connectedPeripheral = nil
        delegate?.bleTransportDidDisconnect(error: error)
    }
}

// MARK: — CBPeripheralDelegate

extension CoreBluetoothBLETransport: CBPeripheralDelegate {

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard error == nil, let services = peripheral.services else {
            delegate?.bleTransportDidFailServiceDiscovery(missing: "service discovery error")
            return
        }
        let hasControl = services.contains { $0.uuid == GoProSpec.controlServiceUUID }
        let hasWiFiAP = services.contains { $0.uuid == GoProSpec.wifiAPServiceUUID }
        if !hasControl || !hasWiFiAP {
            let missing = [
                hasControl ? nil : "Control(FEA6)",
                hasWiFiAP ? nil : "WiFiAP(B5F90001)",
            ].compactMap { $0 }.joined(separator: ", ")
            delegate?.bleTransportDidFailServiceDiscovery(missing: missing)
            return
        }
        for service in services {
            peripheral.discoverCharacteristics(nil, for: service)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard error == nil, let chars = service.characteristics else { return }
        allCharacteristics.append(contentsOf: chars)

        if service.uuid == GoProSpec.controlServiceUUID {
            for char in chars {
                if char.uuid == GoProSpec.commandCharUUID { commandChar = char }
                if char.uuid == GoProSpec.queryCharUUID { queryChar = char }
                if char.uuid == GoProSpec.settingsCharUUID { settingsChar = char }
            }
            controlServiceReady = true
        } else if service.uuid == GoProSpec.wifiAPServiceUUID {
            wifiAPServiceReady = true
        }

        guard controlServiceReady && wifiAPServiceReady else { return }

        let hasSSID = allCharacteristics.contains { $0.uuid == GoProSpec.wifiSSIDCharUUID }
        let hasPassword = allCharacteristics.contains { $0.uuid == GoProSpec.wifiPasswordCharUUID }
        if !hasSSID || !hasPassword {
            let missing = [
                hasSSID ? nil : "SSID(B5F90002)",
                hasPassword ? nil : "Password(B5F90003)",
            ].compactMap { $0 }.joined(separator: ", ")
            delegate?.bleTransportDidFailServiceDiscovery(missing: missing)
            return
        }
        delegate?.bleTransportDidDiscoverServices()
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        guard error == nil else { return }
        if requiredNotificationChars.contains(characteristic.uuid) {
            subscribedChars.insert(characteristic.uuid)
            if subscribedChars.count >= requiredNotificationChars.count {
                delegate?.bleTransportDidSubscribeNotifications()
            }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard error == nil, let value = characteristic.value else { return }
        switch characteristic.uuid {
        case GoProSpec.commandResponseCharUUID:
            delegate?.bleTransportDidReceiveCommandResponse(value)
        case GoProSpec.queryResponseCharUUID:
            delegate?.bleTransportDidReceiveQueryResponse(value)
        default:
            delegate?.bleTransportDidReadCharacteristic(characteristic.uuid, value: value)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        // Write acknowledgement — no action needed
    }
}
