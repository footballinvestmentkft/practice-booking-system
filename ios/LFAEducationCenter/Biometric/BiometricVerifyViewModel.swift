import Foundation

@MainActor
final class BiometricVerifyViewModel: ObservableObject {

    @Published private(set) var result:    BiometricVerifyResult?
    @Published private(set) var isLoading: Bool = false
    @Published              var error:    BiometricClientError?

    private let service: BiometricService

    init(service: BiometricService) {
        self.service = service
    }

    func verify(photoFilename: String?) async {
        isLoading = true
        defer { isLoading = false }
        do {
            result = try await service.verify(photoFilename: photoFilename)
        } catch let e as BiometricClientError {
            error = e
        } catch {
            self.error = .networkError(error)
        }
    }
}
