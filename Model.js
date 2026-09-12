function clampBrightness(value) {
  var n = Number(value)
  if (!isFinite(n)) return 1
  return Math.max(1, Math.min(100, Math.round(n)))
}

function normalizeScale(scale) {
  var n = parseFloat(String(scale || ""))
  if (!isFinite(n)) return ""
  return String(Math.round(n * 100) / 100)
}

function gcd(a, b) {
  while (b) {
    var remainder = a % b
    a = b
    b = remainder
  }
  return a
}

function cleanScale(scale, width, height) {
  var requested = Number(scale)
  var modeWidth = Number(width)
  var modeHeight = Number(height)
  if (!isFinite(requested) || !isFinite(modeWidth) || !isFinite(modeHeight)
      || requested <= 0 || modeWidth <= 0 || modeHeight <= 0) return ""

  var divisor = gcd(Math.round(modeWidth * 120), Math.round(modeHeight * 120))
  var scaleUnits = Math.round(requested * 120)
  if (scaleUnits > divisor) scaleUnits = divisor
  while (divisor % scaleUnits !== 0) scaleUnits++
  return normalizeScale(scaleUnits / 120)
}

function matchingScaleIndex(scales, currentScale, width, height) {
  var current = Number(currentScale)
  if (!Array.isArray(scales) || !isFinite(current)) return -1

  var bestIndex = -1
  var bestDistance = Infinity
  var normalizedCurrent = normalizeScale(current)
  for (var i = 0; i < scales.length; i++) {
    if (cleanScale(scales[i], width, height) !== normalizedCurrent) continue

    var distance = Math.abs(Number(scales[i]) - current)
    if (distance < bestDistance) {
      bestIndex = i
      bestDistance = distance
    }
  }
  return bestIndex
}

function availableScales(scales, width, height) {
  if (!Array.isArray(scales) || Number(width) <= 0 || Number(height) <= 0) return scales || []

  var byEffectiveScale = {}
  for (var i = 0; i < scales.length; i++) {
    var requested = Number(scales[i])
    var effective = Number(cleanScale(requested, width, height))

    if (!isFinite(requested) || !isFinite(effective)) continue

    var key = normalizeScale(effective)
    var existing = byEffectiveScale[key]
    if (!existing || Math.abs(requested - effective) < existing.distance) {
      byEffectiveScale[key] = {
        value: String(scales[i]),
        index: i,
        distance: Math.abs(requested - effective)
      }
    }
  }

  return Object.keys(byEffectiveScale)
    .map(function(key) { return byEffectiveScale[key] })
    .sort(function(a, b) { return a.index - b.index })
    .map(function(candidate) { return candidate.value })
}

function brightnessName(percent) {
  var p = Math.round(percent)
  if (p >= 95) return "Sun blast"
  if (p >= 80) return "Solar flare"
  if (p >= 65) return "Golden hour"
  if (p >= 45) return "Even day"
  if (p >= 30) return "Soft glow"
  if (p >= 20) return "Lamp light"
  if (p >= 10) return "Candlelit"
  return "Night owl"
}

function parseDisplays(raw) {
  var displays = []
  try {
    displays = raw ? JSON.parse(String(raw)) : []
  } catch (e) {
    displays = []
  }
  if (!Array.isArray(displays)) displays = []

  var count = 0
  for (var i = 0; i < displays.length; i++) {
    if (displays[i] && displays[i].enabled) count++
  }

  return {
    displays: displays,
    enabledDisplayCount: count
  }
}

function miracastPhaseLabel(phase) {
  var value = String(phase || "idle")
  if (value === "idle") return "Idle"
  if (value === "scanning") return "Scanning"
  if (value === "connecting") return "Connecting"
  if (value === "dhcp") return "Waiting for IP"
  if (value === "rtsp") return "Starting session"
  if (value === "streaming") return "Mirroring"
  if (value === "error") return "Error"
  return value
}

function miracastPhaseHint(phase, message) {
  var msg = String(message || "").trim()
  if (msg !== "") return msg
  var value = String(phase || "idle")
  if (value === "idle") return "Scan for a Miracast display, then connect"
  if (value === "scanning") return "Looking for Wi‑Fi Display sinks"
  if (value === "connecting") return "Forming Wi‑Fi Direct group"
  if (value === "dhcp") return "P2P is up — waiting for DHCP / RTSP"
  if (value === "rtsp") return "Negotiating Miracast media"
  if (value === "streaming") return "Desktop is mirroring"
  if (value === "error") return "Cast failed — check doctor / firewall"
  return "Ready"
}

function miracastIsActive(phase) {
  var value = String(phase || "idle")
  return value === "connecting" || value === "dhcp" || value === "rtsp" || value === "streaming" || value === "scanning"
}

function miracastDoctorSummary(doctor) {
  if (!doctor) return "Doctor not run yet"
  if (doctor.ready === true) {
    var warns = typeof doctor.warn_count === "number" ? doctor.warn_count : 0
    if (warns > 0) return "Ready with " + warns + " warning" + (warns === 1 ? "" : "s")
    return "Ready to cast"
  }
  var fails = typeof doctor.fail_count === "number" ? doctor.fail_count : 0
  return fails + " blocking issue" + (fails === 1 ? "" : "s")
}

function miracastPeerTitle(peer) {
  if (!peer) return "Unknown"
  return String(peer.name || peer.mac || "Unknown")
}

function miracastPeerSubtitle(peer) {
  if (!peer) return ""
  return String(peer.mac || "")
}

function miracastBarGlyph(phase, multiDisplay) {
  var value = String(phase || "idle")
  if (value === "streaming") return "󰖩"      // wifi / connected
  if (value === "connecting" || value === "dhcp" || value === "rtsp") return "󰑋"  // cast / linking
  if (value === "scanning") return "󰍉"       // search
  return multiDisplay ? "󰍺" : "󰍹"
}

function miracastConnectionSummary(phase, peerName, peerMac, mode) {
  var label = String(peerName || "").trim()
  if (label === "") label = String(peerMac || "").trim()
  var modeLabel = String(mode || "mirror") === "extend" ? "Extend" : "Mirror"
  var value = String(phase || "idle")
  if (value === "streaming") {
    if (label !== "") return "Connected · " + label + " · " + modeLabel
    return "Connected · " + modeLabel
  }
  if (value === "connecting" || value === "dhcp" || value === "rtsp") {
    if (label !== "") return "Connecting · " + label
    return "Connecting…"
  }
  if (value === "scanning") return "Scanning for Miracast sinks…"
  if (value === "error") return "Miracast error"
  if (label !== "") return "Last sink · " + label
  return ""
}

if (typeof module !== "undefined") {
  module.exports = {
    clampBrightness: clampBrightness,
    normalizeScale: normalizeScale,
    cleanScale: cleanScale,
    matchingScaleIndex: matchingScaleIndex,
    availableScales: availableScales,
    brightnessName: brightnessName,
    parseDisplays: parseDisplays,
    miracastPhaseLabel: miracastPhaseLabel,
    miracastPhaseHint: miracastPhaseHint,
    miracastIsActive: miracastIsActive,
    miracastDoctorSummary: miracastDoctorSummary,
    miracastPeerTitle: miracastPeerTitle,
    miracastPeerSubtitle: miracastPeerSubtitle,
    miracastBarGlyph: miracastBarGlyph,
    miracastConnectionSummary: miracastConnectionSummary
  }
}
