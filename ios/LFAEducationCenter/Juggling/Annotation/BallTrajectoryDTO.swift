import Foundation

// MARK: — Ball Trajectory DTOs (AN-3B2D-3)

struct BallTrajectoryPointDTO: Decodable, Equatable {
    let frameMs: Int
    let ballX: Double?
    let ballY: Double?
    let confidence: Double?
    let isManual: Bool
    let trackingState: String

    enum CodingKeys: String, CodingKey {
        case frameMs       = "frame_ms"
        case ballX         = "ball_x"
        case ballY         = "ball_y"
        case confidence
        case isManual      = "is_manual"
        case trackingState = "tracking_state"
    }
}

struct BallTrajectoryResponseDTO: Decodable {
    let status: String
    let points: [BallTrajectoryPointDTO]
}
