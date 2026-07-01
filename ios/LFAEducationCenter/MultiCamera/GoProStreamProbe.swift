import Foundation
import Network
import VideoToolbox
import CoreMedia
import UIKit

// MARK: — GoPro Live Preview POC (debug-only, eldobható kód)
//
// See docs/GOPRO_LIVE_PREVIEW_POC_PLAN.md — this is Step 1-5 of that POC:
// stream/start over HTTP, UDP receive on port 8554 bound to the GoPro WiFi
// interface, best-effort MPEG-TS demux + H.264 NAL extraction, VideoToolbox
// decode, and a structured gopro_stream_diag.json (same evidence pattern as
// GoProDiagRecorder — idevicesyslog print() capture is not reliable).
//
// This is intentionally NOT production-quality: no PES header parsing
// (NAL boundaries found by scanning for Annex-B start codes directly in the
// reassembled PID payload), no error recovery across TS continuity gaps, no
// B-frame reordering. If decode fails, packet-level + format-level evidence
// (PAT/PMT found, video PID, SPS/PPS seen) is still captured per the POC's
// "honest pass/fail" requirement.

#if DEBUG

struct PMTStreamEntry {
    let pid: Int
    let streamType: UInt8
    let descriptorTags: [UInt8]

    var streamTypeHex: String { String(format: "0x%02X", streamType) }
    var descriptorTagsHex: [String] { descriptorTags.map { String(format: "0x%02X", $0) } }

    /// Known video-ish stream_types per ISO/IEC 13818-1 + ATSC/DVB registrations.
    /// 0x1B = H.264/AVC, 0x24 = H.265/HEVC, 0x02 = MPEG-2 video, 0x10 = MPEG-4 video.
    var isVideoCandidate: Bool { [0x1B, 0x24, 0x02, 0x10].contains(streamType) }
    var codecGuess: String? {
        switch streamType {
        case 0x1B: return "h264"
        case 0x24: return "hevc"
        case 0x02: return "mpeg2"
        case 0x10: return "mpeg4"
        default: return nil
        }
    }
}

private struct MPEGTSDemuxer {
    private(set) var videoPID: Int?
    private(set) var selectedCodec: String?
    private(set) var pmtPID: Int?
    private(set) var pmtStreams: [PMTStreamEntry] = []
    private(set) var patParseCount = 0
    private(set) var pmtParseCount = 0
    private var payload = Data()

    var videoCandidatePIDs: [Int] { pmtStreams.filter { $0.isVideoCandidate }.map { $0.pid } }

    var reasonNoVideoPID: String? {
        guard videoPID == nil else { return nil }
        guard pmtPID != nil else { return "PAT found but PMT never parsed within the observation window" }
        guard !pmtStreams.isEmpty else { return "PMT PID known but no PMT section parsed yet (0 streams found)" }
        let summary = pmtStreams.map { "pid=\($0.pid) streamType=\($0.streamTypeHex)" }.joined(separator: ", ")
        return "PMT parsed (\(pmtStreams.count) stream(s)) but none matched a known video stream_type " +
               "(0x1B=H.264, 0x24=HEVC, 0x02=MPEG2, 0x10=MPEG4). Found: [\(summary)]"
    }

    // Self-validating TS sync search diagnostics — do NOT assume byte 0 of
    // every UDP datagram is a TS sync byte (0x47). GoPro's preview stream may
    // wrap MPEG-TS in RTP (typically a 12-byte header before the TS payload),
    // which silently breaks a fixed offset=0 assumption: byte 0 is read as a
    // TS header, every field downstream is garbage, and PAT/PMT detection
    // becomes a matter of luck instead of working reliably.
    private(set) var detectedSyncOffset: Int?
    private(set) var syncHitDatagrams = 0
    private(set) var syncMissDatagrams = 0

    var formatGuess: String {
        guard let offset = detectedSyncOffset else { return "unknown_no_sync_found" }
        switch offset {
        case 0: return "raw_mpegts (sync offset 0)"
        case 10...14: return "rtp_wrapped_mpegts (sync offset \(offset), consistent with a 12-byte RTP header)"
        default: return "unknown_offset_\(offset)"
        }
    }

    /// Self-validating sync search: try offsets 0...16 and accept the first
    /// one where 0x47 repeats at the expected 188-byte TS packet stride
    /// three times in a row (offset, offset+188, offset+376). This handles
    /// both raw MPEG-TS-over-UDP (offset 0) and RTP-wrapped MPEG-TS (offset
    /// ~12) without assuming which one the GoPro is actually sending.
    private static func findSyncOffset(in bytes: [UInt8]) -> Int? {
        let maxOffsetForTriple = min(16, bytes.count - 377)
        if maxOffsetForTriple >= 0 {
            for offset in 0...maxOffsetForTriple {
                if bytes[offset] == 0x47, bytes[offset + 188] == 0x47, bytes[offset + 376] == 0x47 {
                    return offset
                }
            }
        }
        // Datagram too short to validate 3 strides (e.g. a small trailing
        // packet) — fall back to a weaker double-stride check.
        let maxOffsetForDouble = min(16, bytes.count - 189)
        if maxOffsetForDouble >= 0 {
            for offset in 0...maxOffsetForDouble {
                if bytes[offset] == 0x47, bytes[offset + 188] == 0x47 {
                    return offset
                }
            }
        }
        return nil
    }

    /// Feed one UDP datagram. Returns newly completed Annex-B NAL units
    /// (start-code delimited) found in the video PID's payload since the
    /// last call.
    mutating func feed(_ datagram: Data) -> [Data] {
        let bytes = [UInt8](datagram)
        guard let offset = Self.findSyncOffset(in: bytes) else {
            syncMissDatagrams += 1
            return []
        }
        syncHitDatagrams += 1
        if detectedSyncOffset == nil { detectedSyncOffset = offset }

        var cursor = offset
        while cursor + 188 <= bytes.count {
            if bytes[cursor] == 0x47 {
                let packet = datagram.subdata(
                    in: datagram.index(datagram.startIndex, offsetBy: cursor)
                        ..< datagram.index(datagram.startIndex, offsetBy: cursor + 188)
                )
                parsePacket(packet)
            }
            cursor += 188
        }
        return drainNALs()
    }

    private mutating func parsePacket(_ packet: Data) {
        let bytes = [UInt8](packet)
        guard bytes.count == 188 else { return }
        let pid = (Int(bytes[1] & 0x1F) << 8) | Int(bytes[2])
        let adaptationFieldControl = (bytes[3] & 0x30) >> 4
        var cursor = 4
        if adaptationFieldControl == 2 { return } // adaptation field only, no payload
        if adaptationFieldControl == 3 {
            let afLen = Int(bytes[4])
            cursor = 5 + afLen
        }
        guard cursor < 188 else { return }
        let payloadBytes = packet[(packet.startIndex + cursor)...]

        if pid == 0x0000 {
            parsePAT(payloadBytes)
        } else if let pmt = pmtPID, pid == pmt {
            parsePMT(payloadBytes)
        } else if let vpid = videoPID, pid == vpid {
            payload.append(payloadBytes)
        }
    }

    private mutating func parsePAT(_ section: Data) {
        let b = [UInt8](section)
        guard b.count > 8 else { return }
        // pointer_field (b[0]) then table_id, section_length...
        let pointer = Int(b[0])
        let base = 1 + pointer
        guard base + 8 <= b.count else { return }
        let sectionLength = (Int(b[base + 1] & 0x0F) << 8) | Int(b[base + 2])
        var i = base + 8 // skip table_id..last_section_number
        let end = base + 3 + sectionLength - 4 // exclude CRC32
        var foundProgram = false
        while i + 4 <= end, i + 4 <= b.count {
            let programNumber = (Int(b[i]) << 8) | Int(b[i + 1])
            let pid = (Int(b[i + 2] & 0x1F) << 8) | Int(b[i + 3])
            if programNumber != 0 { pmtPID = pid; foundProgram = true } // first non-PAT-itself program
            i += 4
        }
        if foundProgram { patParseCount += 1 }
    }

    /// Parses every elementary stream entry in the PMT (not just the first
    /// H.264 match) — pid, stream_type, and raw descriptor tags for each —
    /// so a "no video PID found" result is fully explainable from the diag
    /// file: what stream_types WERE present, any AVC/HEVC descriptors, any
    /// private/GoPro-specific (telemetry/metadata) streams, etc.
    private mutating func parsePMT(_ section: Data) {
        let b = [UInt8](section)
        guard b.count > 12 else { return }
        let pointer = Int(b[0])
        let base = 1 + pointer
        guard base + 12 <= b.count else { return }
        let sectionLength = (Int(b[base + 1] & 0x0F) << 8) | Int(b[base + 2])
        let programInfoLength = (Int(b[base + 10] & 0x0F) << 8) | Int(b[base + 11])
        var i = base + 12 + programInfoLength
        let end = base + 3 + sectionLength - 4
        var streams: [PMTStreamEntry] = []
        while i + 5 <= end, i + 5 <= b.count {
            let streamType = b[i]
            let pid = (Int(b[i + 1] & 0x1F) << 8) | Int(b[i + 2])
            let esInfoLength = (Int(b[i + 3] & 0x0F) << 8) | Int(b[i + 4])
            var descriptorTags: [UInt8] = []
            var d = i + 5
            let esEnd = min(d + esInfoLength, b.count)
            while d + 2 <= esEnd {
                descriptorTags.append(b[d]) // descriptor_tag (e.g. 0x28 AVC, 0x38/0x39 HEVC)
                let descLen = Int(b[d + 1])
                d += 2 + descLen
            }
            streams.append(PMTStreamEntry(pid: pid, streamType: streamType, descriptorTags: descriptorTags))
            i += 5 + esInfoLength
        }
        guard !streams.isEmpty else { return }
        pmtStreams = streams
        pmtParseCount += 1
        if videoPID == nil {
            // Prefer H.264 (matches the decode pipeline today); fall back to
            // HEVC (VPS/SPS/PPS path) if that's what the camera is sending.
            if let h264 = streams.first(where: { $0.streamType == 0x1B }) {
                videoPID = h264.pid; selectedCodec = "h264"
            } else if let hevc = streams.first(where: { $0.streamType == 0x24 }) {
                videoPID = hevc.pid; selectedCodec = "hevc"
            } else if let other = streams.first(where: { $0.isVideoCandidate }) {
                videoPID = other.pid; selectedCodec = other.codecGuess
            }
        }
    }

    private mutating func drainNALs() -> [Data] {
        guard !payload.isEmpty else { return [] }
        var starts: [Int] = []
        let bytes = [UInt8](payload)
        var i = 0
        while i + 3 < bytes.count {
            if bytes[i] == 0, bytes[i + 1] == 0, bytes[i + 2] == 1 {
                starts.append(i)
                i += 3
            } else if i + 4 < bytes.count, bytes[i] == 0, bytes[i + 1] == 0, bytes[i + 2] == 0, bytes[i + 3] == 1 {
                starts.append(i)
                i += 4
            } else {
                i += 1
            }
        }
        guard starts.count > 1 else { return [] } // keep buffering until we have ≥2 boundaries
        var nals: [Data] = []
        for idx in 0..<(starts.count - 1) {
            let codeLen = (bytes[starts[idx] + 2] == 1) ? 3 : 4
            let nalStart = starts[idx] + codeLen
            let nalEnd = starts[idx + 1]
            guard nalStart < nalEnd else { continue }
            nals.append(payload.subdata(in: payload.index(payload.startIndex, offsetBy: nalStart) ..< payload.index(payload.startIndex, offsetBy: nalEnd)))
        }
        // Keep everything from the last detected start code onward (incomplete NAL).
        payload = payload.subdata(in: payload.index(payload.startIndex, offsetBy: starts.last!) ..< payload.endIndex)
        return nals
    }
}

@MainActor
final class GoProStreamProbe: ObservableObject {
    static let shared = GoProStreamProbe()

    @Published private(set) var lastFrame: UIImage?
    @Published private(set) var isRunning = false

    private var listener: NWListener?
    private var demuxer = MPEGTSDemuxer()
    private var decompressionSession: VTDecompressionSession?
    private var formatDescription: CMVideoFormatDescription? {
        didSet {
            guard let fmtDesc = formatDescription else { previewWidth = nil; previewHeight = nil; return }
            let dims = CMVideoFormatDescriptionGetDimensions(fmtDesc)
            previewWidth = Int(dims.width)
            previewHeight = Int(dims.height)
        }
    }
    /// Actual decoded preview dimensions, read from the SPS-derived format
    /// description — NOT assumed from any stream/start query parameter,
    /// since the Open GoPro preview stream's real resolution/aspect was
    /// never measured before this probe (see docs/GOPRO_LIVE_PREVIEW_POC_PLAN.md).
    private var previewWidth: Int?
    private var previewHeight: Int?
    private var spsData: Data?  // H.264 SPS, or HEVC SPS (codec disambiguated by demuxer.selectedCodec)
    private var ppsData: Data?  // H.264 PPS, or HEVC PPS
    private var vpsData: Data?  // HEVC VPS only

    private var packetsReceived = 0
    private var bytesReceived = 0
    private var firstPacketAt: Date?
    private var lastPacketAt: Date?
    private var decodeAttempts = 0
    // Published (not private) so the dashboard can read live decode-success count as the
    // GoPro panel's "source frame" diagnostic (2026-07-01 flow audit — PoseOverlayDiagWriter).
    @Published private(set) var decodeSuccesses = 0
    private var lastError: String?
    private var frameTimestamps: [Date] = []

    private init() {}

    /// Runs the full POC: stream/start → UDP receive for `durationSeconds` →
    /// stream/stop. Returns a diagnostic dictionary matching the
    /// gopro_stream_diag.json schema in GOPRO_LIVE_PREVIEW_POC_PLAN.md.
    func run(durationSeconds: TimeInterval = 25) async -> [String: Any] {
        reset()
        isRunning = true
        let transport = GoProHTTPClientTransport()
        var diag: [String: Any] = ["timestamp": ISO8601DateFormatter().string(from: Date())]

        do {
            _ = try await transport.get(path: GoProSpec.streamStartPath, timeout: 5)
            diag["streamStartHTTPStatus"] = "ok"
            print("[GOPRO-STREAM-POC] stream/start OK")
        } catch {
            diag["streamStartHTTPStatus"] = "error: \(error)"
            lastError = "stream_start_failed: \(error)"
            print("[GOPRO-STREAM-POC] stream/start FAILED: \(error)")
        }

        startListener()
        try? await Task.sleep(nanoseconds: UInt64(durationSeconds * 1_000_000_000))
        stopListener()

        do {
            _ = try await transport.get(path: GoProSpec.streamStopPath, timeout: 5)
            diag["streamStopHTTPStatus"] = "ok"
        } catch {
            diag["streamStopHTTPStatus"] = "error: \(error)"
        }

        isRunning = false
        diag["udpPacketsReceived"] = packetsReceived
        diag["udpBytesReceived"] = bytesReceived
        diag["tsSyncOffsetDetected"] = demuxer.detectedSyncOffset ?? NSNull()
        diag["tsSyncFormatGuess"] = demuxer.formatGuess
        diag["tsSyncHitDatagrams"] = demuxer.syncHitDatagrams
        diag["tsSyncMissDatagrams"] = demuxer.syncMissDatagrams
        diag["pmtPIDFound"] = demuxer.pmtPID != nil
        diag["pmtParseCount"] = demuxer.pmtParseCount
        diag["patParseCount"] = demuxer.patParseCount
        diag["pmtStreams"] = demuxer.pmtStreams.map { entry -> [String: Any] in
            [
                "pid": entry.pid,
                "streamType": entry.streamTypeHex,
                "descriptorTags": entry.descriptorTagsHex,
            ]
        }
        diag["videoCandidatePIDs"] = demuxer.videoCandidatePIDs
        diag["selectedVideoPID"] = demuxer.videoPID ?? NSNull()
        diag["selectedCodec"] = demuxer.selectedCodec ?? NSNull()
        diag["reasonNoVideoPID"] = demuxer.reasonNoVideoPID ?? NSNull()
        diag["videoPIDFound"] = demuxer.videoPID != nil
        diag["spsSeen"] = spsData != nil
        diag["ppsSeen"] = ppsData != nil
        diag["vpsSeen"] = vpsData != nil
        diag["decodeAttempts"] = decodeAttempts
        diag["decodeSuccesses"] = decodeSuccesses
        diag["fps"] = estimateFPS()
        diag["previewWidth"] = previewWidth ?? NSNull()
        diag["previewHeight"] = previewHeight ?? NSNull()
        diag["previewAspectRatio"] = Self.aspectRatioLabel(width: previewWidth, height: previewHeight) ?? NSNull()
        if let first = firstPacketAt, let last = lastPacketAt {
            diag["firstPacketLatencyMs"] = first.timeIntervalSince1970 * 1000
            diag["streamDurationObservedMs"] = last.timeIntervalSince(first) * 1000
        }
        diag["errorReason"] = lastError ?? NSNull()
        print("[GOPRO-STREAM-POC] result: \(diag)")
        return diag
    }

    private func reset() {
        packetsReceived = 0
        bytesReceived = 0
        firstPacketAt = nil
        lastPacketAt = nil
        decodeAttempts = 0
        decodeSuccesses = 0
        lastError = nil
        frameTimestamps = []
        demuxer = MPEGTSDemuxer()
        spsData = nil
        ppsData = nil
        vpsData = nil
        formatDescription = nil
        if let session = decompressionSession {
            VTDecompressionSessionInvalidate(session)
        }
        decompressionSession = nil
        lastFrame = nil
    }

    // MARK: — UDP receive (Network.framework, WiFi-only — see Block 1 dual-network notes)

    private func startListener() {
        let params = NWParameters.udp
        params.requiredInterfaceType = .wifi // GoPro AP only reachable over WiFi, never cellular
        guard let port = NWEndpoint.Port(rawValue: GoProSpec.previewStreamPort) else { return }
        do {
            let listener = try NWListener(using: params, on: port)
            listener.newConnectionHandler = { [weak self] connection in
                connection.start(queue: .main)
                Task { @MainActor in self?.receiveLoop(on: connection) }
            }
            listener.stateUpdateHandler = { state in
                print("[GOPRO-STREAM-POC] UDP listener state: \(state)")
            }
            listener.start(queue: .main)
            self.listener = listener
        } catch {
            lastError = "udp_listener_failed: \(error)"
            print("[GOPRO-STREAM-POC] UDP listener setup FAILED: \(error)")
        }
    }

    private func stopListener() {
        listener?.cancel()
        listener = nil
    }

    private func receiveLoop(on connection: NWConnection) {
        connection.receiveMessage { [weak self] data, _, isComplete, error in
            Task { @MainActor in
                guard let self else { return }
                if let data, !data.isEmpty {
                    self.handleDatagram(data)
                }
                if let error {
                    print("[GOPRO-STREAM-POC] UDP receive error: \(error)")
                    return
                }
                if connection.state == .ready || connection.state == .preparing {
                    self.receiveLoop(on: connection)
                }
            }
        }
    }

    private func handleDatagram(_ data: Data) {
        packetsReceived += 1
        bytesReceived += data.count
        let now = Date()
        if firstPacketAt == nil { firstPacketAt = now }
        lastPacketAt = now

        let nals = demuxer.feed(data)
        for nal in nals { handleNAL(nal) }
    }

    // MARK: — H.264/HEVC NAL handling + VideoToolbox decode
    //
    // The PMT's selected stream_type (demuxer.selectedCodec) decides how NAL
    // headers are parsed: H.264 uses a 1-byte header (type = byte0 & 0x1F),
    // HEVC uses a 2-byte header (type = (byte0 >> 1) & 0x3F) with an extra
    // parameter set (VPS) alongside SPS/PPS.

    private func handleNAL(_ nal: Data) {
        switch demuxer.selectedCodec {
        case "hevc":
            handleHEVCNAL(nal)
        default: // "h264", mpeg2/mpeg4 fallbacks are not decoded (no VideoToolbox path for them here)
            handleH264NAL(nal)
        }
    }

    private func handleH264NAL(_ nal: Data) {
        guard let firstByte = nal.first else { return }
        let nalType = firstByte & 0x1F
        switch nalType {
        case 7: // SPS
            spsData = nal
            tryBuildH264FormatDescription()
        case 8: // PPS
            ppsData = nal
            tryBuildH264FormatDescription()
        case 5, 1: // IDR / non-IDR slice
            decodeFrame(nal)
        default:
            break
        }
    }

    private func handleHEVCNAL(_ nal: Data) {
        guard nal.count >= 2 else { return }
        let nalType = (nal[nal.startIndex] >> 1) & 0x3F
        switch nalType {
        case 32: // VPS
            vpsData = nal
            tryBuildHEVCFormatDescription()
        case 33: // SPS
            spsData = nal
            tryBuildHEVCFormatDescription()
        case 34: // PPS
            ppsData = nal
            tryBuildHEVCFormatDescription()
        case 0...31: // VCL (slice) NAL units — trailing/leading/IDR/CRA etc.
            decodeFrame(nal)
        default:
            break
        }
    }

    private func tryBuildH264FormatDescription() {
        guard let sps = spsData, let pps = ppsData else { return }
        let spsBytes = [UInt8](sps)
        let ppsBytes = [UInt8](pps)
        let result = spsBytes.withUnsafeBufferPointer { spsPtr -> OSStatus in
            ppsBytes.withUnsafeBufferPointer { ppsPtr -> OSStatus in
                let pointers: [UnsafePointer<UInt8>] = [spsPtr.baseAddress!, ppsPtr.baseAddress!]
                let sizes: [Int] = [spsPtr.count, ppsPtr.count]
                var fmtDesc: CMVideoFormatDescription?
                let status = CMVideoFormatDescriptionCreateFromH264ParameterSets(
                    allocator: kCFAllocatorDefault,
                    parameterSetCount: 2,
                    parameterSetPointers: pointers,
                    parameterSetSizes: sizes,
                    nalUnitHeaderLength: 4,
                    formatDescriptionOut: &fmtDesc
                )
                if status == noErr { self.formatDescription = fmtDesc }
                return status
            }
        }
        if result == noErr {
            print("[GOPRO-STREAM-POC] H.264 format description created from SPS/PPS")
            createDecompressionSession()
        } else {
            lastError = "h264_format_description_failed: OSStatus \(result)"
        }
    }

    private func tryBuildHEVCFormatDescription() {
        guard let vps = vpsData, let sps = spsData, let pps = ppsData else { return }
        let vpsBytes = [UInt8](vps)
        let spsBytes = [UInt8](sps)
        let ppsBytes = [UInt8](pps)
        let result = vpsBytes.withUnsafeBufferPointer { vpsPtr -> OSStatus in
            spsBytes.withUnsafeBufferPointer { spsPtr -> OSStatus in
                ppsBytes.withUnsafeBufferPointer { ppsPtr -> OSStatus in
                    let pointers: [UnsafePointer<UInt8>] = [vpsPtr.baseAddress!, spsPtr.baseAddress!, ppsPtr.baseAddress!]
                    let sizes: [Int] = [vpsPtr.count, spsPtr.count, ppsPtr.count]
                    var fmtDesc: CMVideoFormatDescription?
                    let status = CMVideoFormatDescriptionCreateFromHEVCParameterSets(
                        allocator: kCFAllocatorDefault,
                        parameterSetCount: 3,
                        parameterSetPointers: pointers,
                        parameterSetSizes: sizes,
                        nalUnitHeaderLength: 4,
                        extensions: nil,
                        formatDescriptionOut: &fmtDesc
                    )
                    if status == noErr { self.formatDescription = fmtDesc }
                    return status
                }
            }
        }
        if result == noErr {
            print("[GOPRO-STREAM-POC] HEVC format description created from VPS/SPS/PPS")
            createDecompressionSession()
        } else {
            lastError = "hevc_format_description_failed: OSStatus \(result)"
        }
    }

    private func createDecompressionSession() {
        guard let fmtDesc = formatDescription else { return }
        if let existing = decompressionSession { VTDecompressionSessionInvalidate(existing) }
        var callback = VTDecompressionOutputCallbackRecord(
            decompressionOutputCallback: { refCon, _, status, _, imageBuffer, _, _ in
                guard status == noErr, let imageBuffer else { return }
                let probe = Unmanaged<GoProStreamProbe>.fromOpaque(refCon!).takeUnretainedValue()
                Task { @MainActor in probe.publishDecodedFrame(imageBuffer) }
            },
            decompressionOutputRefCon: Unmanaged.passUnretained(self).toOpaque()
        )
        var session: VTDecompressionSession?
        let status = VTDecompressionSessionCreate(
            allocator: kCFAllocatorDefault,
            formatDescription: fmtDesc,
            decoderSpecification: nil,
            imageBufferAttributes: nil,
            outputCallback: &callback,
            decompressionSessionOut: &session
        )
        if status == noErr {
            decompressionSession = session
            print("[GOPRO-STREAM-POC] VTDecompressionSession created")
        } else {
            lastError = "decompression_session_create_failed: OSStatus \(status)"
            print("[GOPRO-STREAM-POC] VTDecompressionSession create FAILED: \(status)")
        }
    }

    private func decodeFrame(_ nal: Data) {
        guard let session = decompressionSession else { return } // no SPS/PPS yet — drop
        decodeAttempts += 1

        // Annex-B NAL → AVCC length-prefixed for CMBlockBuffer.
        var lengthPrefixed = Data()
        var length = UInt32(nal.count).bigEndian
        withUnsafeBytes(of: &length) { lengthPrefixed.append(contentsOf: $0) }
        lengthPrefixed.append(nal)

        var blockBuffer: CMBlockBuffer?
        let blockStatus = lengthPrefixed.withUnsafeBytes { rawBuf -> OSStatus in
            CMBlockBufferCreateWithMemoryBlock(
                allocator: kCFAllocatorDefault,
                memoryBlock: nil, blockLength: rawBuf.count,
                blockAllocator: kCFAllocatorDefault, customBlockSource: nil,
                offsetToData: 0, dataLength: rawBuf.count, flags: 0,
                blockBufferOut: &blockBuffer
            )
        }
        guard blockStatus == noErr, let bb = blockBuffer else {
            lastError = "block_buffer_create_failed: OSStatus \(blockStatus)"
            return
        }
        lengthPrefixed.withUnsafeBytes { rawBuf in
            _ = CMBlockBufferReplaceDataBytes(with: rawBuf.baseAddress!, blockBuffer: bb, offsetIntoDestination: 0, dataLength: rawBuf.count)
        }

        var sampleBuffer: CMSampleBuffer?
        var sampleSizeArray = [lengthPrefixed.count]
        let sbStatus = CMSampleBufferCreate(
            allocator: kCFAllocatorDefault, dataBuffer: bb, dataReady: true,
            makeDataReadyCallback: nil, refcon: nil,
            formatDescription: formatDescription, sampleCount: 1,
            sampleTimingEntryCount: 0, sampleTimingArray: nil,
            sampleSizeEntryCount: 1, sampleSizeArray: &sampleSizeArray,
            sampleBufferOut: &sampleBuffer
        )
        guard sbStatus == noErr, let sb = sampleBuffer else {
            lastError = "sample_buffer_create_failed: OSStatus \(sbStatus)"
            return
        }

        let flags: VTDecodeFrameFlags = [._EnableAsynchronousDecompression]
        var flagsOut = VTDecodeInfoFlags()
        let decodeStatus = VTDecompressionSessionDecodeFrame(
            session, sampleBuffer: sb, flags: flags,
            frameRefcon: nil, infoFlagsOut: &flagsOut
        )
        if decodeStatus != noErr {
            lastError = "decode_frame_failed: OSStatus \(decodeStatus)"
        }
    }

    private func publishDecodedFrame(_ pixelBuffer: CVPixelBuffer) {
        decodeSuccesses += 1
        frameTimestamps.append(Date())
        if frameTimestamps.count > 60 { frameTimestamps.removeFirst() }

        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let context = CIContext()
        guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else { return }
        lastFrame = UIImage(cgImage: cgImage)
    }

    private func estimateFPS() -> Double {
        guard frameTimestamps.count > 1,
              let first = frameTimestamps.first, let last = frameTimestamps.last else { return 0 }
        let elapsed = last.timeIntervalSince(first)
        guard elapsed > 0 else { return 0 }
        return Double(frameTimestamps.count - 1) / elapsed
    }

    /// Reduces width:height to a simple ratio string (e.g. "16:9") via GCD —
    /// read from the actual decoded SPS, not assumed from any stream/start
    /// query parameter (which the Open GoPro API may or may not honor; see
    /// GitHub issues #118/#459 cited in the aspect-ratio audit).
    private static func aspectRatioLabel(width: Int?, height: Int?) -> String? {
        guard let w = width, let h = height, w > 0, h > 0 else { return nil }
        func gcd(_ a: Int, _ b: Int) -> Int { b == 0 ? a : gcd(b, a % b) }
        let d = gcd(w, h)
        return "\(w/d):\(h/d)"
    }
}

/// Writes the GoProStreamProbe diagnostic dict to Documents/gopro_stream_diag.json,
/// pulled by the regression script via the same devicectl appDataContainer copy
/// used for gopro_diag.json (lib.copy_app_container_file) — no console log
/// parsing required.
enum GoProStreamDiagWriter {
    static let fileName = "gopro_stream_diag.json"

    static func write(_ diag: [String: Any]) {
        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
              JSONSerialization.isValidJSONObject(diag),
              let data = try? JSONSerialization.data(withJSONObject: diag, options: [.prettyPrinted]) else { return }
        let url = docs.appendingPathComponent(fileName)
        try? data.write(to: url, options: .atomic)
        print("[GOPRO-STREAM-POC] wrote \(fileName): \(diag)")
    }
}

// MARK: — GoPro Preview Aspect Probe (distinct from gopro-camera-state-probe)
//
// gopro-camera-state-probe = HTTP camera/state read only, NO preview started.
// gopro-preview-aspect-probe = ACTUALLY starts the live preview stream and
// measures its real decoded width/height/aspect/codec/fps — the camera/state
// settings (VideoAspectRatio etc.) describe the ARCHIVAL recording profile,
// NOT necessarily the separate, independent preview stream's actual geometry
// (see docs/GOPRO_LIVE_PREVIEW_POC_PLAN.md and the aspect-ratio audit —
// Open GoPro GitHub issues #118/#459 document that preview resolution query
// parameters are unreliable across firmware/models).
enum GoProPreviewAspectDiagWriter {
    static let fileName = "gopro_preview_aspect_diag.json"

    /// Pulls just the aspect-relevant subset out of a full GoProStreamProbe
    /// diag dict into its own artifact, per the explicit separate-file
    /// requirement (Block: GoPro Preview Aspect Probe).
    static func write(from fullDiag: [String: Any]) {
        let subset: [String: Any] = [
            "timestamp": fullDiag["timestamp"] ?? NSNull(),
            "previewWidth": fullDiag["previewWidth"] ?? NSNull(),
            "previewHeight": fullDiag["previewHeight"] ?? NSNull(),
            "previewAspectRatio": fullDiag["previewAspectRatio"] ?? NSNull(),
            "previewCodec": fullDiag["selectedCodec"] ?? NSNull(),
            "previewFPS": fullDiag["fps"] ?? NSNull(),
            "decodedFrameCount": fullDiag["decodeSuccesses"] ?? NSNull(),
            "decodeAttempts": fullDiag["decodeAttempts"] ?? NSNull(),
            "streamStartHTTPStatus": fullDiag["streamStartHTTPStatus"] ?? NSNull(),
            "errorReason": fullDiag["errorReason"] ?? NSNull(),
        ]
        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
              JSONSerialization.isValidJSONObject(subset),
              let data = try? JSONSerialization.data(withJSONObject: subset, options: [.prettyPrinted]) else { return }
        let url = docs.appendingPathComponent(fileName)
        try? data.write(to: url, options: .atomic)
        print("[GOPRO-PREVIEW-ASPECT] wrote \(fileName): \(subset)")
    }
}

// MARK: — GoPro Preview + Recording Combined Cycle Proof (Block 3, debug-only)
//
// Preview (stream/start, validated above) and recording (shutter/start, the
// existing record-then-download model from docs/AN3B_PR4B_GOPRO_CAPTURE_POC_PLAN.md)
// are two independent GoPro Open GoPro API calls — this had never been
// validated together. Runs both concurrently for the same window and proves,
// via media/list diffing (not console logs), that a new file actually lands
// on the GoPro's SD card while the preview keeps decoding frames.
private struct GoProMediaListResponse: Decodable {
    struct Directory: Decodable {
        struct MediaFile: Decodable { let n: String }
        let d: String
        let fs: [MediaFile]
    }
    let media: [Directory]
}

enum GoProRecordingCycleProbe {

    /// Starts GoPro recording (shutter/start) and the live preview
    /// (GoProStreamProbe) concurrently for `previewDurationSeconds`, then
    /// stops recording (shutter/stop) and diffs media/list before vs after
    /// to detect a genuinely new file on the GoPro's SD card.
    static func run(previewDurationSeconds: TimeInterval = 15) async -> [String: Any] {
        var diag: [String: Any] = ["timestamp": ISO8601DateFormatter().string(from: Date())]
        let gp = GoProConnectionManager.shared
        let transport = GoProHTTPClientTransport()

        let before = await fetchMediaFileNames(transport: transport)
        diag["mediaCountBefore"] = before.count
        print("[GOPRO-RECORDING-POC] media before: \(before.count) file(s)")

        do {
            try await gp.startRecording()
            diag["shutterStartOK"] = true
            print("[GOPRO-RECORDING-POC] shutter/start OK")
        } catch {
            diag["shutterStartOK"] = false
            diag["shutterStartError"] = "\(error)"
            print("[GOPRO-RECORDING-POC] shutter/start FAILED: \(error)")
        }
        diag["recordingStateAfterStart"] = "\(await gp.recordingState)"

        // Preview runs for the SAME window recording is active — this IS the
        // "combined" proof. GoProStreamProbe.run() internally sleeps for
        // previewDurationSeconds, so awaiting it doubles as the record window.
        let previewDiag = await GoProStreamProbe.shared.run(durationSeconds: previewDurationSeconds)
        GoProStreamDiagWriter.write(previewDiag)
        diag["previewRanConcurrently"] = true
        diag["previewDecodeAttempts"] = previewDiag["decodeAttempts"] ?? 0
        diag["previewDecodeSuccesses"] = previewDiag["decodeSuccesses"] ?? 0

        do {
            try await gp.stopRecording()
            diag["shutterStopOK"] = true
            print("[GOPRO-RECORDING-POC] shutter/stop OK")
        } catch {
            diag["shutterStopOK"] = false
            diag["shutterStopError"] = "\(error)"
            print("[GOPRO-RECORDING-POC] shutter/stop FAILED: \(error)")
        }
        diag["recordingStateAfterStop"] = "\(await gp.recordingState)"

        try? await Task.sleep(nanoseconds: 3_000_000_000) // let the GoPro finalize the file

        let after = await fetchMediaFileNames(transport: transport)
        diag["mediaCountAfter"] = after.count
        let newFiles = after.subtracting(before)
        diag["newFilesDetected"] = Array(newFiles).sorted()
        diag["newFileCountDelta"] = after.count - before.count
        print("[GOPRO-RECORDING-POC] media after: \(after.count) file(s), new: \(newFiles.sorted())")

        return diag
    }

    private static func fetchMediaFileNames(transport: GoProHTTPClientTransport) async -> Set<String> {
        guard let data = try? await transport.get(path: GoProSpec.mediaListPath, timeout: 10) else { return [] }
        guard let decoded = try? JSONDecoder().decode(GoProMediaListResponse.self, from: data) else { return [] }
        var names: Set<String> = []
        for dir in decoded.media {
            for f in dir.fs { names.insert("\(dir.d)/\(f.n)") }
        }
        return names
    }
}

enum GoProRecordingDiagWriter {
    static let fileName = "gopro_recording_diag.json"

    static func write(_ diag: [String: Any]) {
        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
              JSONSerialization.isValidJSONObject(diag),
              let data = try? JSONSerialization.data(withJSONObject: diag, options: [.prettyPrinted]) else { return }
        let url = docs.appendingPathComponent(fileName)
        try? data.write(to: url, options: .atomic)
        print("[GOPRO-RECORDING-POC] wrote \(fileName): \(diag)")
    }
}

// MARK: — GoPro camera/state RAW read (Capture Quality block, step "read it out first")
//
// GoProCameraStatus (GoProConnectionManager.swift) decodes camera/state into
// firmwareVersion/isRecording/batteryLevel/sdCardSpaceRemaining — but every
// physical run so far has shown firmware="unknown", which means that decode
// has likely NEVER matched the real HERO13 response shape (Open GoPro
// camera/state is documented to return nested {"status": {...}, "settings":
// {...}} with NUMERIC setting IDs, not these flat named fields). Rather than
// guess which numeric ID maps to resolution/fps without a confirmed spec,
// this probe captures and surfaces the RAW response text so a human can
// read the actual current preset before any preset-WRITE code is attempted.
enum GoProCameraStateProbe {

    static func run() async -> [String: Any] {
        let transport = GoProHTTPClientTransport()
        var diag: [String: Any] = ["timestamp": ISO8601DateFormatter().string(from: Date())]
        do {
            let data = try await transport.get(path: GoProSpec.cameraStatePath, timeout: 10)
            let text = String(data: data, encoding: .utf8) ?? "(binary \(data.count)B)"
            diag["rawResponseOK"] = true
            diag["rawResponseText"] = text
            diag["rawResponseByteCount"] = data.count
            // Best-effort: if the response IS valid JSON, also surface its
            // top-level keys so we know the actual shape without parsing
            // assumptions (e.g. "status"/"settings" vs flat fields).
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                diag["topLevelKeys"] = json.keys.sorted()
            }
            print("[GOPRO-CAMERA-STATE-POC] camera/state raw: \(text.prefix(2000))")
        } catch {
            diag["rawResponseOK"] = false
            diag["error"] = "\(error)"
            print("[GOPRO-CAMERA-STATE-POC] camera/state FAILED: \(error)")
        }
        return diag
    }
}

enum GoProCameraStateDiagWriter {
    static let fileName = "gopro_camera_state_diag.json"

    static func write(_ diag: [String: Any]) {
        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
              JSONSerialization.isValidJSONObject(diag),
              let data = try? JSONSerialization.data(withJSONObject: diag, options: [.prettyPrinted]) else { return }
        let url = docs.appendingPathComponent(fileName)
        try? data.write(to: url, options: .atomic)
        print("[GOPRO-CAMERA-STATE-POC] wrote \(fileName)")
    }
}
#endif
