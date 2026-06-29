import Foundation

#if DEBUG
// MARK: — GoPro 8:7 Recording Preset Read/Write Validation (debug-only)
//
// This is the FIRST GoPro POC in this project that actually WRITES a camera
// setting — deliberately kept in its own file, separate from the read-only
// GoProCameraStateProbe and the preview-only GoProStreamProbe, so the "this
// one mutates camera state" risk is never accidentally mixed with the
// read-only probes.
//
// Setting IDs and values are sourced directly from the official Open GoPro
// Python SDK (github.com/gopro/OpenGoPro,
// demos/python/sdk_wireless_camera_control/open_gopro/models/constants/settings.py),
// confirmed to match the HTTP camera/state response 1:1 in this session's
// physical gopro-camera-state-probe run (same numeric IDs over HTTP as BLE).
//
// IMPORTANT NAMING COLLISION: setting ID 108 is "Video Aspect Ratio", but
// completely unrelated to that, the VALUE 108 for setting ID 2 ("Video
// Resolution") happens to mean "4K 8:7 V2". These are two different
// namespaces that happen to share the number 108 — every use below is
// explicit about which one it is.
enum GoProPresetIds {
    static let resolutionSettingId = 2
    static let fpsSettingId = 3
    static let aspectRatioSettingId = 108
    static let lensSettingId = 121
    static let stabilizationSettingId = 135
    static let horizonLevelingSettingId = 150

    // Known option values (Open GoPro SDK settings.py enums)
    static let resolution4K = 1
    static let resolution4K_8_7_V2 = 108   // VideoResolution.NUM_4K_8_7_V2 — NOT the aspect-ratio setting ID
    static let resolution5_3K_8_7_V2 = 107 // VideoResolution.NUM_5_3K_8_7_V2 — fallback if 4K 8:7 rejected
    static let fps30 = 8                    // FramesPerSecond.NUM_30_0
    static let aspectRatio16_9 = 1
    static let aspectRatio8_7 = 3
    static let lensWide = 0
    static let stabilizationLow = 1
}

/// A decoded snapshot of the camera/state settings relevant to this probe —
/// raw integer values (as the camera actually reports them) plus a
/// human-readable label per field, built from the same enum tables used in
/// the aspect-ratio audit. Equatable on the raw values only, so before/after
/// comparison is exact and not affected by label string changes.
struct GoProSettingsSnapshot: Equatable {
    let resolutionRaw: Int?
    let fpsRaw: Int?
    let aspectRatioRaw: Int?
    let lensRaw: Int?
    let stabilizationRaw: Int?
    let horizonLevelingRaw: Int?

    static func == (lhs: GoProSettingsSnapshot, rhs: GoProSettingsSnapshot) -> Bool {
        lhs.resolutionRaw == rhs.resolutionRaw &&
        lhs.fpsRaw == rhs.fpsRaw &&
        lhs.aspectRatioRaw == rhs.aspectRatioRaw &&
        lhs.lensRaw == rhs.lensRaw &&
        lhs.stabilizationRaw == rhs.stabilizationRaw &&
        lhs.horizonLevelingRaw == rhs.horizonLevelingRaw
    }

    /// Comparison restricted to the 3 fields this probe actually writes —
    /// used for rollback confirmation, since lens/stabilization/horizon are
    /// never touched by this probe and shouldn't gate rollback success.
    func writtenFieldsMatch(_ other: GoProSettingsSnapshot) -> Bool {
        resolutionRaw == other.resolutionRaw &&
        fpsRaw == other.fpsRaw &&
        aspectRatioRaw == other.aspectRatioRaw
    }

    var resolutionLabel: String { Self.label(resolutionRaw, table: [1: "4K", 108: "4K_8:7_V2", 107: "5.3K_8:7_V2", 9: "1080p", 12: "720p"]) }
    var fpsLabel: String { Self.label(fpsRaw, table: [8: "30fps", 10: "24fps", 9: "25fps", 5: "60fps"]) }
    var aspectRatioLabel: String { Self.label(aspectRatioRaw, table: [0: "4:3", 1: "16:9", 3: "8:7", 4: "9:16", 5: "21:9", 6: "1:1"]) }
    var lensLabel: String { Self.label(lensRaw, table: [0: "Wide", 2: "Narrow", 3: "SuperView", 4: "Linear"]) }
    var stabilizationLabel: String { Self.label(stabilizationRaw, table: [0: "Off", 1: "Low", 2: "High", 3: "Boost", 4: "AutoBoost"]) }
    var horizonLevelingLabel: String { horizonLevelingRaw == nil ? "not present" : Self.label(horizonLevelingRaw, table: [0: "Off", 2: "Locked"]) }

    private static func label(_ raw: Int?, table: [Int: String]) -> String {
        guard let raw else { return "unknown (field absent)" }
        return table[raw] ?? "unrecognized (raw=\(raw))"
    }

    var asDict: [String: Any] {
        [
            "resolutionRaw": resolutionRaw ?? NSNull(), "resolutionLabel": resolutionLabel,
            "fpsRaw": fpsRaw ?? NSNull(), "fpsLabel": fpsLabel,
            "aspectRatioRaw": aspectRatioRaw ?? NSNull(), "aspectRatioLabel": aspectRatioLabel,
            "lensRaw": lensRaw ?? NSNull(), "lensLabel": lensLabel,
            "stabilizationRaw": stabilizationRaw ?? NSNull(), "stabilizationLabel": stabilizationLabel,
            "horizonLevelingRaw": horizonLevelingRaw ?? NSNull(), "horizonLevelingLabel": horizonLevelingLabel,
        ]
    }

    static func fetch(transport: GoProHTTPClientTransport) async -> GoProSettingsSnapshot? {
        guard let data = try? await transport.get(path: GoProSpec.cameraStatePath, timeout: 10),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let settings = json["settings"] as? [String: Any] else { return nil }
        func intValue(_ key: Int) -> Int? { (settings["\(key)"] as? NSNumber)?.intValue }
        return GoProSettingsSnapshot(
            resolutionRaw: intValue(GoProPresetIds.resolutionSettingId),
            fpsRaw: intValue(GoProPresetIds.fpsSettingId),
            aspectRatioRaw: intValue(GoProPresetIds.aspectRatioSettingId),
            lensRaw: intValue(GoProPresetIds.lensSettingId),
            stabilizationRaw: intValue(GoProPresetIds.stabilizationSettingId),
            horizonLevelingRaw: intValue(GoProPresetIds.horizonLevelingSettingId)
        )
    }
}

enum GoProRecordingPresetProbe {

    /// Full read → write → verify → recording-proof → preview-after-write
    /// chain, with mandatory rollback on ANY step failure. Returns the final
    /// outcome dict (also written to gopro_preset_final_diag.json).
    ///
    /// Per explicit product requirement: a rollback-triggered path is a
    /// HANDLED FAIL, never PASS — only the unbroken full chain (write +
    /// verify=8:7 + recording proof + preview-after-write all succeeding)
    /// counts as PASS. Rollback itself failing is CRITICAL FAIL.
    static func run() async -> [String: Any] {
        let transport = GoProHTTPClientTransport()
        var finalDiag: [String: Any] = ["timestamp": ISO8601DateFormatter().string(from: Date())]

        // 1. Read-before-write — mandatory.
        guard let before = await GoProSettingsSnapshot.fetch(transport: transport) else {
            finalDiag["outcome"] = "critical_fail_before_read_failed"
            finalDiag["reason"] = "camera/state could not be read before any write was attempted — no write occurred."
            print("[GOPRO-PRESET-POC] CRITICAL: before-read failed, aborting before any write")
            return finalDiag
        }
        GoProPresetBeforeDiagWriter.write(before.asDict)
        print("[GOPRO-PRESET-POC] before: \(before.asDict)")

        var writeLog: [[String: Any]] = []
        var changedAspect = false
        var changedResolution = false
        var changedFPS = false
        var resolutionValueUsed: Int?

        func writeSetting(_ settingId: Int, _ value: Int, label: String) async -> Bool {
            let path = "\(GoProSpec.settingPath)?setting=\(settingId)&option=\(value)"
            do {
                _ = try await transport.get(path: path, timeout: 10)
                writeLog.append(["setting": label, "settingId": settingId, "value": value, "httpStatus": "ok"])
                print("[GOPRO-PRESET-POC] write OK: \(label) (setting=\(settingId), option=\(value))")
                return true
            } catch {
                writeLog.append(["setting": label, "settingId": settingId, "value": value, "httpStatus": "error: \(error)"])
                print("[GOPRO-PRESET-POC] write FAILED: \(label) (setting=\(settingId), option=\(value)): \(error)")
                return false
            }
        }

        // 2. Write sequence: aspect ratio -> resolution -> fps, with a brief
        //    settle delay between each so the camera finishes applying one
        //    change before the next arrives.
        var writeChainOK = true

        if await writeSetting(GoProPresetIds.aspectRatioSettingId, GoProPresetIds.aspectRatio8_7, label: "VideoAspectRatio=8:7") {
            changedAspect = true
            try? await Task.sleep(nanoseconds: 3_000_000_000)
        } else {
            writeChainOK = false
        }

        if writeChainOK {
            if await writeSetting(GoProPresetIds.resolutionSettingId, GoProPresetIds.resolution4K_8_7_V2, label: "VideoResolution=4K_8:7_V2") {
                changedResolution = true
                resolutionValueUsed = GoProPresetIds.resolution4K_8_7_V2
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            } else if await writeSetting(GoProPresetIds.resolutionSettingId, GoProPresetIds.resolution5_3K_8_7_V2, label: "VideoResolution=5.3K_8:7_V2 (fallback)") {
                changedResolution = true
                resolutionValueUsed = GoProPresetIds.resolution5_3K_8_7_V2
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            } else {
                writeChainOK = false
            }
        }

        if writeChainOK, before.fpsRaw != GoProPresetIds.fps30 {
            if await writeSetting(GoProPresetIds.fpsSettingId, GoProPresetIds.fps30, label: "FramesPerSecond=30") {
                changedFPS = true
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            } else {
                writeChainOK = false
            }
        }

        let writeDiag: [String: Any] = [
            "timestamp": ISO8601DateFormatter().string(from: Date()),
            "writeChainOK": writeChainOK,
            "changedAspect": changedAspect, "changedResolution": changedResolution, "changedFPS": changedFPS,
            "resolutionValueUsed": resolutionValueUsed ?? NSNull(),
            "writeLog": writeLog,
        ]
        GoProPresetWriteDiagWriter.write(writeDiag)

        // Helper: roll back everything this run changed, then verify by
        // re-reading state and comparing the WRITTEN fields only.
        func rollback() async -> (confirmed: Bool, afterRollback: GoProSettingsSnapshot?) {
            print("[GOPRO-PRESET-POC] rolling back changed settings to original values...")
            if changedFPS, let fps = before.fpsRaw {
                _ = await writeSetting(GoProPresetIds.fpsSettingId, fps, label: "rollback FramesPerSecond")
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
            if changedResolution, let res = before.resolutionRaw {
                _ = await writeSetting(GoProPresetIds.resolutionSettingId, res, label: "rollback VideoResolution")
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
            if changedAspect, let aspect = before.aspectRatioRaw {
                _ = await writeSetting(GoProPresetIds.aspectRatioSettingId, aspect, label: "rollback VideoAspectRatio")
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
            let after = await GoProSettingsSnapshot.fetch(transport: transport)
            let confirmed = after.map { before.writtenFieldsMatch($0) } ?? false
            return (confirmed, after)
        }

        if !writeChainOK {
            let (confirmed, afterRollback) = await rollback()
            finalDiag["outcome"] = confirmed ? "handled_fail_write_failed_rollback_ok" : "critical_fail_write_failed_rollback_failed"
            finalDiag["rollbackAttempted"] = true
            finalDiag["rollbackConfirmed"] = confirmed
            finalDiag["afterRollbackState"] = afterRollback?.asDict ?? NSNull()
            finalDiag["writeLog"] = writeLog
            print("[GOPRO-PRESET-POC] === \(confirmed ? "HANDLED FAIL" : "CRITICAL FAIL") (write failed, rollback confirmed=\(confirmed)) ===")
            GoProPresetFinalDiagWriter.write(finalDiag)
            return finalDiag
        }

        // 3. Verify-after-write.
        guard let after = await GoProSettingsSnapshot.fetch(transport: transport) else {
            let (confirmed, afterRollback) = await rollback()
            finalDiag["outcome"] = confirmed ? "handled_fail_verify_read_failed_rollback_ok" : "critical_fail_verify_read_failed_rollback_failed"
            finalDiag["rollbackAttempted"] = true
            finalDiag["rollbackConfirmed"] = confirmed
            finalDiag["afterRollbackState"] = afterRollback?.asDict ?? NSNull()
            GoProPresetFinalDiagWriter.write(finalDiag)
            print("[GOPRO-PRESET-POC] === \(confirmed ? "HANDLED FAIL" : "CRITICAL FAIL") (post-write state unreadable) ===")
            return finalDiag
        }
        GoProPresetAfterDiagWriter.write(after.asDict)
        print("[GOPRO-PRESET-POC] after: \(after.asDict)")

        let aspectIs8_7 = after.aspectRatioRaw == GoProPresetIds.aspectRatio8_7
        let resolutionIsAspectCompatible = resolutionValueUsed != nil && after.resolutionRaw == resolutionValueUsed
        let fpsRemained30 = after.fpsRaw == GoProPresetIds.fps30

        if !(aspectIs8_7 && resolutionIsAspectCompatible && fpsRemained30) {
            let (confirmed, afterRollback) = await rollback()
            finalDiag["outcome"] = confirmed ? "handled_fail_verify_mismatch_rollback_ok" : "critical_fail_verify_mismatch_rollback_failed"
            finalDiag["rollbackAttempted"] = true
            finalDiag["rollbackConfirmed"] = confirmed
            finalDiag["afterRollbackState"] = afterRollback?.asDict ?? NSNull()
            finalDiag["verifyAspectIs8_7"] = aspectIs8_7
            finalDiag["verifyResolutionCompatible"] = resolutionIsAspectCompatible
            finalDiag["verifyFPSRemained30"] = fpsRemained30
            GoProPresetFinalDiagWriter.write(finalDiag)
            print("[GOPRO-PRESET-POC] === \(confirmed ? "HANDLED FAIL" : "CRITICAL FAIL") (verify-after-write mismatch) ===")
            return finalDiag
        }

        // 4. Recording proof + 5. Preview-after-write — GoProRecordingCycleProbe
        //    already runs both concurrently (Block 3), so one call covers both
        //    scope items at once.
        print("[GOPRO-PRESET-POC] settings verified at 8:7 — running recording proof + preview-after-write...")
        let recordingDiag = await GoProRecordingCycleProbe.run(previewDurationSeconds: 15)
        GoProRecordingDiagWriter.write(recordingDiag)
        GoProPreviewAspectDiagWriter.write(from: recordingDiag)

        let newFileOK = (recordingDiag["newFileCountDelta"] as? Int ?? 0) > 0
        let shutterOK = (recordingDiag["shutterStartOK"] as? Bool ?? false) && (recordingDiag["shutterStopOK"] as? Bool ?? false)
        let previewOK = (recordingDiag["previewDecodeSuccesses"] as? Int ?? 0) > 0

        if !(newFileOK && shutterOK && previewOK) {
            let (confirmed, afterRollback) = await rollback()
            finalDiag["outcome"] = confirmed ? "handled_fail_recording_or_preview_failed_rollback_ok" : "critical_fail_recording_or_preview_failed_rollback_failed"
            finalDiag["rollbackAttempted"] = true
            finalDiag["rollbackConfirmed"] = confirmed
            finalDiag["afterRollbackState"] = afterRollback?.asDict ?? NSNull()
            finalDiag["recordingNewFileOK"] = newFileOK
            finalDiag["recordingShutterOK"] = shutterOK
            finalDiag["recordingPreviewOK"] = previewOK
            GoProPresetFinalDiagWriter.write(finalDiag)
            print("[GOPRO-PRESET-POC] === \(confirmed ? "HANDLED FAIL" : "CRITICAL FAIL") (recording/preview proof failed) ===")
            return finalDiag
        }

        // Full chain succeeded — this is the ONLY path that is a true PASS.
        finalDiag["outcome"] = "applied_full_chain_pass"
        finalDiag["rollbackAttempted"] = false
        finalDiag["rollbackConfirmed"] = NSNull()
        finalDiag["beforeState"] = before.asDict
        finalDiag["afterState"] = after.asDict
        finalDiag["recordingNewFileOK"] = newFileOK
        finalDiag["recordingShutterOK"] = shutterOK
        finalDiag["recordingPreviewOK"] = previewOK
        finalDiag["previewWidth"] = recordingDiag["previewWidth"] ?? NSNull()
        finalDiag["previewHeight"] = recordingDiag["previewHeight"] ?? NSNull()
        finalDiag["previewAspectRatio"] = recordingDiag["previewAspectRatio"] ?? NSNull()
        GoProPresetFinalDiagWriter.write(finalDiag)
        print("[GOPRO-PRESET-POC] === PASS: 8:7 preset applied, verified, recorded, and preview measured ===")
        return finalDiag
    }
}

enum GoProPresetBeforeDiagWriter {
    static let fileName = "gopro_preset_before_diag.json"
    static func write(_ diag: [String: Any]) { GoProPresetDiagWriterCore.write(diag, fileName: fileName) }
}

enum GoProPresetWriteDiagWriter {
    static let fileName = "gopro_preset_write_diag.json"
    static func write(_ diag: [String: Any]) { GoProPresetDiagWriterCore.write(diag, fileName: fileName) }
}

enum GoProPresetAfterDiagWriter {
    static let fileName = "gopro_preset_after_diag.json"
    static func write(_ diag: [String: Any]) { GoProPresetDiagWriterCore.write(diag, fileName: fileName) }
}

enum GoProPresetFinalDiagWriter {
    static let fileName = "gopro_preset_final_diag.json"
    static func write(_ diag: [String: Any]) { GoProPresetDiagWriterCore.write(diag, fileName: fileName) }
}

private enum GoProPresetDiagWriterCore {
    static func write(_ diag: [String: Any], fileName: String) {
        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
              JSONSerialization.isValidJSONObject(diag),
              let data = try? JSONSerialization.data(withJSONObject: diag, options: [.prettyPrinted]) else { return }
        try? data.write(to: docs.appendingPathComponent(fileName), options: .atomic)
        print("[GOPRO-PRESET-POC] wrote \(fileName)")
    }
}
#endif
