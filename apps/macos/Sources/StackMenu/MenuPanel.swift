import SwiftUI

// ── The panel under the menu bar icon ─────────────────────────────────────
//
// Ordered by the questions an admin opens it with: is everything fine, what
// needs me, what is running, what broke recently, is the data backed up,
// how full is the machine.

struct MenuPanel: View {
    @ObservedObject var store: StackStore
    @State private var showAvailable = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            if store.checkout == nil {
                Text("No checkout found. Choose the folder you run ./stack from.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            if let error = store.loadError {
                Notice(text: error, tint: .red)
            }
            if let failure = store.lastFailure {
                failureNotice(failure)
            }
            if let status = store.status {
                let attention = store.attention
                if !attention.isEmpty {
                    Section("Needs attention") {
                        ForEach(attention) { AttentionRow(item: $0, store: store) }
                    }
                }
                if !status.stale.isEmpty { staleNotice(status.stale) }
                Section("Stacklets") { stackletList(status) }
                if let errors = store.errors {
                    Section("Errors, last \(errors.since)") { ErrorsList(report: errors, store: store) }
                }
                if let line = backupLine(status) {
                    Section("Backup") {
                        Text(line).font(.callout).foregroundStyle(.secondary)
                    }
                }
                if let figures = store.hostFigures {
                    Section("This Mac") { HostMetrics(figures: figures) }
                }
            }
            Divider()
            footer
        }
        .padding(12)
        .frame(width: 340)
        .task { await store.panelOpened() }
    }

    // ── Header: the verdict ──────────────────────────────────────────────

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
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

    private var verdict: some View {
        let items = store.attention.filter { $0.level >= .warn }
        let failing = store.status?.installed.filter { $0.health.needsAttention }.count ?? 0
        let count = items.count + failing
        let worst = failing > 0 || items.contains { $0.level == .error }
        return Text(count == 0 ? "All good" : "\(count) to look at")
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 6).padding(.vertical, 1)
            .background((count == 0 ? Color.green : worst ? .red : .orange).opacity(0.18), in: Capsule())
    }

    private func subtitle(_ status: StackStatus) -> String {
        let running = status.installed.filter { $0.online }.count
        var parts = ["\(running) of \(status.installed.count) running", status.version]
        if let up = store.host?.uptimeSeconds { parts.append("up \(up / 86400) days") }
        return parts.joined(separator: " · ")
    }

    // ── Stacklets ────────────────────────────────────────────────────────

    private func stackletList(_ status: StackStatus) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(status.installed) { stacklet in
                StackletRow(stacklet: stacklet, busyVerb: store.busy[stacklet.id],
                            memory: store.memory(of: stacklet.id), store: store)
            }
            if !status.available.isEmpty {
                DisclosureGroup("Not installed (\(status.available.count))", isExpanded: $showAvailable) {
                    ForEach(status.available) { stacklet in
                        HStack {
                            Text(stacklet.name).foregroundStyle(.secondary)
                            Spacer()
                            Button("Install…") { store.openInTerminal(["up", stacklet.id]) }
                                .buttonStyle(.borderless).font(.caption)
                                .help("Opens Terminal: the first start asks setup questions")
                        }
                        .padding(.vertical, 2)
                    }
                }
                .font(.callout)
                .padding(.top, 4)
            }
        }
    }

    // ── Backup ───────────────────────────────────────────────────────────

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

    // ── Notices ──────────────────────────────────────────────────────────

    private func staleNotice(_ stale: [String]) -> some View {
        HStack {
            Image(systemName: "arrow.up.circle").foregroundStyle(.blue)
            Text("New code for \(stale.joined(separator: ", "))")
                .font(.callout).lineLimit(2)
            Spacer()
            if store.busy[""] != nil {
                ProgressView().controlSize(.small)
            } else {
                Button("Restart") { Task { await store.perform("restart") } }
                    .controlSize(.small)
            }
        }
    }

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
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.red.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
    }

    private var footer: some View {
        HStack {
            Button("Doctor") { store.openInTerminal(["doctor"]) }
                .disabled(store.checkout == nil)
            Button("Checkout…") { store.chooseCheckout() }
                .help(store.checkout?.path ?? "No checkout chosen")
            Spacer()
            if let checked = store.checkedAt {
                Text("checked \(relative(checked))").font(.caption2).foregroundStyle(.tertiary)
            }
            Button("Quit") { NSApp.terminate(nil) }
        }
        .buttonStyle(.borderless)
        .font(.callout)
    }
}

// ── Building blocks ───────────────────────────────────────────────────────

struct Section<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
            content
        }
    }
}

struct AttentionRow: View {
    let item: Attention
    @ObservedObject var store: StackStore

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon).foregroundStyle(tint).frame(width: 14)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.callout).lineLimit(2)
                Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                if let fix = item.fix {
                    Button { store.apply(fix: fix) } label: {
                        Text(fix).font(.caption.monospaced())
                    }
                    .buttonStyle(.link)
                    .help("Run \(fix)")
                }
            }
        }
        .padding(.vertical, 2)
    }

    private var icon: String {
        switch item.level {
        case .error: "xmark.octagon.fill"
        case .warn: "exclamationmark.triangle.fill"
        case .info: "info.circle"
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
        HStack(spacing: 8) {
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
                if let memory, memory > 0 {
                    Text(ByteCountFormatter.string(fromByteCount: Int64(memory), countStyle: .memory))
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
                Text(detail).font(.caption.monospacedDigit()).foregroundStyle(.tertiary)
                    .frame(minWidth: 44, alignment: .trailing)
                actions
            }
        }
        .padding(.vertical, 3)
        .padding(.horizontal, 6)
        .background(hovering ? Color.primary.opacity(0.08) : .clear, in: RoundedRectangle(cornerRadius: 5))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture { if stacklet.online { store.openInBrowser(stacklet) } }
        .help(stacklet.online && stacklet.port != nil ? "Open in the browser" : "")
    }

    private var detail: String {
        if stacklet.health == .healthy, let port = stacklet.port { return ":\(port)" }
        if stacklet.health == .healthy { return "" }
        return stacklet.health.label
    }

    private var actions: some View {
        Menu {
            if stacklet.port != nil && stacklet.online {
                Button("Open in Browser") { store.openInBrowser(stacklet) }
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
            VStack(alignment: .leading, spacing: 6) {
                ForEach(report.containers.prefix(Self.shown)) { entry in
                    Button { store.openInTerminal(["logs", entry.stacklet]) } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(entry.container).font(.callout)
                                Spacer()
                                Text(summary(entry)).font(.caption).foregroundStyle(.secondary)
                            }
                            if let last = entry.lines.last {
                                Text(last.text).font(.caption2.monospaced()).foregroundStyle(.secondary)
                                    .lineLimit(2).truncationMode(.tail)
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
    }

    private func summary(_ entry: ErrorsReport.ContainerErrors) -> String {
        let when = entry.lastAt.flatMap(parseTimestamp).map { relative($0) } ?? ""
        return "\(entry.count)× \(when)"
    }
}

struct HostMetrics: View {
    let figures: HostFigures

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let pct = figures.diskUsedPct, let free = figures.diskFreeGb, let total = figures.diskTotalGb {
                Meter(label: "Disk", value: pct / 100, detail: "\(Int(free)) GB free of \(Int(total))")
            }
            if let used = figures.memoryUsedGb, let total = figures.memoryTotalGb {
                Meter(label: "Memory", value: used / max(total, 1),
                      detail: String(format: "%.1f of %.0f GB", used, total))
            }
        }
    }
}

struct Meter: View {
    let label: String
    let value: Double
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(label).font(.caption)
                Spacer()
                Text(detail).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            ProgressView(value: min(max(value, 0), 1))
                .tint(value > 0.9 ? .red : value > 0.75 ? .orange : .accentColor)
        }
    }
}

struct Notice: View {
    let text: String
    let tint: Color

    var body: some View {
        Text(text)
            .font(.caption)
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
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
