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

private struct MPEGTSDemuxer {
    private(set) var videoPID: Int?
    private(set) var pmtPID: Int?
    private var payload = Data()

    /// Feed one or more concatenated 188-byte TS packets. Returns newly
    /// completed Annex-B NAL units (start-code delimited) found in the
    /// video PID's payload since the last call.
    mutating func feed(_ datagram: Data) -> [Data] {
        var offset = 0
        while offset + 188 <= datagram.count {
            let packet = datagram[datagram.startIndex + offset ..< datagram.startIndex + offset + 188]
            offset += 188
            guard packet.first == 0x47 else { continue } // not TS-aligned, drop
            parsePacket(packet)
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
        while i + 4 <= end, i + 4 <= b.count {
            let programNumber = (Int(b[i]) << 8) | Int(b[i + 1])
            let pid = (Int(b[i + 2] & 0x1F) << 8) | Int(b[i + 3])
            if programNumber != 0 { pmtPID = pid } // first non-PAT-itself program
            i += 4
        }
    }

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
        while i + 5 <= end, i + 5 <= b.count {
            let streamType = b[i]
            let pid = (Int(b[i + 1] & 0x1F) << 8) | Int(b[i + 2])
            let esInfoLength = (Int(b[i + 3] & 0x0F) << 8) | Int(b[i + 4])
            if streamType == 0x1B, videoPID == nil { // H.264
                videoPID = pid
            }
            i += 5 + esInfoLength
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
    private var formatDescription: CMVideoFormatDescription?
    private var spsData: Data?
    private var ppsData: Data?

    private var packetsReceived = 0
    private var bytesReceived = 0
    private var firstPacketAt: Date?
    private var lastPacketAt: Date?
    private var decodeAttempts = 0
    private var decodeSuccesses = 0
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
        diag["pmtPIDFound"] = demuxer.pmtPID != nil
        diag["videoPIDFound"] = demuxer.videoPID != nil
        diag["spsSeen"] = spsData != nil
        diag["ppsSeen"] = ppsData != nil
        diag["decodeAttempts"] = decodeAttempts
        diag["decodeSuccesses"] = decodeSuccesses
        diag["fps"] = estimateFPS()
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

    // MARK: — H.264 NAL handling + VideoToolbox decode

    private func handleNAL(_ nal: Data) {
        guard let firstByte = nal.first else { return }
        let nalType = firstByte & 0x1F
        switch nalType {
        case 7: // SPS
            spsData = nal
            tryBuildFormatDescription()
        case 8: // PPS
            ppsData = nal
            tryBuildFormatDescription()
        case 5, 1: // IDR / non-IDR slice
            decodeFrame(nal, isKeyframe: nalType == 5)
        default:
            break
        }
    }

    private func tryBuildFormatDescription() {
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
            print("[GOPRO-STREAM-POC] format description created from SPS/PPS")
            createDecompressionSession()
        } else {
            lastError = "format_description_failed: OSStatus \(result)"
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

    private func decodeFrame(_ nal: Data, isKeyframe: Bool) {
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
#endif
