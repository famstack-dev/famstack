import Foundation

// ── What the CLI returns ──────────────────────────────────────────────────
//
// Only the fields the panel reads are declared; the decoder ignores the rest,
// so the CLI can grow its output without breaking the app. Keys arrive in
// snake_case and are converted.

/// `stack status --json`: polled every minute, cheap (about half a second).
struct StackStatus: Decodable {
    let version: String
    let host: HostFigures?
    let stacklets: [Stacklet]
    let stale: [String]

    var installed: [Stacklet] { stacklets.filter(\.enabled) }
    var available: [Stacklet] { stacklets.filter { !$0.enabled } }
    func isInstalled(_ id: String) -> Bool { installed.contains { $0.id == id } }
}

struct HostFigures: Decodable {
    let memoryTotalGb: Double?
    let memoryUsedGb: Double?
    let diskTotalGb: Double?
    let diskFreeGb: Double?
    let diskUsedPct: Double?
}

struct Stacklet: Decodable, Identifiable {
    let id: String
    let name: String
    let port: Int?
    let enabled: Bool
    let online: Bool
    let starting: Bool
    let failing: Bool
    let degraded: Bool
    let stale: Bool
    let remote: String?
    let healthIssues: [String]

    /// One state per row, derived in the same order `stack list` reads the
    /// flags: a remote stacklet is judged by its remote checks alone, and a
    /// stacklet that is installed but has no running project counts as down.
    var health: Health {
        if !enabled { return .notInstalled }
        if remote != nil { return degraded ? .degraded : .remote }
        if failing { return .failing }
        if starting { return .starting }
        if !online { return .down }
        if degraded { return .degraded }
        return .healthy
    }
}

enum Health {
    case healthy, remote, starting, degraded, failing, down, notInstalled

    /// Down is not a fault: `stack down` leaves a stacklet exactly like this,
    /// and a crashing one restarts (`unless-stopped`) and reads as failing.
    var needsAttention: Bool {
        switch self {
        case .degraded, .failing: true
        default: false
        }
    }

    var label: String {
        switch self {
        case .healthy: "running"
        case .remote: "remote"
        case .starting: "starting"
        case .degraded: "degraded"
        case .failing: "failing"
        case .down: "down"
        case .notInstalled: "not installed"
        }
    }
}

/// `stack doctor --json`.
struct DoctorReport: Decodable {
    let summary: String
    let findings: [Finding]

    struct Finding: Decodable {
        let level: String
        let title: String
        let detail: String
        let fix: String
    }
}

/// `stack errors --json`.
struct ErrorsReport: Decodable {
    let since: String
    let scanned: Int
    let containers: [ContainerErrors]

    struct ContainerErrors: Decodable, Identifiable {
        let stacklet: String
        let container: String
        let count: Int
        let lastAt: String?
        let lines: [Line]

        var id: String { container }
    }

    struct Line: Decodable {
        let at: String?
        let text: String
    }
}

/// `stack host --json`.
struct HostReport: Decodable {
    let memoryTotalGb: Double?
    let memoryUsedGb: Double?
    let diskTotalGb: Double?
    let diskFreeGb: Double?
    let diskUsedPct: Double?
    let uptimeSeconds: Int?
    let stacklets: [Usage]

    struct Usage: Decodable {
        let id: String
        let memoryBytes: Int
        let cpuPct: Double
    }

    var figures: HostFigures {
        HostFigures(memoryTotalGb: memoryTotalGb, memoryUsedGb: memoryUsedGb,
                    diskTotalGb: diskTotalGb, diskFreeGb: diskFreeGb, diskUsedPct: diskUsedPct)
    }
}

/// `stack backup status`, which prints JSON whenever its output is piped.
struct BackupReport: Decodable {
    let targets: [Target]

    struct Target: Decodable {
        let name: String
        let disk: String
        let schedule: String
        let diskMounted: Bool
        let cronInstalled: Bool
        let canary: String
        let lastRun: Run?
    }

    struct Run: Decodable {
        let success: Bool
        let failureReason: String?
        let endedAt: String?
    }
}

// ── Needs attention ───────────────────────────────────────────────────────

/// One thing the admin should look at, with the command that addresses it.
struct Attention: Identifiable {
    enum Level: Int, Comparable {
        case info, warn, error
        static func < (a: Level, b: Level) -> Bool { a.rawValue < b.rawValue }
    }

    let id: String
    let level: Level
    let title: String
    let detail: String
    /// A command line as the CLI prints it, `stack up core`, or nil.
    let fix: String?

    /// Everything that needs the admin, worst first. Stacklet rows already
    /// show which stacklets are failing; this adds what a row cannot: the
    /// doctor's diagnoses, backups and the disk.
    static func collect(status: StackStatus?, doctor: DoctorReport?, backup: BackupReport?,
                        disk: HostFigures?, now: Date = Date()) -> [Attention] {
        var items: [Attention] = []

        // Stale code already has its own notice with one button for all of
        // it; doctor's per-stacklet `stack restart <id>` would repeat it.
        let staleFixes = Set((status?.stale ?? []).map { "stack restart \($0)" })

        for (i, finding) in (doctor?.findings ?? []).enumerated() where !staleFixes.contains(finding.fix) {
            let level: Level = switch finding.level {
            case "error": .error
            case "warn": .warn
            default: .info
            }
            items.append(Attention(id: "doctor-\(i)", level: level, title: finding.title,
                                   detail: finding.detail, fix: finding.fix.isEmpty ? nil : finding.fix))
        }

        if let status {
            if !status.isInstalled("backup") {
                items.append(Attention(
                    id: "backup-missing", level: .warn, title: "Backups are not set up",
                    detail: "Nothing copies the data in this Mac's data dir to a second disk.",
                    fix: "stack up backup"))
            } else if let backup {
                items += backupItems(backup, now: now)
            }
        }

        if let pct = disk?.diskUsedPct, pct >= 90 {
            let free = disk?.diskFreeGb.map { "\(Int($0)) GB left" } ?? "Little space left"
            items.append(Attention(
                id: "disk", level: pct >= 95 ? .error : .warn,
                title: "Disk is \(Int(pct))% full",
                detail: "\(free) on the disk that holds the data dir.", fix: nil))
        }

        return items.sorted { $0.level > $1.level }
    }

    /// A scheduled backup older than two days has missed at least one run.
    private static let overdue: TimeInterval = 2 * 24 * 3600

    private static func backupItems(_ report: BackupReport, now: Date) -> [Attention] {
        report.targets.flatMap { target -> [Attention] in
            var items: [Attention] = []
            let id = "backup-\(target.name)"
            if target.canary == "tampered" {
                items.append(Attention(
                    id: id + "-canary", level: .error, title: "Backup tripwire on \(target.disk) changed",
                    detail: "Something rewrote files on the backup disk. Check it before the next run.",
                    fix: "stack backup status"))
            }
            guard let run = target.lastRun else {
                items.append(Attention(id: id, level: .error, title: "No backup to \(target.disk) has run yet",
                                       detail: "Schedule: \(target.schedule)", fix: "stack backup sync"))
                return items
            }
            if !run.success {
                items.append(Attention(id: id, level: .error, title: "Last backup to \(target.disk) failed",
                                       detail: run.failureReason ?? "No reason recorded.",
                                       fix: "stack backup status"))
            } else if let ended = run.endedAt.flatMap(parseTimestamp), now.timeIntervalSince(ended) > overdue {
                items.append(Attention(id: id, level: .warn,
                                       title: "Last backup to \(target.disk) was \(relative(ended, now))",
                                       detail: target.diskMounted ? "The disk is connected." : "The disk is not connected.",
                                       fix: "stack backup sync"))
            }
            return items
        }
    }
}

// ── Time ──────────────────────────────────────────────────────────────────

/// The CLI's timestamps are ISO 8601 in UTC, from whole seconds (backups) to
/// nanoseconds (`docker logs`). Fractions beyond milliseconds are dropped,
/// which the system parser needs and a panel does not miss.
func parseTimestamp(_ text: String) -> Date? {
    var s = text
    if let dot = s.firstIndex(of: "."), let z = s.firstIndex(of: "Z"), dot < z {
        let fraction = s[s.index(after: dot)..<z].prefix(3)
        s = String(s[..<dot]) + "." + fraction + "Z"
    }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = s.contains(".") ? [.withInternetDateTime, .withFractionalSeconds] : [.withInternetDateTime]
    return formatter.date(from: s)
}

func relative(_ date: Date, _ now: Date = Date()) -> String {
    let formatter = RelativeDateTimeFormatter()
    formatter.unitsStyle = .full
    return formatter.localizedString(for: date, relativeTo: now)
}
