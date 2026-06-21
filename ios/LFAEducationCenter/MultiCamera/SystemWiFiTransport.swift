import Foundation
import NetworkExtension

final class SystemWiFiTransport: GoProWiFiTransport {

    func joinAccessPoint(ssid: String, password: String) async throws {
        let config = NEHotspotConfiguration(ssid: ssid, passphrase: password, isWEP: false)
        config.joinOnce = true

        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            NEHotspotConfigurationManager.shared.apply(config) { error in
                if let error = error {
                    let nsError = error as NSError
                    if nsError.domain == NEHotspotConfigurationErrorDomain {
                        switch nsError.code {
                        case NEHotspotConfigurationError.userDenied.rawValue:
                            cont.resume(throwing: GoProWiFiError.userDenied)
                        case NEHotspotConfigurationError.alreadyAssociated.rawValue:
                            cont.resume(returning: ())
                        case NEHotspotConfigurationError.applicationIsNotInForeground.rawValue:
                            cont.resume(throwing: GoProWiFiError.unavailable)
                        default:
                            cont.resume(throwing: GoProWiFiError.configurationFailed)
                        }
                    } else {
                        cont.resume(throwing: GoProWiFiError.configurationFailed)
                    }
                } else {
                    cont.resume(returning: ())
                }
            }
        }
    }

    func isConnectedToGoProAP() -> Bool {
        false
    }
}
