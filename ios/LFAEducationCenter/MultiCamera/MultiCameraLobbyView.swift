import SwiftUI

#if DEBUG
struct MultiCameraLobbyView: View {

    @StateObject private var vm: MultiCameraSessionViewModel
    @State private var joinUuid = ""
    @Environment(\.presentationMode) private var presentationMode

    init(authManager: AuthManager) {
        _vm = StateObject(wrappedValue: MultiCameraSessionViewModel(authManager: authManager))
    }

    var body: some View {
        NavigationView {
            List {
                switch vm.state {
                case .idle:
                    idleSection
                case .creating:
                    Section { Label("Session létrehozása…", systemImage: "hourglass") }
                case .joining:
                    Section { Label("Csatlakozás…", systemImage: "hourglass") }
                case .inLobby(let session):
                    lobbySection(session)
                case .error(let msg):
                    errorSection(msg)
                }
            }
            .navigationTitle("Session Lab")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { presentationMode.wrappedValue.dismiss() } label: {
                        Image(systemName: "xmark").font(.system(size: 14, weight: .semibold))
                    }
                }
            }
        }
        .navigationViewStyle(.stack)
        .onDisappear { vm.reset() }
    }

    // MARK: — Idle

    private var idleSection: some View {
        Group {
            Section("Új session") {
                Button("Create Session") { vm.createSession() }
                    .font(.body.weight(.semibold))
            }
            Section("Csatlakozás meglévőhöz") {
                TextField("Session UUID", text: $joinUuid)
                    .font(.system(.body, design: .monospaced))
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                Button("Join Session") { vm.joinSession(uuid: joinUuid.trimmingCharacters(in: .whitespaces)) }
                    .disabled(joinUuid.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
    }

    // MARK: — Lobby

    private func lobbySection(_ session: MultiCameraSessionDTO) -> some View {
        Group {
            Section("Session") {
                LabeledRow("UUID", session.sessionUuid)
                LabeledRow("Status", session.status.rawValue)
                LabeledRow("Revision", "\(session.revision)")
            }
            Section("Participants (\(session.participants.count)/\(session.maxParticipants))") {
                ForEach(session.participants, id: \.id) { p in
                    HStack {
                        Text(p.role == .instructor ? "👨‍🏫" : "🏃")
                        Text("\(p.role.rawValue) (user \(p.userId))")
                            .font(.caption)
                        Spacer()
                        if p.leftAt != nil {
                            Text("left").font(.caption2).foregroundColor(.red)
                        }
                    }
                }
            }
            Section("Devices (\(session.devices.count)/\(session.maxDevices))") {
                ForEach(session.devices, id: \.id) { d in
                    HStack {
                        Text(deviceIcon(d.deviceRole))
                        VStack(alignment: .leading, spacing: 2) {
                            Text(d.deviceRole.rawValue).font(.caption.weight(.semibold))
                            Text("status: \(d.status.rawValue)").font(.caption2).foregroundColor(.secondary)
                        }
                        Spacer()
                        if d.managedByDeviceId != nil {
                            Text("managed").font(.caption2).foregroundColor(.orange)
                        }
                    }
                }
            }
            Section("Actions") {
                if vm.isInstructor && session.status == .lobby {
                    Button("Mark Devices Ready") { vm.transitionToDevicesReady() }
                        .font(.body.weight(.semibold))
                        .foregroundColor(.green)
                } else if !vm.isInstructor {
                    Text("Várakozás az instructor-ra…")
                        .font(.caption).foregroundColor(.secondary)
                }
                Button("Cancel Session") { vm.cancelSession() }
                    .foregroundColor(.red)
            }
            if let sdId = vm.sessionDeviceId {
                Section("Debug") {
                    LabeledRow("Session Device ID", "\(sdId)")
                    LabeledRow("Heartbeat", "Active")
                }
            }
        }
    }

    // MARK: — Error

    private func errorSection(_ msg: String) -> some View {
        Section {
            HStack {
                Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.red)
                Text(msg).font(.body)
            }
            Button("Vissza") { vm.reset() }
        }
    }

    // MARK: — Helpers

    private func deviceIcon(_ role: MCDeviceRole) -> String {
        switch role {
        case .instructorPrimary: return "📱"
        case .playerPrimary: return "📱"
        case .playerSecondary: return "📱"
        case .auxiliaryCamera: return "📷"
        }
    }
}

private struct LabeledRow: View {
    let label: String
    let value: String
    init(_ label: String, _ value: String) { self.label = label; self.value = value }
    var body: some View {
        HStack {
            Text(label).font(.caption).foregroundColor(.secondary)
            Spacer()
            Text(value).font(.caption.weight(.semibold))
        }
    }
}
#endif
