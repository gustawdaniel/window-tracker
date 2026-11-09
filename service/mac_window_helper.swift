import AppKit
import ApplicationServices
import Foundation

struct WindowResult: Codable {
    let app: String
    let bundle: String
    let pid: Int32
    let title: String
    let workspace: String
    let monitor: Int
    let idle_sec: Double
}

func getActiveWindow() -> WindowResult {
    let ws = NSWorkspace.shared
    
    // 1. Idle time via Quartz CGEventSource (seconds since last user interaction)
    let idleSec = CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: CGEventType(rawValue: ~0)!)
    
    guard let frontApp = ws.frontmostApplication else {
        return WindowResult(
            app: "Desktop",
            bundle: "",
            pid: 0,
            title: "Desktop",
            workspace: "mac",
            monitor: 0,
            idle_sec: idleSec
        )
    }
    
    let rawName = frontApp.localizedName ?? "Desktop"
    let bundleId = frontApp.bundleIdentifier ?? ""
    let pid = frontApp.processIdentifier
    
    // Normalize application name
    var appName = rawName
    let lowerBundle = bundleId.lowercased()
    let lowerName = rawName.lowercased()
    
    if lowerBundle.contains("antigravity") || (lowerName == "electron" && lowerBundle.contains("antigravity")) {
        appName = "Antigravity"
    } else if lowerBundle.contains("zen") {
        appName = "Zen"
    } else if lowerBundle.contains("ghostty") {
        appName = "Ghostty"
    } else if lowerBundle.contains("alacritty") {
        appName = "Alacritty"
    } else if lowerBundle.contains("iterm") {
        appName = "iTerm2"
    } else if lowerBundle.contains("terminal") {
        appName = "Terminal"
    } else if lowerBundle.contains("code") {
        appName = "Code"
    } else if lowerBundle.contains("cursor") {
        appName = "Cursor"
    } else if lowerBundle.contains("datagrip") {
        appName = "DataGrip"
    }
    
    // 2. Extract window title via Accessibility API (AXUIElement)
    var title = ""
    let appRef = AXUIElementCreateApplication(pid)
    var windowRef: AnyObject?
    
    // Try focused window first
    var axErr = AXUIElementCopyAttributeValue(appRef, kAXFocusedWindowAttribute as CFString, &windowRef)
    if axErr != .success || windowRef == nil {
        // Fallback to main window
        axErr = AXUIElementCopyAttributeValue(appRef, kAXMainWindowAttribute as CFString, &windowRef)
    }
    
    if let win = windowRef {
        let axWin = win as! AXUIElement
        var titleVal: AnyObject?
        if AXUIElementCopyAttributeValue(axWin, kAXTitleAttribute as CFString, &titleVal) == .success,
           let str = titleVal as? String {
            title = str
        }
    }
    
    // Fallback: iterate app's windows if focused window had no title
    if title.isEmpty {
        var windowsVal: AnyObject?
        if AXUIElementCopyAttributeValue(appRef, kAXWindowsAttribute as CFString, &windowsVal) == .success,
           let winList = windowsVal as? [AXUIElement] {
            for win in winList {
                var titleVal: AnyObject?
                if AXUIElementCopyAttributeValue(win, kAXTitleAttribute as CFString, &titleVal) == .success,
                   let str = titleVal as? String, !str.isEmpty {
                    title = str
                    break
                }
            }
        }
    }
    
    return WindowResult(
        app: appName,
        bundle: bundleId,
        pid: pid,
        title: title,
        workspace: "mac",
        monitor: 0,
        idle_sec: idleSec
    )
}

let res = getActiveWindow()
if let data = try? JSONEncoder().encode(res), let jsonStr = String(data: data, encoding: .utf8) {
    print(jsonStr)
}
