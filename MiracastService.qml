import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "Model.js" as Model

Item {
  id: root

  property string pluginDir: ""
  property string monitorName: "eDP-1"
  property string lastPeerMac: ""
  property string lastPeerName: ""
  // Hyprland output name for the live/last Extend headless (from status.monitor).
  property string castMonitor: ""
  property string mode: "mirror"   // mirror | extend
  property string extendPosition: "right"  // right | left | above | below
  // Display panel expansion: false = expand all outputs; true = focused only.
  // Accordion: only the focused display row shows nested controls.
  property bool onlyExpandFocusedDisplay: true
  // Keep the same Extend headless + workspaces when switching Miracast sinks.
  property bool preserveDisplayAcrossMonitors: true
  // Switch default audio to Miracast at PLAY (hold speakers during handshake).
  property bool autoSwitchAudioOutput: true
  property string streamMode: "1280x720p30"  // e.g. 1920x1080p30
  property var streamModes: []
  // RENDER ENGINE preference: dmabuf | vaapi | cpu
  property string captureEncode: "dmabuf"
  // Encode quality tier: high | medium | low (knobs depend on captureEncode)
  property string encodeProfile: "medium"
  // Resolved while streaming (from status / latency): dmabuf | pipe
  property string capturePath: ""
  property string encoder: ""
  property bool captureFallback: false
  // Miracast P2P Wi-Fi radio: "auto" or managed iface (e.g. wlan1).
  property string p2pWifiInterface: "auto"
  property string p2pWifiResolved: ""
  property var p2pWifiRadios: []
  // Human adapter name for the resolved P2P Wi-Fi iface (from list_p2p_radios).
  readonly property string p2pWifiAdapterName: {
    var want = String(p2pWifiResolved || "")
    var radios = p2pWifiRadios || []
    for (var i = 0; i < radios.length; i++) {
      if (radios[i] && String(radios[i].iface) === want)
        return String(radios[i].adapterName || radios[i].driver || "")
    }
    return ""
  }

  property bool ready: false
  property bool running: false
  property string phase: "idle"
  property string statusText: "Checking…"
  property string lastError: ""
  property string actionStatus: ""
  property string positionWarning: ""
  property var peers: []
  property var doctor: ({})

  // Position-change reconnect: stop must finish before start (startCast bails while stopping).
  property bool _restartAfterPosition: false
  property bool _awaitingPositionRecover: false
  property bool _suppressPositionRecover: false
  property string _positionBeforeChange: "right"
  property string _pendingRestartPeer: ""
  property string _pendingRestartAfterStreamMode: ""

  // Session lock: hyprlock/session-lock freezes wlroots screencopy; wf-recorder
  // dies or feeds stale frames while RTSP stays up. Pause on lock, ensure on unlock.
  property bool _sessionLocked: false
  property bool _pausedForLock: false

  readonly property string ctl: pluginDir !== "" ? (pluginDir + "/bin/miracast-ctl") : "miracast-ctl"
  // Background status polls must NOT count as busy — they run every 2s while
  // streaming and would grey out Miracast action buttons via enabled:!busy.
  readonly property bool busy: doctorProcess.running || scanProcess.running || startProcess.running || stopProcess.running || firewallProcess.running || modeProcess.running || positionProcess.running || streamModeProcess.running || captureEncodeProcess.running || encodeProfileProcess.running || preserveDisplayProcess.running || autoSwitchAudioProcess.running || p2pWifiProcess.running
  // Pill values: Auto + each discovered managed iface.
  readonly property var p2pWifiValues: {
    var out = ["auto"]
    var radios = p2pWifiRadios || []
    for (var i = 0; i < radios.length; i++) {
      var iface = radios[i] && radios[i].iface ? String(radios[i].iface) : ""
      if (iface !== "" && out.indexOf(iface) < 0)
        out.push(iface)
    }
    return out
  }
  readonly property bool active: Model.miracastIsActive(phase)
  readonly property bool connecting: phase === "connecting" || phase === "dhcp" || phase === "rtsp" || phase === "scanning"
  readonly property bool streaming: phase === "streaming"
  // Pill highlight: resolved path while streaming, else saved preference.
  readonly property string captureEncodeActive: {
    if (streaming && (capturePath !== "" || encoder !== ""))
      return Model.miracastCaptureEncodeActive(capturePath, encoder, captureEncode)
    return captureEncode
  }

  onStreamingChanged: {
    if (streaming && _awaitingPositionRecover) {
      _awaitingPositionRecover = false
      positionRecoverTimer.stop()
      if (!_suppressPositionRecover) {
        positionWarning = ""
        actionStatus = "Moved extended display (" + extendPositionLabel + ")"
      }
      _suppressPositionRecover = false
    }
  }
  readonly property string connectedLabel: {
    if (lastPeerName !== "") return lastPeerName
    if (lastPeerMac !== "") return lastPeerMac
    return ""
  }
  readonly property string modeLabel: mode === "extend" ? "Extend" : "Mirror"
  readonly property string extendPositionLabel: {
    if (extendPosition === "left") return "Left"
    if (extendPosition === "above") return "Above"
    if (extendPosition === "below") return "Below"
    return "Right"
  }

  function persistPeer(mac, name) {
    var value = String(mac || "").toUpperCase()
    if (value === "") return
    lastPeerMac = value
    if (name !== undefined && String(name || "") !== "") lastPeerName = String(name)
  }

  function refresh() {
    if (statusProcess.running) return
    statusProcess.command = [ctl, "status"]
    statusProcess.running = true
  }

  // fix=true (panel Check & fix): diagnose + open UFW ports when missing.
  // Startup / refresh uses fix=false so we never pop sudo on panel open.
  function runDoctor(fix) {
    if (doctorProcess.running) return
    var doFix = fix === true
    actionStatus = doFix ? "Checking & fixing…" : "Running checks…"
    doctorProcess.command = doFix ? [ctl, "doctor", "--fix"] : [ctl, "doctor"]
    doctorProcess.running = true
  }

  function scanPeers() {
    if (scanProcess.running) return
    phase = "scanning"
    statusText = "Scanning for Miracast sinks…"
    actionStatus = "Scanning…"
    scanProcess.command = [ctl, "scan"]
    scanProcess.running = true
  }

  // CLI / scripts: floating terminal help. The Display panel Info button uses
  // a themed in-shell card instead (see DisplayPanel helpOpen).
  function showInfo() {
    if (infoProcess.running) return
    actionStatus = "Opening Miracast help…"
    infoProcess.command = [ctl, "info"]
    infoProcess.running = true
  }

  // Kept for CLI parity / scripts; panel folds this into runDoctor(true).
  function openFirewall() {
    if (firewallProcess.running) return
    if (doctor && doctor.firewall_needs_open === false) {
      actionStatus = "Firewall already OK — nothing to open"
      return
    }
    actionStatus = "Opening UFW Miracast ports…"
    firewallProcess.command = [ctl, "firewall-open"]
    firewallProcess.running = true
  }

  readonly property bool firewallActionUseful: Model.miracastFirewallNeedsOpen(doctor)

  function setMode(nextMode) {
    var value = String(nextMode || "mirror")
    if (value !== "mirror" && value !== "extend") value = "mirror"
    if (value === mode && !modeProcess.running) return
    var wasActive = active
    var peer = lastPeerMac
    mode = value
    if (modeProcess.running) return
    modeProcess.command = [ctl, "set-mode", value]
    modeProcess.running = true
    actionStatus = value === "extend" ? "Mode: Extend (virtual display)" : "Mode: Mirror (copy desktop)"
    // Mode is applied at FluxCast start — stop then reconnect after stop finishes
    // (same path as stream-mode / position). Qt.callLater(start) races async stop.
    if (wasActive && peer !== "") {
      actionStatus = "Switching to " + (value === "extend" ? "Extend" : "Mirror") + "…"
      _pendingRestartPeer = peer
      stopCast(preserveDisplayAcrossMonitors)
    }
  }

  function setPreserveDisplayAcrossMonitors(enabled) {
    var on = !!enabled
    if (on === preserveDisplayAcrossMonitors && !preserveDisplayProcess.running) return
    preserveDisplayAcrossMonitors = on
    if (preserveDisplayProcess.running) return
    preserveDisplayProcess.command = [ctl, "set-persist-display", on ? "true" : "false"]
    preserveDisplayProcess.running = true
    actionStatus = on
      ? "Persist display across monitors: on"
      : "Persist display across monitors: off"
  }

  function setAutoSwitchAudioOutput(enabled) {
    var on = !!enabled
    if (on === autoSwitchAudioOutput && !autoSwitchAudioProcess.running) return
    autoSwitchAudioOutput = on
    if (autoSwitchAudioProcess.running) return
    autoSwitchAudioProcess.command = [ctl, "set-auto-switch-audio", on ? "true" : "false"]
    autoSwitchAudioProcess.running = true
    actionStatus = on
      ? "Automatically switch audio output: on"
      : "Automatically switch audio output: off"
  }

  function positionLabelFor(value) {
    if (value === "left") return "Left"
    if (value === "above") return "Above"
    if (value === "below") return "Below"
    return "Right"
  }

  // Keep the position pills honest: settings.json can say "left" while a
  // monitors.lua reload has parked the headless on the right.
  function syncExtendPositionFromDisplays(displays) {
    if (mode !== "extend") return
    if (positionProcess.running) return
    var inferred = Model.inferExtendPosition(displays)
    if (!inferred) return
    if (inferred === extendPosition) return
    extendPosition = inferred
  }

  function setExtendPosition(nextPos) {
    var value = String(nextPos || "right")
    if (value !== "right" && value !== "left" && value !== "above" && value !== "below") value = "right"
    if (value === extendPosition && !positionProcess.running) return
    if (positionProcess.running) return
    _positionBeforeChange = extendPosition
    extendPosition = value
    positionWarning = ""
    lastError = ""
    _restartAfterPosition = active && lastPeerMac !== ""
    _suppressPositionRecover = false
    positionProcess.command = [ctl, "set-extend-position", value]
    positionProcess.running = true
    actionStatus = "Extend position: " + positionLabelFor(value)
  }

  function streamModeLabel(id) {
    var value = String(id || "")
    for (var i = 0; i < streamModes.length; i++) {
      if (streamModes[i] && String(streamModes[i].id) === value)
        return String(streamModes[i].label || value)
    }
    return value
  }

  function p2pWifiLabel(id) {
    var key = String(id || "")
    if (key === "" || key === "auto")
      return "Auto"
    var radios = p2pWifiRadios || []
    for (var i = 0; i < radios.length; i++) {
      if (radios[i] && String(radios[i].iface) === key) {
        // Keep pills short: iface name only. Details live in list-p2p-radios /
        // tooltips later; "P2P busy" was confusing next to Auto on one radio.
        return key
      }
    }
    return key
  }

  function setP2pWifiInterface(next) {
    var value = String(next || "auto")
    if (value === "") value = "auto"
    if (p2pWifiProcess.running || stopProcess.running) return
    if (value === p2pWifiInterface) return
    p2pWifiInterface = value
    actionStatus = "RADIO: " + p2pWifiLabel(value)
    p2pWifiProcess.command = [ctl, "set-p2p-wifi-interface", value]
    p2pWifiProcess.running = true
  }

  function captureEncodeLabel(id) {
    return Model.miracastCaptureEncodeLabel(id)
  }

  function encodeProfileLabel(id) {
    return Model.miracastEncodeProfileLabel(id)
  }

  function setCaptureEncode(value) {
    var next = String(value || "")
    if (next !== "dmabuf" && next !== "vaapi" && next !== "cpu") return
    if (captureEncodeProcess.running || stopProcess.running) return
    if (next === captureEncode && !active) return
    captureEncode = next
    lastError = ""
    if (active)
      actionStatus = "Switching RENDER ENGINE to " + captureEncodeLabel(next) + "…"
    else
      actionStatus = "RENDER ENGINE: " + captureEncodeLabel(next)
    captureEncodeProcess.command = [ctl, "set-render-engine", next]
    captureEncodeProcess.running = true
  }

  function setEncodeProfile(value) {
    var next = String(value || "").toLowerCase()
    if (next !== "high" && next !== "medium" && next !== "low") return
    if (encodeProfileProcess.running || stopProcess.running) return
    if (next === encodeProfile && !active) return
    encodeProfile = next
    lastError = ""
    if (active)
      actionStatus = "PRESET QUALITY " + encodeProfileLabel(next)
          + " saved — reconnect to apply…"
    else
      actionStatus = "PRESET QUALITY: " + encodeProfileLabel(next)
    encodeProfileProcess.command = [ctl, "set-quality", next]
    encodeProfileProcess.running = true
  }

  function setStreamMode(nextMode) {
    var value = String(nextMode || "").trim()
    if (value === "" || streamModeProcess.running || stopProcess.running) return
    if (value === streamMode && !active) return
    streamMode = value
    lastError = ""
    // Resolution/fps apply on the next RTSP negotiation — reconnect if live.
    if (active && lastPeerMac !== "") {
      _pendingRestartAfterStreamMode = lastPeerMac
      actionStatus = "Switching stream to " + streamModeLabel(value) + "…"
    } else {
      _pendingRestartAfterStreamMode = ""
      actionStatus = "Stream mode: " + streamModeLabel(value)
    }
    streamModeProcess.command = [ctl, "set-stream-mode", value]
    streamModeProcess.running = true
  }

  function revertExtendPosition(failedPos) {
    var prev = _positionBeforeChange || "right"
    var failed = failedPos || extendPosition
    if (prev === failed) {
      positionWarning = "Extend position change failed — still on " + positionLabelFor(prev) + "."
      lastError = positionWarning
      actionStatus = ""
      return
    }
    positionWarning = "Extend position change failed — reverted to " + positionLabelFor(prev) + "."
    lastError = positionWarning
    actionStatus = ""
    _suppressPositionRecover = true
    _awaitingPositionRecover = false
    positionRecoverTimer.stop()
    extendPosition = prev
    _restartAfterPosition = lastPeerMac !== ""
    positionProcess.command = [ctl, "set-extend-position", prev]
    positionProcess.running = true
  }

  function startCast(peerMac) {
    if (startProcess.running || stopProcess.running) return
    var mac = String(peerMac || lastPeerMac || "").toUpperCase()
    if (mac === "") {
      lastError = "No peer MAC — scan and select a sink first"
      return
    }
    var name = lastPeerName
    for (var i = 0; i < peers.length; i++) {
      if (String(peers[i].mac || "").toUpperCase() === mac) {
        name = String(peers[i].name || name)
        break
      }
    }
    persistPeer(mac, name)
    lastError = ""
    actionStatus = "Starting " + modeLabel.toLowerCase() + " to " + (name || mac) + "…"
    phase = "connecting"
    statusText = "Connecting to " + (name || mac)

    // Stream knobs live in settings.json (miracast-ctl defaults / streamMode).
    // Passing nothing here lets ctl apply fps, bitrate, and output resolution.
    var cmd = [
      ctl, "start",
      "--peer", mac,
      "--mode", mode,
      "--monitor", monitorName || "eDP-1",
      "--go-intent", "15"
    ]
    if (name !== "") {
      cmd.push("--peer-name")
      cmd.push(name)
    }
    startProcess.command = cmd
    startProcess.running = true
  }

  function stopCast(keepWorkspaces) {
    if (stopProcess.running) return
    _pausedForLock = false
    _sessionLocked = false
    resumeLockTimer.stop()
    actionStatus = "Stopping…"
    // keepWorkspaces: leave Extend desktop on the headless (peer/mode reconnect
    // with preserveDisplayAcrossMonitors). Explicit Stop omits the flag.
    if (keepWorkspaces)
      stopProcess.command = [ctl, "stop", "--keep-workspaces"]
    else
      stopProcess.command = [ctl, "stop"]
    stopProcess.running = true
  }

  function toggleCast() {
    if (active) stopCast()
    else startCast(lastPeerMac)
  }

  function pauseCaptureForLock() {
    if (!streaming && !running) return
    if (pauseLockProcess.running) return
    _pausedForLock = true
    actionStatus = "Paused capture for screen lock"
    pauseLockProcess.command = [ctl, "pause-capture"]
    pauseLockProcess.running = true
  }

  function resumeCaptureAfterLock() {
    // Always try ensure when unlocking during an active cast — senders may
    // have died even if we never successfully paused.
    if (!streaming && !running && !_pausedForLock) return
    resumeLockTimer.restart()
  }

  Timer {
    interval: connecting || streaming ? 2000 : 5000
    running: true
    repeat: true
    onTriggered: root.refresh()
  }

  // Poll compositor session-lock state while casting (or while paused for lock).
  Timer {
    interval: 1200
    running: root.streaming || root.running || root._pausedForLock
    repeat: true
    onTriggered: {
      if (!lockProbe.running) lockProbe.running = true
    }
  }

  Timer {
    id: resumeLockTimer
    interval: 900
    repeat: false
    onTriggered: {
      if (ensureLockProcess.running) return
      actionStatus = "Resuming capture after unlock…"
      ensureLockProcess.command = [ctl, "ensure-capture"]
      ensureLockProcess.running = true
      root._pausedForLock = false
    }
  }

  Process {
    id: lockProbe
    command: ["omarchy-hyprland-session-locked"]
    onExited: function(exitCode) {
      // 0 = locked, 1 = unlocked, 2 = undetermined
      if (exitCode === 2) return
      var locked = exitCode === 0
      if (locked === root._sessionLocked) return
      root._sessionLocked = locked
      if (locked) root.pauseCaptureForLock()
      else root.resumeCaptureAfterLock()
    }
  }

  Process {
    id: pauseLockProcess
    stdout: StdioCollector { waitForEnd: true }
  }

  Process {
    id: ensureLockProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false)
            root.actionStatus = "Capture resume failed — try Stop/Start"
          else if (data.captureRestarted)
            root.actionStatus = "Capture resumed after unlock"
          else
            root.actionStatus = ""
        } catch (e) {
        }
        root.refresh()
      }
    }
  }

  // Watchdog: while streaming (and not session-locked), heal dead/zombie
  // capture that leave RTSP up but the TV frozen (e.g. after position moves).
  Timer {
    interval: 4000
    running: root.streaming && !root._sessionLocked && !root._pausedForLock
    repeat: true
    onTriggered: {
      if (captureHealthProcess.running || ensureWatchdogProcess.running) return
      if (pauseLockProcess.running || ensureLockProcess.running) return
      captureHealthProcess.command = [ctl, "capture-health"]
      captureHealthProcess.running = true
    }
  }

  Process {
    id: captureHealthProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.paused === true) return
          if (data.healthy === true) return
          if (ensureWatchdogProcess.running) return
          root.actionStatus = "Recovering capture…"
          ensureWatchdogProcess.command = [ctl, "ensure-capture", "2", "force"]
          ensureWatchdogProcess.running = true
        } catch (e) {
        }
      }
    }
  }

  Process {
    id: ensureWatchdogProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false)
            root.actionStatus = "Capture recovery failed"
          else if (data.captureRestarted)
            root.actionStatus = "Capture recovered"
          else
            root.actionStatus = ""
        } catch (e) {
        }
        root.refresh()
      }
    }
  }

  // After a live position move we reconnect; if streaming never returns, revert.
  Timer {
    id: positionRecoverTimer
    interval: 45000
    repeat: false
    onTriggered: {
      if (!root._awaitingPositionRecover || root._suppressPositionRecover) return
      root._awaitingPositionRecover = false
      if (root.streaming) return
      root.revertExtendPosition(root.extendPosition)
    }
  }

  // Give RTP ports a moment to free after stop before reconnecting.
  Timer {
    id: pendingRestartTimer
    interval: 1200
    repeat: false
    property string peer: ""
    onTriggered: {
      var mac = peer
      peer = ""
      if (mac === "") return
      root.actionStatus = "Reconnecting with " + root.modeLabel + "…"
      root.startCast(mac)
      if (!root._suppressPositionRecover) {
        root._awaitingPositionRecover = true
        positionRecoverTimer.restart()
      }
    }
  }

  Component.onCompleted: {
    runDoctor()
    refresh()
  }

  Process {
    id: doctorProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          root.doctor = JSON.parse(String(text || "{}"))
          root.ready = root.doctor.ready === true
          root.actionStatus = Model.miracastDoctorSummary(root.doctor)
          if (!root.ready) root.lastError = Model.miracastDoctorSummary(root.doctor)
          else if (root.phase === "idle") root.lastError = ""
        } catch (e) {
          root.lastError = "Doctor parse failed"
        }
      }
    }
  }

  Process {
    id: scanProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          root.peers = data.peers || []
          var n = root.peers.length
          if (data.ok === false) {
            root.lastError = String(data.error || "Scan failed")
            root.actionStatus = root.lastError
            if (root.phase === "scanning") {
              root.phase = "idle"
              root.statusText = Model.miracastPhaseHint("idle", "")
            }
          } else {
            var src = data.source ? (" via " + data.source) : ""
            var ms = data.elapsed_ms != null ? (" in " + data.elapsed_ms + "ms") : ""
            var live = data.session_active ? " (while casting)" : ""
            root.actionStatus = n + " sink" + (n === 1 ? "" : "s") + " found" + src + ms + live
            if (data.warning) root.actionStatus += " — " + String(data.warning)
            root.lastError = ""
            if (root.phase === "scanning") {
              root.phase = "idle"
              root.statusText = Model.miracastPhaseHint("idle", "")
            }
            if (root.peers.length === 1) root.persistPeer(root.peers[0].mac, root.peers[0].name)
          }
        } catch (e) {
          root.lastError = "Scan parse failed"
          root.phase = "error"
        }
        root.refresh()
      }
    }
  }

  Process {
    id: statusProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          root.phase = String(data.phase || "idle")
          root.running = data.running === true
          root.statusText = Model.miracastPhaseHint(root.phase, data.message || "")
          if (data.mode) root.mode = String(data.mode)
          // Do NOT apply data.extendPosition from settings while Extend is live —
          // monitors.lua reloads can park the headless on another edge while
          // settings still say "left". Position pills follow live geometry via
          // syncExtendPositionFromDisplays() instead.
          if (data.extendPosition && root.mode === "extend" && !root.active)
            root.extendPosition = String(data.extendPosition)
          else if (data.extendPosition && root.mode !== "extend")
            root.extendPosition = String(data.extendPosition)
          if (data.onlyExpandFocusedDisplay !== undefined)
            root.onlyExpandFocusedDisplay = data.onlyExpandFocusedDisplay === true
          if (data.preserveDisplayAcrossMonitors !== undefined)
            root.preserveDisplayAcrossMonitors = data.preserveDisplayAcrossMonitors === true
          if (data.autoSwitchAudioOutput !== undefined)
            root.autoSwitchAudioOutput = data.autoSwitchAudioOutput === true
          if (data.streamMode) root.streamMode = String(data.streamMode)
          if (data.streamModes && data.streamModes.length)
            root.streamModes = data.streamModes
          if (data.captureEncode) root.captureEncode = String(data.captureEncode)
          if (data.encodeProfile) {
            var ep = String(data.encodeProfile).toLowerCase()
            if (ep === "high" || ep === "medium" || ep === "low")
              root.encodeProfile = ep
          }
          if (data.capturePath) root.capturePath = String(data.capturePath)
          else if (!root.streaming) root.capturePath = ""
          if (data.encoder) root.encoder = String(data.encoder)
          else if (!root.streaming) root.encoder = ""
          root.captureFallback = data.captureFallback === true
          if (data.p2pWifiInterface !== undefined && data.p2pWifiInterface !== null)
            root.p2pWifiInterface = String(data.p2pWifiInterface || "auto")
          if (data.p2pWifiResolved !== undefined && data.p2pWifiResolved !== null)
            root.p2pWifiResolved = String(data.p2pWifiResolved || "")
          if (data.p2pWifiRadios)
            root.p2pWifiRadios = data.p2pWifiRadios
          if (root.captureFallback && root.streaming
              && String(root.actionStatus).indexOf("RENDER ENGINE") < 0)
            root.actionStatus = "RENDER ENGINE fell back to "
                + root.captureEncodeLabel(root.captureEncodeActive)
          if (data.peer) root.persistPeer(data.peer, data.peerName || root.lastPeerName)
          else if (data.peerName) root.lastPeerName = String(data.peerName)
          if (data.monitor) root.castMonitor = String(data.monitor)
          // Drop stale doctor "Ready to cast" once a session is up.
          if (root.active && String(root.actionStatus).indexOf("Ready") === 0)
            root.actionStatus = ""
          // Surface setup/stream failures instead of quietly returning to idle.
          if (root.phase === "error" && data.message) {
            root.lastError = String(data.message)
          } else if (root.phase === "idle") {
            var msg = String(data.message || "")
            if (/fail|disconnect|dropped|error/i.test(msg) && msg !== "Idle" && msg !== "Stopped") {
              root.lastError = msg
            } else if (msg === "Idle" || msg === "Stopped" || msg === "Session ended") {
              // keep prior error until next explicit start/scan clears it
            }
          }
        } catch (e) {
        }
      }
    }
  }

  Process {
    id: startProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false) {
            root.phase = "error"
            root.lastError = String(data.error || "Start failed")
            root.actionStatus = root.lastError
          } else {
            root.actionStatus = "Cast started"
            root.phase = String(data.phase || "connecting")
            if (data.mode) root.mode = String(data.mode)
            if (data.peer) root.persistPeer(data.peer, data.peerName || "")
          }
        } catch (e) {
          root.actionStatus = "Cast launched"
        }
        root.refresh()
      }
    }
  }

  Process {
    id: stopProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.phase = "idle"
        root.running = false
        root.statusText = Model.miracastPhaseHint("idle", "")
        if (root._pendingRestartPeer !== "") {
          var peer = root._pendingRestartPeer
          root._pendingRestartPeer = ""
          root.actionStatus = "Reconnecting with " + root.modeLabel + "…"
          pendingRestartTimer.peer = peer
          pendingRestartTimer.restart()
        } else if (root._pendingRestartAfterStreamMode !== "") {
          var streamPeer = root._pendingRestartAfterStreamMode
          root._pendingRestartAfterStreamMode = ""
          root.actionStatus = "Reconnecting with new stream mode…"
          pendingRestartTimer.peer = streamPeer
          pendingRestartTimer.restart()
        } else {
          root.actionStatus = "Stopped"
        }
        root.refresh()
      }
    }
  }

  Process {
    id: p2pWifiProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false) {
            root.lastError = String(data.error || "Failed to set P2P Wi-Fi iface")
            root.actionStatus = root.lastError
          } else {
            if (data.p2pWifiInterface)
              root.p2pWifiInterface = String(data.p2pWifiInterface)
            if (data.resolved !== undefined)
              root.p2pWifiResolved = String(data.resolved || "")
            root.actionStatus = "RADIO: " + root.p2pWifiLabel(root.p2pWifiInterface)
            root.lastError = ""
          }
        } catch (e) {
          root.lastError = "RADIO parse failed"
        }
        root.refresh()
      }
    }
  }

  Process {
    id: captureEncodeProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false) {
            root.lastError = String(data.error || "Failed to set RENDER ENGINE")
            root.actionStatus = ""
          } else {
            if (data.captureEncode) root.captureEncode = String(data.captureEncode)
            if (data.encodeProfile) {
              var ep = String(data.encodeProfile).toLowerCase()
              if (ep === "high" || ep === "medium" || ep === "low")
                root.encodeProfile = ep
            }
            if (data.capturePath) root.capturePath = String(data.capturePath)
            if (data.encoder) root.encoder = String(data.encoder)
            root.captureFallback = data.captureFallback === true
            var label = root.captureEncodeLabel(root.captureEncode)
            if (root.captureFallback)
              root.actionStatus = "RENDER ENGINE fell back to "
                  + root.captureEncodeLabel(root.captureEncodeActive)
            else if (data.restarted || data.captureRestarted)
              root.actionStatus = "RENDER ENGINE: " + label + " (applied)"
            else if (data.needsReconnect)
              root.actionStatus = "RENDER ENGINE: " + label
                  + " — reconnect to apply quality knobs"
            else
              root.actionStatus = "RENDER ENGINE saved (" + label + ")"
          }
        } catch (e) {
          root.actionStatus = "RENDER ENGINE updated"
        }
        root.refresh()
      }
    }
  }

  Process {
    id: encodeProfileProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false) {
            root.lastError = String(data.error || "Failed to set PRESET QUALITY")
            root.actionStatus = ""
          } else {
            if (data.encodeProfile) root.encodeProfile = String(data.encodeProfile)
            var label = root.encodeProfileLabel(root.encodeProfile)
            if (data.captureRestarted)
              root.actionStatus = "PRESET QUALITY: " + label + " (applied)"
            else if (data.needsReconnect)
              root.actionStatus = "PRESET QUALITY " + label + " — reconnect to apply"
            else
              root.actionStatus = "PRESET QUALITY: " + label
            root.lastError = ""
          }
        } catch (e) {
          root.actionStatus = "PRESET QUALITY updated"
        }
        root.refresh()
      }
    }
  }

  Process {
    id: streamModeProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false) {
            root.lastError = String(data.error || "Failed to set stream mode")
            root.actionStatus = ""
            root._pendingRestartAfterStreamMode = ""
            return
          }
          if (data.streamMode) root.streamMode = String(data.streamMode)
          if (root._pendingRestartAfterStreamMode !== "") {
            root.stopCast(root.preserveDisplayAcrossMonitors)
          } else {
            root.actionStatus = "Stream mode saved (" + root.streamModeLabel(root.streamMode) + ")"
          }
        } catch (e) {
        }
        root.refresh()
      }
    }
  }

  Process {
    id: firewallProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          root.actionStatus = String(data.message || "Firewall rules requested")
          if (data.firewall_needs_open !== undefined && root.doctor)
            root.doctor.firewall_needs_open = data.firewall_needs_open === true
        } catch (e) {
          root.actionStatus = "Firewall rules requested"
        }
        root.runDoctor(false)
      }
    }
  }

  Process {
    id: infoProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false)
            root.actionStatus = String(data.error || "Help failed to open")
          else
            root.actionStatus = "Miracast help opened"
        } catch (e) {
          root.actionStatus = "Miracast help opened"
        }
      }
    }
  }

  Process {
    id: modeProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.mode) root.mode = String(data.mode)
        } catch (e) {
        }
      }
    }
  }

  Process {
    id: preserveDisplayProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false) {
            root.lastError = String(data.error || "Failed to set persist display")
            return
          }
          if (data.preserveDisplayAcrossMonitors !== undefined)
            root.preserveDisplayAcrossMonitors = data.preserveDisplayAcrossMonitors === true
        } catch (e) {
        }
        root.refresh()
      }
    }
  }

  Process {
    id: autoSwitchAudioProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false) {
            root.lastError = String(data.error || "Failed to set auto-switch audio")
            return
          }
          if (data.autoSwitchAudioOutput !== undefined)
            root.autoSwitchAudioOutput = data.autoSwitchAudioOutput === true
        } catch (e) {
        }
        root.refresh()
      }
    }
  }

  Process {
    id: positionProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.position) root.extendPosition = String(data.position)
          if (data.ok === false) {
            root.lastError = String(data.error || "Failed to set extend position")
            root._restartAfterPosition = false
            root._suppressPositionRecover = false
            return
          }
          // Geometry changes black many sinks if we only USR1-rebind capture
          // while RTSP stays up. Always renegotiate (Persist keeps workspaces).
          if (root._restartAfterPosition && root.lastPeerMac !== "") {
            root._restartAfterPosition = false
            root._pendingRestartPeer = root.lastPeerMac
            root.actionStatus = "Reconnecting after moving display (" + root.extendPositionLabel + ")…"
            root.stopCast(root.preserveDisplayAcrossMonitors)
            return
          }
          root._restartAfterPosition = false
          if (data.applied === true)
            root.actionStatus = "Moved extended display (" + root.extendPositionLabel + ")"
          else
            root.actionStatus = "Extend position saved (" + root.extendPositionLabel + ")"
        } catch (e) {
          root._restartAfterPosition = false
          root._suppressPositionRecover = false
        }
      }
    }
  }
}
