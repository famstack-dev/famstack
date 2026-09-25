import AppKit
import SwiftUI

@main
struct StackMenuApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = StackStore()

    var body: some Scene {
        MenuBarExtra {
            MenuPanel(store: store)
        } label: {
            Image(systemName: store.summary.symbol)
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
        // The store starts loading on its own; wait for the slow half.
        while store.checkedAt == nil { try? await Task.sleep(for: .milliseconds(200)) }
        for tab in PanelTab.allCases {
            let view = MenuPanel(store: store, tab: tab, scrolls: false)
                .background(Color(nsColor: .windowBackgroundColor))
                .environment(\.colorScheme, .dark)
            let url = URL(fileURLWithPath: "\(prefix)-\(tab.rawValue.lowercased()).png")
            render(view, to: url)
        }
        NSApp.terminate(nil)
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
