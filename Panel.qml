import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import "Model.js" as Model

Panel {
  id: root
  moduleName: "omarchy.monitor"
  ipcTarget: "omarchy.monitor"
  manageIpc: false

  // manageIpc: false so this panel can own the single IpcHandler the target
  // permits — needed for the brightness + state methods below.
  property int brightnessPercent: 0
  property int pendingBrightnessPercent: 0
  property bool brightnessSetQueued: false
  property bool brightnessAvailable: false
  property string internalMonitor: ""
  property string externalMonitor: ""
  property string focusedMonitor: ""
  property bool internalEnabled: false
  property bool mirrorEnabled: false
  property string monitorScale: ""
  property var displays: []
  property int enabledDisplayCount: 0

  // Carry sub-notch touchpad deltas between wheel events.
  property real wheelAccumulator: 0

  // Cursor model shared by keyboard and mouse. Sections:
  //   "monitors"   - expandable display rows; brightness + scale nest under
  //                  the expanded row. j/k walks displays; h/l walks scale
  //                  pills when that display is expanded.
  //   "monitorBrightness" - brightness slider for one display (selectedIndex
  //                  = display row index).
  //   "monitorScale" - scale pills for one display; selectedIndex = pill index,
  //                  scaleFocusMonitor names the target output.
  //   "miracastMode" / "miracastPos" / "miracastStream" / "miracast" / "miracastPeers"
  //   "textsize"   - global shell/GTK/terminal text size (not per-display).
  readonly property var miracastModeValues: ["mirror", "extend"]
  readonly property var miracastPosValues: ["left", "right", "above", "below"]
  readonly property var miracastStreamModeIds: {
    var out = []
    var modes = (miracast && miracast.streamModes) ? miracast.streamModes : []
    for (var i = 0; i < modes.length; i++) {
      if (modes[i] && modes[i].id) out.push(String(modes[i].id))
    }
    return out
  }
  // Mouse hover on a target updates root state via the components' `hovered`
  // signal so keyboard cursor and pointer share one highlight.
  readonly property var scalePresets: ["1", "1.25", "1.6", "2", "3", "4"]
  property string focusSection: "monitors"
  property int selectedIndex: 0
  property bool cursorActive: false
  // Accordion: at most one display shows nested brightness/scale controls.
  property string expandedMonitor: ""
  // Which monitor's scale-pill row currently has keyboard focus.
  property string scaleFocusMonitor: ""
  // After open, wait for a fresh monitor-state read before expanding — otherwise
  // we lock onto a stale focusedMonitor (usually eDP) from the last poll.
  property bool syncExpandToFocusPending: false

  // Resolve this plugin's install dir from Panel.qml so any clone id works
  // (Omarchy installs clones as <user>.monitor, not a fixed path).
  readonly property string miracastPluginDir: {
    var s = String(Qt.resolvedUrl("./"))
    if (s.indexOf("file://") === 0)
      s = decodeURIComponent(s.substring(7))
    while (s.length > 1 && s.charAt(s.length - 1) === "/")
      s = s.substring(0, s.length - 1)
    return s
  }
  readonly property string pluginBin: miracastPluginDir + "/bin"
  readonly property string miracastMonitorName: focusedMonitor !== "" ? focusedMonitor : "eDP-1"

  // Text size slider — curated macOS-style notches (px). The panel snaps to
  // these stops; the CLI (omarchy-display-text-size) accepts any integer in range.
  readonly property var textSizeStops: [9, 10, 11, 12, 14, 16, 20]
  // While a change is in flight, the chosen stop index overrides the live
  // base-size so the knob doesn't snap back during the file round-trip. -1 =
  // no pending change; follow Style.font.baseSize.
  property int textSizePreviewIndex: -1

  // A text-size change reflows the whole panel (both font and spacing scale),
  // which slides rows under a stationary pointer and fires synthetic hover.
  // While true, hover is not allowed to hijack the keyboard focus section —
  // otherwise h/l on the text-size slider can jump focus to another row.
  property bool reflowingText: false
  function markReflowing() {
    root.reflowingText = true
    reflowSettle.restart()
  }

  // DISPLAYS always hosts per-output brightness/scale, even with one panel.
  readonly property bool showDisplaysSection: displays.length > 0

  // Mode/position only matter while a session is live (and the virtual
  // display exists for Extend). Hide them when idle to save panel space.
  readonly property bool showMiracastSessionControls: !!(miracast && miracast.active)

  readonly property var visibleSections: {
    var list = []
    if (showDisplaysSection) list.push("monitors")
    if (showMiracastSessionControls) {
      list.push("miracastMode")
      if (miracast && miracast.mode === "extend") list.push("miracastPos")
      if (miracastStreamModeIds.length > 0) list.push("miracastStream")
    }
    list.push("miracast")
    if (miracast && miracast.peers && miracast.peers.length > 0) list.push("miracastPeers")
    list.push("textsize")
    return list
  }

  function sectionCount(section) {
    if (section === "textsize") return 0    // slider sentinel at -1
    if (section === "monitorBrightness") return 0
    if (section === "monitorScale") {
      var d = displayByName(scaleFocusMonitor)
      return d ? scaleValuesFor(d).length : 0
    }
    if (section === "monitors") return displays ? displays.length : 0
    if (section === "miracastMode") return miracastModeValues.length
    if (section === "miracastPos") return miracastPosValues.length
    if (section === "miracastStream") return miracastStreamModeIds.length
    if (section === "miracast") return 0    // action row sentinel at -1
    if (section === "miracastPeers")
      return (miracast && miracast.peers) ? miracast.peers.length : 0
    return 0
  }

  function sectionIsSingleRow(section) {
    // text size / nested brightness are lone sliders; scale/mode/pos sit horizontally;
    // miracast actions are one control row.
    return section === "textsize" || section === "monitorBrightness" || section === "monitorScale"
      || section === "miracast" || section === "miracastMode" || section === "miracastPos"
      || section === "miracastStream"
  }

  function sectionFirstIndex(section) {
    if (section === "textsize" || section === "miracast" || section === "monitorBrightness") return -1
    if (section === "miracastMode") return Math.max(0, miracastModeValues.indexOf(miracast.mode))
    if (section === "miracastPos") return Math.max(0, miracastPosValues.indexOf(miracast.extendPosition))
    if (section === "miracastStream") return Math.max(0, miracastStreamModeIds.indexOf(miracast.streamMode))
    if (section === "monitorScale") return Math.max(0, activeScaleIndexFor(displayByName(scaleFocusMonitor)))
    return 0
  }

  function displayByName(name) {
    var target = String(name || "")
    for (var i = 0; i < displays.length; i++) {
      if (displays[i] && displays[i].name === target) return displays[i]
    }
    return null
  }

  function scaleValuesFor(display) {
    if (!display) return scalePresets
    return Model.availableScales(scalePresets, display.width, display.height)
  }

  function displayBrightness(display) {
    if (!display) return root.brightnessPercent
    if (display.brightness !== undefined && display.brightness !== null)
      return Model.clampBrightness(display.brightness)
    if (display.name === root.focusedMonitor) return root.brightnessPercent
    return 50
  }

  function isExpanded(name) {
    return root.expandedMonitor !== "" && root.expandedMonitor === String(name || "")
  }

  function toggleExpanded(name) {
    var target = String(name || "")
    if (target === "") return
    root.expandedMonitor = root.expandedMonitor === target ? "" : target
  }

  function selectFocusedDisplayRow() {
    if (!showDisplaysSection) return
    root.focusSection = "monitors"
    root.selectedIndex = 0
    for (var i = 0; i < displays.length; i++) {
      if (displays[i] && displays[i].focused) {
        root.selectedIndex = i
        break
      }
    }
  }

  // preferFocus: expand focused when nothing usable is expanded.
  // forceFocus: always expand the currently focused output (open / focus change).
  function ensureExpandedMonitor(preferFocus, forceFocus) {
    if (!displays || displays.length === 0) {
      root.expandedMonitor = ""
      return
    }
    if (forceFocus) {
      var forced = root.focusedMonitor
      if (forced !== "" && displayByName(forced)) {
        root.expandedMonitor = forced
        return
      }
      for (var k = 0; k < displays.length; k++) {
        if (displays[k] && displays[k].focused) {
          root.expandedMonitor = displays[k].name
          return
        }
      }
    }
    if (root.expandedMonitor !== "") {
      for (var i = 0; i < displays.length; i++) {
        if (displays[i] && displays[i].name === root.expandedMonitor) return
      }
      // Previously expanded output disappeared — fall through and pick again.
    } else if (!preferFocus) {
      // User collapsed the accordion; don't force it back open on refresh.
      return
    }
    // Auto-expand the focused output (fallback: first enabled display).
    var focus = root.focusedMonitor
    if (focus !== "" && displayByName(focus)) {
      root.expandedMonitor = focus
      return
    }
    for (var j = 0; j < displays.length; j++) {
      if (displays[j] && displays[j].focused) {
        root.expandedMonitor = displays[j].name
        return
      }
    }
    for (var n = 0; n < displays.length; n++) {
      if (displays[n] && displays[n].enabled) {
        root.expandedMonitor = displays[n].name
        return
      }
    }
    root.expandedMonitor = displays[0] ? displays[0].name : ""
  }

  function moveCursor(delta) {
    // Nested brightness/scale sit under a display row — vertical nav returns
    // to that display header first, then continues through sections.
    if (focusSection === "monitorBrightness") {
      focusSection = "monitors"
      if (selectedIndex < 0) selectedIndex = 0
    } else if (focusSection === "monitorScale") {
      var idx = 0
      for (var i = 0; i < displays.length; i++) {
        if (displays[i] && displays[i].name === scaleFocusMonitor) { idx = i; break }
      }
      focusSection = "monitors"
      selectedIndex = idx
    }

    var sections = visibleSections
    if (!sections || sections.length === 0) return
    var sIdx = sections.indexOf(focusSection)
    if (sIdx < 0) {
      focusSection = sections[0]
      selectedIndex = sectionFirstIndex(focusSection)
      return
    }
    var inSingleRow = sectionIsSingleRow(focusSection)
    var max = inSingleRow ? 0 : sectionCount(focusSection) - 1

    if (delta > 0) {
      if (!inSingleRow && selectedIndex < max) { selectedIndex = selectedIndex + 1; return }
      if (sIdx < sections.length - 1) {
        focusSection = sections[sIdx + 1]
        selectedIndex = sectionFirstIndex(focusSection)
      }
    } else {
      if (!inSingleRow && selectedIndex > 0) { selectedIndex = selectedIndex - 1; return }
      if (sIdx > 0) {
        var prev = sections[sIdx - 1]
        focusSection = prev
        // Coming up from below — land on the last navigable row of the prev
        // section, or its sentinel for single-row sections.
        selectedIndex = sectionIsSingleRow(prev) ? sectionFirstIndex(prev) : sectionCount(prev) - 1
      }
    }
  }

  // h/l: walks scale / mode / position pills; brightness uses adjustBrightness.
  function moveCursorH(delta) {
    if (focusSection === "monitorScale") {
      var scales = scaleValuesFor(displayByName(scaleFocusMonitor))
      var next = selectedIndex + delta
      if (next < 0) next = 0
      if (next > scales.length - 1) next = scales.length - 1
      selectedIndex = next
      return
    }
    if (focusSection === "monitors" && selectedIndex >= 0 && selectedIndex < displays.length) {
      var row = displays[selectedIndex]
      if (row && isExpanded(row.name)) {
        root.scaleFocusMonitor = row.name
        root.focusSection = "monitorScale"
        root.selectedIndex = Math.max(0, activeScaleIndexFor(row))
        moveCursorH(delta)
      }
      return
    }
    if (focusSection === "miracastMode") {
      var modeNext = selectedIndex + delta
      if (modeNext < 0) modeNext = 0
      if (modeNext > miracastModeValues.length - 1) modeNext = miracastModeValues.length - 1
      selectedIndex = modeNext
      return
    }
    if (focusSection === "miracastPos") {
      var posNext = selectedIndex + delta
      if (posNext < 0) posNext = 0
      if (posNext > miracastPosValues.length - 1) posNext = miracastPosValues.length - 1
      selectedIndex = posNext
      return
    }
    if (focusSection === "miracastStream") {
      var streamNext = selectedIndex + delta
      if (streamNext < 0) streamNext = 0
      if (streamNext > miracastStreamModeIds.length - 1) streamNext = miracastStreamModeIds.length - 1
      selectedIndex = streamNext
    }
  }

  function adjustBrightness(delta) {
    var name = ""
    if (focusSection === "monitorBrightness" && selectedIndex >= 0 && selectedIndex < displays.length)
      name = displays[selectedIndex] ? displays[selectedIndex].name : ""
    else if (focusSection === "monitors" && selectedIndex >= 0 && selectedIndex < displays.length) {
      var row = displays[selectedIndex]
      if (row && isExpanded(row.name) && row.brightnessAvailable) name = row.name
    }
    if (name === "") return
    var display = displayByName(name)
    if (!display || !display.brightnessAvailable) return
    setBrightness(name, displayBrightness(display) + delta)
  }

  function activateCursor() {
    if (focusSection === "monitorScale") {
      var scales = scaleValuesFor(displayByName(scaleFocusMonitor))
      if (selectedIndex >= 0 && selectedIndex < scales.length)
        setScale(scaleFocusMonitor, scales[selectedIndex])
      return
    }
    if (focusSection === "miracastMode" && selectedIndex >= 0 && selectedIndex < miracastModeValues.length) {
      miracast.setMode(miracastModeValues[selectedIndex])
      return
    }
    if (focusSection === "miracastPos" && selectedIndex >= 0 && selectedIndex < miracastPosValues.length) {
      miracast.setExtendPosition(miracastPosValues[selectedIndex])
      return
    }
    if (focusSection === "miracastStream" && selectedIndex >= 0 && selectedIndex < miracastStreamModeIds.length) {
      miracast.setStreamMode(miracastStreamModeIds[selectedIndex])
      return
    }
    if (focusSection === "monitors" && selectedIndex >= 0 && selectedIndex < displays.length) {
      var d = displays[selectedIndex]
      if (d) toggleExpanded(d.name)
      return
    }
    if (focusSection === "miracast") {
      if (miracast.active) miracast.stopCast()
      else miracast.scanPeers()
      return
    }
    if (focusSection === "miracastPeers" && selectedIndex >= 0 && selectedIndex < miracast.peers.length) {
      var peer = miracast.peers[selectedIndex]
      if (peer) miracast.startCast(peer.mac)
    }
  }

  function clampCursor() {
    var sections = visibleSections
    if (!sections || !sections.length) return
    // Nested monitor controls aren't top-level sections — keep them as-is.
    if (focusSection === "monitorBrightness" || focusSection === "monitorScale") {
      if (focusSection === "monitorBrightness") {
        if (selectedIndex < 0 || selectedIndex >= displays.length) selectedIndex = 0
      } else {
        var count = sectionCount("monitorScale")
        if (count <= 0 || scaleFocusMonitor === "" || !displayByName(scaleFocusMonitor)) {
          focusSection = "monitors"
          selectedIndex = 0
        } else if (selectedIndex < 0 || selectedIndex >= count) {
          selectedIndex = sectionFirstIndex("monitorScale")
        }
      }
      return
    }
    if (sections.indexOf(focusSection) < 0) {
      focusSection = sections[0]
      selectedIndex = sectionFirstIndex(focusSection)
      return
    }
    var count = sectionCount(focusSection)
    if (sectionIsSingleRow(focusSection)) {
      if (focusSection === "textsize" || focusSection === "miracast") selectedIndex = -1
      else if (selectedIndex < 0 || selectedIndex >= count) selectedIndex = sectionFirstIndex(focusSection)
      return
    }
    if (count === 0) {
      var sIdx = sections.indexOf(focusSection)
      focusSection = sIdx > 0 ? sections[sIdx - 1] : sections[0]
      selectedIndex = sectionFirstIndex(focusSection)
      return
    }
    if (selectedIndex > count - 1) selectedIndex = count - 1
    if (selectedIndex < 0) selectedIndex = 0
  }

  // Keep the keyboard-focused row inside the viewport when the panel grows
  // taller than its allotted height (lots of displays). Mirrors audio's
  // ensureCursorVisible helper.
  function ensureCursorVisible(item) {
    if (!item || !scrollArea) return
    var flick = scrollArea.contentItem
    if (!flick || flick.contentY === undefined) return
    var pt = item.mapToItem(flick.contentItem || flick, 0, 0)
    var top = pt.y
    var bottom = top + (item.height || 0)
    var viewTop = flick.contentY
    var viewBottom = viewTop + flick.height
    var margin = 6
    if (top < viewTop + margin) flick.contentY = Math.max(0, top - margin)
    else if (bottom > viewBottom - margin)
      flick.contentY = bottom + margin - flick.height
  }

  function brightnessIpc(percent) {
    var value = Number(percent)
    root.setBrightness(root.focusedMonitor, value)
    return "got " + root.pendingBrightnessPercent
  }

  function stateIpc() {
    return JSON.stringify({
      brightness: root.brightnessPercent,
      brightnessAvailable: root.brightnessAvailable,
      focusedMonitor: root.focusedMonitor,
      scale: root.monitorScale,
      displays: root.displays
    })
  }

  IpcHandler {
    target: "omarchy.monitor"

    function brightness(percent: string): string { return root.brightnessIpc(percent) }
    function state(): string { return root.stateIpc() }
    function open() { root.open() }
    function close() { root.close() }
    function toggle() { root.toggle() }
    function show() { root.open() }
    function hide() { root.close() }
  }

  function refresh() {
    if (!stateProc.running) stateProc.running = true
  }

  // Pending brightness target monitor for debounced / queued writes.
  property string brightnessTargetMonitor: ""

  function setBrightness(monitorName, value) {
    var name = String(monitorName || root.focusedMonitor || "")
    var percent = Model.clampBrightness(value)
    root.brightnessTargetMonitor = name
    root.pendingBrightnessPercent = percent
    if (name === root.focusedMonitor || name === "")
      root.brightnessPercent = percent
    updateDisplayBrightness(name, percent)

    if (setBrightnessProc.running) {
      root.brightnessSetQueued = true
      return
    }

    root.brightnessSetQueued = false
    setBrightnessProc.command = ["omarchy-brightness-display", "--no-osd", "--monitor", name, percent + "%"]
    setBrightnessProc.running = true
  }

  function previewBrightness(monitorName, value) {
    var name = String(monitorName || root.focusedMonitor || "")
    var percent = Model.clampBrightness(value)
    root.brightnessTargetMonitor = name
    if (name === root.focusedMonitor || name === "")
      root.brightnessPercent = percent
    updateDisplayBrightness(name, percent)
    brightnessDebounce.restart()
  }

  function updateDisplayBrightness(name, percent) {
    var target = String(name || "")
    if (target === "") return
    var next = []
    for (var i = 0; i < displays.length; i++) {
      var src = displays[i]
      if (!src) continue
      var d = {
        name: src.name,
        enabled: src.enabled,
        focused: src.focused,
        width: src.width,
        height: src.height,
        scale: src.scale,
        refreshRate: src.refreshRate,
        x: src.x,
        y: src.y,
        brightness: src.brightness,
        brightnessAvailable: src.brightnessAvailable
      }
      if (d.name === target) {
        d.brightness = Model.clampBrightness(percent)
        d.brightnessAvailable = true
      }
      next.push(d)
    }
    root.displays = next
  }

  function showBrightnessOsd(percent) {
    if (!bar || !bar.shell) return
    bar.shell.summon("omarchy.osd", JSON.stringify({
      icon: "brightness",
      value: percent
    }))
  }

  function normalizeScale(scale) {
    return Model.normalizeScale(scale)
  }

  function activeScaleIndexFor(display) {
    if (!display) return -1
    var current = display.scale !== undefined ? display.scale : (display.focused ? monitorScale : "")
    return Model.matchingScaleIndex(scaleValuesFor(display), current, display.width, display.height)
  }

  function effectiveScaleFor(display, scale) {
    if (!display) return normalizeScale(scale)
    return Model.cleanScale(scale, display.width, display.height)
  }

  // Playful mood-name for a given brightness percent. Bands intentionally
  // span ~10–20 points so casual tweaks change the label, while small
  // nudges within one band don't.
  function brightnessName(percent) {
    return Model.brightnessName(percent)
  }

  function updateDisplays(displaysJson) {
    var parsed = Model.parseDisplays(displaysJson)
    root.displays = parsed.displays
    root.enabledDisplayCount = parsed.enabledDisplayCount
  }

  function toggleDisplay(name, enabled) {
    if (!name) return
    if (enabled && root.enabledDisplayCount <= 1) return

    actionProc.command = ["hyprctl", "keyword", "monitor", name + (enabled ? ",disable" : ",preferred,auto,auto")]
    if (!actionProc.running) actionProc.running = true
  }

  // Queue scale while actionProc is busy — otherwise a mid-cast scale click
  // only updates .command and never re-runs (capture stays paused/dead).
  property string pendingScaleMonitor: ""
  property string pendingScaleValue: ""
  property bool scaleQueued: false

  function setScale(monitorName, scale) {
    var name = String(monitorName || root.focusedMonitor || "")
    if (name === "") return
    // Optimistic UI update so the active pill changes immediately.
    updateDisplayScale(name, scale)
    if (actionProc.running) {
      root.pendingScaleMonitor = name
      root.pendingScaleValue = String(scale)
      root.scaleQueued = true
      return
    }
    root.scaleQueued = false
    actionProc.command = [root.pluginBin + "/monitor-scale", name, String(scale)]
    actionProc.running = true
  }

  function updateDisplayScale(name, scale) {
    var target = String(name || "")
    if (target === "") return
    var next = []
    for (var i = 0; i < displays.length; i++) {
      var src = displays[i]
      if (!src) continue
      var d = {
        name: src.name,
        enabled: src.enabled,
        focused: src.focused,
        width: src.width,
        height: src.height,
        scale: src.scale,
        refreshRate: src.refreshRate,
        x: src.x,
        y: src.y,
        brightness: src.brightness,
        brightnessAvailable: src.brightnessAvailable,
        miracast: src.miracast
      }
      if (d.name === target) d.scale = Number(scale)
      next.push(d)
    }
    root.displays = next
    if (target === root.focusedMonitor) root.monitorScale = root.normalizeScale(scale)
  }

  // ---- Text size (shell base font + GTK text-scaling, via one CLI) ----
  function nearestTextStop(px) {
    var best = 0
    var bestDist = 1e9
    for (var i = 0; i < textSizeStops.length; i++) {
      var d = Math.abs(textSizeStops[i] - px)
      if (d < bestDist) { bestDist = d; best = i }
    }
    return best
  }

  // Effective stop index: the pending choice while a change is in flight,
  // otherwise whatever Style's live base-size rounds to.
  function currentTextIndex() {
    return textSizePreviewIndex >= 0 ? textSizePreviewIndex : nearestTextStop(Style.font.baseSize)
  }

  // px shown in the header: the pending stop if any, else the true base-size
  // (which may be an off-notch value set from the CLI).
  function displayedTextPx() {
    return textSizePreviewIndex >= 0 ? textSizeStops[textSizePreviewIndex] : Style.font.baseSize
  }

  function setTextSize(px) {
    textScaleProc.command = ["omarchy-display-text-size", String(px)]
    if (!textScaleProc.running) textScaleProc.running = true
  }

  function adjustTextSize(deltaSteps) {
    var idx = currentTextIndex() + deltaSteps
    if (idx < 0) idx = 0
    if (idx > textSizeStops.length - 1) idx = textSizeStops.length - 1
    markReflowing()
    textSizePreviewIndex = idx
    setTextSize(textSizeStops[idx])
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  MiracastService {
    id: miracast
    pluginDir: root.miracastPluginDir
    monitorName: root.miracastMonitorName
  }

  Connections {
    target: miracast
    function onPeersChanged() { root.clampCursor() }
    function onPhaseChanged() { root.clampCursor() }
  }

  Component.onCompleted: refresh()

  // KeyboardPanel primes focus at open-time, so SUPER-bound IPC summons land
  // with j/k ready to navigate. Keep a default landing point, but don't paint
  // the cursor until hover or the first navigation key.
  onOpenedChanged: {
    if (opened) {
      // Defer accordion expand until monitor-state returns fresh focus.
      root.syncExpandToFocusPending = true
      refresh()
      miracast.refresh()
      if (showDisplaysSection) {
        focusSection = "monitors"
        selectedIndex = 0
      } else if (showMiracastSessionControls) {
        focusSection = "miracastMode"
        selectedIndex = Math.max(0, miracastModeValues.indexOf(miracast.mode))
      } else {
        focusSection = "miracast"
        selectedIndex = -1
      }
      cursorActive = false
    } else {
      root.syncExpandToFocusPending = false
    }
  }

  onBrightnessAvailableChanged: clampCursor()
  onDisplaysChanged: {
    if (!root.syncExpandToFocusPending)
      ensureExpandedMonitor(false)
    clampCursor()
    // Position pills follow live geometry, not a stale settings.json value.
    if (miracast)
      miracast.syncExtendPositionFromDisplays(root.displays)
  }

  // Status polls rewrite settings-backed fields; re-sync position from layout
  // whenever Miracast status refreshes while Extend is up.
  Connections {
    target: miracast
    function onPhaseChanged() {
      if (miracast && miracast.mode === "extend" && miracast.active)
        miracast.syncExtendPositionFromDisplays(root.displays)
    }
    function onStreamingChanged() {
      if (miracast && miracast.streaming)
        miracast.syncExtendPositionFromDisplays(root.displays)
    }
  }
  onFocusedMonitorChanged: {
    // While the panel is open, keep the accordion on the focused output
    // (e.g. opened from the Miracast display after state catches up).
    if (root.opened)
      ensureExpandedMonitor(true, true)
    else if (root.expandedMonitor === "" || !displayByName(root.expandedMonitor))
      ensureExpandedMonitor(true)
  }
  onVisibleSectionsChanged: clampCursor()

  // Only poll while the panel is open; the bar glyph tracks monitor count via
  // Quickshell.screens, and open-time refresh + Component.onCompleted cover the
  // rest. External brightness changes are reflected whenever the panel is open.
  Timer {
    interval: 5000
    running: root.opened
    repeat: true
    onTriggered: root.refresh()
  }

  // Keep Extend position pills aligned with Hyprland after eDP scale / lua reload.
  Timer {
    interval: 1500
    running: root.opened && !!(miracast && miracast.active && miracast.mode === "extend")
    repeat: true
    onTriggered: {
      if (miracast)
        miracast.syncExtendPositionFromDisplays(root.displays)
    }
  }

  Process {
    id: stateProc
    command: [root.pluginBin + "/monitor-state"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text || "").split("\n")
        var brightness = String(lines[0] || "").trim()
        root.brightnessAvailable = brightness !== "unavailable" && brightness !== ""
        root.brightnessPercent = root.brightnessAvailable ? Math.max(0, Math.min(100, parseInt(brightness, 10))) : 0
        root.internalMonitor = String(lines[1] || "").trim()
        root.externalMonitor = String(lines[2] || "").trim()
        root.internalEnabled = String(lines[3] || "").trim() !== ""
        root.mirrorEnabled = String(lines[4] || "").trim() === root.externalMonitor && root.externalMonitor !== ""
        root.focusedMonitor = String(lines[5] || "").trim()
        root.monitorScale = root.normalizeScale(String(lines[6] || "").trim())
        root.updateDisplays(String(lines[7] || "[]").trim())
        if (root.opened && root.syncExpandToFocusPending) {
          root.syncExpandToFocusPending = false
          root.ensureExpandedMonitor(true, true)
          root.selectFocusedDisplayRow()
        }
      }
    }
  }

  Timer {
    id: brightnessDebounce
    interval: 180
    repeat: false
    onTriggered: root.setBrightness(root.brightnessTargetMonitor || root.focusedMonitor, root.pendingBrightnessPercent)
  }

  Process {
    id: setBrightnessProc
    stdout: StdioCollector { waitForEnd: true }
    // Do NOT call refresh() after a brightness set completes — local state is
    // authoritative. External changes still arrive via the 5s refresh.
    onRunningChanged: {
      if (running) return
      if (root.brightnessSetQueued) {
        root.setBrightness(root.brightnessTargetMonitor || root.focusedMonitor, root.pendingBrightnessPercent)
      }
    }
  }

  Process {
    id: actionProc
    stdout: StdioCollector { waitForEnd: true }
    onRunningChanged: {
      if (running) return
      if (root.scaleQueued) {
        var name = root.pendingScaleMonitor
        var scale = root.pendingScaleValue
        root.scaleQueued = false
        root.pendingScaleMonitor = ""
        root.pendingScaleValue = ""
        if (name !== "" && scale !== "") {
          actionProc.command = [root.pluginBin + "/monitor-scale", name, scale]
          actionProc.running = true
          return
        }
      }
      root.refresh()
    }
  }

  // Applies text size via the CLI, which rewrites the shell override file;
  // Style picks the new base-size up through its own file watch, so there's
  // nothing to refresh here.
  Process {
    id: textScaleProc
    stdout: StdioCollector { waitForEnd: true }
  }

  // Clears the hover-suppression flag once the reflow triggered by a text-size
  // change has settled.
  Timer {
    id: reflowSettle
    interval: 300
    repeat: false
    onTriggered: root.reflowingText = false
  }

  // Once Style's base-size catches up to the pending choice, drop the preview
  // so the slider tracks the live value again. The change itself reflows the
  // panel, so suppress hover for a beat while it lands.
  Connections {
    target: Style
    function onFontBaseSizeChanged() {
      root.markReflowing()
      if (root.textSizePreviewIndex >= 0
          && root.nearestTextStop(Style.font.baseSize) === root.textSizePreviewIndex)
        root.textSizePreviewIndex = -1
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // Match stock Display: always use the normal bar foreground. Miracast
    // state is shown only via the small overlay, never by greying the icon.
    text: ""
    active: false
    useActiveColor: false
    iconComponent: Component {
      MiracastBarIcon {
        iconSize: Style.bar.iconFont
        color: button.foreground
        phase: miracast.phase
        multiDisplay: Quickshell.screens.length > 1 || root.displays.length > 1
        fontFamily: button.fontFamily
      }
    }
    onPressed: function(b) { root.toggle() }
    onWheelMoved: function(delta) {
      if (!root.brightnessAvailable) return
      var wheel = Util.wheelSteps(root.wheelAccumulator, delta)
      root.wheelAccumulator = wheel.remainder
      if (wheel.steps === 0) return
      root.setBrightness(root.focusedMonitor, root.brightnessPercent + wheel.steps * 5)
      root.showBrightnessOsd(root.brightnessPercent)
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(panelColumn.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        if (dy !== 0) root.moveCursor(dy)
        else if (dx !== 0) {
          if (root.focusSection === "monitorBrightness") root.adjustBrightness(dx * 5)
          else if (root.focusSection === "textsize") root.adjustTextSize(dx)
          else if (root.focusSection === "monitors" || root.focusSection === "monitorScale"
                   || root.focusSection === "miracastMode" || root.focusSection === "miracastPos"
                   || root.focusSection === "miracastStream")
            root.moveCursorH(dx)
        }
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "s" || t === "S") miracast.scanPeers()
        else if (t === "f" || t === "F") miracast.openFirewall()
        else if (t === "d" || t === "D") miracast.runDoctor()
        else if (t === "c" || t === "C") miracast.startCast("")
        else if (t === "x" || t === "X") miracast.stopCast()
        else if (t === "m" || t === "M") miracast.setMode("mirror")
        else if (t === "e" || t === "E") miracast.setMode("extend")
      }

      ScrollView {
        id: scrollArea
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: panelColumn.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        Binding {
          target: scrollArea.contentItem
          property: "interactive"
          value: panelColumn.implicitHeight > scrollArea.height
        }

        Column {
          id: panelColumn
          width: scrollArea.availableWidth
          spacing: Style.space(14)

          // ---------- Hero: display icon · title/status ----------
          Item {
            width: parent.width
            implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight)

            MiracastBarIcon {
              id: heroIcon
              iconSize: Style.font.display
              width: iconSize
              height: iconSize
              color: root.bar.foreground
              phase: miracast.phase
              multiDisplay: root.displays.length > 1
              fontFamily: root.bar.fontFamily
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            Column {
              id: heroLabels
              anchors.left: heroIcon.right
              anchors.leftMargin: Style.space(14)
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Text {
                text: miracast.streaming && miracast.connectedLabel !== ""
                      ? miracast.connectedLabel
                      : "Display"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
                elide: Text.ElideRight
                width: parent.width
              }

              Text {
                id: heroLabel
                text: {
                  var summary = Model.miracastConnectionSummary(
                    miracast.phase, miracast.lastPeerName, miracast.lastPeerMac, miracast.mode)
                  if (summary !== "") return summary.toUpperCase()
                  if (root.brightnessAvailable) {
                    return root.brightnessName(root.brightnessPercent).toUpperCase()
                  }
                  return "FIXED BRIGHTNESS"
                }
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.2
                elide: Text.ElideRight
                width: parent.width
              }
            }
          }

          // ---------- Displays (top) ----------
          PanelSeparator {
            visible: root.showDisplaysSection
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: root.showDisplaysSection

            PanelSectionHeader {
              text: "DISPLAYS"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
            }

            Repeater {
              model: root.displays

              MonitorRow {
                required property var modelData
                required property int index

                width: panelColumn.width
                display: modelData
                rowIndex: index
              }
            }
          }

          // ---------- Miracast / Wi-Fi Display ----------
          PanelSeparator {
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(8)

            Item {
              width: parent.width
              implicitHeight: Math.max(miracastHeader.implicitHeight, miracastPhase.implicitHeight)

              PanelSectionHeader {
                id: miracastHeader
                text: "MIRACAST"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                id: miracastPhase
                text: Model.miracastPhaseLabel(miracast.phase).toUpperCase()
                color: miracast.active ? root.bar.foreground : Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                anchors.right: parent.right
                anchors.rightMargin: Style.space(6)
                anchors.verticalCenter: parent.verticalCenter
              }
            }

            Text {
              visible: miracast.connectedLabel !== "" || miracast.active
              width: parent.width
              text: {
                if (miracast.streaming)
                  return "Connected to " + (miracast.connectedLabel || "Miracast sink")
                    + " · " + miracast.modeLabel
                if (miracast.connecting)
                  return "Connecting to " + (miracast.connectedLabel || "Miracast sink") + "…"
                if (miracast.connectedLabel !== "")
                  return "Last device: " + miracast.connectedLabel
                return ""
              }
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            Text {
              // While connected, the line above is enough — don't also show
              // idle/doctor hints like "Ready to cast".
              readonly property string detail: {
                if (miracast.lastError !== "" && miracast.actionStatus === "")
                  return miracast.lastError
                if (miracast.actionStatus !== "") {
                  if (miracast.active && String(miracast.actionStatus).indexOf("Ready") === 0)
                    return ""
                  return miracast.actionStatus
                }
                if (miracast.active)
                  return ""
                return miracast.statusText
              }
              visible: detail !== ""
              width: parent.width
              text: detail
              color: miracast.lastError !== "" && miracast.actionStatus === ""
                     ? (root.bar.urgent || root.bar.foreground)
                     : Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Column {
              visible: root.showMiracastSessionControls
              width: parent.width
              spacing: Style.space(8)

              PanelSectionHeader {
                text: "CAST MODE"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
              }

              Grid {
                id: miracastModeRow
                width: parent.width
                columns: root.miracastModeValues.length
                spacing: Style.spacing.xs
                readonly property real cellWidth: root.miracastModeValues.length > 0
                  ? (width - spacing * (columns - 1)) / columns
                  : 0

                Repeater {
                  model: root.miracastModeValues
                  MiracastModePill {
                    required property string modelData
                    required property int index
                    modeValue: modelData
                    modeIndex: index
                    width: miracastModeRow.cellWidth
                  }
                }
              }

              Column {
                visible: miracast.mode === "extend"
                width: parent.width
                spacing: Style.space(8)

                PanelSectionHeader {
                  text: "EXTEND POSITION"
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                }

                Text {
                  visible: miracast.positionWarning !== ""
                  width: parent.width
                  text: miracast.positionWarning
                  color: root.bar.urgent || root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap

                  PanelToolTip {
                    visible: miracast.positionWarning !== ""
                    delay: 0
                    text: miracast.positionWarning
                  }
                }

                Grid {
                  id: miracastPosRow
                  width: parent.width
                  columns: root.miracastPosValues.length
                  spacing: Style.spacing.xs
                  readonly property real cellWidth: root.miracastPosValues.length > 0
                    ? (width - spacing * (columns - 1)) / columns
                    : 0

                  Repeater {
                    model: root.miracastPosValues
                    MiracastPosPill {
                      required property string modelData
                      required property int index
                      posValue: modelData
                      posIndex: index
                      width: miracastPosRow.cellWidth
                    }
                  }
                }
              }

              Column {
                visible: root.miracastStreamModeIds.length > 0
                width: parent.width
                spacing: Style.space(8)

                PanelSectionHeader {
                  text: "STREAM MODE"
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                }

                Grid {
                  id: miracastStreamRow
                  width: parent.width
                  columns: Math.min(root.miracastStreamModeIds.length, 4)
                  spacing: Style.spacing.xs
                  readonly property real cellWidth: columns > 0
                    ? (width - spacing * (columns - 1)) / columns
                    : 0

                  Repeater {
                    model: root.miracastStreamModeIds
                    MiracastStreamModePill {
                      required property string modelData
                      required property int index
                      modeId: modelData
                      modeIndex: index
                      width: miracastStreamRow.cellWidth
                    }
                  }
                }
              }
            }

            CursorSurface {
              id: miracastActionsRow
              width: parent.width
              implicitHeight: miracastActions.implicitHeight + Style.spacing.controlGap
              hasCursor: root.cursorActive && root.focusSection === "miracast" && root.selectedIndex === -1
              onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(miracastActionsRow)
              foreground: root.bar.foreground
              outline: true

              Row {
                id: miracastActions
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.space(8)
                anchors.rightMargin: Style.space(8)
                spacing: Style.space(8)

                PanelActionButton {
                  iconText: "󰍉"
                  tooltipText: "Scan sinks (S)"
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  enabled: !miracast.busy
                  onClicked: miracast.scanPeers()
                }
                PanelActionButton {
                  iconText: "󰈀"
                  tooltipText: "Open firewall (F)"
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  enabled: !miracast.busy
                  onClicked: miracast.openFirewall()
                }
                PanelActionButton {
                  iconText: "󰒓"
                  tooltipText: "Doctor (D)"
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  enabled: !miracast.busy
                  onClicked: miracast.runDoctor()
                }
                Item { width: Style.space(8); height: 1 }
                PanelActionButton {
                  iconText: miracast.active ? "󰓛" : "󰑐"
                  tooltipText: miracast.active
                    ? "Stop (X)"
                    : ("Reconnect to last device"
                       + (miracast.connectedLabel !== ""
                          ? " (" + miracast.connectedLabel + ")"
                          : "")
                       + " (C)")
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  enabled: !miracast.busy
                  onClicked: miracast.active ? miracast.stopCast() : miracast.startCast("")
                }
              }

              HoverHandler {
                onHoveredChanged: if (hovered && !root.reflowingText) {
                  root.cursorActive = true
                  root.focusSection = "miracast"
                  root.selectedIndex = -1
                }
              }
            }

            Column {
              visible: miracast.peers.length > 0
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: miracast.peers
                MiracastPeerRow {
                  required property var modelData
                  required property int index
                  width: panelColumn.width
                  peer: modelData
                  rowIndex: index
                }
              }
            }
          }

          // ---------- Text size (global) ----------
          PanelSeparator {
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            Item {
              width: parent.width
              implicitHeight: Math.max(textSizeHeader.implicitHeight, textSizePx.implicitHeight)

              PanelSectionHeader {
                id: textSizeHeader
                text: "TEXT SIZE"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                id: textSizePx
                text: (textSizeSlider.dragging
                       ? root.textSizeStops[Math.round(textSizeSlider.liveValue)]
                       : root.displayedTextPx()) + "px"
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                anchors.right: parent.right
                anchors.rightMargin: Style.space(6)
                anchors.verticalCenter: parent.verticalCenter
              }
            }

            CursorSurface {
              id: textSizeRow
              width: parent.width
              height: textSizeSlider.implicitHeight + Style.spacing.controlGap
              hasCursor: root.cursorActive && root.focusSection === "textsize" && root.selectedIndex === -1
              onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(textSizeRow)
              foreground: root.bar.foreground
              outline: true

              PanelSlider {
                id: textSizeSlider
                bar: root.bar
                anchors.fill: parent
                anchors.leftMargin: Style.space(6)
                anchors.rightMargin: Style.space(6)
                minimum: 0
                maximum: root.textSizeStops.length - 1
                step: 1
                integer: true
                tickCount: root.textSizeStops.length
                value: root.currentTextIndex()
                onReleased: function(v) { root.setTextSize(root.textSizeStops[Math.round(v)]) }
              }

              HoverHandler {
                onHoveredChanged: if (hovered && !root.reflowingText) {
                  root.cursorActive = true
                  root.focusSection = "textsize"
                  root.selectedIndex = -1
                }
              }
            }
          }

          Item {
            width: parent.width
            height: Style.space(4)
          }
        }
      }
    }
  }

  component ScalePill: Button {
    id: pill
    required property string scaleValue
    required property int scaleIndex
    required property var display

    text: root.effectiveScaleFor(display, scaleValue) + "x"
    fontSize: Style.font.caption
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.sm
    verticalPadding: Style.spacing.controlPaddingY
    bordered: true

    active: root.activeScaleIndexFor(display) === scaleIndex
    hasCursor: root.cursorActive
      && root.focusSection === "monitorScale"
      && root.scaleFocusMonitor === (display ? display.name : "")
      && root.selectedIndex === scaleIndex

    onClicked: root.setScale(display ? display.name : "", scaleValue)
    onHovered: function(isHovered) {
      if (!isHovered || root.reflowingText || !display) return
      root.cursorActive = true
      root.focusSection = "monitorScale"
      root.scaleFocusMonitor = display.name
      root.selectedIndex = pill.scaleIndex
    }
  }

  component MiracastModePill: Button {
    id: modePill
    required property string modeValue
    required property int modeIndex

    text: modeValue === "extend" ? "Extend" : "Mirror"
    fontSize: Style.font.caption
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.sm
    verticalPadding: Style.spacing.controlPaddingY
    bordered: true

    active: miracast.mode === modeValue
    hasCursor: root.cursorActive && root.focusSection === "miracastMode" && root.selectedIndex === modeIndex
    // Allow selecting a mode anytime; applying to a live session restarts the cast.
    enabled: true

    onClicked: miracast.setMode(modeValue)
    onHovered: function(isHovered) {
      if (!isHovered || root.reflowingText) return
      root.cursorActive = true
      root.focusSection = "miracastMode"
      root.selectedIndex = modePill.modeIndex
    }
  }

  component MiracastPosPill: Button {
    id: posPill
    required property string posValue
    required property int posIndex

    text: posValue === "left" ? "Left"
          : posValue === "above" ? "Above"
          : posValue === "below" ? "Below"
          : "Right"
    fontSize: Style.font.caption
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.sm
    verticalPadding: Style.spacing.controlPaddingY
    bordered: true

    active: miracast.extendPosition === posValue
    hasCursor: root.cursorActive && root.focusSection === "miracastPos" && root.selectedIndex === posIndex
    enabled: true

    onClicked: miracast.setExtendPosition(posValue)
    onHovered: function(isHovered) {
      if (!isHovered || root.reflowingText) return
      root.cursorActive = true
      root.focusSection = "miracastPos"
      root.selectedIndex = posPill.posIndex
    }
  }

  component MiracastStreamModePill: Button {
    id: streamPill
    required property string modeId
    required property int modeIndex

    text: miracast.streamModeLabel(modeId)
    fontSize: Style.font.caption
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.sm
    verticalPadding: Style.spacing.controlPaddingY
    bordered: true

    active: miracast.streamMode === modeId
    hasCursor: root.cursorActive && root.focusSection === "miracastStream" && root.selectedIndex === modeIndex
    enabled: !miracast.busy

    onClicked: miracast.setStreamMode(modeId)
    onHovered: function(isHovered) {
      if (!isHovered || root.reflowingText) return
      root.cursorActive = true
      root.focusSection = "miracastStream"
      root.selectedIndex = streamPill.modeIndex
    }
  }

  component MonitorRow: Column {
    id: monitorRow
    required property var display
    required property int rowIndex

    readonly property bool isFocused: display && display.focused
    readonly property bool canToggle: display && (!display.enabled || root.enabledDisplayCount > 1)
    readonly property bool expanded: display && root.isExpanded(display.name)
    readonly property var scaleValues: root.scaleValuesFor(display)
    readonly property bool showBrightness: display && display.brightnessAvailable === true && display.enabled

    width: parent ? parent.width : 0
    spacing: Style.space(6)

    CursorSurface {
      id: monitorHeader
      width: parent.width
      hasCursor: root.cursorActive && root.focusSection === "monitors" && root.selectedIndex === monitorRow.rowIndex
      onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(monitorHeader)
      current: monitorRow.isFocused
      foreground: root.bar.foreground
      fill: Style.hoverFillFor(root.bar.foreground, Color.accent)
      currentFill: Style.selectedFillFor(root.bar.foreground, Color.accent)
      implicitHeight: headerInner.implicitHeight + Style.spacing.xl
      opacity: monitorRow.display && monitorRow.display.enabled ? 1.0 : 0.55

      Row {
        id: headerInner
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Style.space(6)
        anchors.rightMargin: Style.space(6)
        spacing: Style.space(8)

        // Collapsed: right chevron (󰅂). Expanded: down chevron (󰅀).
        Text {
          text: monitorRow.expanded ? "󰅀" : "󰅂"
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          width: Style.space(16)
          horizontalAlignment: Text.AlignHCenter
          anchors.verticalCenter: parent.verticalCenter
        }

        Text {
          text: "󰍹"
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.title
          width: Style.space(22)
          horizontalAlignment: Text.AlignHCenter
          anchors.verticalCenter: parent.verticalCenter
        }

        Text {
          text: {
            var bits = [monitorRow.display.name]
            if (monitorRow.display.miracast) bits.push("Miracast")
            if (monitorRow.display.focused) bits.push("focused")
            if (!monitorRow.display.enabled) bits.push("off")
            return bits.join(" · ")
          }
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
          width: parent.width - Style.space(16) - Style.space(22) - Style.space(14) - Style.space(24)
          anchors.verticalCenter: parent.verticalCenter
        }

        // Enable/disable hit target (separate from expand).
        Text {
          id: enableMark
          text: monitorRow.display.enabled ? "󰄬" : "󰄱"
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.subtitle
          width: Style.space(14)
          horizontalAlignment: Text.AlignRight
          anchors.verticalCenter: parent.verticalCenter
          opacity: monitorRow.canToggle ? 1.0 : 0.35

          MouseArea {
            anchors.fill: parent
            anchors.margins: -Style.space(4)
            enabled: monitorRow.canToggle
            cursorShape: Qt.PointingHandCursor
            onClicked: root.toggleDisplay(monitorRow.display.name, monitorRow.display.enabled)
          }
        }
      }

      MouseArea {
        anchors.fill: parent
        anchors.rightMargin: Style.space(28)  // leave the enable mark clickable
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onContainsMouseChanged: if (containsMouse && !root.reflowingText) {
          root.cursorActive = true
          root.focusSection = "monitors"
          root.selectedIndex = monitorRow.rowIndex
        }
        onClicked: root.toggleExpanded(monitorRow.display.name)
      }
    }

    Column {
      visible: monitorRow.expanded && monitorRow.display && monitorRow.display.enabled
      width: parent.width - Style.space(18)
      x: Style.space(18)
      spacing: Style.space(8)

      // ---- Brightness (only when this output has a controllable backlight/DDC) ----
      Column {
        visible: monitorRow.showBrightness
        width: parent.width
        spacing: Style.space(4)

        Item {
          width: parent.width
          implicitHeight: Math.max(bLabel.implicitHeight, bPct.implicitHeight)

          Text {
            id: bLabel
            text: "Brightness"
            color: Qt.darker(root.bar.foreground, 1.25)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
          }
          Text {
            id: bPct
            text: Math.round(nestedBrightness.dragging ? nestedBrightness.liveValue : root.displayBrightness(monitorRow.display)) + "%"
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
          }
        }

        CursorSurface {
          id: nestedBrightnessRow
          width: parent.width
          height: nestedBrightness.implicitHeight + Style.spacing.controlGap
          hasCursor: root.cursorActive && root.focusSection === "monitorBrightness" && root.selectedIndex === monitorRow.rowIndex
          onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(nestedBrightnessRow)
          foreground: root.bar.foreground
          outline: true

          PanelSlider {
            id: nestedBrightness
            bar: root.bar
            anchors.fill: parent
            anchors.leftMargin: Style.space(6)
            anchors.rightMargin: Style.space(6)
            minimum: 1
            maximum: 100
            step: 1
            value: root.displayBrightness(monitorRow.display)
            integer: true
            onMoved: function(v) { root.previewBrightness(monitorRow.display.name, v) }
            onReleased: function(v) {
              brightnessDebounce.stop()
              root.setBrightness(monitorRow.display.name, v)
            }
          }

          HoverHandler {
            onHoveredChanged: if (hovered && !root.reflowingText) {
              root.cursorActive = true
              root.focusSection = "monitorBrightness"
              root.selectedIndex = monitorRow.rowIndex
            }
          }
        }
      }

      // ---- Scale ----
      Column {
        width: parent.width
        spacing: Style.space(4)

        Text {
          text: "Scale"
          color: Qt.darker(root.bar.foreground, 1.25)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: true
        }

        Grid {
          id: nestedScaleRow
          width: parent.width
          columns: Math.max(1, monitorRow.scaleValues.length)
          spacing: Style.spacing.xs
          readonly property real cellWidth: columns > 0
            ? (width - spacing * (columns - 1)) / columns
            : 0

          Repeater {
            model: monitorRow.scaleValues
            ScalePill {
              required property string modelData
              required property int index
              display: monitorRow.display
              scaleValue: modelData
              scaleIndex: index
              width: nestedScaleRow.cellWidth
            }
          }
        }
      }
    }
  }

  component MiracastPeerRow: CursorSurface {
    id: peerRow
    required property var peer
    required property int rowIndex

    hasCursor: root.cursorActive && root.focusSection === "miracastPeers" && root.selectedIndex === rowIndex
    onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(peerRow)
    foreground: root.bar.foreground
    fill: Style.hoverFillFor(root.bar.foreground, Color.accent)
    currentFill: Style.selectedFillFor(root.bar.foreground, Color.accent)
    implicitHeight: peerInner.implicitHeight + Style.spacing.xl

    Row {
      id: peerInner
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        text: "󰑋"
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.title
        width: Style.space(22)
        horizontalAlignment: Text.AlignHCenter
        anchors.verticalCenter: parent.verticalCenter
      }

      Column {
        width: parent.width - Style.space(22) - Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(1)

        Text {
          width: parent.width
          text: Model.miracastPeerTitle(peerRow.peer)
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }
        Text {
          width: parent.width
          text: Model.miracastPeerSubtitle(peerRow.peer)
          color: Qt.darker(root.bar.foreground, 1.4)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse && !root.reflowingText) {
        root.cursorActive = true
        root.focusSection = "miracastPeers"
        root.selectedIndex = peerRow.rowIndex
      }
      onClicked: miracast.startCast(peerRow.peer ? peerRow.peer.mac : "")
    }
  }
}
