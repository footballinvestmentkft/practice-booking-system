import Foundation

// Auto-retry policy for biometric_reference_not_found (Celery timing race).
// The reference embedding is generated asynchronously after liveness submit.
// If verify is called before the Celery task completes, the backend returns 404.
// We retry silently up to kMaxRetries times with kRetryDelayNs between each attempt.
// After exhausting retries the error surfaces to the UI for a controlled alert.
// Task.sleep(nanoseconds:) is available from iOS 13 — no iOS 16+ Duration needed.
private let kMaxRetries:   Int    = 5
private let kRetryDelayNs: UInt64 = 2_000_000_000   // 2 seconds in nanoseconds

@MainActor
final class BiometricVerifyViewModel: ObservableObject {

    @Published private(set) var result:    BiometricVerifyResult?
    @Published private(set) var isLoading: Bool = false
    @Published              var error:    BiometricClientError?

    // True while an auto-retry sleep is in progress.
    // Keeps the loading overlay visible between attempts.
    @Published private(set) var isRetrying: Bool = false

    private let service:    BiometricService
    private var retryCount: Int = 0

    init(service: BiometricService) {
        self.service = service
    }

    // MARK: — Verify (with auto-retry on referenceNotFound)

    func verify(photoFilename: String?) async {
        retryCount = 0
        await _attempt(photoFilename: photoFilename)
    }

    // Explicit retry fired by the retry alert (after retries are exhausted).
    func retryVerify(photoFilename: String?) async {
        retryCount = 0
        result     = nil
        await _attempt(photoFilename: photoFilename)
    }

    // MARK: — Private

    private func _attempt(photoFilename: String?) async {
        isLoading  = true
        isRetrying = false
        error      = nil
        defer { isLoading = false; isRetrying = false }

        while true {
            do {
                result = try await service.verify(photoFilename: photoFilename)
                return   // success — exit retry loop
            } catch BiometricClientError.referenceNotFound {
                retryCount += 1
                if retryCount < kMaxRetries {
                    // Silent retry: keep loading overlay, sleep, then re-attempt.
                    isRetrying = true
                    try? await Task.sleep(nanoseconds: kRetryDelayNs)
                    isRetrying = false
                    continue
                }
                // Retries exhausted — surface to UI for controlled alert.
                error = .referenceNotFound
                return
            } catch let e as BiometricClientError {
                error = e
                return
            } catch {
                self.error = .networkError(error)
                return
            }
        }
    }
}
