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

    // MARK: — POST (Multipart/form-data)
    // Used for file upload endpoints (e.g. POST /api/v1/users/me/profile-photo).
    // fieldName must match the FastAPI File(...) parameter name.

    static func multipartPost<T: Decodable>(
        path:      String,
        imageData: Data,
        mimeType:  String,
        fieldName: String = "photo",
        token:     String? = nil
    ) async throws -> T {
        let boundary = "Boundary-\(UUID().uuidString)"
        var request  = try buildMultipartRequest(path: path, boundary: boundary, token: token)
        request.httpBody = buildMultipartBody(
            imageData: imageData, mimeType: mimeType,
            fieldName: fieldName, boundary: boundary
        )
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

    // MARK: — DELETE (No Content — 204)

    static func deleteNoContent(path: String, token: String? = nil) async throws {
        let request = try buildRequest(path: path, method: "DELETE", token: token)
        let (_, response) = try await perform(request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.networkError(URLError(.badServerResponse))
        }
        guard (200...299).contains(http.statusCode) else {
            throw APIError.httpError(statusCode: http.statusCode, detail: nil)
        }
    }

    // MARK: — GET (JSON)

    static func get<T: Decodable>(
        path:  String,
        token: String? = nil
    ) async throws -> T {
        let request = try buildRequest(path: path, method: "GET", token: token)
        return try await execute(request)
    }

    // MARK: — GET (binary — for thumbnail / media endpoints)
    // Returns raw Data. Does not attempt JSON decode.
    // Accept header is set to */* so binary responses (JPEG, MP4) are received cleanly.

    static func fetchData(path: String, token: String? = nil) async throws -> Data {
        var request = try buildRequest(path: path, method: "GET", token: token)
        request.setValue("*/*", forHTTPHeaderField: "Accept")
        let (data, response) = try await perform(request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.networkError(URLError(.badServerResponse))
        }
        guard (200...299).contains(http.statusCode) else {
            let detail = try? JSONDecoder().decode(ErrorBody.self, from: data)
            throw APIError.httpError(statusCode: http.statusCode, detail: detail?.detail)
        }
        return data
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

    private static func buildMultipartRequest(path: String, boundary: String, token: String?) throws -> URLRequest {
        guard let url = URL(string: APIConfig.baseURL + path) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token = token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private static func buildMultipartBody(
        imageData: Data, mimeType: String, fieldName: String, boundary: String
    ) -> Data {
        var body = Data()
        let crlf = "\r\n"
        body.append(Data("--\(boundary)\(crlf)".utf8))
        body.append(Data("Content-Disposition: form-data; name=\"\(fieldName)\"; filename=\"photo.jpg\"\(crlf)".utf8))
        body.append(Data("Content-Type: \(mimeType)\(crlf)\(crlf)".utf8))
        body.append(imageData)
        body.append(Data("\(crlf)--\(boundary)--\(crlf)".utf8))
        return body
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

// Handles all backend error shapes:
//   {"detail": "some message"}                           — standard FastAPI HTTPException
//   {"detail": {"error": "...", "message": "..."}}       — FastAPI detail object
//   {"error": {"code": "http_NNN", "message": "..."}}    — ProductionExceptionHandler JSON format
private struct ErrorBody: Decodable {
    let detail: String?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let s = try? container.decode(String.self, forKey: .detail) {
            detail = s
        } else if let obj = try? container.decode(ErrorDetailObject.self, forKey: .detail) {
            detail = obj.message
        } else if let wrapper = try? container.decode(ErrorDetailObject.self, forKey: .error) {
            detail = wrapper.message
        } else {
            detail = nil
        }
    }

    enum CodingKeys: String, CodingKey { case detail, error }
}

private struct ErrorDetailObject: Decodable {
    let message: String?
}
