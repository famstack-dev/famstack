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

struct StackCLI {
    let connection: Connection

    struct Result {
        let exitCode: Int32
        let stdout: String
        let stderr: String

        var succeeded: Bool { exitCode == 0 }

        /// The last lines are where the CLI says what went wrong. Both
        /// streams, because commands print their progress to either.
        func tail(_ lines: Int = 6) -> String {
            (stdout + "\n" + stderr).split(separator: "\n", omittingEmptySubsequences: true)
                .suffix(lines).joined(separator: "\n")
        }
    }

    enum Failure: LocalizedError {
        case exited(Result)
        case undecodable(command: String, reason: String)

        var errorDescription: String? {
            switch self {
            case .exited(let result): result.tail(3)
            case .undecodable(let command, let reason): "Unexpected output from stack \(command): \(reason)"
            }
        }
    }

    /// Reading calls give up after a minute; a lifecycle action may pull
    /// images and gets much longer.
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

    /// Runs a command and decodes its JSON output.
    ///
    /// The exit code alone does not decide: `doctor` exits 1 when it has
    /// found an error and still prints its report. Anything before the first
    /// `{` line is skipped, such as a one-time notice the CLI prints on the
    /// first run after a config change.
    private func json<T: Decodable>(_ arguments: [String], snakeCase: Bool = true) async throws -> T {
        let result = await run(arguments, timeout: Self.readTimeout)
        let lines = result.stdout.split(separator: "\n", omittingEmptySubsequences: false)
        guard let start = lines.firstIndex(where: { $0.hasPrefix("{") }) else {
            throw Failure.exited(result)
        }
        let body = lines[start...].joined(separator: "\n")
        let decoder = JSONDecoder()
        if snakeCase { decoder.keyDecodingStrategy = .convertFromSnakeCase }
        do {
            return try decoder.decode(T.self, from: Data(body.utf8))
        } catch {
            throw Failure.undecodable(command: arguments.joined(separator: " "), reason: String(describing: error))
        }
    }

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
            let line = "cd \(Self.remotePath(checkout)) && ./stack " + arguments.map(Self.shellQuote).joined(separator: " ")
            return (URL(fileURLWithPath: "/usr/bin/ssh"),
                    Self.sshOptions(batch: true) + [host, "zsh -lc " + Self.shellQuote(line)], nil)
        }
    }

    /// Runs `./stack <arguments>` to completion off the main thread, and
    /// stops it after `timeout`. Stdin is empty so a hook that prompts fails
    /// instead of waiting on an answer nobody can give.
    func run(_ arguments: [String], timeout: TimeInterval = actionTimeout) async -> Result {
        let (executable, argv, directory) = invocation(arguments)
        let description = "stack " + arguments.joined(separator: " ")
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                process.executableURL = executable
                process.arguments = argv
                if let directory { process.currentDirectoryURL = directory }
                process.environment = Self.environment()
                process.standardInput = FileHandle.nullDevice
                let out = Pipe(), err = Pipe()
                process.standardOutput = out
                process.standardError = err
                do {
                    try process.run()
                } catch {
                    continuation.resume(returning: Result(exitCode: -1, stdout: "", stderr: error.localizedDescription))
                    return
                }
                let timedOut = Flag()
                DispatchQueue.global().asyncAfter(deadline: .now() + timeout) {
                    if process.isRunning {
                        timedOut.value = true
                        process.terminate()
                    }
                }
                // Both pipes drain at once: a command that fills one while
                // the other is being read would otherwise block forever.
                let errData = Collected()
                let group = DispatchGroup()
                group.enter()
                DispatchQueue.global().async {
                    errData.data = err.fileHandleForReading.readDataToEndOfFile()
                    group.leave()
                }
                let outData = out.fileHandleForReading.readDataToEndOfFile()
                group.wait()
                process.waitUntilExit()
                var stderr = Self.stripANSI(String(decoding: errData.data, as: UTF8.self))
                if timedOut.value { stderr += "\n\(description) did not finish within \(Int(timeout)) seconds." }
                continuation.resume(returning: Result(
                    exitCode: process.terminationStatus,
                    stdout: Self.stripANSI(String(decoding: outData, as: UTF8.self)),
                    stderr: stderr))
            }
        }
    }

    private final class Collected: @unchecked Sendable { var data = Data() }
    private final class Flag: @unchecked Sendable { var value = false }

    // ── Terminal ─────────────────────────────────────────────────────────

    /// Opens Terminal on `./stack <arguments>` on the connected machine.
    /// Used for anything interactive or long to read: installs, logs, doctor.
    func openInTerminal(_ arguments: [String]) {
        openInTerminal(line: "./stack " + arguments.map(Self.shellQuote).joined(separator: " "),
                       name: arguments.joined(separator: "-"))
    }

    /// Opens Terminal on a command line exactly as the CLI printed it, such
    /// as a doctor fix. `stack …` runs the checkout's wrapper.
    func openInTerminal(fix: String) {
        openInTerminal(line: fix.hasPrefix("stack ") ? "./" + fix : fix, name: "fix")
    }

    /// Opens a file of the instance in a text editor: TextEdit (or the
    /// default editor) for a local file, the remote `$EDITOR` in Terminal
    /// for a remote one.
    func edit(_ path: String) {
        switch connection {
        case .local:
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
            process.arguments = ["-t", path]
            try? process.run()
        case .remote:
            openInTerminal(line: "${EDITOR:-nano} " + Self.shellQuote(path), name: "edit")
        }
    }

    /// Writes `line`, run in the checkout, to a `.command` file and opens
    /// it. Launch Services opens it in Terminal without the Automation
    /// permission that scripting Terminal would need.
    private func openInTerminal(line: String, name: String) {
        let body: String
        switch connection {
        case .local(let checkout):
            // Terminal does not inherit this app's environment. An app
            // pointed at another instance with STACK_DIR passes it on.
            let stackDir = ProcessInfo.processInfo.environment["STACK_DIR"]
                .map { "export STACK_DIR=\(Self.shellQuote($0))\n" } ?? ""
            body = "\(stackDir)cd \(Self.shellQuote(checkout.path)) || exit 1\n\(line)\n"
        case .remote(let host, let checkout):
            let remote = "cd \(Self.remotePath(checkout)) && \(line)"
            let ssh = (["ssh", "-t"] + Self.sshOptions(batch: false) + [host]).map(Self.shellQuote).joined(separator: " ")
            body = "\(ssh) \(Self.shellQuote("zsh -lc " + Self.shellQuote(remote)))\n"
        }
        let script = "#!/bin/zsh -l\n" + body
        let safeName = name.filter { $0.isLetter || $0.isNumber || $0 == "-" }
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("stack-\(safeName).command")
        do {
            try script.write(to: file, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: file.path)
            NSWorkspace.shared.open(file)
        } catch {
            NSSound.beep()
        }
    }

    // ── Helpers ──────────────────────────────────────────────────────────

    /// `BatchMode` makes ssh fail instead of asking for a password or a
    /// host key nobody can type into; Terminal sessions may ask.
    private static func sshOptions(batch: Bool) -> [String] {
        (batch ? ["-o", "BatchMode=yes"] : []) + ["-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR"]
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

    private static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private static func stripANSI(_ s: String) -> String {
        s.replacingOccurrences(of: "\u{1B}\\[[0-9;?]*[A-Za-z]", with: "", options: .regularExpression)
    }
}
