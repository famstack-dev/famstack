import AppKit
import Foundation

// ── The stack CLI as the app's only backend ───────────────────────────────
//
// The app holds no state about the stack and starts no services itself. It
// runs `./stack` in a checkout and renders what comes back, so the CLI stays
// the one owner of the lifecycle, as it is for every other client.

struct StackCLI {
    let checkout: URL

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

    /// A checkout is a directory holding the `stack` wrapper and `stacklets/`,
    /// the same marker the CLI itself walks up to.
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
        let result = await run(arguments)
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

    /// Runs `./stack <arguments>` to completion off the main thread.
    ///
    /// The working directory is the checkout because the CLI finds its root
    /// by walking up from the current directory, not from where the script
    /// lives. Stdin is empty so a hook that prompts fails instead of waiting
    /// on an answer nobody can give.
    func run(_ arguments: [String]) async -> Result {
        let checkout = self.checkout
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                process.executableURL = checkout.appendingPathComponent("stack")
                process.arguments = arguments
                process.currentDirectoryURL = checkout
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
                continuation.resume(returning: Result(
                    exitCode: process.terminationStatus,
                    stdout: Self.stripANSI(String(decoding: outData, as: UTF8.self)),
                    stderr: Self.stripANSI(String(decoding: errData.data, as: UTF8.self))))
            }
        }
    }

    private final class Collected: @unchecked Sendable { var data = Data() }

    /// Opens Terminal on `./stack <arguments>` in the checkout. Used for
    /// anything interactive or long to read: first installs, logs, doctor.
    func openInTerminal(_ arguments: [String]) {
        let line = ([checkout.appendingPathComponent("stack").path] + arguments)
            .map(Self.shellQuote).joined(separator: " ")
        openInTerminal(commandLine: line, name: arguments.joined(separator: "-"))
    }

    /// Opens Terminal on a command line exactly as the CLI printed it, such
    /// as a doctor fix. `stack …` runs this checkout's wrapper.
    func openInTerminal(fix: String) {
        let line = fix.hasPrefix("stack ") ? "./" + fix : fix
        openInTerminal(commandLine: line, name: "fix")
    }

    /// A `.command` file opens in Terminal through Launch Services, which
    /// needs no Automation permission, unlike scripting Terminal directly.
    private func openInTerminal(commandLine: String, name: String) {
        let script = "#!/bin/zsh -l\ncd \(Self.shellQuote(checkout.path)) || exit 1\n\(commandLine)\n"
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

    /// Opens a file in the default text editor, as `open -t` does.
    static func openInEditor(_ path: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = ["-t", path]
        try? process.run()
    }

    private static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private static func stripANSI(_ s: String) -> String {
        s.replacingOccurrences(of: "\u{1B}\\[[0-9;?]*[A-Za-z]", with: "", options: .regularExpression)
    }
}
