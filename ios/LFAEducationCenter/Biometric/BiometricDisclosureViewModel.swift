import Foundation

@MainActor
final class BiometricDisclosureViewModel: ObservableObject {

    enum Phase {
        case loading
        case unavailable(String)   // feature flag off or blocking error
        case disclosure            // user must accept tájékoztató
        case consent               // disclosure done, consent still needed
        case done                  // both active — ready for liveness
    }

    @Published private(set) var phase:             Phase = .loading
    @Published private(set) var isLoading:         Bool  = false
    @Published              var error:             BiometricClientError?

    // Fired when the user completes both steps — parent view advances to liveness.
    var onReadyForLiveness: (() -> Void)?

    private let service: BiometricService

    init(service: BiometricService) {
        self.service = service
    }

    // MARK: — Load

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let disclosure = try await service.getDisclosureStatus()
            if disclosure.isActive {
                let consent = try await service.getConsentStatus()
                phase = consent.isActive ? .done : .consent
            } else {
                phase = .disclosure
            }
        } catch BiometricClientError.featureDisabled,
                BiometricClientError.rateLimiterUnavailable {
            phase = .unavailable("Biometric verification is currently unavailable.")
        } catch BiometricClientError.parentalConsentRequired {
            phase = .unavailable("Biometric verification requires parental consent for users under 18.")
        } catch let e as BiometricClientError {
            error = e
        } catch {
            self.error = .networkError(error)
        }
    }

    // MARK: — Disclosure

    func acceptDisclosure() async {
        isLoading = true
        defer { isLoading = false }
        do {
            _ = try await service.acceptDisclosure()
            let consent = try await service.getConsentStatus()
            phase = consent.isActive ? .done : .consent
        } catch BiometricClientError.disclosureAlreadyAccepted {
            // Already accepted — advance to consent step.
            phase = .consent
        } catch BiometricClientError.parentalConsentRequired {
            phase = .unavailable("Biometric verification requires parental consent for users under 18.")
        } catch let e as BiometricClientError {
            error = e
        } catch {
            self.error = .networkError(error)
        }
    }

    // MARK: — Consent

    func grantConsent() async {
        isLoading = true
        defer { isLoading = false }
        do {
            _ = try await service.grantConsent()
            phase = .done
            onReadyForLiveness?()
        } catch BiometricClientError.consentAlreadyActive {
            phase = .done
            onReadyForLiveness?()
        } catch let e as BiometricClientError {
            error = e
        } catch {
            self.error = .networkError(error)
        }
    }
}
