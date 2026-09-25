import Foundation

// ── What `./stack status --json` returns ──────────────────────────────────
//
// Only the fields the menu reads are declared; the decoder ignores the rest,
// so the CLI can grow its output without breaking the app.

struct StackStatus: Decodable {
    let version: String
    let host: Host?
    let stacklets: [Stacklet]
    let stale: [String]

    struct Host: Decodable {
        let memoryTotalGb: Double
        let memoryUsedGb: Double
        let diskTotalGb: Double
        let diskFreeGb: Double
        let diskUsedPct: Double
    }

    var installed: [Stacklet] { stacklets.filter(\.enabled) }
    var available: [Stacklet] { stacklets.filter { !$0.enabled } }
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
