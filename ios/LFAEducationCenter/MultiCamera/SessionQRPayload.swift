import Foundation

struct SessionQRPayload: Codable, Equatable {
    let type: String
    let v: Int
    let sessionUuid: String

    enum CodingKeys: String, CodingKey {
        case type, v
        case sessionUuid = "session_uuid"
    }

    static let expectedType = "lfa_multicamera_join"
    static let supportedVersion = 1

    static func encode(sessionUuid: String) -> String? {
        let payload = SessionQRPayload(type: expectedType, v: supportedVersion, sessionUuid: sessionUuid)
        guard let data = try? JSONEncoder().encode(payload) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    enum DecodeError: LocalizedError, Equatable {
        case invalidJSON
        case unknownType(String)
        case unsupportedVersion(Int)
        case missingUUID

        var errorDescription: String? {
            switch self {
            case .invalidJSON:              return "Érvénytelen QR-kód"
            case .unknownType(let t):       return "Ez nem session QR-kód (type: \(t))"
            case .unsupportedVersion(let v): return "Frissítsd az alkalmazást (v\(v) nem támogatott)"
            case .missingUUID:              return "Hiányzó session UUID a QR-kódban"
            }
        }
    }

    static func decode(from string: String) -> Result<SessionQRPayload, DecodeError> {
        guard let data = string.data(using: .utf8),
              let payload = try? JSONDecoder().decode(SessionQRPayload.self, from: data) else {
            return .failure(.invalidJSON)
        }
        guard payload.type == expectedType else {
            return .failure(.unknownType(payload.type))
        }
        guard payload.v == supportedVersion else {
            return .failure(.unsupportedVersion(payload.v))
        }
        guard !payload.sessionUuid.isEmpty else {
            return .failure(.missingUUID)
        }
        return .success(payload)
    }
}
