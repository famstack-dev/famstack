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
    }
}
