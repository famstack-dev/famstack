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
        let output: String

        var succeeded: Bool { exitCode == 0 }

        /// The last lines are where the CLI says what went wrong.
        func tail(_ lines: Int = 6) -> String {
            output.split(separator: "\n", omittingEmptySubsequences: true)
                .suffix(lines).joined(separator: "\n")
        }
    }

    enum Failure: LocalizedError {
        case exited(Result)
        case undecodable(String)

        var errorDescription: String? {
            switch self {
            case .exited(let result): result.tail(3)
            case .undecodable(let reason): "Unexpected output from stack status: \(reason)"
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

    func status() async throws -> StackStatus {
        let result = await run(["status", "--json"])
        guard result.succeeded else { throw Failure.exited(result) }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try decoder.decode(StackStatus.self, from: Data(result.output.utf8))
        } catch {
            throw Failure.undecodable(String(describing: error))
        }
    }

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
                let pipe = Pipe()
                process.standardOutput = pipe
                process.standardError = pipe
                do {
                    try process.run()
                } catch {
                    continuation.resume(returning: Result(exitCode: -1, output: error.localizedDescription))
                    return
                }
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let text = String(decoding: data, as: UTF8.self)
                continuation.resume(returning: Result(exitCode: process.terminationStatus,
                                                      output: Self.stripANSI(text)))
            }
        }
    }

    /// Opens Terminal on `./stack <arguments>` in the checkout. Used for
    /// anything interactive or endless: first installs, logs, doctor.
    ///
    /// A `.command` file opens in Terminal through Launch Services, which
    /// needs no Automation permission, unlike scripting Terminal directly.
    func openInTerminal(_ arguments: [String]) {
        let quoted = ([checkout.appendingPathComponent("stack").path] + arguments)
            .map(Self.shellQuote).joined(separator: " ")
        let script = "#!/bin/zsh -l\ncd \(Self.shellQuote(checkout.path)) || exit 1\n\(quoted)\n"
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent("stack-\(arguments.joined(separator: "-")).command")
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

    private static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private static func stripANSI(_ s: String) -> String {
        s.replacingOccurrences(of: "\u{1B}\\[[0-9;?]*[A-Za-z]", with: "", options: .regularExpression)
    }
}
