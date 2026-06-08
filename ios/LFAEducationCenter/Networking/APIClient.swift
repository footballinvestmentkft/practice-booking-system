import Foundation

// MARK: — APIClient

// Stateless HTTP client. Auth logic (Bearer inject, 401 refresh) lives in AuthManager.
// Base URL sourced from APIConfig — one place to change for all environments.
enum APIClient {

    // MARK: — POST (JSON)

    static func post<B: Encodable, T: Decodable>(
        path:  String,
        body:  B,
        token: String? = nil
    ) async throws -> T {
        var request = try buildRequest(path: path, method: "POST", token: token)
        request.httpBody = try JSONEncoder().encode(body)
        return try await execute(request)
    }

    // MARK: — POST (Form-encoded)
    // Used by endpoints that declare FastAPI Form(...) parameters (e.g. /specialization/unlock).

    static func formPost<T: Decodable>(
        path:   String,
        fields: [String: String],
        token:  String? = nil
    ) async throws -> T {
        var request = try buildFormRequest(path: path, token: token)
        request.httpBody = fields
            .map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? $0.value)" }
            .joined(separator: "&")
            .data(using: .utf8)
        return try await execute(request)
    }

    // MARK: — GET

    static func get<T: Decodable>(
        path:  String,
        token: String? = nil
    ) async throws -> T {
        let request = try buildRequest(path: path, method: "GET", token: token)
        return try await execute(request)
    }

    // MARK: — Private helpers

    private static func buildRequest(path: String, method: String, token: String?) throws -> URLRequest {
        guard let url = URL(string: APIConfig.baseURL + path) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token = token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private static func buildFormRequest(path: String, token: String?) throws -> URLRequest {
        guard let url = URL(string: APIConfig.baseURL + path) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token = token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private static func execute<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await perform(request)

        guard let http = response as? HTTPURLResponse else {
            throw APIError.networkError(URLError(.badServerResponse))
        }

        guard (200...299).contains(http.statusCode) else {
            let detail = try? JSONDecoder().decode(ErrorBody.self, from: data)
            throw APIError.httpError(statusCode: http.statusCode, detail: detail?.detail)
        }

        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decodingError
        }
    }

    // URLSession.data(for:) async is iOS 15+.
    // This continuation wrapper is iOS 13+ compatible.
    private static func perform(_ request: URLRequest) async throws -> (Data, URLResponse) {
        try await withCheckedThrowingContinuation { continuation in
            URLSession.shared.dataTask(with: request) { data, response, error in
                if let error = error {
                    continuation.resume(throwing: APIError.networkError(error))
                } else if let data = data, let response = response {
                    continuation.resume(returning: (data, response))
                } else {
                    continuation.resume(throwing: APIError.networkError(URLError(.unknown)))
                }
            }.resume()
        }
    }
}

// MARK: — APIError

enum APIError: Error {
    case invalidURL
    case httpError(statusCode: Int, detail: String?)
    case decodingError
    case networkError(Error)
    case unauthorized   // no token available or refresh failed
}

private struct ErrorBody: Decodable {
    let detail: String?
}
