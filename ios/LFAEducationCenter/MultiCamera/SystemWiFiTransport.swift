import Foundation

/// In-app Wi-Fi join (NEHotspotConfiguration) requires the Hotspot Configuration
/// entitlement, which Apple does not grant to personal/free-tier development teams.
/// On a personal team this always throws `.unavailable`, so GoProConnectionManager
/// falls back to `.awaitingManualWiFiJoin` and the user joins the GoPro AP via
/// Settings > Wi-Fi instead.
final class SystemWiFiTransport: GoProWiFiTransport {

    func joinAccessPoint(ssid: String, password: String) async throws {
        throw GoProWiFiError.unavailable
    }

    func isConnectedToGoProAP() -> Bool {
        false
    }
}
