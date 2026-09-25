import AppKit
import Foundation

// ── App state: the last status, what is running, what failed ──────────────

@MainActor
final class StackStore: ObservableObject {
    @Published private(set) var checkout: URL?
    @Published private(set) var status: StackStatus?
    @Published private(set) var loadError: String?
    @Published private(set) var refreshing = false
    /// Stacklet id (or "" for the whole stack) to the verb running on it.
    @Published private(set) var busy: [String: String] = [:]
    @Published var lastFailure: ActionFailure?

    struct ActionFailure: Identifiable {
        let id = UUID()
        let command: [String]
        let message: String
    }

    private static let checkoutKey = "checkout"
    private static let pollInterval: TimeInterval = 60
    private var timer: Timer?

    init() {
        checkout = Self.resolveCheckout()
        timer = Timer.scheduledTimer(withTimeInterval: Self.pollInterval, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.refresh() }
        }
        Task { await refresh() }
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

    /// What the menu bar icon shows, worst state first.
    var summary: Summary {
        guard checkout != nil else { return .unconfigured }
        guard let status, loadError == nil else { return .unreachable }
        if !busy.isEmpty { return .busy }
        if status.installed.contains(where: { $0.health.needsAttention }) { return .attention }
        return .healthy
    }

    enum Summary {
        case healthy, attention, busy, unreachable, unconfigured

        var symbol: String {
            switch self {
            case .healthy: "house.fill"
            case .attention: "exclamationmark.triangle.fill"
            case .busy: "arrow.triangle.2.circlepath"
            case .unreachable, .unconfigured: "house"
            }
        }
    }

    // ── Acting ───────────────────────────────────────────────────────────

    /// Runs a lifecycle verb in the background, then re-reads the status.
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
        Task { await refresh() }
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
