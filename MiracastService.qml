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
  property string mode: "mirror"   // mirror | extend
  property string extendPosition: "right"  // right | left | above | below
  property string streamMode: "1280x720p30"  // e.g. 1920x1080p30
  property var streamModes: []

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
  readonly property bool busy: doctorProcess.running || scanProcess.running || startProcess.running || stopProcess.running || firewallProcess.running || modeProcess.running || positionProcess.running || streamModeProcess.running
  readonly property bool active: Model.miracastIsActive(phase)
  readonly property bool connecting: phase === "connecting" || phase === "dhcp" || phase === "rtsp" || phase === "scanning"
  readonly property bool streaming: phase === "streaming"

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

  function runDoctor() {
    if (doctorProcess.running) return
    actionStatus = "Running doctor…"
    doctorProcess.command = [ctl, "doctor"]
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

  function openFirewall() {
    if (firewallProcess.running) return
    actionStatus = "Opening UFW Miracast ports…"
    firewallProcess.command = [ctl, "firewall-open"]
    firewallProcess.running = true
  }

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
    // Mode only applies on the next session — restart if already casting.
    if (wasActive && peer !== "") {
      actionStatus = "Switching to " + (value === "extend" ? "Extend" : "Mirror") + "…"
      stopCast()
      Qt.callLater(function() { root.startCast(peer) })
    }
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

  function stopCast() {
    if (stopProcess.running) return
    _pausedForLock = false
    _sessionLocked = false
    resumeLockTimer.stop()
    actionStatus = "Stopping…"
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
      root.actionStatus = "Reconnecting after moving display…"
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
          root.actionStatus = root.peers.length + " peer" + (root.peers.length === 1 ? "" : "s") + " found"
          if (root.phase === "scanning") {
            root.phase = "idle"
            root.statusText = Model.miracastPhaseHint("idle", "")
          }
          if (root.peers.length === 1) root.persistPeer(root.peers[0].mac, root.peers[0].name)
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
          if (data.streamMode) root.streamMode = String(data.streamMode)
          if (data.streamModes && data.streamModes.length)
            root.streamModes = data.streamModes
          if (data.peer) root.persistPeer(data.peer, data.peerName || root.lastPeerName)
          else if (data.peerName) root.lastPeerName = String(data.peerName)
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
          root.actionStatus = "Reconnecting after moving display…"
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
            root.stopCast()
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
        root.actionStatus = "Firewall rules requested"
        root.runDoctor()
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
          // Prefer live capture rebind (keeps Miracast RTSP/P2P). Fall back to
          // full reconnect only when FluxCast could not restart the pipeline.
          if (root._restartAfterPosition && root.lastPeerMac !== "") {
            root._restartAfterPosition = false
            if (data.captureRestarted === true && data.captureHealthy !== false) {
              root.actionStatus = "Moved extended display (" + root.extendPositionLabel + ") — connection kept"
              root.positionWarning = ""
              return
            }
            root._pendingRestartPeer = root.lastPeerMac
            root.actionStatus = "Reconnecting after moving display (" + root.extendPositionLabel + ")…"
            root.stopCast()
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
