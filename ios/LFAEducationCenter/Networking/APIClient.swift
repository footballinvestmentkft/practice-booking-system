import Foundation

// MARK: — APIClient

// Stateless HTTP client. Auth logic (Bearer inject, 401 refresh) lives in AuthManager.
// Base URL sourced from APIConfig — one place to change for all environments.
enum APIClient {

    // MARK: — Test session override (DEBUG only)
    // Register MockURLProtocol in a URLSessionConfiguration, then assign here.
    // Must be cleared in tearDown to avoid leaking state between tests.
    // Both perform() and performUploadTask() honour this override.
    #if DEBUG
    static var _testURLSession: URLSession? = nil
    #endif

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

    // MARK: — POST (Multipart/form-data — Data)
    // Used for image upload endpoints (e.g. POST /api/v1/users/me/profile-photo).
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

    // MARK: — POST (Multipart/form-data — file URL, streaming)
    // Used for video upload (POST .../upload). Writes multipart envelope to a temp
    // file and uses uploadTask(with:fromFile:) so the video is never fully loaded
    // into memory — the OS streams it from disk. fieldName must be "file" for the
    // juggling upload endpoint (FastAPI: file: UploadFile = File(...)).

    static func multipartUploadFromFile<T: Decodable>(
        path:      String,
        fileURL:   URL,
        mimeType:  String,
        fieldName: String = "file",
        token:     String? = nil
    ) async throws -> T {
        let boundary = "Boundary-\(UUID().uuidString)"
        let request  = try buildMultipartRequest(path: path, boundary: boundary, token: token)

        let tempFile = try buildMultipartTempFile(
            from: fileURL, mimeType: mimeType, fieldName: fieldName, boundary: boundary
        )
        defer { try? FileManager.default.removeItem(at: tempFile) }

        let (data, response) = try await performUploadTask(request, fromFile: tempFile)

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

    // MARK: — PATCH (JSON)

    static func patch<B: Encodable, T: Decodable>(
        path:  String,
        body:  B,
        token: String? = nil
    ) async throws -> T {
        var request = try buildRequest(path: path, method: "PATCH", token: token)
        request.httpBody = try JSONEncoder().encode(body)
        return try await execute(request)
    }

    // MARK: — GET (raw — no 2xx check, no decode)
    // Caller receives (Data, URLResponse) and inspects the status code directly.
    // Used for ETag-based endpoints where 304 is a valid non-error response.

    static func getRaw(
        path:         String,
        token:        String? = nil,
        extraHeaders: [String: String] = [:]
    ) async throws -> (Data, URLResponse) {
        var request = try buildRequest(path: path, method: "GET", token: token)
        for (key, value) in extraHeaders {
            request.setValue(value, forHTTPHeaderField: key)
        }
        return try await perform(request)
    }

    // MARK: — POST (raw status code)
    // Returns (Data, statusCode) for 2xx. Throws APIError.httpError for non-2xx
    // (with parsed detail, so callers can distinguish e.g. 409 idempotency vs version conflicts).
    // Used by annotation endpoints that need to distinguish 200 (duplicate) from 201 (created).

    static func postRaw<B: Encodable>(
        path:  String,
        body:  B,
        token: String? = nil
    ) async throws -> (Data, Int) {
        var request = try buildRequest(path: path, method: "POST", token: token)
        request.httpBody = try JSONEncoder().encode(body)
        return try await executeRaw(request)
    }

    // MARK: — PATCH (raw status code)
    // See postRaw — same semantics, PATCH method.

    static func patchRaw<B: Encodable>(
        path:  String,
        body:  B,
        token: String? = nil
    ) async throws -> (Data, Int) {
        var request = try buildRequest(path: path, method: "PATCH", token: token)
        request.httpBody = try JSONEncoder().encode(body)
        return try await executeRaw(request)
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

    // Returns (Data, statusCode) for 2xx; throws APIError.httpError with parsed
    // detail for non-2xx so callers can branch on status code + body together.
    private static func executeRaw(_ request: URLRequest) async throws -> (Data, Int) {
        let (data, response) = try await perform(request)

        guard let http = response as? HTTPURLResponse else {
            throw APIError.networkError(URLError(.badServerResponse))
        }

        guard (200...299).contains(http.statusCode) else {
            let detail = try? JSONDecoder().decode(ErrorBody.self, from: data)
            throw APIError.httpError(statusCode: http.statusCode, detail: detail?.detail)
        }

        return (data, http.statusCode)
    }

    // URLSession.data(for:) async is iOS 15+.
    // This continuation wrapper is iOS 13+ compatible.
    // Uses _testURLSession (DEBUG only) when set, so tests can intercept calls
    // via MockURLProtocol without touching URLSession.shared.
    private static func perform(_ request: URLRequest) async throws -> (Data, URLResponse) {
        #if DEBUG
        print("[APIClient] ▶ \(request.httpMethod ?? "?") \(request.url?.absoluteString ?? "nil")")
        let activeSession = _testURLSession ?? URLSession.shared
        #else
        let activeSession = URLSession.shared
        #endif
        return try await withCheckedThrowingContinuation { continuation in
            activeSession.dataTask(with: request) { data, response, error in
                if let error = error {
                    #if DEBUG
                    let urlErr = error as? URLError
                    print("[APIClient] ✖ code=\(urlErr?.code.rawValue ?? -99999) type=\(type(of: error)) msg=\(error.localizedDescription)")
                    #endif
                    continuation.resume(throwing: APIError.networkError(error))
                } else if let data = data, let response = response {
                    #if DEBUG
                    if let http = response as? HTTPURLResponse {
                        print("[APIClient] ✔ status=\(http.statusCode)")
                    }
                    #endif
                    continuation.resume(returning: (data, response))
                } else {
                    #if DEBUG
                    print("[APIClient] ✖ no-data-no-error (unknown state)")
                    #endif
                    continuation.resume(throwing: APIError.networkError(URLError(.unknown)))
                }
            }.resume()
        }
    }

    // Builds a temp file containing the complete multipart body:
    //   boundary preamble → video bytes (1 MB chunks) → boundary epilogue.
    // Uses FileHandle so the source video is never fully loaded into memory.
    // Internal visibility allows direct testing of multipart construction.
    internal static func buildMultipartTempFile(
        from sourceURL: URL,
        mimeType:       String,
        fieldName:      String,
        boundary:       String
    ) throws -> URL {
        let tempFile = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".multipart")

        guard FileManager.default.createFile(atPath: tempFile.path, contents: nil) else {
            throw APIError.networkError(URLError(.cannotCreateFile))
        }
        let writer = try FileHandle(forWritingTo: tempFile)
        defer { writer.closeFile() }

        let crlf = "\r\n"
        let preamble = "--\(boundary)\(crlf)"
            + "Content-Disposition: form-data; name=\"\(fieldName)\"; filename=\"\(sourceURL.lastPathComponent)\"\(crlf)"
            + "Content-Type: \(mimeType)\(crlf)"
            + crlf
        writer.write(Data(preamble.utf8))

        let reader = try FileHandle(forReadingFrom: sourceURL)
        defer { reader.closeFile() }
        let chunkSize = 1024 * 1024   // 1 MB — never holds the full video in memory
        while true {
            let chunk = reader.readData(ofLength: chunkSize)
            if chunk.isEmpty { break }
            writer.write(chunk)
        }

        writer.write(Data((crlf + "--\(boundary)--" + crlf).utf8))
        return tempFile
    }

    // URLSession.uploadTask(with:fromFile:) continuation wrapper — iOS 7+ compatible.
    // (URLSession.upload(for:fromFile:) async is iOS 15+.)
    // Streams the multipart temp file as the HTTP body without buffering.
    // Uses _testURLSession when set so MockURLProtocol can intercept upload tasks.
    private static func performUploadTask(
        _ request: URLRequest,
        fromFile fileURL: URL
    ) async throws -> (Data, URLResponse) {
        #if DEBUG
        print("[APIClient] ⬆ uploadTask \(request.url?.absoluteString ?? "nil")")
        let activeSession = _testURLSession ?? URLSession.shared
        #else
        let activeSession = URLSession.shared
        #endif
        return try await withCheckedThrowingContinuation { continuation in
            activeSession.uploadTask(with: request, fromFile: fileURL) { data, response, error in
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
