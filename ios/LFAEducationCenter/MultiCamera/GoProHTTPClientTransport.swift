import Foundation

final class GoProHTTPClientTransport: GoProHTTPTransport {

    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func get(path: String, timeout: TimeInterval) async throws -> Data {
        guard let url = URL(string: GoProSpec.httpBaseURL + path) else {
            throw GoProHTTPError.unreachable
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = timeout

        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw GoProHTTPError.unreachable
            }
            guard (200...299).contains(http.statusCode) else {
                throw GoProHTTPError.httpError(statusCode: http.statusCode)
            }
            return data
        } catch is CancellationError {
            throw GoProHTTPError.cancelled
        } catch let error as GoProHTTPError {
            throw error
        } catch {
            throw GoProHTTPError.timeout
        }
    }

    func isReachable(timeout: TimeInterval) async -> Bool {
        do {
            _ = try await get(path: GoProSpec.cameraStatePath, timeout: timeout)
            return true
        } catch {
            return false
        }
    }
}
