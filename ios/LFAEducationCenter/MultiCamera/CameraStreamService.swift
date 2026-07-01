import Foundation
import MultipeerConnectivity

@MainActor
final class CameraStreamService: NSObject, ObservableObject {

    enum Role { case instructor, player }
    enum PeerState: Equatable {
        case disconnected
        case connecting
        case connected(peerName: String)
    }

    static let serviceType = "lfa-mc1-cam"

    @Published private(set) var peerState: PeerState = .disconnected
    @Published private(set) var lastReceivedFrame: Data?
    @Published private(set) var receivedFPS: Double = 0
    @Published private(set) var lastFrameAge: TimeInterval = 0
    /// Total MultiPeer frames received for the lifetime of this instance — unlike
    /// frameReceiveCount (reset every 1s FPS window), this is a monotonic counter used
    /// as the "source frame" diagnostic for the player panel's pose overlay processor
    /// (2026-07-01 flow audit — distinguishes "MPC never delivered a frame" from
    /// "MPC delivered frames but they failed to decode into a UIImage").
    @Published private(set) var totalFramesReceived: Int = 0

    private let myPeerID: MCPeerID
    private var session: MCSession?
    private var advertiser: MCNearbyServiceAdvertiser?
    private var browser: MCNearbyServiceBrowser?
    private let role: Role
    private let sessionUuid: String

    private var connectedPeer: MCPeerID?
    private var frameReceiveCount = 0
    private var fpsWindowStart = Date()
    private var lastFrameTime = Date.distantPast
    private var fpsTimer: Timer?

    init(role: Role, sessionUuid: String, deviceName: String? = nil) {
        let name = deviceName ?? (role == .instructor ? "iPhone-Instructor" : "iPad-Player")
        self.myPeerID = MCPeerID(displayName: name)
        self.role = role
        self.sessionUuid = sessionUuid
        super.init()
    }

    func start() {
        stop()
        let session = MCSession(peer: myPeerID, securityIdentity: nil, encryptionPreference: .none)
        session.delegate = self
        self.session = session

        let discovery: [String: String] = ["session": String(sessionUuid.prefix(8)), "role": role == .instructor ? "i" : "p"]

        switch role {
        case .player:
            let adv = MCNearbyServiceAdvertiser(peer: myPeerID, discoveryInfo: discovery, serviceType: Self.serviceType)
            adv.delegate = self
            adv.startAdvertisingPeer()
            self.advertiser = adv
            print("[StreamService] player advertising: \(myPeerID.displayName)")

        case .instructor:
            let brw = MCNearbyServiceBrowser(peer: myPeerID, serviceType: Self.serviceType)
            brw.delegate = self
            brw.startBrowsingForPeers()
            self.browser = brw
            print("[StreamService] instructor browsing for players")
        }

        fpsTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.updateFPS() }
        }
    }

    func stop() {
        advertiser?.stopAdvertisingPeer()
        advertiser = nil
        browser?.stopBrowsingForPeers()
        browser = nil
        session?.disconnect()
        session = nil
        connectedPeer = nil
        peerState = .disconnected
        fpsTimer?.invalidate()
        fpsTimer = nil
        frameReceiveCount = 0
        receivedFPS = 0
    }

    func sendFrame(_ jpegData: Data) {
        guard let session, let peer = connectedPeer else { return }
        try? session.send(jpegData, toPeers: [peer], with: .unreliable)
    }

    private func updateFPS() {
        let now = Date()
        let elapsed = now.timeIntervalSince(fpsWindowStart)
        if elapsed >= 1.0 {
            receivedFPS = Double(frameReceiveCount) / elapsed
            frameReceiveCount = 0
            fpsWindowStart = now
        }
        lastFrameAge = now.timeIntervalSince(lastFrameTime)
    }
}

// MARK: — MCSessionDelegate

extension CameraStreamService: MCSessionDelegate {
    nonisolated func session(_ session: MCSession, peer peerID: MCPeerID, didChange state: MCSessionState) {
        Task { @MainActor in
            switch state {
            case .connected:
                connectedPeer = peerID
                peerState = .connected(peerName: peerID.displayName)
                print("[StreamService] connected to \(peerID.displayName)")
            case .connecting:
                peerState = .connecting
                print("[StreamService] connecting to \(peerID.displayName)")
            case .notConnected:
                if connectedPeer == peerID { connectedPeer = nil }
                peerState = .disconnected
                print("[StreamService] disconnected from \(peerID.displayName)")
                if role == .instructor {
                    browser?.startBrowsingForPeers()
                }
            @unknown default:
                break
            }
        }
    }

    nonisolated func session(_ session: MCSession, didReceive data: Data, fromPeer peerID: MCPeerID) {
        Task { @MainActor in
            lastReceivedFrame = data
            lastFrameTime = Date()
            frameReceiveCount += 1
            totalFramesReceived += 1
        }
    }

    nonisolated func session(_ session: MCSession, didReceive stream: InputStream, withName: String, fromPeer: MCPeerID) {}
    nonisolated func session(_ session: MCSession, didStartReceivingResourceWithName: String, fromPeer: MCPeerID, with: Progress) {}
    nonisolated func session(_ session: MCSession, didFinishReceivingResourceWithName: String, fromPeer: MCPeerID, at: URL?, withError: Error?) {}
}

// MARK: — MCNearbyServiceAdvertiserDelegate

extension CameraStreamService: MCNearbyServiceAdvertiserDelegate {
    nonisolated func advertiser(_ advertiser: MCNearbyServiceAdvertiser, didReceiveInvitationFromPeer peerID: MCPeerID, withContext: Data?, invitationHandler: @escaping (Bool, MCSession?) -> Void) {
        Task { @MainActor in
            print("[StreamService] received invitation from \(peerID.displayName)")
            invitationHandler(true, session)
        }
    }

    nonisolated func advertiser(_ advertiser: MCNearbyServiceAdvertiser, didNotStartAdvertisingPeer error: Error) {
        print("[StreamService] advertising failed: \(error)")
    }
}

// MARK: — MCNearbyServiceBrowserDelegate

extension CameraStreamService: MCNearbyServiceBrowserDelegate {
    nonisolated func browser(_ browser: MCNearbyServiceBrowser, foundPeer peerID: MCPeerID, withDiscoveryInfo info: [String: String]?) {
        Task { @MainActor in
            print("[StreamService] found peer: \(peerID.displayName) info=\(info ?? [:])")
            guard let session else { return }
            browser.invitePeer(peerID, to: session, withContext: nil, timeout: 10)
        }
    }

    nonisolated func browser(_ browser: MCNearbyServiceBrowser, lostPeer peerID: MCPeerID) {
        print("[StreamService] lost peer: \(peerID.displayName)")
    }

    nonisolated func browser(_ browser: MCNearbyServiceBrowser, didNotStartBrowsingForPeers error: Error) {
        print("[StreamService] browsing failed: \(error)")
    }
}
