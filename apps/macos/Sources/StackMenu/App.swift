import AppKit
import SwiftUI

@main
struct StackMenuApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = StackStore()

    /// The tool modes render and quit. They must not put a second icon in
    /// the menu bar next to the running app's.
    private static let isTool = CommandLine.arguments.contains("--snapshot")

    init() {
        // Used by build.sh to make the bundle's icon from the same drawing.
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--icon"), i + 1 < args.count {
            do {
                try Logo.writeIconset(to: URL(fileURLWithPath: args[i + 1]))
                exit(0)
            } catch {
                FileHandle.standardError.write(Data("\(error)\n".utf8))
                exit(1)
            }
        }
    }

    var body: some Scene {
        MenuBarExtra(isInserted: .constant(!Self.isTool)) {
            MenuPanel(store: store)
        } label: {
            Image(nsImage: Logo.menuBarImage(store.summary.logo))
        }
        .menuBarExtraStyle(.window)
    }
}

/// A menu bar app has no Dock icon. The bundle says so with LSUIElement;
/// setting the policy here as well keeps `swift run` from showing one.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count {
            Task { @MainActor in await Snapshot.write(prefix: args[i + 1]) }
        }
    }
}

// ── Snapshots ─────────────────────────────────────────────────────────────
//
// `StackMenu --snapshot /tmp/panel` loads the real data, renders each tab
// into `/tmp/panel-overview.png` and `/tmp/panel-setup.png`, and quits. The
// panel is drawn in an offscreen window of the app's own, so no screen
// recording permission is needed, and a layout change can be checked
// without opening the menu.

@MainActor
enum Snapshot {
    static func write(prefix: String) async {
        let store = StackStore()
        // The store starts loading on its own; wait for the slow half, but
        // not past the read timeout: a machine that does not answer renders
        // as the panel shows it then.
        let deadline = Date().addingTimeInterval(StackCLI.readTimeout + 10)
        while store.checkedAt == nil && store.connection != nil && Date() < deadline {
            try? await Task.sleep(for: .milliseconds(200))
        }
        render(menuBarStates, to: URL(fileURLWithPath: "\(prefix)-menubar.png"))
        for tab in PanelTab.allCases {
            let view = MenuPanel(store: store, tab: tab, scrolls: false)
                .background(Color(nsColor: .windowBackgroundColor))
                .environment(\.colorScheme, .dark)
            let url = URL(fileURLWithPath: "\(prefix)-\(tab.rawValue.lowercased()).png")
            render(view, to: url)
        }
        NSApp.terminate(nil)
    }

    /// Every icon state on a light and a dark bar, tinted as macOS tints
    /// a template image.
    private static var menuBarStates: some View {
        let states: [Logo.State] = [.normal, .attention, .busy, .unreachable]
        return VStack(spacing: 0) {
            ForEach([Color.white, Color(white: 0.16)], id: \.self) { bar in
                HStack(spacing: 18) {
                    ForEach(states.indices, id: \.self) { i in
                        Image(nsImage: Logo.menuBarImage(states[i]))
                            .renderingMode(.template)
                            .foregroundStyle(bar == .white ? Color.black : Color.white)
                    }
                }
                .padding(.horizontal, 14).padding(.vertical, 5)
                .background(bar)
            }
        }
        .scaleEffect(3, anchor: .topLeading)
        .frame(width: 3 * 150, height: 3 * 56, alignment: .topLeading)
    }

    private static func render(_ view: some View, to url: URL) {
        let host = NSHostingView(rootView: view)
        host.appearance = NSAppearance(named: .darkAqua)
        host.frame.size = host.fittingSize
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return }
        host.cacheDisplay(in: host.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }
}
