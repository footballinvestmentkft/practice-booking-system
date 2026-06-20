import Foundation

enum LegacySkeletonAdapter {

    static func adaptV1ToV2(
        keypoints: PoseKeypointsDTO,
        sessionId: String,
        captureId: String,
        frameId: String,
        sourceTimestampNs: Int64 = 0,
        cameraId: String = "iphone_primary"
    ) -> Skeleton3DFrame {
        let joints = AppleVisionJointMapper.mapToCanonical(body: keypoints.body)
        return Skeleton3DFrame(
            schemaVersion: "2",
            sessionId: sessionId,
            captureId: captureId,
            cameraId: cameraId,
            calibrationId: nil,
            frameId: frameId,
            sourceTimestampNs: sourceTimestampNs,
            synchronizedTimestampNs: nil,
            personId: 0,
            joints: joints,
            coordinateSystem: "camera_a_origin_rh_meters",
            triangulationMethod: nil,
            processingVersion: "1.0.0"
        )
    }
}
