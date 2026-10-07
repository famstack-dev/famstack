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
    /// When `status` was last read successfully.
    @Published private(set) var statusAt: Date?
    /// Why the last status read failed; the panel then marks what it shows
    /// as the last known state.
    @Published private(set) var loadProblem: Problem?
    @Published private(set) var refreshing = false

    @Published private(set) var doctor: DoctorReport?
    @Published private(set) var errors: ErrorsReport?
    @Published private(set) var host: HostReport?
    @Published private(set) var backup: BackupReport?
    @Published private(set) var config: ConfigReport?
    /// Reports that could not be read, by name, so a missing section is
    /// explained instead of silently absent.
    @Published private(set) var reportProblems: [(report: String, problem: Problem)] = []
    @Published private(set) var checking = false
    @Published private(set) var checkedAt: Date?

    /// The one lifecycle action running. Actions run one at a time: two
    /// `up`s racing each other on one Docker is not something to invite.
    @Published private(set) var running: RunningAction?
    @Published var banner: Banner?

    struct RunningAction: Equatable {
        let id = UUID()
        /// A stacklet id, or "" for an action on the whole stack.
        let key: String
        let verb: String
        let startedAt: Date
    }

    /// A message at the top of the panel: an action's outcome, or a choice
    /// that could not be applied.
    struct Banner: Identifiable {
        enum Kind { case success, info, failure }

        let id = UUID()
        let kind: Kind
        let title: String
        var hint: String?
        var detail: String?
        /// Stack arguments to run again in Terminal, where the output stays.
        var retry: [String]?
        var terminal: Problem.TerminalCommand?

        init(_ kind: Kind, _ title: String, hint: String? = nil) {
            self.kind = kind
            self.title = title
            self.hint = hint
        }

        init(_ kind: Kind, _ problem: Problem, title: String? = nil, retry: [String]? = nil) {
            self.kind = kind
            self.title = title ?? problem.title
            hint = problem.hint
            detail = problem.detail
            terminal = problem.terminal
            self.retry = retry
        }
    }

    private static let checkoutKey = "checkout"
    private static let modeKey = "mode"
    private static let remoteHostKey = "remoteHost"
    private static let remoteCheckoutKey = "remoteCheckout"
    private static let defaultRemoteCheckout = "~/famstack"
    private static let statusInterval: TimeInterval = 60
    private static let detailInterval: TimeInterval = 600
    private static let successBannerSeconds: UInt64 = 6

    private var timers: [Timer] = []
    private var wakeObserver: NSObjectProtocol?
    /// Bumped on every switch of machine, so an answer that arrives after
    /// the switch is dropped instead of shown for the wrong machine.
    private var generation = 0
    /// A read asked for while one runs is not dropped: it runs right after,
    /// against whatever machine is selected by then.
    private var refreshQueued = false
    private var checkQueued = false

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
        // After sleep the last answer is hours old. The network needs a
        // moment to come back, a remote machine especially.
        wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(5))
                await self?.refresh()
                await self?.check()
            }
        }
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
        if refreshing { refreshQueued = true; return }
        refreshing = true
        defer { refreshing = false }
        repeat {
            refreshQueued = false
            guard let cli else { return }
            let started = generation
            do {
                let fresh = try await cli.status()
                guard started == generation else { continue }
                status = fresh
                statusAt = Date()
                loadProblem = nil
            } catch {
                guard started == generation else { continue }
                loadProblem = error as? Problem
                    ?? Problem(kind: .command, title: "Could not read the stack's status", detail: "\(error)")
            }
        } while refreshQueued
    }

    /// The slower reports, all at once. One failing leaves the others.
    func check() async {
        if checking { checkQueued = true; return }
        checking = true
        defer { checking = false }
        repeat {
            checkQueued = false
            guard let cli else { return }
            let started = generation
            // Right after a switch of machine there is no status yet, and
            // without it the backup report would be skipped.
            if status == nil { await refresh() }
            let wantsBackup = status?.isInstalled("backup") ?? false
            async let doctor = Self.attempt { try await cli.doctor() }
            async let errors = Self.attempt { try await cli.errors() }
            async let host = Self.attempt { try await cli.host() }
            async let backup = wantsBackup ? Self.attempt { try await cli.backup() } : (nil, nil)
            async let config = Self.attempt { try await cli.config() }
            let (d, e, h, b, c) = await (doctor, errors, host, backup, config)
            guard started == generation else { continue }
            (self.doctor, self.errors, self.host, self.backup, self.config) = (d.0, e.0, h.0, b.0, c.0)
            reportProblems = [("Doctor", d.1), ("Errors", e.1), ("Memory", h.1), ("Backup", b.1), ("Setup", c.1)]
                .compactMap { name, problem in problem.map { (name, $0) } }
            checkedAt = Date()
        } while checkQueued
    }

    private static func attempt<T>(_ read: () async throws -> T) async -> (T?, Problem?) {
        do {
            return (try await read(), nil)
        } catch let problem as Problem {
            return (nil, problem)
        } catch {
            return (nil, Problem(kind: .command, title: "\(error)"))
        }
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

    func name(of stacklet: String) -> String {
        status?.stacklets.first { $0.id == stacklet }?.name ?? stacklet
    }

    /// What the menu bar icon shows, worst state first. Only errors turn it
    /// into a warning: a warning that never clears (no backup set up) would
    /// teach the admin to stop looking at the icon.
    var summary: Summary {
        guard connection != nil else { return .unconfigured }
        guard let status, loadProblem == nil else { return .unreachable }
        if running != nil { return .busy }
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

    /// Runs a lifecycle verb in the background, says how it went, and
    /// re-reads everything.
    func perform(_ verb: String, _ stacklet: String? = nil) async {
        guard let cli, running == nil else { return }
        let key = stacklet ?? ""
        let command = [verb] + (stacklet.map { [$0] } ?? [])
        let started = generation
        let action = RunningAction(key: key, verb: verb, startedAt: Date())
        running = action
        banner = nil

        let result = await cli.run(command, timeout: StackCLI.actionTimeout, overrun: .detach { [weak self] late in
            Task { @MainActor in self?.finish(command, late, generation: started) }
        })
        // Only this action's own indicator is cleared: after a detach or a
        // switch of machine, another action may be the running one by now.
        if running?.id == action.id { running = nil }
        if result.stillRunning {
            // Left running; its callback reports the outcome later. The
            // panel is free for other actions meanwhile.
            show(Banner(.info, cli.diagnose(result, command)))
            return
        }
        finish(command, result, generation: started)
    }

    private func finish(_ command: [String], _ result: StackCLI.Result, generation started: Int) {
        guard started == generation, let cli else { return }
        let target = command.count > 1 ? name(of: command[1]) : nil
        if result.succeeded {
            show(Banner(.success, Self.done(command[0], target)))
        } else {
            let problem = cli.diagnose(result, command)
            let title = problem.kind == .command ? Self.failed(command[0], target) : nil
            show(Banner(.failure, problem, title: title, retry: command))
        }
        Task {
            await refresh()
            await check()
        }
    }

    private static func done(_ verb: String, _ target: String?) -> String {
        guard let target else { return verb == "restart" ? "Restarted what was running old code" : "Done" }
        return switch verb {
        case "up": "\(target) started"
        case "down": "\(target) stopped"
        case "restart": "\(target) restarted"
        default: "Done"
        }
    }

    private static func failed(_ verb: String, _ target: String?) -> String {
        let what = target ?? "the stacklets running old code"
        return switch verb {
        case "up": "Could not start \(what)"
        case "down": "Could not stop \(what)"
        case "restart": "Could not restart \(what)"
        default: "stack \(verb) failed"
        }
    }

    /// Shows a banner; a success clears itself after a few seconds.
    private func show(_ new: Banner) {
        banner = new
        guard new.kind == .success else { return }
        Task {
            try? await Task.sleep(nanoseconds: Self.successBannerSeconds * 1_000_000_000)
            if banner?.id == new.id { banner = nil }
        }
    }

    /// A doctor fix: `stack up|restart|down <id>` runs in the background like
    /// the row actions; anything else opens in Terminal, where it can be read.
    func apply(fix: String) {
        let words = fix.split(separator: " ").map(String.init)
        if words.count == 3, words[0] == "stack", ["up", "restart", "down"].contains(words[1]) {
            Task { await perform(words[1], words[2]) }
        } else if cli?.openInTerminal(fix: fix) == false {
            show(Banner(.failure, "Could not open Terminal"))
        }
    }

    func openInTerminal(_ arguments: [String]) {
        if cli?.openInTerminal(arguments) == false {
            show(Banner(.failure, "Could not open Terminal"))
        }
    }

    func openTerminal(_ command: Problem.TerminalCommand) {
        if !StackCLI.openShell(command.line) {
            show(Banner(.failure, "Could not open Terminal"))
        }
    }

    func edit(_ path: String) {
        if cli?.edit(path) == false {
            show(Banner(.failure, "Could not open \((path as NSString).lastPathComponent)",
                        hint: "Open it yourself: \(path)"))
        }
    }

    /// The address the CLI names for the stacklet. A checkout older than
    /// that field names none; on this Mac, localhost then reaches every UI,
    /// because ports bind here in both port and domain mode.
    func openInBrowser(_ stacklet: Stacklet) {
        var address = stacklet.url
        if address == nil, mode == .local, let port = stacklet.port { address = "http://localhost:\(port)" }
        guard let address, let url = URL(string: address), NSWorkspace.shared.open(url) else {
            show(Banner(.failure, "Could not open \(stacklet.name) in the browser"))
            return
        }
    }

    func canOpen(_ stacklet: Stacklet) -> Bool {
        stacklet.online && (stacklet.url != nil || (mode == .local && stacklet.port != nil))
    }

    // ── Which machine and checkout ───────────────────────────────────────

    func setMode(_ new: Mode) {
        guard new != mode else { return }
        // Switching to a remote that was never set up asks for it first,
        // and a cancel leaves the switch where it was.
        if new == .remote && remoteHost.isEmpty && !editRemote(switching: true) { return }
        mode = new
        UserDefaults.standard.set(new.rawValue, forKey: Self.modeKey)
        reconnect()
    }

    /// Asks for the SSH host and the checkout path on it. Returns whether
    /// something was saved.
    @discardableResult
    func editRemote(switching: Bool = false) -> Bool {
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
        alert.informativeText = "The app runs ./stack there over SSH with your key, without a password. "
            + "That Mac needs Remote Login turned on."
        alert.accessoryView = fields
        alert.addButton(withTitle: "Connect")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        alert.window.initialFirstResponder = host
        guard alert.runModal() == .alertFirstButtonReturn else { return false }

        let newHost = host.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let typedPath = path.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        // Whatever is typed becomes an ssh argument: a leading dash would be
        // read as an option, and whitespace is never part of a host.
        guard !newHost.isEmpty, !newHost.hasPrefix("-"), !newHost.contains(where: \.isWhitespace) else {
            show(Banner(.failure, "“\(newHost)” is not an SSH host",
                        hint: "Use an alias from ~/.ssh/config or user@host."))
            return false
        }
        guard !typedPath.contains(where: \.isNewline) else {
            show(Banner(.failure, "The checkout path cannot span lines"))
            return false
        }
        remoteHost = newHost
        remoteCheckout = typedPath.isEmpty ? Self.defaultRemoteCheckout : typedPath
        UserDefaults.standard.set(remoteHost, forKey: Self.remoteHostKey)
        UserDefaults.standard.set(remoteCheckout, forKey: Self.remoteCheckoutKey)
        if mode == .remote && !switching { reconnect() }
        return true
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
        status = nil; statusAt = nil; loadProblem = nil; banner = nil; running = nil
        doctor = nil; errors = nil; host = nil; backup = nil; config = nil
        reportProblems = []; checkedAt = nil
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
            show(Banner(.failure, "\(url.lastPathComponent) is not a famstack checkout",
                        hint: "Choose the folder that holds ./stack and stacklets/."))
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
