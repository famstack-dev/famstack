import AppKit
import Foundation

// ── App state: what the CLI last said, what is running, what failed ───────
//
// Two refresh rates. `status` is cheap and drives the icon, so it runs every
// minute. The rest (doctor, errors, host, backup, config) take two to three seconds
// each, so they run when the panel opens, at most once a minute, and every
// ten minutes in the background so the icon also reflects the doctor.

@MainActor
final class StackStore: ObservableObject {
    @Published private(set) var checkout: URL?
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
    private static let statusInterval: TimeInterval = 60
    private static let detailInterval: TimeInterval = 600
    private var timers: [Timer] = []

    init() {
        checkout = Self.resolveCheckout()
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

    private var cli: StackCLI? { checkout.map(StackCLI.init) }

    // ── Reading ──────────────────────────────────────────────────────────

    func refresh() async {
        guard let cli, !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        do {
            status = try await cli.status()
            loadError = nil
        } catch {
            loadError = error.localizedDescription
        }
    }

    /// The slower reports, all at once. One failing leaves the others.
    func check() async {
        guard let cli, !checking else { return }
        checking = true
        defer { checking = false }
        let wantsBackup = status?.isInstalled("backup") ?? false
        async let doctor = try? cli.doctor()
        async let errors = try? cli.errors()
        async let host = try? cli.host()
        async let backup = wantsBackup ? try? cli.backup() : nil
        async let config = try? cli.config()
        (self.doctor, self.errors, self.host, self.backup, self.config) =
            await (doctor, errors, host, backup, config)
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
        guard checkout != nil else { return .unconfigured }
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

    func openInBrowser(_ stacklet: Stacklet) {
        // Ports bind on this Mac in both port and domain mode (127.0.0.1 in
        // the latter), so localhost reaches every UI from the server itself.
        guard let port = stacklet.port, let url = URL(string: "http://localhost:\(port)") else { return }
        NSWorkspace.shared.open(url)
    }

    // ── Which checkout ───────────────────────────────────────────────────

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
        status = nil
        doctor = nil; errors = nil; host = nil; backup = nil; config = nil; checkedAt = nil
        Task {
            await refresh()
            await check()
        }
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
