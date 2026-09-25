import SwiftUI

// ── The panel under the menu bar icon ─────────────────────────────────────

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
                if !status.stale.isEmpty { staleNotice(status.stale) }
                stackletList(status)
                if let host = status.host {
                    Divider()
                    HostMetrics(host: host)
                }
            }
            Divider()
            footer
        }
        .padding(12)
        .frame(width: 320)
        .task { await store.refresh() }
    }

    // ── Sections ─────────────────────────────────────────────────────────

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("famstack").font(.headline)
                if let status = store.status {
                    let running = status.installed.filter { $0.online }.count
                    Text("\(running) of \(status.installed.count) running · \(status.version)")
                        .font(.caption).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle)
                }
            }
            Spacer()
            if store.refreshing {
                ProgressView().controlSize(.small)
            } else {
                Button { Task { await store.refresh() } } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(.borderless)
                    .help("Refresh")
            }
        }
    }

    private func stackletList(_ status: StackStatus) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(status.installed) { stacklet in
                StackletRow(stacklet: stacklet, busyVerb: store.busy[stacklet.id], store: store)
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
                .padding(.top, 6)
            }
        }
    }

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
            Button("Quit") { NSApp.terminate(nil) }
        }
        .buttonStyle(.borderless)
        .font(.callout)
    }
}

// ── One installed stacklet ────────────────────────────────────────────────

struct StackletRow: View {
    let stacklet: Stacklet
    let busyVerb: String?
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
                Text(detail).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                actions
            }
        }
        .padding(.vertical, 4)
        .padding(.horizontal, 6)
        .background(hovering ? Color.primary.opacity(0.08) : .clear, in: RoundedRectangle(cornerRadius: 5))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture { if stacklet.online { store.openInBrowser(stacklet) } }
    }

    private var detail: String {
        if stacklet.health == .healthy, let port = stacklet.port { return ":\(port)" }
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

// ── Host figures from `stack status` ──────────────────────────────────────

struct HostMetrics: View {
    let host: StackStatus.Host

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Meter(label: "Disk",
                  value: host.diskUsedPct / 100,
                  detail: "\(Int(host.diskFreeGb)) GB free of \(Int(host.diskTotalGb))")
            Meter(label: "Memory",
                  value: host.memoryUsedGb / max(host.memoryTotalGb, 1),
                  detail: String(format: "%.1f of %.0f GB", host.memoryUsedGb, host.memoryTotalGb))
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
