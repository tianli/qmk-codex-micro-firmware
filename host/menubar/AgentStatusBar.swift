import Cocoa
import Foundation

// MARK: - Data Models

struct SlotInfo: Decodable, Equatable {
    let slot: Int
    let key: Int
    let sessionId: String
    let status: String
    let parentStatus: String?
    let subagentCount: Int?
    let waitingSubagents: Int?
    let workspace: String?
    let provider: String?
    let clientApp: String?
    let terminalApp: String?
    let isRemote: Bool?

    enum CodingKeys: String, CodingKey {
        case slot
        case key
        case sessionId = "session_id"
        case status
        case parentStatus = "parent_status"
        case subagentCount = "subagent_count"
        case waitingSubagents = "waiting_subagents"
        case workspace
        case provider
        case clientApp = "client_app"
        case terminalApp = "terminal_app"
        case isRemote = "is_remote"
    }

    var displayLabel: String {
        if let ws = workspace, !ws.isEmpty {
            return ws
        }
        return String(sessionId.prefix(8))
    }

    var statusLabel: String {
        let subCount = subagentCount ?? 0
        let waitingSubs = waitingSubagents ?? 0
        switch status {
        case "waiting":
            if waitingSubs > 0 {
                return "Waiting (\(waitingSubs) subagent\(waitingSubs == 1 ? "" : "s") blocked)"
            }
            return "Waiting for input"
        case "executing":
            if subCount > 0 {
                return "Executing (\(subCount) subagent\(subCount == 1 ? "" : "s"))"
            }
            return "Executing"
        case "thinking":
            return "Thinking"
        case "complete":
            return "Complete"
        case "error":
            return "Error"
        default:
            return "Idle"
        }
    }

    var hostBadge: String {
        if let c = clientApp, !c.isEmpty {
            return c
        }
        if let t = terminalApp, !t.isEmpty {
            return t
        }
        if isRemote == true {
            return "remote"
        }
        return provider ?? "agent"
    }
}

struct SlotsSnapshot: Decodable {
    let version: Int?
    let activeSession: String?
    let slots: [SlotInfo]

    enum CodingKeys: String, CodingKey {
        case version
        case activeSession = "active_session"
        case slots
    }
}

// MARK: - Native NSImage Renderers (replicated by macOS across every display menu bar)

enum StatusStyle {
    static func fillColor(for status: String, pulseOn: Bool) -> NSColor {
        switch status {
        case "waiting":
            return pulseOn
                ? NSColor(calibratedRed: 1.00, green: 0.62, blue: 0.00, alpha: 1.0)
                : NSColor(calibratedRed: 0.88, green: 0.44, blue: 0.00, alpha: 1.0)
        case "executing":
            return NSColor(calibratedRed: 0.62, green: 0.22, blue: 0.98, alpha: 1.0)
        case "thinking":
            return NSColor(calibratedRed: 0.16, green: 0.42, blue: 1.00, alpha: 1.0)
        case "complete":
            return NSColor(calibratedRed: 0.07, green: 0.72, blue: 0.30, alpha: 1.0)
        case "error":
            return NSColor(calibratedRed: 0.90, green: 0.12, blue: 0.20, alpha: 1.0)
        default:
            return NSColor(calibratedRed: 0.43, green: 0.46, blue: 0.51, alpha: 1.0)
        }
    }

    static func menuDotImage(for status: String) -> NSImage {
        let size = NSSize(width: 10, height: 10)
        let img = NSImage(size: size, flipped: false) { rect in
            let color = fillColor(for: status, pulseOn: true)
            color.setFill()
            NSBezierPath(ovalIn: rect.insetBy(dx: 1, dy: 1)).fill()
            return true
        }
        img.isTemplate = false
        return img
    }

    static func renderIdleImage(connected: Bool) -> NSImage {
        let size = NSSize(width: 20, height: 18)
        let img = NSImage(size: size, flipped: false) { _ in
            let ringRect = NSRect(x: 3.5, y: 2.5, width: 13, height: 13)
            let path = NSBezierPath(roundedRect: ringRect, xRadius: 4, yRadius: 4)
            path.lineWidth = 1.5
            if connected {
                NSColor.labelColor.withAlphaComponent(0.80).setStroke()
                path.stroke()
                let dot = NSBezierPath(ovalIn: NSRect(x: 8.0, y: 7.0, width: 4.0, height: 4.0))
                NSColor.labelColor.withAlphaComponent(0.80).setFill()
                dot.fill()
            } else {
                NSColor.secondaryLabelColor.withAlphaComponent(0.60).setStroke()
                let dash: [CGFloat] = [2.5, 2.0]
                path.setLineDash(dash, count: 2, phase: 0)
                path.stroke()
            }
            return true
        }
        img.isTemplate = true
        return img
    }

    static func renderSinglePillImage(
        item: SlotInfo,
        pulseOn: Bool,
        connected: Bool,
        flashing: Bool
    ) -> NSImage {
        let canvasWidth: CGFloat = 22.0
        let canvasHeight: CGFloat = 20.0
        let pillWidth: CGFloat = 21.0
        let pillHeight: CGFloat = 17.5
        let x = (canvasWidth - pillWidth) / 2.0
        let y = (canvasHeight - pillHeight) / 2.0

        let img = NSImage(size: NSSize(width: canvasWidth, height: canvasHeight), flipped: false) { _ in
            let pillRect = NSRect(x: x, y: y, width: pillWidth, height: pillHeight)
            let pillPath = NSBezierPath(roundedRect: pillRect, xRadius: 5.0, yRadius: 5.0)

            let baseColor = fillColor(for: item.status, pulseOn: pulseOn)
            var fill = connected ? baseColor : baseColor.withAlphaComponent(0.45)
            if flashing {
                fill = fill.blended(withFraction: 0.35, of: .white) ?? fill
            }
            fill.setFill()
            pillPath.fill()

            if flashing {
                NSColor.white.withAlphaComponent(0.95).setStroke()
                let borderPath = NSBezierPath(roundedRect: pillRect.insetBy(dx: 0.75, dy: 0.75), xRadius: 4.5, yRadius: 4.5)
                borderPath.lineWidth = 1.5
                borderPath.stroke()
            } else if item.status == "waiting" {
                let strokeColor = pulseOn
                    ? NSColor.white.withAlphaComponent(0.95)
                    : NSColor(calibratedRed: 1.0, green: 0.86, blue: 0.45, alpha: 0.75)
                strokeColor.setStroke()
                let borderPath = NSBezierPath(roundedRect: pillRect.insetBy(dx: 0.75, dy: 0.75), xRadius: 4.5, yRadius: 4.5)
                borderPath.lineWidth = 1.4
                borderPath.stroke()
            } else {
                NSColor.black.withAlphaComponent(0.18).setStroke()
                let borderPath = NSBezierPath(roundedRect: pillRect.insetBy(dx: 0.5, dy: 0.5), xRadius: 4.8, yRadius: 4.8)
                borderPath.lineWidth = 0.75
                borderPath.stroke()
            }

            let digit = "\(item.key)"
            let font = NSFont.monospacedDigitSystemFont(ofSize: 12.0, weight: .bold)
            let paragraph = NSMutableParagraphStyle()
            paragraph.alignment = .center
            let attrs: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: NSColor.white,
                .paragraphStyle: paragraph,
            ]
            let textSize = (digit as NSString).size(withAttributes: attrs)
            let textRect = NSRect(
                x: x,
                y: y + (pillHeight - textSize.height) / 2.0 - 0.3,
                width: pillWidth,
                height: textSize.height
            )
            (digit as NSString).draw(in: textRect, withAttributes: attrs)

            if (item.subagentCount ?? 0) > 0 {
                let dotSize: CGFloat = 5.0
                let dotRect = NSRect(
                    x: x + pillWidth - dotSize + 0.5,
                    y: y + pillHeight - dotSize + 0.5,
                    width: dotSize,
                    height: dotSize
                )
                let dotPath = NSBezierPath(ovalIn: dotRect)
                let dotColor = (item.waitingSubagents ?? 0) > 0
                    ? NSColor(calibratedRed: 1.0, green: 0.92, blue: 0.35, alpha: 1.0)
                    : NSColor.white.withAlphaComponent(0.95)
                dotColor.setFill()
                dotPath.fill()
                NSColor.black.withAlphaComponent(0.45).setStroke()
                dotPath.lineWidth = 0.7
                dotPath.stroke()
            }

            return true
        }
        img.isTemplate = false
        return img
    }
}

// MARK: - SSE Client

final class SSEClient: NSObject, URLSessionDataDelegate {
    private let baseURL: URL
    private let onSnapshot: (SlotsSnapshot) -> Void
    private let onConnectionChanged: (Bool) -> Void

    private let queue = DispatchQueue(label: "com.codexmicro.agentstatusbar.sse")
    private lazy var delegateQueue: OperationQueue = {
        let q = OperationQueue()
        q.underlyingQueue = queue
        q.maxConcurrentOperationCount = 1
        return q
    }()

    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var reconnectWorkItem: DispatchWorkItem?
    private var buffer = Data()
    private var stopped = false

    init(
        baseURL: URL,
        onSnapshot: @escaping (SlotsSnapshot) -> Void,
        onConnectionChanged: @escaping (Bool) -> Void
    ) {
        self.baseURL = baseURL
        self.onSnapshot = onSnapshot
        self.onConnectionChanged = onConnectionChanged
        super.init()
    }

    func start() {
        queue.async {
            self.stopped = false
            self.connectLocked()
        }
    }

    func stop() {
        queue.async {
            self.stopped = true
            self.reconnectWorkItem?.cancel()
            self.reconnectWorkItem = nil
            self.task?.cancel()
            self.task = nil
            self.session?.invalidateAndCancel()
            self.session = nil
        }
    }

    private func connectLocked() {
        guard !stopped else { return }
        reconnectWorkItem?.cancel()
        reconnectWorkItem = nil
        task?.cancel()
        task = nil
        session?.invalidateAndCancel()
        session = nil
        buffer.removeAll(keepingCapacity: true)

        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3600
        config.timeoutIntervalForResource = 0
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        let sess = URLSession(configuration: config, delegate: self, delegateQueue: delegateQueue)
        self.session = sess

        let url = baseURL.appendingPathComponent("events")
        var req = URLRequest(url: url)
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let dataTask = sess.dataTask(with: req)
        self.task = dataTask
        dataTask.resume()
    }

    private func scheduleReconnectLocked() {
        guard !stopped, reconnectWorkItem == nil else { return }
        DispatchQueue.main.async {
            self.onConnectionChanged(false)
        }
        let item = DispatchWorkItem { [weak self] in
            guard let self = self else { return }
            self.reconnectWorkItem = nil
            self.connectLocked()
        }
        reconnectWorkItem = item
        queue.asyncAfter(deadline: .now() + 2.0, execute: item)
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        guard dataTask === self.task else {
            completionHandler(.cancel)
            return
        }
        if let http = response as? HTTPURLResponse, http.statusCode == 200 {
            DispatchQueue.main.async {
                self.onConnectionChanged(true)
            }
            completionHandler(.allow)
        } else {
            completionHandler(.cancel)
            scheduleReconnectLocked()
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard dataTask === self.task else { return }
        buffer.append(data)
        let delimiter = Data("\n\n".utf8)
        while let range = buffer.range(of: delimiter) {
            let chunk = buffer.subdata(in: 0..<range.lowerBound)
            buffer.removeSubrange(0..<range.upperBound)
            if let text = String(data: chunk, encoding: .utf8) {
                parseSSEEvent(text)
            }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard task === self.task else { return }
        self.task = nil
        if !stopped {
            scheduleReconnectLocked()
        }
    }

    private func parseSSEEvent(_ eventBlock: String) {
        var dataLines: [String] = []
        for rawLine in eventBlock.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(rawLine)
            if line.hasPrefix("data:") {
                let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                dataLines.append(payload)
            }
        }
        guard !dataLines.isEmpty else { return }
        let jsonStr = dataLines.joined(separator: "\n")
        guard let jsonData = jsonStr.data(using: .utf8) else { return }
        if let snapshot = try? JSONDecoder().decode(SlotsSnapshot.self, from: jsonData) {
            DispatchQueue.main.async {
                self.onConnectionChanged(true)
                self.onSnapshot(snapshot)
            }
        }
    }
}

// MARK: - App Delegate & Multi-Item Status Bar Controller

final class AgentStatusBarController: NSObject, NSApplicationDelegate {
    private let baseURL: URL
    private var sseClient: SSEClient!

    // One native NSStatusItem per active slot so macOS renders pills on EVERY
    // display menu bar AND routes clicks directly to the exact pill with zero
    // coordinate hit-testing.
    private var pillItems: [Int: NSStatusItem] = [:]
    private var activeSlotOrder: [Int] = []
    private var idleItem: NSStatusItem?

    private var slots: [SlotInfo] = []
    private var flashingSlots: Set<Int> = []
    private var connected: Bool = false
    private var pulseOn: Bool = true
    private var pulseTimer: Timer?

    private let pillItemLength: CGFloat = 24.0

    init(baseURL: URL) {
        self.baseURL = baseURL
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        syncStatusItems()

        sseClient = SSEClient(
            baseURL: baseURL,
            onSnapshot: { [weak self] snapshot in
                guard let self = self else { return }
                self.slots = snapshot.slots.sorted { $0.slot < $1.slot }
                self.updatePulseTimer()
                self.syncStatusItems()
            },
            onConnectionChanged: { [weak self] isConnected in
                guard let self = self else { return }
                if self.connected != isConnected {
                    self.connected = isConnected
                    self.renderImagesOnly()
                }
            }
        )
        sseClient.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        pulseTimer?.invalidate()
        sseClient?.stop()
        removeAllPillItems()
        removeIdleItem()
    }

    private func updatePulseTimer() {
        let hasWaiting = slots.contains { $0.status == "waiting" }
        if hasWaiting && pulseTimer == nil {
            pulseOn = true
            pulseTimer = Timer.scheduledTimer(withTimeInterval: 0.65, repeats: true) { [weak self] _ in
                guard let self = self else { return }
                self.pulseOn.toggle()
                self.renderWaitingPillsOnly()
            }
            if let timer = pulseTimer {
                RunLoop.main.add(timer, forMode: .common)
            }
        } else if !hasWaiting, let timer = pulseTimer {
            timer.invalidate()
            pulseTimer = nil
            pulseOn = true
            renderImagesOnly()
        }
    }

    private func removeAllPillItems() {
        for item in pillItems.values {
            NSStatusBar.system.removeStatusItem(item)
        }
        pillItems.removeAll()
        activeSlotOrder = []
    }

    private func removeIdleItem() {
        if let idle = idleItem {
            NSStatusBar.system.removeStatusItem(idle)
            idleItem = nil
        }
    }

    private func syncStatusItems() {
        if slots.isEmpty {
            removeAllPillItems()
            if idleItem == nil {
                let item = NSStatusBar.system.statusItem(withLength: 24.0)
                if let button = item.button {
                    button.imagePosition = .imageOnly
                    button.imageScaling = .scaleNone
                    button.target = self
                    button.action = #selector(handleIdleClick(_:))
                    button.sendAction(on: [.leftMouseUp, .rightMouseDown])
                }
                idleItem = item
            }
            renderIdleItem()
            return
        }

        removeIdleItem()

        let newOrder = slots.map { $0.slot }
        if newOrder != activeSlotOrder {
            // Recreate in reverse order because macOS inserts each new
            // NSStatusItem to the left of previously created items,
            // guaranteeing strict left-to-right 1 2 3 4 5 6 7 8 9 0 order.
            removeAllPillItems()
            for slotInfo in slots.reversed() {
                let item = NSStatusBar.system.statusItem(withLength: pillItemLength)
                if let button = item.button {
                    button.tag = slotInfo.slot
                    button.imagePosition = .imageOnly
                    button.imageScaling = .scaleNone
                    button.target = self
                    button.action = #selector(handlePillClick(_:))
                    button.sendAction(on: [.leftMouseUp, .rightMouseDown])
                }
                pillItems[slotInfo.slot] = item
            }
            activeSlotOrder = newOrder
        }

        renderImagesOnly()
    }

    private func renderIdleItem() {
        guard let button = idleItem?.button else { return }
        button.image = StatusStyle.renderIdleImage(connected: connected)
        button.toolTip = connected
            ? "Agent Status — No active sessions (click for menu)"
            : "Agent Status — Waiting for bridge at \(baseURL.absoluteString) (click for menu)"
    }

    private func renderImagesOnly() {
        if slots.isEmpty {
            renderIdleItem()
            return
        }
        for slotInfo in slots {
            updatePillButton(for: slotInfo)
        }
    }

    private func renderWaitingPillsOnly() {
        for slotInfo in slots where slotInfo.status == "waiting" {
            updatePillButton(for: slotInfo)
        }
    }

    private func updatePillButton(for slotInfo: SlotInfo) {
        guard let button = pillItems[slotInfo.slot]?.button else { return }
        button.image = StatusStyle.renderSinglePillImage(
            item: slotInfo,
            pulseOn: pulseOn,
            connected: connected,
            flashing: flashingSlots.contains(slotInfo.slot)
        )
        button.toolTip = "[\(slotInfo.key)] \(slotInfo.displayLabel) — \(slotInfo.statusLabel) (\(slotInfo.hostBadge))\nClick to switch window · Right-click for menu"
    }

    private func flashSlot(_ slot: Int) {
        flashingSlots.insert(slot)
        if let info = slots.first(where: { $0.slot == slot }) {
            updatePillButton(for: info)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.16) { [weak self] in
            guard let self = self else { return }
            self.flashingSlots.remove(slot)
            if let info = self.slots.first(where: { $0.slot == slot }) {
                self.updatePillButton(for: info)
            }
        }
    }

    @objc private func handleIdleClick(_ sender: NSStatusBarButton) {
        if let item = idleItem {
            showDropdownMenu(on: item)
        }
    }

    @objc private func handlePillClick(_ sender: NSStatusBarButton) {
        let slot = sender.tag
        guard let item = pillItems[slot] else { return }

        if let event = NSApp.currentEvent {
            let isRightClick = event.type == .rightMouseDown
                || event.type == .rightMouseUp
                || event.modifierFlags.contains(.control)
                || event.modifierFlags.contains(.option)
            if isRightClick || !connected {
                showDropdownMenu(on: item)
                return
            }
        }

        flashSlot(slot)
        // Let macOS complete menu-bar click handling before focusing target window/Space
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.03) { [weak self] in
            self?.postAction(path: "slots/\(slot)/focus")
        }
    }

    private func showDropdownMenu(on statusItem: NSStatusItem) {
        let menu = NSMenu()
        menu.autoenablesItems = false

        let headerTitle = connected
            ? (slots.isEmpty ? "Agent Status — No Active Sessions" : "Active Agent Sessions (\(slots.count))")
            : "Bridge Disconnected (\(baseURL.host ?? "127.0.0.1"):\(baseURL.port ?? 41718))"
        let header = NSMenuItem(title: headerTitle, action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        menu.addItem(NSMenuItem.separator())

        if !slots.isEmpty {
            for item in slots {
                let title = "[\(item.key)] \(item.displayLabel)  ·  \(item.statusLabel)"
                let row = NSMenuItem(title: title, action: #selector(menuFocusSlot(_:)), keyEquivalent: "")
                row.target = self
                row.representedObject = item.slot
                row.image = StatusStyle.menuDotImage(for: item.status)
                menu.addItem(row)
            }

            menu.addItem(NSMenuItem.separator())

            let clearMenuItem = NSMenuItem(title: "Clear Slot", action: nil, keyEquivalent: "")
            let clearSubmenu = NSMenu()
            for item in slots {
                let clearRow = NSMenuItem(
                    title: "Clear [\(item.key)] \(item.displayLabel)",
                    action: #selector(menuClearSlot(_:)),
                    keyEquivalent: ""
                )
                clearRow.target = self
                clearRow.representedObject = item.slot
                clearSubmenu.addItem(clearRow)
            }
            clearMenuItem.submenu = clearSubmenu
            menu.addItem(clearMenuItem)

            menu.addItem(NSMenuItem.separator())
        }

        let prevWin = NSMenuItem(title: "Switch to Previous Window", action: #selector(menuPreviousWindow), keyEquivalent: "")
        prevWin.target = self
        prevWin.isEnabled = connected
        menu.addItem(prevWin)

        let clearCompleted = NSMenuItem(title: "Clear Completed Slots", action: #selector(menuClearCompleted), keyEquivalent: "")
        clearCompleted.target = self
        clearCompleted.isEnabled = connected && slots.contains { $0.status == "complete" || $0.status == "idle" }
        menu.addItem(clearCompleted)

        let clearAll = NSMenuItem(title: "Clear All Slots", action: #selector(menuClearAll), keyEquivalent: "")
        clearAll.target = self
        clearAll.isEnabled = connected && !slots.isEmpty
        menu.addItem(clearAll)

        menu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: "Quit Agent Status Bar", action: #selector(menuQuit), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil
    }

    @objc private func menuFocusSlot(_ sender: NSMenuItem) {
        if let slot = sender.representedObject as? Int {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.03) { [weak self] in
                self?.postAction(path: "slots/\(slot)/focus")
            }
        }
    }

    @objc private func menuClearSlot(_ sender: NSMenuItem) {
        if let slot = sender.representedObject as? Int {
            postAction(path: "slots/\(slot)/clear")
        }
    }

    @objc private func menuPreviousWindow() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.03) { [weak self] in
            self?.postAction(path: "previous-window")
        }
    }

    @objc private func menuClearCompleted() {
        postAction(path: "slots/clear-completed")
    }

    @objc private func menuClearAll() {
        postAction(path: "slots/clear-all")
    }

    @objc private func menuQuit() {
        NSApp.terminate(nil)
    }

    private func postAction(path: String) {
        let url = baseURL.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 3.0
        URLSession.shared.dataTask(with: req).resume()
    }
}

// MARK: - Entry Point

func parseBaseURL() -> URL {
    var urlString = ProcessInfo.processInfo.environment["MF17_STATUS_API_URL"] ?? "http://127.0.0.1:41718"
    let args = CommandLine.arguments
    var idx = 1
    while idx < args.count {
        let arg = args[idx]
        if arg == "--url", idx + 1 < args.count {
            urlString = args[idx + 1]
            idx += 2
            continue
        }
        if arg == "--port", idx + 1 < args.count {
            urlString = "http://127.0.0.1:\(args[idx + 1])"
            idx += 2
            continue
        }
        idx += 1
    }
    return URL(string: urlString) ?? URL(string: "http://127.0.0.1:41718")!
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = AgentStatusBarController(baseURL: parseBaseURL())
app.delegate = controller
app.run()
