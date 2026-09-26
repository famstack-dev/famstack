import AppKit
import Foundation

// ── The stack CLI as the app's only backend ───────────────────────────────
//
// The app holds no state about the stack and starts no services itself. It
// runs `./stack` in a checkout and renders what comes back, so the CLI stays
// the one owner of the lifecycle, as it is for every other client.
//
// The checkout is on this Mac or on another one reached over SSH. Every
// command goes through `invocation`, so reading, acting, Terminal links and
// editing all reach the same machine the panel shows.

enum Connection: Equatable {
    case local(checkout: URL)
    /// `host` is anything `ssh` accepts: an alias from ~/.ssh/config or
    /// `user@host`. `checkout` is a path on that machine; `~/` is expanded
    /// there.
    case remote(host: String, checkout: String)

    var label: String {
        switch self {
        case .local: "This Mac"
        case .remote(let host, _): host
        }
    }

    var isRemote: Bool {
        if case .remote = self { return true }
        return false
    }
}

// ── What went wrong, for a person ─────────────────────────────────────────

/// A failure as the panel shows it: what happened, what to do, and the raw
/// output for anyone who wants it.
struct Problem: Equatable, Error {
    enum Kind { case connection, timeout, command }

    let kind: Kind
    let title: String
    /// What to do about it, when there is something to do.
    var hint: String?
    /// The last lines the command printed.
    var detail: String?
    /// A command line that helps, run in Terminal on this Mac.
    var terminal: TerminalCommand?

    struct TerminalCommand: Equatable {
        let label: String
        let line: String
    }
}

struct StackCLI {
    let connection: Connection

    struct Result {
        var exitCode: Int32 = 0
        var stdout = ""
        var stderr = ""
        /// Stopped because it ran past its time.
        var timedOut = false
        /// Past its time but left running (lifecycle actions).
        var stillRunning = false
        /// The process could not be started at all.
        var launchError: String?

        var succeeded: Bool { exitCode == 0 && !timedOut && !stillRunning && launchError == nil }

        /// The last lines are where the CLI says what went wrong. Both
        /// streams, because commands print their progress to either.
        func tail(_ lines: Int = 6) -> String {
            (stdout + "\n" + stderr).split(separator: "\n", omittingEmptySubsequences: true)
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
                .suffix(lines).joined(separator: "\n")
        }
    }

    /// What happens when a command runs past its time.
    enum Overrun {
        /// Stop it. For reads: an answer that late is worth nothing.
        case stop
        /// Stop waiting but let it finish, calling back when it does. For
        /// lifecycle actions: a slow `up` is usually still pulling images,
        /// and killing it halfway helps nobody.
        case detach(whenDone: @Sendable (Result) -> Void)
    }

    static let readTimeout: TimeInterval = 60
    static let actionTimeout: TimeInterval = 15 * 60

    /// A local checkout is a directory holding the `stack` wrapper and
    /// `stacklets/`, the same marker the CLI itself walks up to.
    static func isCheckout(_ url: URL) -> Bool {
        let fm = FileManager.default
        return fm.isExecutableFile(atPath: url.appendingPathComponent("stack").path)
            && fm.fileExists(atPath: url.appendingPathComponent("stacklets").path)
    }

    // ── Reading ──────────────────────────────────────────────────────────

    func status() async throws -> StackStatus { try await json(["status", "--json"]) }
    func doctor() async throws -> DoctorReport { try await json(["doctor", "--json"]) }
    func errors() async throws -> ErrorsReport { try await json(["errors", "--json"]) }
    func host() async throws -> HostReport { try await json(["host", "--json"]) }
    /// Prints JSON because its output is a pipe; it takes no `--json`.
    func backup() async throws -> BackupReport { try await json(["backup", "status"]) }
    /// Keys are TOML keys, kept exactly as written.
    func config() async throws -> ConfigReport { try await json(["config", "--json"], snakeCase: false) }

    /// Runs a command and decodes its JSON output, or throws a `Problem`.
    ///
    /// The exit code alone does not decide: `doctor` exits 1 when it has
    /// found an error and still prints its report. Anything before the first
    /// `{` line is skipped, such as a one-time notice the CLI prints on the
    /// first run after a config change.
    private func json<T: Decodable>(_ arguments: [String], snakeCase: Bool = true) async throws -> T {
        let result = await run(arguments, timeout: Self.readTimeout, overrun: .stop)
        // A run that was killed or never started may have printed half a
        // document; what it printed says nothing.
        if result.timedOut || result.launchError != nil { throw diagnose(result, arguments) }
        let lines = result.stdout.split(separator: "\n", omittingEmptySubsequences: false)
        guard let start = lines.firstIndex(where: { $0.hasPrefix("{") }) else {
            throw readProblem(result, arguments)
        }
        let body = Data(lines[start...].joined(separator: "\n").utf8)
        let decoder = JSONDecoder()
        if snakeCase { decoder.keyDecodingStrategy = .convertFromSnakeCase }
        do {
            return try decoder.decode(T.self, from: body)
        } catch {
            // A failing command answers `{"error": "..."}` (the CLI's
            // contract): that message is for people, so it is the title.
            if let reply = try? JSONDecoder().decode(ErrorReply.self, from: body) {
                throw Problem(kind: .command, title: reply.error)
            }
            throw Problem(kind: .command,
                          title: "The app does not understand what stack \(arguments[0]) returned",
                          hint: "The app and the checkout are probably from different versions.",
                          detail: String(describing: error))
        }
    }

    private struct ErrorReply: Decodable { let error: String }

    // ── Running ──────────────────────────────────────────────────────────

    /// What to execute for `./stack <arguments>`.
    ///
    /// Locally the working directory is the checkout, because the CLI finds
    /// its root by walking up from the current directory, not from where the
    /// script lives. Remotely a login shell runs it, which gives the remote
    /// Python and docker their PATH, as it does for a person logging in.
    private func invocation(_ arguments: [String]) -> (executable: URL, arguments: [String], directory: URL?) {
        switch connection {
        case .local(let checkout):
            return (checkout.appendingPathComponent("stack"), arguments, checkout)
        case .remote(let host, let checkout):
            let line = Self.inRemoteCheckout(checkout, "./stack " + arguments.map(Self.shellQuote).joined(separator: " "))
            return (URL(fileURLWithPath: "/usr/bin/ssh"),
                    Self.sshOptions(batch: true) + ["--", host, "zsh -lc " + Self.shellQuote(line)], nil)
        }
    }

    /// Runs `./stack <arguments>` off the main thread. Stdin is empty, so a
    /// hook that prompts fails instead of waiting for an answer nobody can
    /// give.
    func run(_ arguments: [String], timeout: TimeInterval, overrun: Overrun) async -> Result {
        let (executable, argv, directory) = invocation(arguments)
        return await withCheckedContinuation { continuation in
            let process = Process()
            process.executableURL = executable
            process.arguments = argv
            if let directory { process.currentDirectoryURL = directory }
            process.environment = Self.environment()
            process.standardInput = FileHandle.nullDevice
            let run = ProcessRun(process: process, continuation: continuation)
            run.start(timeout: timeout, overrun: overrun)
        }
    }

    // ── Terminal ─────────────────────────────────────────────────────────

    /// Opens Terminal on `./stack <arguments>` on the connected machine.
    /// Used for anything interactive or long to read: installs, logs, doctor.
    @discardableResult
    func openInTerminal(_ arguments: [String]) -> Bool {
        openInTerminal(inCheckout: "./stack " + arguments.map(Self.shellQuote).joined(separator: " "))
    }

    /// Opens Terminal on a command line exactly as the CLI printed it, such
    /// as a doctor fix. `stack …` runs the checkout's wrapper.
    @discardableResult
    func openInTerminal(fix: String) -> Bool {
        openInTerminal(inCheckout: fix.hasPrefix("stack ") ? "./" + fix : fix)
    }

    /// Opens a file of the instance in a text editor: the default editor for
    /// a local file, the remote `$EDITOR` in Terminal for a remote one.
    @discardableResult
    func edit(_ path: String) -> Bool {
        switch connection {
        case .local:
            return NSWorkspace.shared.open(URL(fileURLWithPath: path)) || Self.openWithTextEdit(path)
        case .remote:
            return openInTerminal(inCheckout: "${EDITOR:-nano} " + Self.shellQuote(path))
        }
    }

    private static func openWithTextEdit(_ path: String) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = ["-t", path]
        return (try? process.run()) != nil
    }

    /// `line`, run in the checkout on the connected machine.
    private func openInTerminal(inCheckout line: String) -> Bool {
        switch connection {
        case .local(let checkout):
            // Terminal does not inherit this app's environment. An app
            // pointed at another instance with STACK_DIR passes it on.
            let stackDir = ProcessInfo.processInfo.environment["STACK_DIR"]
                .map { "export STACK_DIR=\(Self.shellQuote($0))\n" } ?? ""
            return Self.openShell("\(stackDir)cd \(Self.shellQuote(checkout.path)) || exit 1\n\(line)")
        case .remote(let host, let checkout):
            let remote = Self.inRemoteCheckout(checkout, line)
            let ssh = (["ssh", "-t"] + Self.sshOptions(batch: false) + ["--", host]).map(Self.shellQuote)
                .joined(separator: " ")
            return Self.openShell("\(ssh) \(Self.shellQuote("zsh -lc " + Self.shellQuote(remote)))")
        }
    }

    /// Runs `script` in a new Terminal window on this Mac. A `.command` file
    /// opens there through Launch Services, which needs no Automation
    /// permission, unlike scripting Terminal directly. Each gets its own
    /// file, so two windows never share one that is being rewritten.
    @discardableResult
    static func openShell(_ script: String) -> Bool {
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent("famstack-\(UUID().uuidString.prefix(8)).command")
        do {
            try ("#!/bin/zsh -l\nrm -f -- \"$0\"\n" + script + "\n").write(to: file, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        } catch {
            return false
        }
        return NSWorkspace.shared.open(file)
    }

    // ── Diagnosis ────────────────────────────────────────────────────────

    /// A read that returned no JSON. Its stdout is never shown: an older CLI
    /// that ignores `--json` can print a whole file there, stack.toml with
    /// its credentials included.
    private func readProblem(_ result: Result, _ arguments: [String]) -> Problem {
        var problem = diagnose(result, arguments)
        guard problem.kind == .command else { return problem }
        let machine = connection.isRemote ? connection.label : "this Mac"
        let stderr = result.stderr.split(whereSeparator: \.isNewline).suffix(2).joined(separator: "\n")
        // argparse exits 2 for a subcommand or flag it does not know.
        if result.exitCode == 2 {
            return Problem(kind: .command, title: "Not in the famstack version on \(machine)",
                           hint: "Update that checkout with `./stack update`.")
        }
        problem = Problem(kind: .command, title: "stack \(arguments[0]) gave no report the app can read",
                          hint: "The famstack on \(machine) may be older than the app; `./stack update` brings it level.",
                          detail: stderr.isEmpty ? nil : stderr)
        return problem
    }

    /// Turns a failed run into what the panel says. It reads only what the
    /// app controls (the process, its timeout, ssh's documented exit code,
    /// the exit code of its own remote wrapper) and otherwise shows the
    /// CLI's last lines as they are: those are written for people already.
    func diagnose(_ result: Result, _ arguments: [String]) -> Problem {
        let command = "stack " + arguments.joined(separator: " ")
        let detail = result.tail(4).isEmpty ? nil : result.tail(4)

        if let launch = result.launchError {
            return Problem(kind: .connection, title: "Could not run ./stack",
                           hint: "Choose the checkout again in Setup → Connection.", detail: launch)
        }
        if result.stillRunning {
            return Problem(kind: .timeout, title: "\(command) is still running",
                           hint: "It continues in the background; the panel updates when it finishes.")
        }
        if result.timedOut {
            return Problem(kind: .timeout, title: "\(command) did not answer in time",
                           hint: "Docker on \(connection.label) may be stuck, or the connection dropped.",
                           detail: detail)
        }
        if case .remote(let host, let checkout) = connection {
            switch result.exitCode {
            case Self.sshFailed:
                return Problem(kind: .connection, title: "Could not connect to \(host) over SSH",
                               hint: "`ssh \(host)` has to work without a password, and that Mac needs Remote Login.",
                               detail: result.stderr.split(whereSeparator: \.isNewline).last
                                   .map { $0.trimmingCharacters(in: .whitespaces) },
                               terminal: .init(label: "Test in Terminal",
                                               line: "ssh -- \(Self.shellQuote(host)) exit && echo 'SSH works.'"))
            case Self.noRemoteCheckout:
                return Problem(kind: .connection, title: "No famstack checkout at \(checkout) on \(host)",
                               hint: "Set the checkout path in Setup → Connection.")
            default:
                break
            }
        }
        return Problem(kind: .command, title: "\(command) failed", detail: detail)
    }

    /// ssh's own failures (it documents 255 for them), and the code the
    /// remote wrapper exits with when the path holds no checkout.
    private static let sshFailed: Int32 = 255
    private static let noRemoteCheckout: Int32 = 96

    /// `line` run in the remote checkout, or exit 96 when there is none.
    private static func inRemoteCheckout(_ checkout: String, _ line: String) -> String {
        "{ cd \(remotePath(checkout)) 2>/dev/null && [ -x ./stack ]; } || exit \(noRemoteCheckout); \(line)"
    }

    // ── Helpers ──────────────────────────────────────────────────────────

    /// `BatchMode` makes ssh fail instead of asking for a password or a
    /// host key nobody can type into; Terminal sessions may ask.
    private static func sshOptions(batch: Bool) -> [String] {
        (batch ? ["-o", "BatchMode=yes"] : [])
            + ["-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15", "-o", "LogLevel=ERROR"]
    }

    /// A remote path quoted for the remote shell, with a leading `~/` left
    /// outside the quotes so that shell expands it.
    private static func remotePath(_ path: String) -> String {
        if path == "~" { return "~" }
        if path.hasPrefix("~/") { return "~/" + shellQuote(String(path.dropFirst(2))) }
        return shellQuote(path)
    }

    /// An app started from Finder or at login gets launchd's minimal PATH,
    /// in which the wrapper finds only Apple's Python 3.9 and no docker.
    /// Prepend where Homebrew and OrbStack install them.
    private static func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let extra = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "\(home)/.orbstack/bin"]
        env["PATH"] = (extra + [env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"]).joined(separator: ":")
        return env
    }

    static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    fileprivate static func stripANSI(_ s: String) -> String {
        s.replacingOccurrences(of: "\u{1B}\\[[0-9;?]*[A-Za-z]", with: "", options: .regularExpression)
    }
}

// ── One process ───────────────────────────────────────────────────────────

/// Runs one process and collects both of its streams.
///
/// It is done when the process exits, not when its pipes close: `stack`
/// starts docker, and a docker child can hold the pipes open after `stack`
/// itself is gone. So after the exit the readers get a moment to drain, and
/// then the result is handed back regardless.
private final class ProcessRun: @unchecked Sendable {
    private let process: Process
    private let lock = NSLock()
    private var stdout = Data()
    private var stderr = Data()
    private var continuation: CheckedContinuation<StackCLI.Result, Never>?
    private var whenDone: (@Sendable (StackCLI.Result) -> Void)?
    private var timedOut = false
    private var exited = false
    private let drained = DispatchGroup()

    init(process: Process, continuation: CheckedContinuation<StackCLI.Result, Never>) {
        self.process = process
        self.continuation = continuation
    }

    func start(timeout: TimeInterval, overrun: StackCLI.Overrun) {
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        collect(out) { self.stdout.append($0) }
        collect(err) { self.stderr.append($0) }

        process.terminationHandler = { [self] process in
            lock.withLock { exited = true }
            DispatchQueue.global().async { [self] in
                _ = drained.wait(timeout: .now() + 1)
                out.fileHandleForReading.readabilityHandler = nil
                err.fileHandleForReading.readabilityHandler = nil
                finish(exitCode: process.terminationReason == .uncaughtSignal
                           ? 128 + process.terminationStatus : process.terminationStatus)
            }
        }

        do {
            try process.run()
        } catch {
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            deliver(StackCLI.Result(exitCode: -1, launchError: error.localizedDescription))
            return
        }

        DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { [self] in
            // Decided under the lock that `finish` also takes, so an exit in
            // the same instant either wins completely or not at all.
            let overran = lock.withLock { () -> Bool in
                guard !exited else { return false }
                switch overrun {
                case .stop: timedOut = true
                case .detach(let callback): whenDone = callback
                }
                return true
            }
            guard overran else { return }
            switch overrun {
            case .stop:
                process.terminate()
                // A process that ignores SIGTERM gets SIGKILL.
                DispatchQueue.global().asyncAfter(deadline: .now() + 5) { [self] in
                    if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                }
            case .detach:
                deliver(StackCLI.Result(stillRunning: true))
            }
        }
    }

    private func collect(_ pipe: Pipe, into append: @escaping (Data) -> Void) {
        drained.enter()
        pipe.fileHandleForReading.readabilityHandler = { [self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                drained.leave()
            } else {
                lock.withLock { append(data) }
            }
        }
    }

    private func finish(exitCode: Int32) {
        let result = lock.withLock {
            StackCLI.Result(exitCode: exitCode,
                            stdout: StackCLI.stripANSI(String(decoding: stdout, as: UTF8.self)),
                            stderr: StackCLI.stripANSI(String(decoding: stderr, as: UTF8.self)),
                            timedOut: timedOut)
        }
        // Whoever is still waiting gets the result; a caller that was
        // already told "still running" hears it through the callback.
        if !deliver(result), let callback = lock.withLock({ whenDone }) { callback(result) }
    }

    /// Resumes the caller exactly once, whichever of exit, timeout or launch
    /// failure comes first. Returns whether this call was the one.
    @discardableResult
    private func deliver(_ result: StackCLI.Result) -> Bool {
        let pending = lock.withLock { () -> CheckedContinuation<StackCLI.Result, Never>? in
            defer { continuation = nil }
            return continuation
        }
        pending?.resume(returning: result)
        return pending != nil
    }
}
