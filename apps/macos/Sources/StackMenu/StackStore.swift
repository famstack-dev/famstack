import AppKit
import Foundation

// ── App state: what the CLI last said, what is running, what failed ───────
//
// Two refresh rates. `status` is cheap and drives the icon, so it runs every
// minute. The rest (doctor, errors, host, backup, config) take two to three
// seconds each, so they run when the panel opens, at most once a minute, and
// every ten minutes in the background so the icon also reflects the doctor.
//
// The stack is on this Mac or on another one over SSH. Both are remembered and
// a switch picks one, so going back and forth needs no retyping.

@MainActor
final class StackStore: ObservableObject {
    enum Mode: String { case local, remote }

    @Published private(set) var mode: Mode
    @Published private(set) var checkout: URL?
    @Published private(set) var remoteHost: String
    @Published private(set) var remoteCheckout: String
    @Published private(set) var status: StackStatus?
    @Published private(set) var loadError: String?
    @Published private(set) var refreshing = false

    @Published private(set) var doctor: DoctorReport?
    @Published private(set) var errors: ErrorsReport?
    @Published private(set) var host: HostReport?
    @Published private(set) var backup: BackupReport?
    @Published private(set) var config: ConfigReport?
    @Published private(set) var checking = false
    @Published private(set) var checkedAt: Date?

    /// Stacklet id (or "" for the whole stack) to the verb running on it.
    @Published private(set) var busy: [String: String] = [:]
    @Published var lastFailure: ActionFailure?

    struct ActionFailure: Identifiable {
        let id = UUID()
        let command: [String]
        let message: String
    }

    private static let checkoutKey = "checkout"
    private static let modeKey = "mode"
    private static let remoteHostKey = "remoteHost"
    private static let remoteCheckoutKey = "remoteCheckout"
    private static let defaultRemoteCheckout = "~/famstack"
    private static let statusInterval: TimeInterval = 60
    private static let detailInterval: TimeInterval = 600
    private var timers: [Timer] = []
    /// Bumped on every switch of machine, so an answer that arrives after
    /// the switch is dropped instead of shown for the wrong machine.
    private var generation = 0

    init() {
        let defaults = UserDefaults.standard
        mode = Mode(rawValue: defaults.string(forKey: Self.modeKey) ?? "") ?? .local
        checkout = Self.resolveCheckout()
        remoteHost = defaults.string(forKey: Self.remoteHostKey) ?? ""
        remoteCheckout = defaults.string(forKey: Self.remoteCheckoutKey) ?? Self.defaultRemoteCheckout
        timers = [
            Timer.scheduledTimer(withTimeInterval: Self.statusInterval, repeats: true) { [weak self] _ in
                Task { @MainActor in await self?.refresh() }
            },
            Timer.scheduledTimer(withTimeInterval: Self.detailInterval, repeats: true) { [weak self] _ in
                Task { @MainActor in await self?.check() }
            },
        ]
        Task {
            await refresh()
            await check()
        }
    }

    /// The machine the panel shows, or nil until one is set up.
    var connection: Connection? {
        switch mode {
        case .local: checkout.map { .local(checkout: $0) }
        case .remote: remoteHost.isEmpty ? nil : .remote(host: remoteHost, checkout: remoteCheckout)
        }
    }

    private var cli: StackCLI? { connection.map(StackCLI.init) }

    // ── Reading ──────────────────────────────────────────────────────────

    func refresh() async {
        guard let cli, !refreshing else { return }
        let started = generation
        refreshing = true
        defer { refreshing = false }
        do {
            let fresh = try await cli.status()
            guard started == generation else { return }
            status = fresh
            loadError = nil
        } catch {
            guard started == generation else { return }
            loadError = error.localizedDescription
        }
    }

    /// The slower reports, all at once. One failing leaves the others.
    func check() async {
        guard let cli, !checking else { return }
        let started = generation
        checking = true
        defer { checking = false }
        let wantsBackup = status?.isInstalled("backup") ?? false
        async let doctor = try? cli.doctor()
        async let errors = try? cli.errors()
        async let host = try? cli.host()
        async let backup = wantsBackup ? try? cli.backup() : nil
        async let config = try? cli.config()
        let reports = await (doctor, errors, host, backup, config)
        guard started == generation else { return }
        (self.doctor, self.errors, self.host, self.backup, self.config) = reports
        checkedAt = Date()
    }

    /// When the panel opens: status always, the rest unless just done.
    func panelOpened() async {
        await refresh()
        if checkedAt.map({ Date().timeIntervalSince($0) > 60 }) ?? true {
            await check()
        }
    }

    // ── Derived ──────────────────────────────────────────────────────────

    var hostFigures: HostFigures? { host?.figures ?? status?.host }

    var attention: [Attention] {
        Attention.collect(status: status, doctor: doctor, backup: backup, disk: hostFigures)
    }

    func memory(of stacklet: String) -> Int? {
        host?.stacklets.first { $0.id == stacklet }?.memoryBytes
    }

    /// What the menu bar icon shows, worst state first. Only errors turn it
    /// into a warning: a warning that never clears (no backup set up) would
    /// teach the admin to stop looking at the icon.
    var summary: Summary {
        guard connection != nil else { return .unconfigured }
        guard let status, loadError == nil else { return .unreachable }
        if !busy.isEmpty { return .busy }
        if status.installed.contains(where: { $0.health.needsAttention }) { return .attention }
        if attention.contains(where: { $0.level == .error }) { return .attention }
        return .healthy
    }

    enum Summary {
        case healthy, attention, busy, unreachable, unconfigured

        var logo: Logo.State {
            switch self {
            case .healthy: .normal
            case .attention: .attention
            case .busy: .busy
            case .unreachable, .unconfigured: .unreachable
            }
        }
    }

    // ── Acting ───────────────────────────────────────────────────────────

    /// Runs a lifecycle verb in the background, then re-reads everything.
    /// One verb per stacklet at a time; the CLI serialises the rest.
    func perform(_ verb: String, _ stacklet: String? = nil) async {
        guard let cli else { return }
        let key = stacklet ?? ""
        guard busy[key] == nil else { return }
        let command = [verb] + (stacklet.map { [$0] } ?? [])
        busy[key] = verb
        let result = await cli.run(command)
        busy[key] = nil
        if !result.succeeded {
            lastFailure = ActionFailure(command: command, message: result.tail())
        }
        await refresh()
        await check()
    }

    /// A doctor fix: `stack up|restart|down <id>` runs in the background like
    /// the row actions; anything else opens in Terminal, where it can be read.
    func apply(fix: String) {
        let words = fix.split(separator: " ").map(String.init)
        if words.count == 3, words[0] == "stack", ["up", "restart", "down"].contains(words[1]) {
            Task { await perform(words[1], words[2]) }
        } else {
            cli?.openInTerminal(fix: fix)
        }
    }

    func openInTerminal(_ arguments: [String]) {
        cli?.openInTerminal(arguments)
    }

    func edit(_ path: String) {
        cli?.edit(path)
    }

    /// The address the CLI names for the stacklet. A checkout older than
    /// that field names none; on this Mac, localhost then reaches every UI,
    /// because ports bind here in both port and domain mode.
    func openInBrowser(_ stacklet: Stacklet) {
        var address = stacklet.url
        if address == nil, mode == .local, let port = stacklet.port { address = "http://localhost:\(port)" }
        guard let address, let url = URL(string: address) else { return }
        NSWorkspace.shared.open(url)
    }

    func canOpen(_ stacklet: Stacklet) -> Bool {
        stacklet.online && (stacklet.url != nil || (mode == .local && stacklet.port != nil))
    }

    // ── Which machine and checkout ───────────────────────────────────────

    func setMode(_ new: Mode) {
        guard new != mode else { return }
        mode = new
        UserDefaults.standard.set(new.rawValue, forKey: Self.modeKey)
        if new == .remote && remoteHost.isEmpty {
            editRemote()
        }
        reconnect()
    }

    /// Asks for the SSH host and the checkout path on it.
    func editRemote() {
        let host = NSTextField(string: remoteHost)
        host.placeholderString = "ssh alias or user@host"
        let path = NSTextField(string: remoteCheckout)
        path.placeholderString = Self.defaultRemoteCheckout
        for field in [host, path] { field.frame.size.width = 280 }
        let fields = NSStackView(views: [label("SSH host"), host, label("Checkout on that machine"), path])
        fields.orientation = .vertical
        fields.alignment = .leading
        fields.spacing = 6
        fields.frame = NSRect(x: 0, y: 0, width: 280, height: 110)

        let alert = NSAlert()
        alert.messageText = "Connect to a stack on another Mac"
        alert.informativeText = "The app runs ./stack there over SSH with your key. The server needs Remote Login turned on."
        alert.accessoryView = fields
        alert.addButton(withTitle: "Connect")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        alert.window.initialFirstResponder = host
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        remoteHost = host.stringValue.trimmingCharacters(in: .whitespaces)
        let typed = path.stringValue.trimmingCharacters(in: .whitespaces)
        remoteCheckout = typed.isEmpty ? Self.defaultRemoteCheckout : typed
        UserDefaults.standard.set(remoteHost, forKey: Self.remoteHostKey)
        UserDefaults.standard.set(remoteCheckout, forKey: Self.remoteCheckoutKey)
        if mode == .remote { reconnect() }
    }

    private func label(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = .systemFont(ofSize: NSFont.smallSystemFontSize)
        label.textColor = .secondaryLabelColor
        return label
    }

    /// Forgets everything read from the previous machine and reads again.
    private func reconnect() {
        generation += 1
        status = nil; loadError = nil; lastFailure = nil
        doctor = nil; errors = nil; host = nil; backup = nil; config = nil; checkedAt = nil
        Task {
            await refresh()
            await check()
        }
    }

    func chooseCheckout() {
        let panel = NSOpenPanel()
        panel.message = "Choose the folder you run ./stack from"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        guard StackCLI.isCheckout(url) else {
            loadError = "\(url.path) has no ./stack and stacklets/ folder"
            return
        }
        UserDefaults.standard.set(url.path, forKey: Self.checkoutKey)
        checkout = url
        if mode == .local { reconnect() }
    }

    /// The saved choice, else `~/famstack`, where the README's clone lands
    /// when run from the home folder.
    private static func resolveCheckout() -> URL? {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates = [UserDefaults.standard.string(forKey: checkoutKey).map { URL(fileURLWithPath: $0) },
                          home.appendingPathComponent("famstack")]
        return candidates.compactMap { $0 }.first(where: StackCLI.isCheckout)
    }
}
