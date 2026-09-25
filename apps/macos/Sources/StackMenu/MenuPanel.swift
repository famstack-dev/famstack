import SwiftUI

// ── The panel under the menu bar icon ─────────────────────────────────────
//
// Two tabs. Overview is ordered by the questions an admin opens the panel
// with: is everything fine, what needs me, what is running, what broke
// recently, how full is the machine. Setup holds what changes rarely: the
// configuration, the stacklets not installed, which checkout this is.

enum PanelTab: String, CaseIterable {
    case overview = "Overview"
    case setup = "Setup"
}

struct MenuPanel: View {
    @ObservedObject var store: StackStore
    @State var tab: PanelTab = .overview
    /// Off for a snapshot, which renders the whole panel at its full height.
    var scrolls = true
    @State private var contentHeight: CGFloat = 400

    private static let width: CGFloat = 360

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            Picker("", selection: $tab) {
                ForEach(PanelTab.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            if scrolls {
                ScrollView {
                    content
                        .background(GeometryReader { geo in
                            Color.clear.preference(key: HeightKey.self, value: geo.size.height)
                        })
                }
                .frame(height: min(contentHeight, Self.maxContentHeight))
                .onPreferenceChange(HeightKey.self) { contentHeight = $0 }
            } else {
                content
            }
            footer
        }
        .padding(14)
        .frame(width: Self.width)
        .task { await store.panelOpened() }
    }

    /// The panel never grows past the screen; beyond that it scrolls.
    private static var maxContentHeight: CGFloat {
        (NSScreen.main?.visibleFrame.height ?? 900) - 180
    }

    @ViewBuilder private var content: some View {
        VStack(alignment: .leading, spacing: 14) {
            if store.checkout == nil {
                Notice(text: "No checkout found. Choose the folder you run ./stack from in Setup.", tint: .orange)
            }
            if let error = store.loadError {
                Notice(text: error, tint: .red)
            }
            if let failure = store.lastFailure {
                failureNotice(failure)
            }
            switch tab {
            case .overview: overview
            case .setup: setup
            }
        }
    }

    // ── Header: the verdict ──────────────────────────────────────────────

    private var header: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(nsImage: Logo.appIcon(side: 34))
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 8) {
                    Text("famstack").font(.headline)
                    if store.status != nil { verdict }
                }
                if let status = store.status {
                    Text(subtitle(status))
                        .font(.caption).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle)
                }
            }
            Spacer()
            if store.refreshing || store.checking {
                ProgressView().controlSize(.small)
            } else {
                Button {
                    Task {
                        await store.refresh()
                        await store.check()
                    }
                } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(.borderless)
                    .help("Check again")
            }
        }
    }

    private var lookAtCount: (count: Int, worst: Bool) {
        let items = store.attention.filter { $0.level >= .warn }
        let failing = store.status?.installed.filter { $0.health.needsAttention }.count ?? 0
        return (items.count + failing, failing > 0 || items.contains { $0.level == .error })
    }

    private var verdict: some View {
        let (count, worst) = lookAtCount
        return Text(count == 0 ? "All good" : "\(count) to look at")
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background((count == 0 ? Color.green : worst ? .red : .orange).opacity(0.2), in: Capsule())
    }

    private func subtitle(_ status: StackStatus) -> String {
        let running = status.installed.filter { $0.online }.count
        var parts = ["\(running) of \(status.installed.count) running"]
        if let up = store.host?.uptimeSeconds { parts.append("up \(up / 86400) days") }
        return parts.joined(separator: " · ")
    }

    // ── Overview ─────────────────────────────────────────────────────────

    @ViewBuilder private var overview: some View {
        if let status = store.status {
            let attention = store.attention
            if !attention.isEmpty || !status.stale.isEmpty {
                Card("Needs attention") {
                    ForEach(attention) { AttentionRow(item: $0, store: store) }
                    if !status.stale.isEmpty { staleRow(status.stale) }
                }
            }
            Card("Stacklets", spacing: 2) {
                ForEach(status.installed) { stacklet in
                    StackletRow(stacklet: stacklet, busyVerb: store.busy[stacklet.id],
                                memory: store.memory(of: stacklet.id), store: store)
                }
            }
            if let errors = store.errors {
                Card("Errors, last \(errors.since)") { ErrorsList(report: errors, store: store) }
            }
            if let line = backupLine(status) {
                Card("Backup") { Text(line).font(.callout) }
            }
            if let figures = store.hostFigures {
                Card("This Mac") { HostMetrics(figures: figures) }
            }
        } else if store.checkout != nil {
            HStack { Spacer(); ProgressView(); Spacer() }.padding(.vertical, 30)
        }
    }

    private func staleRow(_ stale: [String]) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "arrow.up.circle.fill").foregroundStyle(.blue).frame(width: 16)
            VStack(alignment: .leading, spacing: 3) {
                Text("New code is not running yet").font(.callout)
                Text(stale.joined(separator: ", ")).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if store.busy[""] != nil {
                ProgressView().controlSize(.small)
            } else {
                Button("Restart") { Task { await store.perform("restart") } }
                    .controlSize(.small)
            }
        }
    }

    /// The healthy case only; anything wrong with backups is in the
    /// attention list, which says what to do about it.
    private func backupLine(_ status: StackStatus) -> String? {
        guard status.isInstalled("backup"), let report = store.backup else { return nil }
        let lines = report.targets.compactMap { target -> String? in
            guard let run = target.lastRun, run.success,
                  let ended = run.endedAt.flatMap(parseTimestamp) else { return nil }
            return "Last backup to \(target.disk) \(relative(ended))"
        }
        return lines.isEmpty ? nil : lines.joined(separator: "\n")
    }

    // ── Setup ────────────────────────────────────────────────────────────

    @ViewBuilder private var setup: some View {
        if let config = store.config {
            Card("Configuration") { SetupGrid(report: config) }
        }
        if let status = store.status, !status.available.isEmpty {
            Card("Not installed", spacing: 2) {
                ForEach(status.available) { stacklet in
                    HStack {
                        Text(stacklet.name)
                        Spacer()
                        Button("Install…") { store.openInTerminal(["up", stacklet.id]) }
                            .controlSize(.small)
                            .help("Opens Terminal: the first start asks setup questions")
                    }
                    .padding(.vertical, 3)
                }
            }
        }
        Card("Checkout") {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(store.checkout?.path ?? "None chosen")
                        .font(.callout.monospaced()).lineLimit(1).truncationMode(.middle)
                        .foregroundStyle(store.checkout == nil ? .secondary : .primary)
                    if let version = store.status?.version {
                        Text(version).font(.caption.monospaced()).foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
                Spacer()
                Button("Change…") { store.chooseCheckout() }.controlSize(.small)
            }
        }
    }

    // ── Notices and footer ───────────────────────────────────────────────

    private func failureNotice(_ failure: StackStore.ActionFailure) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("stack \(failure.command.joined(separator: " ")) failed").font(.callout.bold())
            Text(failure.message).font(.caption.monospaced()).lineLimit(6)
            HStack {
                Button("Run in Terminal") {
                    store.openInTerminal(failure.command)
                    store.lastFailure = nil
                }
                Button("Dismiss") { store.lastFailure = nil }
            }
            .controlSize(.small)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.red.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
    }

    private var footer: some View {
        HStack(spacing: 14) {
            Button("Doctor") { store.openInTerminal(["doctor"]) }
                .disabled(store.checkout == nil)
            Spacer()
            if let checked = store.checkedAt {
                Text("checked \(relative(checked))").font(.caption).foregroundStyle(.tertiary)
            }
            Button("Quit") { NSApp.terminate(nil) }
        }
        .buttonStyle(.borderless)
        .font(.callout)
    }
}

private struct HeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

// ── Building blocks ───────────────────────────────────────────────────────

/// A titled group on a faint rounded background, as in Control Center.
struct Card<Content: View>: View {
    let title: String
    let spacing: CGFloat
    @ViewBuilder let content: Content

    init(_ title: String, spacing: CGFloat = 10, @ViewBuilder content: () -> Content) {
        self.title = title
        self.spacing = spacing
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                .padding(.leading, 4)
            VStack(alignment: .leading, spacing: spacing) { content }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
        }
    }
}

struct AttentionRow: View {
    let item: Attention
    @ObservedObject var store: StackStore

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon).foregroundStyle(tint).frame(width: 16)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.title).font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                if let fix = item.fix {
                    Button { store.apply(fix: fix) } label: {
                        Text(fix).font(.caption.monospaced())
                    }
                    .buttonStyle(.link)
                    .help("Run \(fix)")
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var icon: String {
        switch item.level {
        case .error: "xmark.octagon.fill"
        case .warn: "exclamationmark.triangle.fill"
        case .info: "info.circle.fill"
        }
    }

    private var tint: Color {
        switch item.level {
        case .error: .red
        case .warn: .orange
        case .info: .secondary
        }
    }
}

struct StackletRow: View {
    let stacklet: Stacklet
    let busyVerb: String?
    let memory: Int?
    @ObservedObject var store: StackStore
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 10) {
            Circle().fill(stacklet.health.color).frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text(stacklet.name)
                ForEach(stacklet.healthIssues, id: \.self) { issue in
                    Text(issue).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer()
            if let busyVerb {
                Text(busyVerb).font(.caption).foregroundStyle(.secondary)
                ProgressView().controlSize(.small)
            } else {
                Text(detail)
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                actions
            }
        }
        .padding(.vertical, 5)
        .padding(.horizontal, 6)
        .background(hovering ? Color.primary.opacity(0.08) : .clear, in: RoundedRectangle(cornerRadius: 6))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture { if stacklet.online { store.openInBrowser(stacklet) } }
        .help(stacklet.online && stacklet.port != nil ? "Open in the browser" : "")
    }

    /// Memory while it runs, otherwise the state. The port is one hover away
    /// in the menu; the memory is what an admin compares across rows.
    private var detail: String {
        if stacklet.health == .healthy, let memory, memory > 0 {
            return ByteCountFormatter.string(fromByteCount: Int64(memory), countStyle: .memory)
        }
        return stacklet.health == .healthy ? "" : stacklet.health.label
    }

    private var actions: some View {
        Menu {
            if let port = stacklet.port, stacklet.online {
                Button("Open in Browser (:\(port))") { store.openInBrowser(stacklet) }
                Divider()
            }
            if stacklet.health == .down {
                Button("Start") { Task { await store.perform("up", stacklet.id) } }
            } else {
                Button("Restart") { Task { await store.perform("restart", stacklet.id) } }
                Button("Stop") { Task { await store.perform("down", stacklet.id) } }
            }
            Divider()
            Button("Logs") { store.openInTerminal(["logs", stacklet.id]) }
        } label: {
            Image(systemName: "ellipsis.circle")
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
    }
}

struct ErrorsList: View {
    let report: ErrorsReport
    @ObservedObject var store: StackStore
    private static let shown = 4

    var body: some View {
        if report.containers.isEmpty {
            Label("No errors in \(report.scanned) containers", systemImage: "checkmark.circle")
                .font(.callout).foregroundStyle(.secondary)
        } else {
            ForEach(report.containers.prefix(Self.shown)) { entry in
                Button { store.openInTerminal(["logs", entry.stacklet]) } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(entry.container).font(.callout)
                            Spacer()
                            Text(summary(entry)).font(.caption).foregroundStyle(.secondary)
                        }
                        if let last = entry.lines.last {
                            Text(last.text).font(.caption.monospaced()).foregroundStyle(.secondary)
                                .lineLimit(2).truncationMode(.tail)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Open the logs in Terminal")
            }
            if report.containers.count > Self.shown {
                Button("\(report.containers.count - Self.shown) more…") {
                    store.openInTerminal(["errors"])
                }
                .buttonStyle(.link).font(.caption)
            }
        }
    }

    private func summary(_ entry: ErrorsReport.ContainerErrors) -> String {
        let when = entry.lastAt.flatMap(parseTimestamp).map { relative($0) } ?? ""
        return "\(entry.count)× \(when)"
    }
}

/// The choices in stack.toml and users.toml an admin looks for first.
/// Read-only: the buttons open the files, and doctor then lists every
/// container still running with the old setting, with the command to apply it.
struct SetupGrid: View {
    let report: ConfigReport

    private var config: JSONValue { report.config }

    var body: some View {
        Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 12, verticalSpacing: 8) {
            row("Family", family)
            row("Address", address)
            row("Language", language)
            aiRow
            row("Updates", updates)
            row("Data", config["core"]?["data_dir"]?.text ?? "default")
        }
        .font(.callout)
        Divider()
        HStack(spacing: 14) {
            Button("Edit stack.toml") { StackCLI.openInEditor(report.stackToml) }
            Button("Edit users.toml") { StackCLI.openInEditor(report.usersToml) }
                .disabled(!FileManager.default.fileExists(atPath: report.usersToml))
        }
        .buttonStyle(.link).font(.callout)
        Text("A change applies with stack up <stacklet>. Doctor lists what still runs the old setting.")
            .font(.caption).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func row(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary).gridColumnAlignment(.trailing)
            Text(value).fixedSize(horizontal: false, vertical: true)
        }
    }

    // ── What each row says ───────────────────────────────────────────────

    private var family: String {
        let people = report.users.compactMap { user -> String? in
            guard let name = user["name"]?.text ?? user["id"]?.text else { return nil }
            return user["role"]?.text == "admin" ? "\(name) (admin)" : name
        }
        if people.isEmpty { return "nobody in users.toml" }
        let owner = config["core"]?["stack_owner"]?.text.map { "\($0): " } ?? ""
        return owner + people.joined(separator: ", ")
    }

    private var address: String {
        if let domain = config["core"]?["domain"]?.text { return "Domain mode, *.\(domain)" }
        if let host = config["core"]?["host"]?.text { return "Port mode, \(host)" }
        return "Port mode"
    }

    private var language: String {
        let parts = [config["core"]?["language"]?.text, config["core"]?["timezone"]?.text].compactMap { $0 }
        return parts.isEmpty ? "default" : parts.joined(separator: ", ")
    }

    /// The model on one line, where it is, below it. Model ids are long and
    /// hyphenated, so the line is shortened in the middle rather than wrapped.
    private var aiRow: some View {
        let section = config["ai"]
        let model = section?["default"]?.text
        let provider = section?["provider"]?.text
        let local = provider == nil || provider == "local"
        let place = local ? "This Mac" : section?["openai_url"]?.text.flatMap { URL(string: $0)?.host } ?? provider
        return GridRow {
            Text("AI").foregroundStyle(.secondary).gridColumnAlignment(.trailing)
            VStack(alignment: .leading, spacing: 1) {
                Text(model ?? "default model").lineLimit(1).truncationMode(.middle).help(model ?? "")
                if let place { Text(place).font(.caption).foregroundStyle(.secondary) }
            }
        }
    }

    /// `[updates] schedule` is a cron expression with seconds. The common
    /// daily form reads as a time; anything else is shown as written.
    private var updates: String {
        guard let cron = config["updates"]?["schedule"]?.text else { return "default" }
        let f = cron.split(separator: " ").map(String.init)
        if f.count == 6, f[3...].allSatisfy({ $0 == "*" }), let m = Int(f[1]), let h = Int(f[2]) {
            return String(format: "Daily at %02d:%02d", h, m)
        }
        return cron
    }
}

struct HostMetrics: View {
    let figures: HostFigures

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            if let pct = figures.diskUsedPct, let free = figures.diskFreeGb {
                Meter(label: "Disk", value: pct / 100, detail: "\(Int(free)) GB free")
            }
            if let used = figures.memoryUsedGb, let total = figures.memoryTotalGb {
                Meter(label: "Memory", value: used / max(total, 1),
                      detail: String(format: "%.0f of %.0f GB", used, total))
            }
        }
    }
}

struct Meter: View {
    let label: String
    let value: Double
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                Text(label).font(.callout)
                Spacer()
                Text(detail).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.primary.opacity(0.1))
                    Capsule().fill(tint).frame(width: geo.size.width * min(max(value, 0), 1))
                }
            }
            .frame(height: 5)
        }
        .frame(maxWidth: .infinity)
    }

    private var tint: Color { value > 0.9 ? .red : value > 0.75 ? .orange : .accentColor }
}

struct Notice: View {
    let text: String
    let tint: Color

    var body: some View {
        Text(text)
            .font(.callout)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
    }
}

extension Health {
    var color: Color {
        switch self {
        case .healthy: .green
        case .remote: .blue
        case .starting: .yellow
        case .degraded: .orange
        case .failing: .red
        case .down, .notInstalled: .gray
        }
    }
}
