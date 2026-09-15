import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import "Model.js" as Model

Panel {
  id: root
  moduleName: "colin.monitor"
  ipcTarget: "colin.monitor"
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
  //   "miracastMode" / "miracastPos" / "miracastStream" / "miracastEncode"
  //   / "miracastRadio" / "miracast" / "miracastPeers"
  //   "textsize"   - global shell/GTK/terminal text size (not per-display).
  readonly property var miracastModeValues: ["mirror", "extend"]
  // Display order ← ↑ ↓ → (left, above, below, right).
  readonly property var miracastPosValues: ["left", "above", "below", "right"]
  readonly property var miracastEncodeValues: Model.miracastCaptureEncodeValues()
  readonly property var miracastEncodeProfileValues: Model.miracastEncodeProfileValues()
  readonly property var miracastRadioValues: (miracast && miracast.p2pWifiValues)
    ? miracast.p2pWifiValues : ["auto"]
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
  // Which display rows show nested controls (brightness/scale/cast).
  // Accordion by default (onlyExpandFocusedDisplay); expand-all when that is off.
  property var expandedMonitors: []
  property var knownDisplayNames: []
  readonly property bool onlyExpandFocusedDisplay: !!(miracast && miracast.onlyExpandFocusedDisplay)
  // MIRACAST → ADVANCED SETTINGS (STREAM / RENDER / PRESET QUALITY).
  property bool advancedSettingsExpanded: false
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
    }
    // STREAM / RENDER / PRESET QUALITY / RADIO under MIRACAST → ADVANCED SETTINGS.
    if (root.advancedSettingsExpanded) {
      if (miracastStreamModeIds.length > 0) list.push("miracastStream")
      list.push("miracastEncode")
      list.push("miracastEncodeProfile")
      if (miracastRadioValues.length > 0) list.push("miracastRadio")
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
    if (section === "miracastEncode") return miracastEncodeValues.length
    if (section === "miracastEncodeProfile") return miracastEncodeProfileValues.length
    if (section === "miracastRadio") return miracastRadioValues.length
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
      || section === "miracastStream" || section === "miracastEncode"
      || section === "miracastEncodeProfile" || section === "miracastRadio"
  }

  function sectionFirstIndex(section) {
    if (section === "textsize" || section === "miracast" || section === "monitorBrightness") return -1
    if (section === "miracastMode") return Math.max(0, miracastModeValues.indexOf(miracast.mode))
    if (section === "miracastPos") return Math.max(0, miracastPosValues.indexOf(miracast.extendPosition))
    if (section === "miracastStream") return Math.max(0, miracastStreamModeIds.indexOf(miracast.streamMode))
    if (section === "miracastEncode")
      return Math.max(0, miracastEncodeValues.indexOf(miracast.captureEncodeActive))
    if (section === "miracastEncodeProfile")
      return Math.max(0, miracastEncodeProfileValues.indexOf(miracast.encodeProfile || "medium"))
    if (section === "miracastRadio")
      return Math.max(0, miracastRadioValues.indexOf(miracast.p2pWifiInterface || "auto"))
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
    var target = String(name || "")
    if (target === "") return false
    var list = root.expandedMonitors || []
    for (var i = 0; i < list.length; i++) {
      if (String(list[i]) === target) return true
    }
    return false
  }

  function toggleExpanded(name) {
    var target = String(name || "")
    if (target === "") return
    if (root.onlyExpandFocusedDisplay) {
      root.expandedMonitors = root.isExpanded(target) ? [] : [target]
      return
    }
    var next = []
    var found = false
    var list = root.expandedMonitors || []
    for (var i = 0; i < list.length; i++) {
      if (String(list[i]) === target) {
        found = true
        continue
      }
      next.push(list[i])
    }
    if (!found) next.push(target)
    root.expandedMonitors = next
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

  function enabledDisplayNames() {
    var names = []
    for (var i = 0; i < displays.length; i++) {
      if (displays[i] && displays[i].enabled && displays[i].name)
        names.push(String(displays[i].name))
    }
    return names
  }

  // preferFocus: expand something when nothing usable is expanded.
  // forceFocus: panel open / focus change — reset to mode default.
  function ensureExpandedMonitor(preferFocus, forceFocus) {
    if (!displays || displays.length === 0) {
      root.expandedMonitors = []
      root.knownDisplayNames = []
      return
    }
    var enabled = root.enabledDisplayNames()

    if (root.onlyExpandFocusedDisplay) {
      var pick = ""
      if (forceFocus || preferFocus || root.expandedMonitors.length === 0) {
        var forced = root.focusedMonitor
        if (forced !== "" && displayByName(forced)) pick = forced
        if (pick === "") {
          for (var k = 0; k < displays.length; k++) {
            if (displays[k] && displays[k].focused) {
              pick = String(displays[k].name)
              break
            }
          }
        }
        if (pick === "" && enabled.length > 0) pick = enabled[0]
        root.expandedMonitors = pick !== "" ? [pick] : []
      } else {
        // Keep single expansion if that output still exists; else re-pick.
        var cur = root.expandedMonitors.length === 1 ? String(root.expandedMonitors[0]) : ""
        if (cur === "" || !displayByName(cur)) {
          ensureExpandedMonitor(true, true)
          return
        }
      }
      root.knownDisplayNames = enabled.slice()
      return
    }

    // Expand-all mode (default): open expands every enabled output; refresh
    // keeps user collapses and auto-expands newly appeared displays.
    if (forceFocus) {
      root.expandedMonitors = enabled.slice()
      root.knownDisplayNames = enabled.slice()
      return
    }
    var next = []
    var known = root.knownDisplayNames || []
    for (var i = 0; i < enabled.length; i++) {
      var name = enabled[i]
      var isNew = true
      for (var j = 0; j < known.length; j++) {
        if (String(known[j]) === name) { isNew = false; break }
      }
      if (isNew || root.isExpanded(name))
        next.push(name)
    }
    if (next.length === 0 && preferFocus)
      next = enabled.slice()
    root.expandedMonitors = next
    root.knownDisplayNames = enabled.slice()
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
      return
    }
    if (focusSection === "miracastEncode") {
      var encodeNext = selectedIndex + delta
      if (encodeNext < 0) encodeNext = 0
      if (encodeNext > miracastEncodeValues.length - 1) encodeNext = miracastEncodeValues.length - 1
      selectedIndex = encodeNext
      return
    }
    if (focusSection === "miracastEncodeProfile") {
      var profNext = selectedIndex + delta
      if (profNext < 0) profNext = 0
      if (profNext > miracastEncodeProfileValues.length - 1)
        profNext = miracastEncodeProfileValues.length - 1
      selectedIndex = profNext
      return
    }
    if (focusSection === "miracastRadio") {
      var radioNext = selectedIndex + delta
      if (radioNext < 0) radioNext = 0
      if (radioNext > miracastRadioValues.length - 1) radioNext = miracastRadioValues.length - 1
      selectedIndex = radioNext
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
    if (focusSection === "miracastEncode" && selectedIndex >= 0 && selectedIndex < miracastEncodeValues.length) {
      miracast.setCaptureEncode(miracastEncodeValues[selectedIndex])
      return
    }
    if (focusSection === "miracastEncodeProfile"
        && selectedIndex >= 0 && selectedIndex < miracastEncodeProfileValues.length) {
      miracast.setEncodeProfile(miracastEncodeProfileValues[selectedIndex])
      return
    }
    if (focusSection === "miracastRadio" && selectedIndex >= 0 && selectedIndex < miracastRadioValues.length) {
      miracast.setP2pWifiInterface(miracastRadioValues[selectedIndex])
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
    target: "colin.monitor"

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

  function isMiracastOutputName(name, disp) {
    var n = String(name || "")
    if (!n) return false
    if (disp && disp.miracast) return true
    if (n.indexOf("HEADLESS") === 0) return true
    if (miracast) {
      if (miracast.lastPeerName && n === String(miracast.lastPeerName)) return true
      // status.monitor while streaming / last session
      if (miracast.castMonitor && n === String(miracast.castMonitor)) return true
    }
    return false
  }

  function toggleDisplay(name, enabled) {
    if (!name) return
    if (enabled && root.enabledDisplayCount <= 1) return

    // NEVER hyprctl-disable a Miracast virtual output. That path freezes eDP
    // under screencopy (30s–power-cycle). Always tear down via miracast-ctl stop,
    // even if the UI thinks the cast is already inactive.
    if (enabled) {
      var disp = null
      for (var i = 0; i < displays.length; i++) {
        if (displays[i] && displays[i].name === name) {
          disp = displays[i]
          break
        }
      }
      if (isMiracastOutputName(name, disp)) {
        if (miracast) miracast.stopCast()
        return
      }
    }

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
    if (root.opened) {
      // Accordion mode follows focus; expand-all only picks up new outputs.
      if (root.onlyExpandFocusedDisplay)
        ensureExpandedMonitor(true, true)
      else
        ensureExpandedMonitor(false, false)
    } else if ((root.expandedMonitors || []).length === 0) {
      ensureExpandedMonitor(true)
    }
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
        // Vim hjkl on the CAST MODE row set Extend position (h← j↓ k↑ l→).
        // Only while Extend is active and that row is focused — elsewhere
        // hjkl keep navigating sections / pills.
        if (miracast && miracast.mode === "extend"
            && (root.focusSection === "miracastMode" || root.focusSection === "miracastPos")
            && (dx !== 0 || dy !== 0)) {
          var pos = dx < 0 ? "left"
                  : dx > 0 ? "right"
                  : dy < 0 ? "above"
                  : "below"
          miracast.setExtendPosition(pos)
          root.focusSection = "miracastPos"
          root.selectedIndex = Math.max(0, root.miracastPosValues.indexOf(pos))
          return
        }
        if (dy !== 0) root.moveCursor(dy)
        else if (dx !== 0) {
          if (root.focusSection === "monitorBrightness") root.adjustBrightness(dx * 5)
          else if (root.focusSection === "textsize") root.adjustTextSize(dx)
          else if (root.focusSection === "monitors" || root.focusSection === "monitorScale"
                   || root.focusSection === "miracastMode" || root.focusSection === "miracastPos"
                   || root.focusSection === "miracastStream" || root.focusSection === "miracastEncode"
                   || root.focusSection === "miracastEncodeProfile"
                   || root.focusSection === "miracastRadio")
            root.moveCursorH(dx)
        }
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      // PanelKeyCatcher maps X → deleteRequested (not textKey), same as
      // Bluetooth/Network "forget" shortcuts. Wire Stop here or X is a no-op.
      onDeleteRequested: {
        if (miracast && miracast.active) miracast.stopCast()
      }
      onTextKey: function(t) {
        if (t === "s" || t === "S") miracast.scanPeers()
        else if (t === "f" || t === "F") miracast.openFirewall()
        else if (t === "d" || t === "D") miracast.runDoctor()
        else if (t === "c" || t === "C") miracast.startCast("")
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
          // Slightly denser than stock, but leave room to breathe.
          spacing: Style.space(6)

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
              // Drop the wifi arcs inside the monitor glass (hero size).
              wifiVerticalNudge: 1
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            Column {
              id: heroLabels
              anchors.left: heroIcon.right
              anchors.leftMargin: Style.space(10)
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(1)

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

          // ---------- Displays + Miracast (single column; no rule between them) ----------
          PanelSeparator {
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(4)

            Column {
              width: parent.width
              spacing: Style.space(4)
              visible: root.showDisplaysSection

              DenseSectionLabel {
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

            // Extra air between DISPLAYS and MIRACAST (beyond column spacing).
            Item {
              visible: root.showDisplaysSection
              width: parent.width
              height: Style.space(8)
            }

            // MIRACAST section: slightly more air between header / STATUS /
            // CONTROLS / RADIO than the panel default (cap ~15px).
            Column {
              width: parent.width
              spacing: Style.space(10)

            DenseSectionLabel {
              id: miracastHeader
              text: "MIRACAST"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
            }

            // ---- STATUS (indented under MIRACAST, above CONTROLS) ----
            Column {
              x: Style.space(10)
              width: parent.width - Style.space(10)
              spacing: Style.space(4)

              Text {
                text: "STATUS"
                color: Qt.darker(root.bar.foreground, 1.25)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
              }

              Text {
                id: miracastPhase
                width: parent.width
                text: Model.miracastPhaseLabel(
                        miracast.phase,
                        miracast.p2pWifiResolved,
                        miracast.p2pWifiAdapterName).toUpperCase()
                color: miracast.active ? root.bar.foreground : Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                wrapMode: Text.WordWrap
              }

              Text {
                // Hero already shows the peer name while streaming — skip the
                // duplicate "Connected to …" line to save vertical space.
                visible: {
                  if (miracast.streaming) return false
                  if (miracast.connecting) return true
                  return miracast.connectedLabel !== ""
                }
                width: parent.width
                text: {
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
                // While connected, the phase line is enough — don't also show
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
            }

            // ---- CONTROLS (indented under MIRACAST) ----
            Column {
              x: Style.space(10)
              width: parent.width - Style.space(10)
              spacing: Style.space(4)

              Text {
                text: "CONTROLS"
                color: Qt.darker(root.bar.foreground, 1.25)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
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
                  anchors.leftMargin: Style.space(6)
                  anchors.rightMargin: Style.space(6)
                  spacing: Style.space(6)

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

              // Options directly under CONTROLS buttons
              Column {
                width: parent.width
                spacing: Style.space(4)

                Row {
                  width: parent.width
                  spacing: Style.spacing.sm

                  Text {
                    text: miracast.preserveDisplayAcrossMonitors ? "󰄬" : "󰄱"
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.body
                    verticalAlignment: Text.AlignVCenter

                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: miracast.setPreserveDisplayAcrossMonitors(
                        !miracast.preserveDisplayAcrossMonitors)
                    }
                  }

                  Text {
                    text: "Persist display across monitors"
                    color: Qt.darker(root.bar.foreground, 1.15)
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    verticalAlignment: Text.AlignVCenter
                    width: parent.width - parent.spacing - 28
                    wrapMode: Text.WordWrap

                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: miracast.setPreserveDisplayAcrossMonitors(
                        !miracast.preserveDisplayAcrossMonitors)
                    }

                    PanelToolTip {
                      delay: 400
                      text: miracast.preserveDisplayAcrossMonitors
                        ? "On: switching TVs keeps the Extend desktop on the shared persistent-miracast output."
                        : "Off: switching TVs migrates windows to the laptop and seeds a fresh peer-named Extend desktop."
                    }
                  }
                }

                Row {
                  width: parent.width
                  spacing: Style.spacing.sm

                  Text {
                    text: miracast.autoSwitchAudioOutput ? "󰄬" : "󰄱"
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.body
                    verticalAlignment: Text.AlignVCenter

                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: miracast.setAutoSwitchAudioOutput(
                        !miracast.autoSwitchAudioOutput)
                    }
                  }

                  Text {
                    text: "Automatically switch audio output"
                    color: Qt.darker(root.bar.foreground, 1.15)
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    verticalAlignment: Text.AlignVCenter
                    width: parent.width - parent.spacing - 28
                    wrapMode: Text.WordWrap

                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: miracast.setAutoSwitchAudioOutput(
                        !miracast.autoSwitchAudioOutput)
                    }

                    PanelToolTip {
                      delay: 400
                      text: miracast.autoSwitchAudioOutput
                        ? "On: after the cast is streaming, set the default audio output to Miracast (speakers stay default during connect)."
                        : "Off: leave the current audio output selected; Miracast sink is still used for capture if you route to it manually."
                    }
                  }
                }
              }
            }

            // ---- ADVANCED SETTINGS (STREAM / RENDER / PRESET QUALITY) ----
            Column {
              x: Style.space(10)
              width: parent.width - Style.space(10)
              spacing: Style.space(4)

              // Use Item+Row (not MouseArea-in-Row anchors) so the header
              // always lays out and receives clicks — same pattern as Persist.
              Item {
                width: parent.width
                implicitHeight: advancedHeaderRow.implicitHeight

                Row {
                  id: advancedHeaderRow
                  width: parent.width
                  spacing: Style.spacing.sm

                  Text {
                    text: root.advancedSettingsExpanded ? "󰅀" : "󰅂"
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.body
                    verticalAlignment: Text.AlignVCenter
                  }

                  Text {
                    text: "ADVANCED SETTINGS"
                    color: Qt.darker(root.bar.foreground, 1.25)
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    verticalAlignment: Text.AlignVCenter
                  }
                }

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.advancedSettingsExpanded = !root.advancedSettingsExpanded
                }
              }

              Column {
                visible: root.advancedSettingsExpanded
                width: parent.width
                spacing: Style.space(6)

                Column {
                  width: parent.width
                  spacing: Style.space(4)
                  visible: root.miracastStreamModeIds.length > 0

                  Text {
                    text: "STREAM MODE"
                    color: Qt.darker(root.bar.foreground, 1.25)
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
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

                Column {
                  width: parent.width
                  spacing: Style.space(4)

                  Text {
                    text: "RENDER ENGINE"
                    color: Qt.darker(root.bar.foreground, 1.25)
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }

                  Grid {
                    id: miracastEncodeRow
                    width: parent.width
                    columns: root.miracastEncodeValues.length
                    spacing: Style.spacing.xs
                    readonly property real cellWidth: columns > 0
                      ? (width - spacing * (columns - 1)) / columns
                      : 0

                    Repeater {
                      model: root.miracastEncodeValues
                      MiracastEncodePill {
                        required property string modelData
                        required property int index
                        encodeValue: modelData
                        encodeIndex: index
                        width: miracastEncodeRow.cellWidth
                      }
                    }
                  }
                }

                Column {
                  width: parent.width
                  spacing: Style.space(4)

                  Text {
                    text: "PRESET QUALITY"
                    color: Qt.darker(root.bar.foreground, 1.25)
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }

                  Grid {
                    id: miracastEncodeProfileRow
                    width: parent.width
                    columns: root.miracastEncodeProfileValues.length
                    spacing: Style.spacing.xs
                    readonly property real cellWidth: columns > 0
                      ? (width - spacing * (columns - 1)) / columns
                      : 0

                    Repeater {
                      model: root.miracastEncodeProfileValues
                      MiracastEncodeProfilePill {
                        required property string modelData
                        required property int index
                        profileValue: modelData
                        profileIndex: index
                        width: miracastEncodeProfileRow.cellWidth
                      }
                    }
                  }
                }

                Column {
                  width: parent.width
                  spacing: Style.space(4)
                  visible: root.miracastRadioValues.length > 0

                  Text {
                    text: "RADIO"
                    color: Qt.darker(root.bar.foreground, 1.25)
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }

                  Grid {
                    id: miracastRadioRow
                    width: parent.width
                    columns: Math.min(root.miracastRadioValues.length, 3)
                    spacing: Style.spacing.xs
                    readonly property real cellWidth: columns > 0
                      ? (width - spacing * (columns - 1)) / columns
                      : 0

                    Repeater {
                      model: root.miracastRadioValues
                      MiracastRadioPill {
                        required property string modelData
                        required property int index
                        radioValue: modelData
                        radioIndex: index
                        width: miracastRadioRow.cellWidth
                      }
                    }
                  }
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
            } // MIRACAST section column
          }

          // ---------- Text size (global) ----------
          PanelSeparator {
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(4)

            Item {
              width: parent.width
              implicitHeight: Math.max(textSizeHeader.implicitHeight, textSizePx.implicitHeight)

              DenseSectionLabel {
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

  // Denser than PanelSectionHeader (no glyph overshoot padding) for packed
  // CAST MODE / POSITION / STREAM subsections and major section titles.
  component DenseSectionLabel: Text {
    property color foreground: Color.foreground
    property string fontFamily: Style.font.family
    textFormat: Text.PlainText
    color: Qt.darker(foreground, 1.4)
    font.family: fontFamily
    font.pixelSize: Style.font.caption
    font.bold: true
    topPadding: 0
    bottomPadding: 0
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

    text: posValue === "left" ? "←"
          : posValue === "above" ? "↑"
          : posValue === "below" ? "↓"
          : "→"
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

  component MiracastEncodePill: Button {
    id: encodePill
    required property string encodeValue
    required property int encodeIndex

    text: miracast.captureEncodeLabel(encodeValue)
    fontSize: Style.font.caption
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.sm
    verticalPadding: Style.spacing.controlPaddingY
    bordered: true

    active: miracast.captureEncodeActive === encodeValue
    hasCursor: root.cursorActive && root.focusSection === "miracastEncode" && root.selectedIndex === encodeIndex
    enabled: !miracast.busy

    onClicked: miracast.setCaptureEncode(encodeValue)
    onHovered: function(isHovered) {
      if (!isHovered || root.reflowingText) return
      root.cursorActive = true
      root.focusSection = "miracastEncode"
      root.selectedIndex = encodePill.encodeIndex
    }
  }

  component MiracastEncodeProfilePill: Button {
    id: profilePill
    required property string profileValue
    required property int profileIndex

    text: miracast.encodeProfileLabel(profileValue)
    fontSize: Style.font.caption
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.sm
    verticalPadding: Style.spacing.controlPaddingY
    bordered: true

    active: miracast.encodeProfile === profileValue
    hasCursor: root.cursorActive && root.focusSection === "miracastEncodeProfile"
               && root.selectedIndex === profileIndex
    enabled: !miracast.busy

    onClicked: miracast.setEncodeProfile(profileValue)
    onHovered: function(isHovered) {
      if (!isHovered || root.reflowingText) return
      root.cursorActive = true
      root.focusSection = "miracastEncodeProfile"
      root.selectedIndex = profilePill.profileIndex
    }
  }

  component MiracastRadioPill: Button {
    id: radioPill
    required property string radioValue
    required property int radioIndex

    text: miracast.p2pWifiLabel(radioValue)
    fontSize: Style.font.caption
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.sm
    verticalPadding: Style.spacing.controlPaddingY
    bordered: true

    active: (miracast.p2pWifiInterface || "auto") === radioValue
    hasCursor: root.cursorActive && root.focusSection === "miracastRadio" && root.selectedIndex === radioIndex
    enabled: !miracast.busy

    onClicked: miracast.setP2pWifiInterface(radioValue)
    onHovered: function(isHovered) {
      if (!isHovered || root.reflowingText) return
      root.cursorActive = true
      root.focusSection = "miracastRadio"
      root.selectedIndex = radioPill.radioIndex
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
    // CAST MODE / STREAM / RENDER: Miracast headless row for Extend; laptop row
    // for Mirror (mirror has no headless display to attach controls to).
    readonly property bool showMiracastCastControls: {
      if (!root.showMiracastSessionControls || !monitorRow.display) return false
      if (monitorRow.display.miracast) return true
      if (!(miracast && miracast.mode === "mirror")) return false
      var n = String(monitorRow.display.name || "")
      if (monitorRow.display.focused) return true
      return n.indexOf("eDP") === 0 || n.indexOf("LVDS") === 0 || n.indexOf("DSI") === 0
    }
    // Shared rhythm for nested settings (eDP brightness/scale and Miracast cast controls).
    readonly property int settingsSectionGap: Style.space(3)  // between BRIGHTNESS / CAST MODE / SCALE…
    readonly property int settingsLabelGap: Style.space(3)    // between label and its control
    readonly property int settingsControlPad: Style.space(1) // chrome padding inside outlined controls

    width: parent ? parent.width : 0
    spacing: Style.space(4)

    CursorSurface {
      id: monitorHeader
      width: parent.width
      hasCursor: root.cursorActive && root.focusSection === "monitors" && root.selectedIndex === monitorRow.rowIndex
      onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(monitorHeader)
      current: monitorRow.isFocused
      foreground: root.bar.foreground
      fill: Style.hoverFillFor(root.bar.foreground, Color.accent)
      currentFill: Style.selectedFillFor(root.bar.foreground, Color.accent)
      implicitHeight: headerInner.implicitHeight + Style.space(8)
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
      spacing: monitorRow.settingsSectionGap

      // ---- Brightness (only when this output has a controllable backlight/DDC) ----
      Column {
        visible: monitorRow.showBrightness
        width: parent.width
        spacing: monitorRow.settingsLabelGap

        Item {
          width: parent.width
          implicitHeight: Math.max(bLabel.implicitHeight, bPct.implicitHeight)

          Text {
            id: bLabel
            text: "BRIGHTNESS"
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
          // controlGap (~8) was the bulk of the empty band under the slider on eDP.
          height: nestedBrightness.implicitHeight + monitorRow.settingsControlPad
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

      // ---- CAST MODE / POSITION (before SCALE; on eDP when Mirror, Miracast when Extend) ----
      Column {
        visible: monitorRow.showMiracastCastControls
        width: parent.width
        spacing: monitorRow.settingsLabelGap

        // CAST MODE | EXTEND POSITION — shared row, vertical separator when Extend.
        Row {
          id: miracastCastRow
          width: parent.width
          spacing: Style.spacing.sm
          readonly property bool showPos: miracast.mode === "extend"
                                          && !!(monitorRow.display && monitorRow.display.miracast)
          // Equal pill width across both groups (2 mode + 4 arrows when Extend).
          readonly property int pillCount: root.miracastModeValues.length
            + (showPos ? root.miracastPosValues.length : 0)
          readonly property real sepWidth: showPos ? 1 : 0
          // Gaps: xs between pills within each group + sm on each side of the separator.
          readonly property real pillWidth: pillCount > 0
            ? (width - sepWidth - (showPos ? spacing : 0)
               - Style.spacing.xs * (
                   Math.max(0, root.miracastModeValues.length - 1)
                   + (showPos ? Math.max(0, root.miracastPosValues.length - 1) : 0)
                 )) / pillCount
            : 0

          Column {
            id: miracastModeGroup
            width: miracastCastRow.pillWidth * root.miracastModeValues.length
              + Style.spacing.xs * Math.max(0, root.miracastModeValues.length - 1)
            spacing: monitorRow.settingsLabelGap

            Text {
              text: "CAST MODE"
              color: Qt.darker(root.bar.foreground, 1.25)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }

            Row {
              width: parent.width
              spacing: Style.spacing.xs
              Repeater {
                model: root.miracastModeValues
                MiracastModePill {
                  required property string modelData
                  required property int index
                  modeValue: modelData
                  modeIndex: index
                  width: miracastCastRow.pillWidth
                }
              }
            }
          }

          Rectangle {
            visible: miracastCastRow.showPos
            width: miracastCastRow.sepWidth
            height: Math.max(miracastModeGroup.height, miracastPosGroup.height)
            color: root.bar.foreground
            opacity: 0.25
            radius: 0
          }

          Column {
            id: miracastPosGroup
            visible: miracastCastRow.showPos
            width: miracastCastRow.showPos
              ? miracastCastRow.pillWidth * root.miracastPosValues.length
                + Style.spacing.xs * Math.max(0, root.miracastPosValues.length - 1)
              : 0
            spacing: monitorRow.settingsLabelGap

            Text {
              text: "EXTEND POSITION"
              color: Qt.darker(root.bar.foreground, 1.25)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }

            Row {
              width: parent.width
              spacing: Style.spacing.xs
              Repeater {
                model: root.miracastPosValues
                MiracastPosPill {
                  required property string modelData
                  required property int index
                  posValue: modelData
                  posIndex: index
                  width: miracastCastRow.pillWidth
                }
              }
            }
          }
        }

        Text {
          visible: miracast.mode === "extend" && miracast.positionWarning !== ""
          width: parent.width
          text: miracast.positionWarning
          color: root.bar.urgent || root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap

          PanelToolTip {
            visible: parent.visible
            delay: 0
            text: miracast.positionWarning
          }
        }
      }

      // ---- Scale (after CAST MODE on Miracast rows) ----
      Column {
        width: parent.width
        spacing: monitorRow.settingsLabelGap

        Text {
          text: "SCALE"
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
