// Mink.app: a small native window around Mink's local web app.
//
// On launch it installs Mink's Python packages the first time (with the bundled uv),
// starts the Mink server in the background, and shows it in a window. Quitting the app
// stops the server. Nothing here talks to the internet except the first-run install.

import AppKit
import WebKit

let port = 8765
let homeURL = URL(string: "http://127.0.0.1:\(port)/")!

enum Paths {
    static let resources = Bundle.main.resourceURL!
    static let project = resources.appendingPathComponent("app")
    static let uv = Bundle.main.url(forAuxiliaryExecutable: "uv")!
    static let support = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Mink")
    static let venv = support.appendingPathComponent("venv")
    static let python = venv.appendingPathComponent("bin/python")
    static let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Mink")
    static let log = logs.appendingPathComponent("mink.log")
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private let setup = SetupView()
    private var server: Process?
    private var keepAwake: NSObjectProtocol?
    private var log: FileHandle?

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        // Long transcriptions shouldn't pause because the Mac went to sleep.
        keepAwake = ProcessInfo.processInfo.beginActivity(
            options: [.idleSystemSleepDisabled], reason: "Keep transcriptions running while Mink is open")
        DispatchQueue.global(qos: .userInitiated).async { self.start() }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard Server.isTranscribing() else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "Quit while Mink is transcribing?"
        alert.informativeText = "The recording that's being transcribed will stop. You can start it again later."
        alert.addButton(withTitle: "Keep Running")
        alert.addButton(withTitle: "Quit")
        return alert.runModal() == .alertSecondButtonReturn ? .terminateNow : .terminateCancel
    }

    func applicationWillTerminate(_ notification: Notification) {
        guard let server, server.isRunning else { return }
        server.terminate()
        let deadline = Date().addingTimeInterval(3)
        while server.isRunning && Date() < deadline { usleep(100_000) }
        if server.isRunning { kill(server.processIdentifier, SIGKILL) }
    }

    // MARK: Startup

    private func start() {
        if Server.isUp() {  // Already running, for example from Terminal
            DispatchQueue.main.async { self.showMink() }
            return
        }
        openLog()
        do {
            try installPackages()
            try launchServer()
            try waitForServer()
            DispatchQueue.main.async { self.showMink() }
        } catch {
            DispatchQueue.main.async { self.setup.showError("\(error.localizedDescription)") }
        }
    }

    /// Installs or updates Mink's Python packages. Quick when nothing changed.
    private func installPackages() throws {
        // Only mention the long first-time setup if it actually takes a while.
        let slowNotice = DispatchWorkItem { self.setup.show(
            title: "Setting up Mink",
            detail: "First launch only. This downloads what Mink needs and takes a few minutes.") }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2, execute: slowNotice)
        defer { slowNotice.cancel() }

        let status = try run(Paths.uv, ["sync", "--frozen", "--no-dev", "--python", "3.12"], environment: [
            "UV_PROJECT_ENVIRONMENT": Paths.venv.path,
            "UV_PYTHON_PREFERENCE": "only-managed",
        ]) { line in DispatchQueue.main.async { self.setup.showProgress(line) } }
        if status != 0 {
            throw MinkError("Setup couldn't finish. Check your internet connection, then open Mink again.")
        }
    }

    private func launchServer() throws {
        DispatchQueue.main.async { self.setup.show(title: "Starting Mink", detail: "") }
        let process = Process()
        process.executableURL = Paths.python
        process.arguments = ["-m", "mink"]
        process.currentDirectoryURL = Paths.project
        process.environment = environment(["MINK_NO_BROWSER": "1"])
        process.standardOutput = log
        process.standardError = log
        process.terminationHandler = { finished in
            guard finished.terminationReason == .exit, finished.terminationStatus != 0 else { return }
            DispatchQueue.main.async {
                self.window.contentView = self.setup
                self.setup.showError("Mink stopped unexpectedly.")
            }
        }
        try process.run()
        server = process
    }

    private func waitForServer() throws {
        let deadline = Date().addingTimeInterval(180)  // The first start loads large models
        while Date() < deadline {
            if Server.isUp() { return }
            if server?.isRunning == false { throw MinkError("Mink couldn't start.") }
            Thread.sleep(forTimeInterval: 0.5)
        }
        throw MinkError("Mink is taking too long to start.")
    }

    private func showMink() {
        window.contentView = webView
        webView.load(URLRequest(url: homeURL))
    }

    // MARK: Helpers

    private func openLog() {
        try? FileManager.default.createDirectory(at: Paths.logs, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: Paths.log.path, contents: nil)
        log = try? FileHandle(forWritingTo: Paths.log)
    }

    private func environment(_ extra: [String: String]) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PYTHONDONTWRITEBYTECODE"] = "1"  // Never write into the signed app bundle
        env["PYTHONUNBUFFERED"] = "1"
        env.merge(extra) { _, new in new }
        return env
    }

    /// Runs a command, sends each output line to `onLine`, and returns its exit status.
    private func run(_ tool: URL, _ arguments: [String], environment extra: [String: String],
                     onLine: @escaping (String) -> Void) throws -> Int32 {
        let process = Process()
        process.executableURL = tool
        process.arguments = arguments
        process.currentDirectoryURL = Paths.project
        process.environment = environment(extra)
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self.log?.write(data)
            let text = String(decoding: data, as: UTF8.self)
            if let line = text.split(whereSeparator: \.isNewline).last { onLine(String(line)) }
        }
        try process.run()
        process.waitUntilExit()
        pipe.fileHandleForReading.readabilityHandler = nil
        return process.terminationStatus
    }

    // MARK: Window and menus

    private func buildWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = WebPolicy.shared
        webView.uiDelegate = WebPolicy.shared

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = "Mink"
        window.minSize = NSSize(width: 900, height: 600)
        window.tabbingMode = .disallowed
        window.contentView = setup
        window.center()
        window.setFrameAutosaveName("MinkMainWindow")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildMenu() {
        let main = NSMenu()
        main.addItem(submenu("Mink", [
            item("About Mink", #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            item("Settings…", #selector(openSettings), ",", target: self),
            item("Restart Mink", #selector(restartMink), target: self),
            .separator(),
            item("Hide Mink", #selector(NSApplication.hide(_:)), "h"),
            item("Quit Mink", #selector(NSApplication.terminate(_:)), "q"),
        ]))
        // Without an Edit menu, Cmd+C, Cmd+V and Cmd+Z don't work in text fields.
        main.addItem(submenu("Edit", [
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "Z"),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ]))
        main.addItem(submenu("View", [
            item("Reload", #selector(reload), "r", target: self),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f"),
        ]))
        main.addItem(submenu("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Close", #selector(NSWindow.performClose(_:)), "w"),
        ]))
        main.addItem(submenu("Help", [
            item("Show Mink Log", #selector(showLog), target: self),
        ]))
        NSApp.mainMenu = main
    }

    @objc private func reload() { webView.load(URLRequest(url: homeURL)) }

    // The page does the work, so the menu and the Settings window behave the same.
    @objc private func openSettings() { webView.evaluateJavaScript("openSettings()", completionHandler: nil) }

    @objc private func restartMink() { webView.evaluateJavaScript("restartMink()", completionHandler: nil) }

    @objc private func showLog() { NSWorkspace.shared.activateFileViewerSelecting([Paths.log]) }

    private func submenu(_ title: String, _ items: [NSMenuItem]) -> NSMenuItem {
        let holder = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let menu = NSMenu(title: title)
        for menuItem in items { menu.addItem(menuItem) }
        holder.submenu = menu
        return holder
    }

    private func item(_ title: String, _ action: Selector, _ key: String = "", target: AnyObject? = nil) -> NSMenuItem {
        let menuItem = NSMenuItem(title: title, action: action, keyEquivalent: key)
        menuItem.target = target
        return menuItem
    }
}

/// Talks to the local Mink server.
enum Server {
    static func isUp() -> Bool { fetch(homeURL.appendingPathComponent("api/info")) != nil }

    static func isTranscribing() -> Bool {
        guard let data = fetch(homeURL.appendingPathComponent("api/sessions")),
              let sessions = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return false }
        return sessions.contains { ["queued", "processing"].contains($0["status"] as? String ?? "") }
    }

    private static func fetch(_ url: URL) -> Data? {
        var result: Data?
        let done = DispatchSemaphore(value: 0)
        var request = URLRequest(url: url)
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { data, response, _ in
            if (response as? HTTPURLResponse)?.statusCode == 200 { result = data }
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 2)
        return result
    }
}

/// What the web page is allowed to do: file pickers, dialogs, downloads, and outside links.
final class WebPolicy: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    static let shared = WebPolicy()

    // Links to other sites (like Hugging Face) open in the default browser.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        if url.host != "127.0.0.1" && url.host != "localhost" && ["http", "https"].contains(url.scheme ?? "") {
            NSWorkspace.shared.open(url)
            return decisionHandler(.cancel)
        }
        decisionHandler(action.shouldPerformDownload ? .download : .allow)
    }

    // Exports arrive as attachments. Save them like a browser would.
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")
        let isAttachment = disposition?.lowercased().hasPrefix("attachment") ?? false
        decisionHandler(isAttachment || !response.canShowMIMEType ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        completionHandler(unusedURL(downloads.appendingPathComponent(suggestedFilename)))
    }

    func downloadDidFinish(_ download: WKDownload) {
        NSSound(named: "Glass")?.play()
    }

    // target="_blank" links
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.begin { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.runModal()
        completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        completionHandler(alert.runModal() == .alertFirstButtonReturn ? field.stringValue : nil)
    }

    private func unusedURL(_ url: URL) -> URL {
        var candidate = url
        var number = 2
        while FileManager.default.fileExists(atPath: candidate.path) {
            let name = "\(url.deletingPathExtension().lastPathComponent) (\(number)).\(url.pathExtension)"
            candidate = url.deletingLastPathComponent().appendingPathComponent(name)
            number += 1
        }
        return candidate
    }
}

/// The screen shown while Mink sets itself up or starts.
final class SetupView: NSView {
    private let icon = NSImageView(image: NSApp.applicationIconImage)
    private let title = NSTextField(labelWithString: "Starting Mink")
    private let detail = NSTextField(wrappingLabelWithString: "")
    private let progress = NSTextField(labelWithString: "")
    private let spinner = NSProgressIndicator()
    private let logButton = NSButton(title: "Show Log", target: nil, action: nil)

    init() {
        super.init(frame: .zero)
        title.font = .systemFont(ofSize: 22, weight: .semibold)
        detail.alignment = .center
        detail.textColor = .secondaryLabelColor
        progress.textColor = .tertiaryLabelColor
        progress.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        progress.lineBreakMode = .byTruncatingMiddle
        spinner.style = .spinning
        spinner.startAnimation(nil)
        logButton.target = self
        logButton.action = #selector(openLog)
        logButton.isHidden = true
        icon.imageScaling = .scaleProportionallyUpOrDown

        let stack = NSStackView(views: [icon, title, detail, spinner, progress, logButton])
        stack.orientation = .vertical
        stack.spacing = 14
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            icon.widthAnchor.constraint(equalToConstant: 96),
            icon.heightAnchor.constraint(equalToConstant: 96),
            stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualToConstant: 480),
            progress.widthAnchor.constraint(lessThanOrEqualToConstant: 480),
        ])
    }

    required init?(coder: NSCoder) { fatalError("Not used") }

    func show(title text: String, detail info: String) {
        title.stringValue = text
        detail.stringValue = info
        progress.stringValue = ""
    }

    func showProgress(_ line: String) { progress.stringValue = line }

    func showError(_ message: String) {
        title.stringValue = "Mink couldn't start"
        detail.stringValue = message + " If it keeps happening, the log shows what went wrong."
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        logButton.isHidden = false
    }

    @objc private func openLog() { NSWorkspace.shared.activateFileViewerSelecting([Paths.log]) }
}

struct MinkError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
