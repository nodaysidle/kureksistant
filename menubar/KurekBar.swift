import AppKit
import Foundation

final class KurekBarApp: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var menu: NSMenu!
    private var timer: Timer?
    private var lastFnState = false
    private let serverURL = URL(string: "http://127.0.0.1:8790")!
    private var daemonProcess: Process?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Kurek (Click or Fn to talk)", action: nil, keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Toggle Listening", action: #selector(toggleListening), keyEquivalent: "t"))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit Kurek", action: #selector(quitApp), keyEquivalent: "q"))

        if let button = statusItem.button {
            button.title = "● Kurek"
            button.target = self
            button.action = #selector(statusBarButtonClicked(_:))
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        // Auto-spawn daemon if not already running
        ensureDaemonRunning()

        // Monitor Fn key globally
        NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            self?.handleFlags(event.modifierFlags)
        }
        NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            self?.handleFlags(event.modifierFlags)
            return event
        }

        // Poll backend state at 10 Hz (fast reactive UI)
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            self?.pollBackendState()
        }
    }

    /// Resolve the Kurek install directory without hardcoding a machine path.
    /// Order: KUREK_PROJECT_DIR / KUREK_DIR env → ~/.config/kurek/install_path →
    /// parent of this binary when it lives in <repo>/menubar/.
    private func resolveProjectDir() -> String? {
        let env = ProcessInfo.processInfo.environment
        for key in ["KUREK_PROJECT_DIR", "KUREK_DIR"] {
            if let value = env[key], !value.isEmpty,
               FileManager.default.fileExists(atPath: (value as NSString).appendingPathComponent("kurek_daemon.py")) {
                return value
            }
        }

        let installPathFile = (NSHomeDirectory() as NSString).appendingPathComponent(".config/kurek/install_path")
        if let recorded = try? String(contentsOfFile: installPathFile, encoding: .utf8) {
            let dir = recorded.trimmingCharacters(in: .whitespacesAndNewlines)
            if !dir.isEmpty,
               FileManager.default.fileExists(atPath: (dir as NSString).appendingPathComponent("kurek_daemon.py")) {
                return dir
            }
        }

        let execURL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        let menubarParent = execURL.deletingLastPathComponent() // …/menubar
        let repoRoot = menubarParent.deletingLastPathComponent() // repo root
        let daemon = repoRoot.appendingPathComponent("kurek_daemon.py").path
        if FileManager.default.fileExists(atPath: daemon) {
            return repoRoot.path
        }
        return nil
    }

    private func ensureDaemonRunning() {
        let checkReq = URLRequest(url: serverURL.appendingPathComponent("status"), timeoutInterval: 0.3)
        URLSession.shared.dataTask(with: checkReq) { [weak self] data, _, error in
            if data != nil { return } // Already running

            guard let projectDir = self?.resolveProjectDir() else { return }
            let venvPython = (projectDir as NSString).appendingPathComponent(".venv/bin/python")
            let script = (projectDir as NSString).appendingPathComponent("kurek_daemon.py")

            guard FileManager.default.fileExists(atPath: venvPython) else { return }

            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: venvPython)
            proc.arguments = ["-u", script]
            proc.currentDirectoryURL = URL(fileURLWithPath: projectDir)

            let logFile = URL(fileURLWithPath: "/tmp/kurek_daemon.log")
            if !FileManager.default.fileExists(atPath: logFile.path) {
                FileManager.default.createFile(atPath: logFile.path, contents: nil)
            }
            if let fileHandle = try? FileHandle(forWritingTo: logFile) {
                fileHandle.seekToEndOfFile()
                proc.standardOutput = fileHandle
                proc.standardError = fileHandle
            }

            try? proc.run()
            self?.daemonProcess = proc
        }.resume()
    }

    @objc private func statusBarButtonClicked(_ sender: NSStatusBarButton) {
        let event = NSApp.currentEvent
        if event?.type == .rightMouseUp {
            statusItem.menu = menu
            statusItem.button?.performClick(nil)
            DispatchQueue.main.async { [weak self] in
                self?.statusItem.menu = nil
            }
        } else {
            toggleListening()
        }
    }

    private func handleFlags(_ flags: NSEvent.ModifierFlags) {
        let isFnPressed = flags.contains(.function)
        if isFnPressed && !lastFnState {
            toggleListening()
        }
        lastFnState = isFnPressed
    }

    @objc private func toggleListening() {
        var req = URLRequest(url: serverURL.appendingPathComponent("toggle"))
        req.httpMethod = "POST"
        req.timeoutInterval = 0.5
        URLSession.shared.dataTask(with: req).resume()
    }

    private func pollBackendState() {
        let req = URLRequest(url: serverURL.appendingPathComponent("status"), cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 0.3)
        URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
            guard let data = data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let state = json["state"] as? String else {
                DispatchQueue.main.async { self?.updateStatus(text: "○ Offline") }
                return
            }
            DispatchQueue.main.async {
                switch state.lowercased() {
                case "listening": self?.updateStatus(text: "🟢 Listening")
                case "thinking":  self?.updateStatus(text: "🟡 Thinking")
                case "speaking":  self?.updateStatus(text: "🔵 Speaking")
                default:          self?.updateStatus(text: "● Kurek")
                }
            }
        }.resume()
    }

    private func updateStatus(text: String) {
        if let button = statusItem.button, button.title != text {
            button.title = text
        }
    }

    @objc private func quitApp() {
        daemonProcess?.terminate()
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = KurekBarApp()
app.delegate = delegate
app.run()
