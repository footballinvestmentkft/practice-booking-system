import Foundation

// MC1 Block-1: Network routing diagnostics for iPhone GoPro-WiFi + cellular coexistence.
//
// Probes GoPro local HTTP and backend internet reachability independently,
// and logs structured [NET-DIAG] lines so the regression runner has corroborating
// evidence in the console log. PASS/FAIL is always decided from backend ground
// truth (device_status == ready), not from these logs.
//
// Two sessions are used intentionally:
//   backendProbeSession — waitsForConnectivity=false so we get the raw pre-Assist
//                         state (reveals whether the WiFi→cellular fallback is needed)
//   URLSession.shared   — used for GoPro HTTP (local network, no internet needed)

#if DEBUG
@MainActor
enum BackendNetworkDiagnostics {

    private static let backendProbeSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.allowsCellularAccess = true
        config.waitsForConnectivity = false   // raw state — no waiting
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 12
        return URLSession(configuration: config)
    }()

    /// Run both probes and log results with [NET-DIAG][label] prefix.
    /// Call before and after GoPro WiFi join to observe routing change.
    static func probe(label: String) async {
        let tag = "[NET-DIAG][\(label)]"
        print("\(tag) === start === gopro_state=\(GoProConnectionManager.shared.state)")

        // GoPro local HTTP (10.5.5.9:8080)
        let gpStart = Date()
        let gpOK = await probeGoProHTTP()
        let gpMs = Int(Date().timeIntervalSince(gpStart) * 1000)
        print("\(tag) gopro_http=\(gpOK ? "OK" : "FAIL") latency_ms=\(gpMs)")

        // Backend internet probe (unauthenticated /api/v1/system/time)
        let beStart = Date()
        let (beOK, beErr) = await probeBackend()
        let beMs = Int(Date().timeIntervalSince(beStart) * 1000)
        if beOK {
            print("\(tag) backend=OK latency_ms=\(beMs)")
        } else {
            print("\(tag) backend=FAIL latency_ms=\(beMs) error=\(beErr ?? "unknown")")
        }

        print("\(tag) === end ===")
    }

    // Backend probe uses a raw session (waitsForConnectivity=false) to capture
    // the true routing state before iOS WiFi Assist has had time to switch.
    // The production APIClient.backendSession uses waitsForConnectivity=true
    // which *fixes* the problem — this probe shows the *problem* for diagnostics.
    private static func probeBackend() async -> (Bool, String?) {
        let path = APIConfig.baseURL + "/api/v1/system/time"
        guard let url = URL(string: path) else { return (false, "invalidURL") }
        var req = URLRequest(url: url)
        req.timeoutInterval = 10
        do {
            let (_, resp) = try await backendProbeSession.data(for: req)
            let status = (resp as? HTTPURLResponse)?.statusCode ?? -1
            return ((200...299).contains(status), nil)
        } catch {
            let code = (error as? URLError)?.code
            let errStr = "URLError(\(code?.rawValue ?? -1)) \(error.localizedDescription.prefix(120))"
            return (false, errStr)
        }
    }

    private static func probeGoProHTTP() async -> Bool {
        guard let url = URL(string: GoProSpec.httpBaseURL + GoProSpec.cameraStatePath) else { return false }
        var req = URLRequest(url: url)
        req.timeoutInterval = 5
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            return (resp as? HTTPURLResponse).map { (200...299).contains($0.statusCode) } ?? false
        } catch {
            return false
        }
    }
}
#endif
