import Foundation
import CoreBluetooth

// MARK: — Connection State

enum GoProConnectionState: Equatable {
    case idle
    case waitingForBluetooth
    case bluetoothUnavailable(CBManagerState)
    case discovering(attempt: Int)
    case connecting
    case discoveringServices
    case establishingControl
    case connectedBLE
    case enablingAccessPoint
    case awaitingManualWiFiJoin(ssid: String)
    case connectingWiFi(attempt: Int)
    case verifyingHTTP(attempt: Int)
    case ready(firmware: String)
    case disconnecting
    case failed(GoProConnectionError)

    var isTerminal: Bool {
        switch self {
        case .ready, .failed, .idle, .bluetoothUnavailable: return true
        default: return false
        }
    }

    var userFacingStatus: String {
        switch self {
        case .idle: return "Nincs csatlakoztatva"
        case .waitingForBluetooth: return "Bluetooth inicializálás…"
        case .bluetoothUnavailable: return "Bluetooth nem elérhető"
        case .discovering: return "GoPro keresése…"
        case .connecting: return "Csatlakozás…"
        case .discoveringServices: return "Szolgáltatások felderítése…"
        case .establishingControl: return "Vezérlés létrehozása…"
        case .connectedBLE: return "BLE csatlakozva"
        case .enablingAccessPoint: return "Wi-Fi bekapcsolása…"
        case .awaitingManualWiFiJoin(let ssid): return "Csatlakozz: \(ssid)"
        case .connectingWiFi: return "Wi-Fi csatlakozás…"
        case .verifyingHTTP: return "HTTP ellenőrzése…"
        case .ready: return "Csatlakozva ✓"
        case .disconnecting: return "Szétkapcsolás…"
        case .failed(let err): return "Hiba: \(err.userMessage)"
        }
    }
}

// MARK: — Connection Error

enum GoProConnectionError: Error, Equatable {
    case bluetoothOff
    case bluetoothUnauthorized
    case discoveryTimeout
    case connectFailed(String)
    case serviceDiscoveryFailed
    case controlEstablishmentFailed
    case apActivationFailed
    case wifiUserDenied
    case wifiJoinFailed
    case httpUnreachable
    case unsupportedFirmware(found: String, required: String)
    case disconnectedUnexpectedly
    case cancelled

    var userMessage: String {
        switch self {
        case .bluetoothOff: return "Kapcsold be a Bluetooth-t"
        case .bluetoothUnauthorized: return "Bluetooth engedély szükséges"
        case .discoveryTimeout: return "GoPro nem található"
        case .connectFailed(let msg): return "Csatlakozás sikertelen: \(msg)"
        case .serviceDiscoveryFailed: return "GoPro szolgáltatás nem elérhető"
        case .controlEstablishmentFailed: return "Vezérlés nem létesíthető"
        case .apActivationFailed: return "Wi-Fi bekapcsolás sikertelen"
        case .wifiUserDenied: return "Wi-Fi jóváhagyás elutasítva"
        case .wifiJoinFailed: return "Wi-Fi csatlakozás sikertelen"
        case .httpUnreachable: return "GoPro HTTP nem elérhető"
        case .unsupportedFirmware(let found, let req): return "Firmware \(found) nem támogatott (min: \(req))"
        case .disconnectedUnexpectedly: return "Kapcsolat megszakadt"
        case .cancelled: return "Megszakítva"
        }
    }

    var isRecoverable: Bool {
        switch self {
        case .bluetoothOff, .bluetoothUnauthorized, .unsupportedFirmware, .cancelled:
            return false
        default:
            return true
        }
    }
}

// MARK: — Diagnostic Event

struct GoProDiagnosticEvent {
    let timestamp: Date
    let fromState: String
    let toState: String
    let trigger: String
    let metadata: [String: String]
}
