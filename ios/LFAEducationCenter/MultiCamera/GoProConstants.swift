import Foundation
import CoreBluetooth

// MARK: — Open GoPro Specification Contract
//
// Source: https://github.com/gopro/OpenGoPro (MIT licence)
// Spec version: Open GoPro v2.0
// Target hardware: GoPro HERO12 Black
// Supported firmware: v2.0+
// Pinned reference: OpenGoPro release 2024-Q4 (commit-level pin in docs)

enum GoProSpec {

    // MARK: — BLE

    static let advertisedServiceUUID = CBUUID(string: "FEA6")

    static let controlServiceUUID = CBUUID(string: "0000FEA6-0000-1000-8000-00805F9B34FB")

    static let commandCharUUID = CBUUID(string: "B5F90072-AA8D-11E3-9046-0002A5D5C51B")
    static let commandResponseCharUUID = CBUUID(string: "B5F90073-AA8D-11E3-9046-0002A5D5C51B")

    static let settingsCharUUID = CBUUID(string: "B5F90074-AA8D-11E3-9046-0002A5D5C51B")
    static let settingsResponseCharUUID = CBUUID(string: "B5F90075-AA8D-11E3-9046-0002A5D5C51B")

    static let queryCharUUID = CBUUID(string: "B5F90076-AA8D-11E3-9046-0002A5D5C51B")
    static let queryResponseCharUUID = CBUUID(string: "B5F90077-AA8D-11E3-9046-0002A5D5C51B")

    // Wi-Fi Access Point Service (separate from Control service)
    static let wifiAPServiceUUID = CBUUID(string: "B5F90001-AA8D-11E3-9046-0002A5D5C51B")
    static let wifiSSIDCharUUID = CBUUID(string: "B5F90002-AA8D-11E3-9046-0002A5D5C51B")
    static let wifiPasswordCharUUID = CBUUID(string: "B5F90003-AA8D-11E3-9046-0002A5D5C51B")

    // MARK: — HTTP

    static let httpHost = "10.5.5.9"
    static let httpPort = 8080
    static var httpBaseURL: String { "http://\(httpHost):\(httpPort)" }

    // Endpoints (GET only — per Open GoPro spec)
    static let shutterStartPath = "/gopro/camera/shutter/start"
    static let shutterStopPath = "/gopro/camera/shutter/stop"
    static let cameraStatePath = "/gopro/camera/state"
    static let presetLoadPath = "/gopro/camera/presets/load"
    static let settingPath = "/gopro/camera/setting"
    static let mediaListPath = "/gopro/media/list"
    static let mediaDownloadBase = "/videos/DCIM"

    // MARK: — BLE Commands (raw bytes per spec)

    static let apModeOnCommand: [UInt8] = [0x03, 0x17, 0x01, 0x01]
    static let apModeOffCommand: [UInt8] = [0x03, 0x17, 0x01, 0x00]

    // MARK: — Firmware

    static let minimumFirmwareMajor = 2
    static let minimumFirmwareMinor = 0

    // MARK: — Timeouts

    static let bluetoothInitTimeout: TimeInterval = 10
    static let discoveryTimeout: TimeInterval = 15
    static let connectTimeout: TimeInterval = 10
    static let serviceDiscoveryTimeout: TimeInterval = 10
    static let apActivationTimeout: TimeInterval = 15
    static let wifiJoinTimeout: TimeInterval = 20
    static let httpReachabilityTimeout: TimeInterval = 10
    static let commandTimeout: TimeInterval = 5

    // MARK: — Retry limits

    static let discoveryMaxRetries = 3
    static let wifiJoinMaxRetries = 2
    static let httpVerifyMaxRetries = 2

    // MARK: — MVP Capture Preset

    static let mvpPresetResolution = "1920x1080"
    static let mvpPresetFPS = 30
    static let mvpPresetLensMode = "linear"
    static let mvpPresetStabilization = "off"
}
