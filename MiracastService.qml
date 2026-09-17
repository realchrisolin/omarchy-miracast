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
  // Card-reported GPU product string for RENDER ENGINE pill subtitles.
  property string gpuDeviceName: ""
  property string gpuVendor: ""
  property bool gpuDmabufLikely: true
  // Encoding strategy: smartview (QVBR+ABR) | performance (DMA-BUF CQP)
  property string encodeStrategy: "smartview"
  property string encodeStrategyFallback: ""
  // Encode quality: best | veryhigh | high | medium | low (knobs depend on captureEncode)
  property string encodeProfile: "medium"
  // Concrete ladder step while Best (Dynamic) is selected (legacy named map).
  property string encodeProfileEffective: "medium"
  // Fine Best step index + short label (qp18 / 15M).
  property var encodeProfileEffectiveStep: null
  property string encodeProfileEffectiveLabel: ""
  // Freeze Best fine-step (default off); only meaningful while Best is selected.
  property bool encodeBestLocked: false
  // Live encode knobs (from settings via status) for ENCODER DETAILS.
  property string vaapiRcMode: ""
  property var vaapiQp: null
  property string vaapiQuality: ""
  property string vaapiIQfactor: ""
  property string bitrate: ""
  property string vaapiBitrate: ""
  property var vaapiGop: null
  property var vaapiAsyncDepth: null
  property string vbvMultiplier: ""
  // Resolved while streaming (from status / latency): dmabuf | pipe
  property string capturePath: ""
  property string encoder: ""
  property bool captureFallback: false
  // Miracast P2P Wi-Fi radio: "auto" or managed iface (e.g. wlan1).
  property string p2pWifiInterface: "auto"
  property string p2pWifiResolved: ""
  property var p2pWifiRadios: []

  // Live P2P / STA radio snapshot (from status → attach_radio_channel_fields).
  property var p2pChannel: null
  property var p2pFreqMHz: null
  property var p2pWidthMHz: null
  property string p2pRole: ""
  property var staChannel: null
  property var staFreqMHz: null
  property var staWidthMHz: null
  property var radioMcc: null
  property var p2pSignalDbm: null
  property var p2pTxBitrateMbps: null
  // Very High bitrate exceeds 2.4 GHz / 20 MHz — only when P2P is 5 GHz+.
  readonly property bool encodeVeryHighAllowed: Model.miracastFreqAllowsVeryHigh(p2pFreqMHz)
  property var p2pRxBitrateMbps: null
  property var p2pTxFailed: null
  property var p2pRxDropMisc: null
  property var p2pTxRetryPercent: null
  // Windowed stats from consecutive status samples (status polls ~2s while casting).
  property var p2pTxRetryPercentWindow: null
  property var p2pThroughputMbps: null   // Δ tx_bytes / Δt
  property var p2pRetriesPerSec: null    // Δ tx_retries / Δt
  property var _prevTxPackets: null
  property var _prevTxRetries: null
  property var _prevTxBytes: null
  property var _prevSampleMs: null

  readonly property var radioLink: ({
    p2pChannel: p2pChannel,
    p2pFreqMHz: p2pFreqMHz,
    p2pWidthMHz: p2pWidthMHz,
    p2pRole: p2pRole,
    staChannel: staChannel,
    staFreqMHz: staFreqMHz,
    staWidthMHz: staWidthMHz,
    radioMcc: radioMcc,
    p2pSignalDbm: p2pSignalDbm,
    p2pTxBitrateMbps: p2pTxBitrateMbps,
    p2pRxBitrateMbps: p2pRxBitrateMbps,
    p2pTxFailed: p2pTxFailed,
    p2pRxDropMisc: p2pRxDropMisc,
    p2pThroughputMbps: p2pThroughputMbps,
    p2pRetriesPerSec: p2pRetriesPerSec,
    p2pTxRetryPercent: (p2pTxRetryPercentWindow !== null && p2pTxRetryPercentWindow !== undefined)
      ? p2pTxRetryPercentWindow : p2pTxRetryPercent
  })
  readonly property bool hasRadioLink: p2pChannel !== null && p2pChannel !== undefined

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
  readonly property bool busy: doctorProcess.running || scanProcess.running || startProcess.running || stopProcess.running || firewallProcess.running || modeProcess.running || positionProcess.running || streamModeProcess.running || captureEncodeProcess.running || encodeProfileProcess.running || encodeStrategyProcess.running || preserveDisplayProcess.running || autoSwitchAudioProcess.running || bestLockProcess.running || p2pWifiProcess.running
  // All managed ifaces (for helpers / MORE list).
  readonly property var p2pWifiAllIfaces: {
    var out = []
    var radios = p2pWifiRadios || []
    for (var i = 0; i < radios.length; i++) {
      var iface = radios[i] && radios[i].iface ? String(radios[i].iface) : ""
      if (iface !== "" && out.indexOf(iface) < 0)
        out.push(iface)
    }
    return out
  }

  // Same preference as scripts/list_p2p_radios.py resolve_iface("auto"):
  // P2P-GO → prefer idle → wlan/wlp name → iface sort. USB dongles without a
  // STA association typically win over the laptop NIC that holds home Wi‑Fi.
  readonly property string p2pWifiBestIface: {
    var radios = p2pWifiRadios || []
    if (radios.length === 0)
      return ""
    var go = []
    var i
    for (i = 0; i < radios.length; i++) {
      if (radios[i] && radios[i].p2pGo)
        go.push(radios[i])
    }
    var pool = go.length > 0 ? go : radios.slice()
    var idle = []
    for (i = 0; i < pool.length; i++) {
      if (pool[i] && !pool[i].inUse)
        idle.push(pool[i])
    }
    if (idle.length > 0)
      pool = idle
    function score(r) {
      var iface = String((r && r.iface) || "")
      var goBit = (r && r.p2pGo) ? 1 : 0
      var idleBit = (r && r.inUse) ? 0 : 1
      var nameBit = /^(wlan|wlp)\d/.test(iface) ? 1 : 0
      return [goBit, idleBit, nameBit, iface]
    }
    function better(a, b) {
      var sa = score(a), sb = score(b)
      for (var k = 0; k < 3; k++) {
        if (sa[k] !== sb[k])
          return sa[k] > sb[k]
      }
      return sa[3] > sb[3]  // reverse=True string sort → lexicographically greater wins? 
      // Python: sorted(..., reverse=True) on (int,int,int,str) — for equal ints, larger str wins.
    }
    var best = pool[0]
    for (i = 1; i < pool.length; i++) {
      if (better(pool[i], best))
        best = pool[i]
    }
    return best && best.iface ? String(best.iface) : ""
  }

  // Primary pills: Auto + single best iface (dongle-ready when one appears).
  readonly property var p2pWifiPrimaryValues: {
    var out = ["auto"]
    var best = String(p2pWifiBestIface || "")
    if (best !== "")
      out.push(best)
    return out
  }

  // Extra ifaces behind MORE INTERFACES (everything except the best pick).
  readonly property var p2pWifiMoreValues: {
    var best = String(p2pWifiBestIface || "")
    var out = []
    var all = p2pWifiAllIfaces || []
    for (var i = 0; i < all.length; i++) {
      var iface = String(all[i] || "")
      if (iface !== "" && iface !== best)
        out.push(iface)
    }
    return out
  }

  // Back-compat: all pill values Auto + every iface (keyboard may use primary+more).
  readonly property var p2pWifiValues: {
    var out = ["auto"]
    var all = p2pWifiAllIfaces || []
    for (var i = 0; i < all.length; i++) {
      if (out.indexOf(all[i]) < 0)
        out.push(all[i])
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
  readonly property var encoderDetailLines: Model.miracastEncoderDetailLines({
    captureEncode: captureEncode,
    capturePath: capturePath,
    encoder: encoder,
    vaapiRcMode: vaapiRcMode,
    vaapiQp: vaapiQp,
    vaapiQuality: vaapiQuality,
    vaapiIQfactor: vaapiIQfactor,
    bitrate: bitrate,
    vaapiBitrate: vaapiBitrate,
    vaapiGop: vaapiGop,
    vaapiAsyncDepth: vaapiAsyncDepth,
    vbvMultiplier: vbvMultiplier,
    encodeProfile: encodeProfile,
    encodeProfileEffective: encodeProfileEffective,
    encodeProfileEffectiveLabel: encodeProfileEffectiveLabel,
    encodeBestLocked: encodeBestLocked
  })

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

  function applyEncodeKnobsFromStatus(data) {
    if (!data) return
    if (data.vaapiRcMode !== undefined && data.vaapiRcMode !== null)
      vaapiRcMode = String(data.vaapiRcMode || "")
    if (data.vaapiQp !== undefined && data.vaapiQp !== null && data.vaapiQp !== "")
      vaapiQp = data.vaapiQp
    if (data.vaapiQuality !== undefined && data.vaapiQuality !== null)
      vaapiQuality = String(data.vaapiQuality || "")
    if (data.vaapiIQfactor !== undefined && data.vaapiIQfactor !== null)
      vaapiIQfactor = String(data.vaapiIQfactor || "")
    if (data.bitrate !== undefined && data.bitrate !== null)
      bitrate = String(data.bitrate || "")
    if (data.vaapiBitrate !== undefined && data.vaapiBitrate !== null)
      vaapiBitrate = String(data.vaapiBitrate || "")
    if (data.vaapiGop !== undefined && data.vaapiGop !== null && data.vaapiGop !== "")
      vaapiGop = data.vaapiGop
    if (data.vaapiAsyncDepth !== undefined && data.vaapiAsyncDepth !== null && data.vaapiAsyncDepth !== "")
      vaapiAsyncDepth = data.vaapiAsyncDepth
    if (data.vbvMultiplier !== undefined && data.vbvMultiplier !== null)
      vbvMultiplier = String(data.vbvMultiplier || "")
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

  // fix=true (panel Doctor): diagnose + open UFW ports when missing.
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

  function setEncodeBestLocked(enabled) {
    var on = !!enabled
    if (encodeProfile !== "best") return
    if (on === encodeBestLocked && !bestLockProcess.running) return
    encodeBestLocked = on
    if (bestLockProcess.running) return
    lastError = ""
    actionStatus = on ? "Lock Best: on" : "Lock Best: off"
    bestLockProcess.command = [ctl, "set-best-lock", on ? "true" : "false"]
    bestLockProcess.running = true
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
    return key
  }

  // Adapter name for a pill value: Auto → currently resolved radio; else that iface.
  function p2pWifiDeviceName(id) {
    var key = String(id || "")
    var radios = p2pWifiRadios || []
    var want = ""
    if (key === "" || key === "auto")
      want = String(p2pWifiResolved || "")
    else
      want = key
    if (want === "") {
      // Auto before resolve: fall back to the sole / preferred GO radio.
      for (var i = 0; i < radios.length; i++) {
        if (radios[i] && radios[i].p2pGo)
          return String(radios[i].adapterName || radios[i].driver || "").trim()
      }
      if (radios.length > 0 && radios[0])
        return String(radios[0].adapterName || radios[0].driver || "").trim()
      return ""
    }
    for (var j = 0; j < radios.length; j++) {
      if (radios[j] && String(radios[j].iface) === want)
        return String(radios[j].adapterName || radios[j].driver || "").trim()
    }
    return ""
  }

  // Two-line RADIO pill: title + "(device name)" underneath when known.
  function p2pWifiPillText(id) {
    var title = p2pWifiLabel(id)
    var device = p2pWifiDeviceName(id)
    if (device === "")
      return title
    return title + "\n(" + device + ")"
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

  function captureEncodePillTitle(id) {
    return Model.miracastCaptureEncodePillTitle(id)
  }

  function captureEncodePillSubtitle(id) {
    return Model.miracastCaptureEncodePillSubtitle(id, gpuDeviceName)
  }

  function encodeProfileLabel(id) {
    return Model.miracastEncodeProfileLabel(id)
  }

  function encodeStrategyLabel(id) {
    var base = Model.miracastEncodeStrategyLabel(id)
    if (String(id || "") === "smartview" && String(encodeStrategyFallback || "") === "pipe")
      return base + " (pipe fallback)"
    return base
  }

  function setEncodeStrategy(value) {
    var next = String(value || "").toLowerCase().replace(/[-_]/g, "")
    if (next === "sv" || next === "qvbr" || next === "default") next = "smartview"
    if (next === "perf" || next === "cqp") next = "performance"
    if (next !== "smartview" && next !== "performance") return
    if (encodeStrategyProcess.running || stopProcess.running) return
    if (next === encodeStrategy && !active) return
    encodeStrategy = next
    if (next !== "smartview") encodeStrategyFallback = ""
    lastError = ""
    if (active)
      actionStatus = "ENCODING STRATEGY → " + encodeStrategyLabel(next) + "…"
    else
      actionStatus = "ENCODING STRATEGY: " + encodeStrategyLabel(next)
    encodeStrategyProcess.command = [ctl, "set-encode-strategy", next]
    encodeStrategyProcess.running = true
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
    var next = String(value || "").toLowerCase().replace(/[-_]/g, "")
    if (next !== "best" && next !== "veryhigh" && next !== "high"
        && next !== "medium" && next !== "low") return
    if (encodeProfileProcess.running || stopProcess.running) return
    if (next === "veryhigh" && !encodeVeryHighAllowed) {
      lastError = "Very High needs 5 GHz+ P2P (bitrate exceeds 2.4 GHz budget)"
      actionStatus = lastError
      return
    }
    if (next === encodeProfile && !active) return
    encodeProfile = next
    if (next === "best") {
      encodeProfileEffective = "high"
      encodeProfileEffectiveLabel = ""
      encodeProfileEffectiveStep = null
    } else {
      encodeProfileEffective = next
      encodeProfileEffectiveLabel = ""
      encodeProfileEffectiveStep = null
      encodeBestLocked = false
    }
    lastError = ""
    if (active)
      actionStatus = "PRESET QUALITY → " + encodeProfileLabel(next) + "…"
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
  // Do NOT force-restart on a single unhealthy poll — DMA sticky rebinds
  // briefly drop wf-recorder and that used to pause every ~15–30s.
  property int _captureUnhealthyStreak: 0
  property double _lastCaptureRecoverMs: 0

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
          if (data.paused === true) {
            root._captureUnhealthyStreak = 0
            return
          }
          if (data.healthy === true) {
            root._captureUnhealthyStreak = 0
            return
          }
          // TV already dark / air TX dead — Stop so the connected icon clears.
          // ensure-capture would keep phase=streaming and look "still connected".
          if (data.zombie === true) {
            root._captureUnhealthyStreak = 0
            root.actionStatus = ""
            root.lastError = "Cast ended — media stalled"
            if (root.streaming || root.running)
              root.stopCast()
            else
              root.refresh()
            return
          }
          root._captureUnhealthyStreak += 1
          // ~12s of consecutive unhealthy (3×4s) before healing.
          if (root._captureUnhealthyStreak < 3) return
          var now = Date.now()
          // Cooldown after a recovery restart — avoid rebind storms.
          if (now - root._lastCaptureRecoverMs < 45000) return
          if (ensureWatchdogProcess.running) return
          root.actionStatus = "Recovering capture…"
          // No "force" — if senders are already back, ensure is a no-op.
          ensureWatchdogProcess.command = [ctl, "ensure-capture", "2"]
          ensureWatchdogProcess.running = true
          root._captureUnhealthyStreak = 0
          root._lastCaptureRecoverMs = now
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
          if (data.encodeBestLocked !== undefined)
            root.encodeBestLocked = data.encodeBestLocked === true
          if (data.streamMode) root.streamMode = String(data.streamMode)
          if (data.streamModes && data.streamModes.length)
            root.streamModes = data.streamModes
          if (data.captureEncode) root.captureEncode = String(data.captureEncode)
          if (data.encodeStrategy) {
            var es = String(data.encodeStrategy).toLowerCase().replace(/[-_]/g, "")
            if (es === "smartview" || es === "performance")
              root.encodeStrategy = es
          }
          if (data.encodeStrategyFallback !== undefined && data.encodeStrategyFallback !== null)
            root.encodeStrategyFallback = String(data.encodeStrategyFallback || "")
          if (data.gpuDeviceName !== undefined && data.gpuDeviceName !== null)
            root.gpuDeviceName = String(data.gpuDeviceName || "")
          if (data.gpuVendor !== undefined && data.gpuVendor !== null)
            root.gpuVendor = String(data.gpuVendor || "")
          if (data.gpuDmabufLikely !== undefined)
            root.gpuDmabufLikely = data.gpuDmabufLikely === true
          if (data.encodeProfile) {
            var ep = String(data.encodeProfile).toLowerCase().replace(/[-_]/g, "")
            if (ep === "best" || ep === "veryhigh" || ep === "high"
                || ep === "medium" || ep === "low")
              root.encodeProfile = ep
          }
          if (data.encodeProfileEffective) {
            var epe = String(data.encodeProfileEffective).toLowerCase().replace(/[-_]/g, "")
            if (epe === "veryhigh" || epe === "high" || epe === "medium" || epe === "low")
              root.encodeProfileEffective = epe
          }
          if (data.encodeProfileEffectiveStep !== undefined && data.encodeProfileEffectiveStep !== null
              && data.encodeProfileEffectiveStep !== "")
            root.encodeProfileEffectiveStep = data.encodeProfileEffectiveStep
          if (data.encodeProfileEffectiveLabel !== undefined && data.encodeProfileEffectiveLabel !== null)
            root.encodeProfileEffectiveLabel = String(data.encodeProfileEffectiveLabel || "")
          root.applyEncodeKnobsFromStatus(data)
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

          // Radio / link snapshot for the Display hero (no SSIDs).
          function _num(v) {
            if (v === undefined || v === null || v === "") return null
            var n = Number(v)
            return isFinite(n) ? n : null
          }
          root.p2pChannel = _num(data.p2pChannel)
          root.p2pFreqMHz = _num(data.p2pFreqMHz)
          root.p2pWidthMHz = _num(data.p2pWidthMHz)
          root.p2pRole = data.p2pRole ? String(data.p2pRole) : ""
          root.staChannel = _num(data.staChannel)
          root.staFreqMHz = _num(data.staFreqMHz)
          root.staWidthMHz = _num(data.staWidthMHz)
          root.radioMcc = (data.radioMcc === true) ? true
            : (data.radioMcc === false) ? false : null
          root.p2pSignalDbm = _num(data.p2pSignalDbm)
          root.p2pTxBitrateMbps = _num(data.p2pTxBitrateMbps)
          root.p2pRxBitrateMbps = _num(data.p2pRxBitrateMbps)
          root.p2pTxFailed = _num(data.p2pTxFailed)
          root.p2pRxDropMisc = _num(data.p2pRxDropMisc)
          root.p2pTxRetryPercent = _num(data.p2pTxRetryPercent)
          var pk = _num(data.p2pTxPackets)
          var rt = _num(data.p2pTxRetries)
          var tb = _num(data.p2pTxBytes)
          var nowMs = Date.now()
          if (root._prevSampleMs !== null && nowMs > root._prevSampleMs) {
            var dtSec = (nowMs - root._prevSampleMs) / 1000.0
            if (dtSec > 0.2 && dtSec < 30) {
              if (tb !== null && root._prevTxBytes !== null && tb >= root._prevTxBytes) {
                // bytes/sec → megabits/sec (SI): *8 / 1e6
                var bitsPerSec = 8.0 * (tb - root._prevTxBytes) / dtSec
                root.p2pThroughputMbps = Math.round(bitsPerSec / 1e6 * 100) / 100
              }
              if (rt !== null && root._prevTxRetries !== null && rt >= root._prevTxRetries) {
                root.p2pRetriesPerSec =
                  Math.round(10.0 * (rt - root._prevTxRetries) / dtSec) / 10
              }
              if (pk !== null && rt !== null && root._prevTxPackets !== null
                  && root._prevTxRetries !== null) {
                var dpk = pk - root._prevTxPackets
                var drt = rt - root._prevTxRetries
                if (dpk > 0 && drt >= 0)
                  root.p2pTxRetryPercentWindow = Math.round(10000.0 * drt / dpk) / 100.0
                else if (dpk === 0)
                  root.p2pTxRetryPercentWindow = 0
              }
            }
          }
          if (pk !== null) root._prevTxPackets = pk
          if (rt !== null) root._prevTxRetries = rt
          if (tb !== null) root._prevTxBytes = tb
          root._prevSampleMs = nowMs
          if (root.p2pChannel === null) {
            root.p2pTxRetryPercentWindow = null
            root.p2pThroughputMbps = null
            root.p2pRetriesPerSec = null
            root._prevTxPackets = null
            root._prevTxRetries = null
            root._prevTxBytes = null
            root._prevSampleMs = null
          }

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
          if (data.gpuDeviceName !== undefined && data.gpuDeviceName !== null)
            root.gpuDeviceName = String(data.gpuDeviceName || "")
          if (data.gpuVendor !== undefined && data.gpuVendor !== null)
            root.gpuVendor = String(data.gpuVendor || "")
          if (data.gpuDmabufLikely !== undefined)
            root.gpuDmabufLikely = data.gpuDmabufLikely === true
            if (data.encodeProfile) {
              var ep = String(data.encodeProfile).toLowerCase().replace(/[-_]/g, "")
              if (ep === "best" || ep === "veryhigh" || ep === "high"
                  || ep === "medium" || ep === "low")
                root.encodeProfile = ep
            }
            if (data.encodeProfileEffective) {
              var epe2 = String(data.encodeProfileEffective).toLowerCase().replace(/[-_]/g, "")
              if (epe2 === "veryhigh" || epe2 === "high" || epe2 === "medium" || epe2 === "low")
                root.encodeProfileEffective = epe2
            }
            if (data.encodeProfileEffectiveStep !== undefined && data.encodeProfileEffectiveStep !== null
                && data.encodeProfileEffectiveStep !== "")
              root.encodeProfileEffectiveStep = data.encodeProfileEffectiveStep
            if (data.encodeProfileEffectiveLabel !== undefined && data.encodeProfileEffectiveLabel !== null)
              root.encodeProfileEffectiveLabel = String(data.encodeProfileEffectiveLabel || "")
            root.applyEncodeKnobsFromStatus(data)
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
            if (data.encodeProfile) {
              var ep3 = String(data.encodeProfile).toLowerCase().replace(/[-_]/g, "")
              if (ep3 === "best" || ep3 === "veryhigh" || ep3 === "high"
                  || ep3 === "medium" || ep3 === "low")
                root.encodeProfile = ep3
            }
            if (data.encodeProfileEffective) {
              var epe3 = String(data.encodeProfileEffective).toLowerCase().replace(/[-_]/g, "")
              if (epe3 === "veryhigh" || epe3 === "high" || epe3 === "medium" || epe3 === "low")
                root.encodeProfileEffective = epe3
            }
            if (data.encodeProfileEffectiveStep !== undefined && data.encodeProfileEffectiveStep !== null
                && data.encodeProfileEffectiveStep !== "")
              root.encodeProfileEffectiveStep = data.encodeProfileEffectiveStep
            if (data.encodeProfileEffectiveLabel !== undefined && data.encodeProfileEffectiveLabel !== null)
              root.encodeProfileEffectiveLabel = String(data.encodeProfileEffectiveLabel || "")
            if (data.encodeBestLocked !== undefined)
              root.encodeBestLocked = data.encodeBestLocked === true
            else if (root.encodeProfile !== "best")
              root.encodeBestLocked = false
            root.applyEncodeKnobsFromStatus(data)
            var label = root.encodeProfileLabel(root.encodeProfile)
            if (root.encodeProfile === "best") {
              var bl = String(root.encodeProfileEffectiveLabel || "").trim()
              if (bl !== "")
                label = "Best (" + bl + ")"
              else if (root.encodeProfileEffective)
                label = "Best (" + root.encodeProfileLabel(root.encodeProfileEffective) + ")"
            }
            if (data.captureRestarted)
              root.actionStatus = "PRESET QUALITY: " + label + " (applied)"
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
    id: encodeStrategyProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false) {
            root.lastError = String(data.error || "Failed to set encoding strategy")
            root.actionStatus = ""
            return
          }
          if (data.encodeStrategy) root.encodeStrategy = String(data.encodeStrategy)
          if (data.encodeStrategyFallback !== undefined)
            root.encodeStrategyFallback = String(data.encodeStrategyFallback || "")
          if (data.captureEncode) root.captureEncode = String(data.captureEncode)
          if (data.vaapiRcMode) root.vaapiRcMode = String(data.vaapiRcMode)
          root.actionStatus = "ENCODING STRATEGY: " + root.encodeStrategyLabel(root.encodeStrategy)
            + (data.captureRestarted ? " (applied)" : "")
        } catch (e) {
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
    id: bestLockProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text || "{}"))
          if (data.ok === false) {
            root.lastError = String(data.error || "Failed to set Lock Best")
            root.encodeBestLocked = false
            return
          }
          if (data.encodeBestLocked !== undefined)
            root.encodeBestLocked = data.encodeBestLocked === true
          root.actionStatus = root.encodeBestLocked
            ? "Lock Best: on (QP frozen)"
            : "Lock Best: off"
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
