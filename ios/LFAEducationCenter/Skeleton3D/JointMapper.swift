import Foundation

enum AppleVisionJointMapper {

    static func mapToCanonical(body: [BodyLandmarkDTO]) -> [Skeleton3DJoint] {
        let byName = Dictionary(uniqueKeysWithValues: body.map { ($0.name, $0) })
        var result: [Skeleton3DJoint] = []
        for entry in AppleVisionJointMapping.map {
            guard let lm = byName[entry.visionName] else { continue }
            result.append(Skeleton3DJoint(
                canonicalJointName: entry.canonical.rawValue,
                sourceJointName: entry.sourceRawValue,
                sourceModel: "apple_vision_body_pose_v1",
                sourceConfidence: lm.confidence,
                imageX: lm.x,
                imageY: lm.y,
                imageConfidence: lm.confidence,
                isSynthetic: false,
                worldX: nil, worldY: nil, worldZ: nil,
                worldConfidence: nil,
                reprojectionErrorPx: nil,
                sourceViewIds: [],
                triangulationStatus: TriangulationStatus.singleViewOnly.rawValue
            ))
        }
        return result
    }
}
