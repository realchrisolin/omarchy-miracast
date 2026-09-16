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

// Infer Extend side from live Hyprland geometry (logical boxes). Used so the
// position pills track reality when eDP scale / monitors.lua reload shoves the
// headless to a different edge than settings.json claims.
function inferExtendPosition(displays) {
  if (!Array.isArray(displays)) return ""
  var primary = null
  var virt = null
  var i
  for (i = 0; i < displays.length; i++) {
    var d = displays[i]
    if (!d || d.enabled === false) continue
    if (d.miracast) {
      if (!virt) virt = d
      continue
    }
    if (d.focused) primary = d
  }
  if (!primary) {
    for (i = 0; i < displays.length; i++) {
      if (displays[i] && displays[i].enabled !== false && !displays[i].miracast) {
        primary = displays[i]
        break
      }
    }
  }
  if (!primary || !virt) return ""

  var ps = Number(primary.scale) || 1
  var vs = Number(virt.scale) || 1
  if (!(ps > 0)) ps = 1
  if (!(vs > 0)) vs = 1
  var plw = Math.round(Number(primary.width) / ps)
  var plh = Math.round(Number(primary.height) / ps)
  var vlw = Math.round(Number(virt.width) / vs)
  var vlh = Math.round(Number(virt.height) / vs)
  var px = Number(primary.x) || 0
  var py = Number(primary.y) || 0
  var vx = Number(virt.x) || 0
  var vy = Number(virt.y) || 0
  var dx = (vx + vlw / 2) - (px + plw / 2)
  var dy = (vy + vlh / 2) - (py + plh / 2)
  if (Math.abs(dx) >= Math.abs(dy))
    return dx < 0 ? "left" : "right"
  return dy < 0 ? "above" : "below"
}

function miracastPhaseLabel(phase, iface, adapterName) {
  var value = String(phase || "idle")
  if (value === "idle") return "Idle"
  if (value === "scanning") return "Scanning"
  if (value === "connecting") return "Connecting"
  if (value === "dhcp") return "Waiting for IP"
  if (value === "rtsp") return "Starting session"
  if (value === "streaming") {
    var ifc = String(iface || "").trim()
    var adapter = String(adapterName || "").trim()
    if (ifc !== "" && adapter !== "")
      return "Casting on " + ifc + " (" + adapter + ")"
    if (ifc !== "")
      return "Casting on " + ifc
    return "Casting"
  }
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
  if (value === "streaming") return "Desktop is casting"
  if (value === "error") return "Cast failed — run Doctor"
  return "Ready"
}

function miracastIsActive(phase) {
  var value = String(phase || "idle")
  return value === "connecting" || value === "dhcp" || value === "rtsp" || value === "streaming" || value === "scanning"
}

function miracastDoctorSummary(doctor) {
  if (!doctor) return "Doctor not run yet"
  var suffix = ""
  if (doctor.firewall_fix_started === true)
    suffix = " — opening UFW ports (approve sudo if prompted)"
  if (doctor.ready === true) {
    var warns = typeof doctor.warn_count === "number" ? doctor.warn_count : 0
    if (warns > 0) {
      var names = doctor.warn_names
      var hint = ""
      if (names && names.length)
        hint = " (" + names.slice(0, 3).join(", ") + (names.length > 3 ? "…" : "") + ")"
      return "Ready with " + warns + " warning" + (warns === 1 ? "" : "s") + hint + suffix
    }
    return "Ready to cast" + suffix
  }
  var fails = typeof doctor.fail_count === "number" ? doctor.fail_count : 0
  return fails + " blocking issue" + (fails === 1 ? "" : "s") + suffix
}

function miracastFirewallNeedsOpen(doctor) {
  if (!doctor) return true
  if (doctor.firewall_needs_open === true) return true
  if (doctor.firewall_needs_open === false) return false
  return true
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

/** RENDER ENGINE preference / pill ids. */
function miracastCaptureEncodeValues() {
  return ["dmabuf", "vaapi", "cpu"]
}

/** Encode quality tier pill ids (per-engine knobs). */
function miracastEncodeProfileValues() {
  return ["high", "medium", "low"]
}

function miracastEncodeProfileLabel(id) {
  var v = String(id || "")
  if (v === "high") return "High"
  if (v === "medium") return "Medium"
  if (v === "low") return "Low"
  return v || "Medium"
}

function miracastCaptureEncodeLabel(value) {
  var v = String(value || "")
  if (v === "dmabuf") return "GPU · DMA-BUF"
  if (v === "vaapi") return "GPU · VAAPI"
  if (v === "cpu") return "CPU"
  return v
}

/**
 * Active RENDER ENGINE pill while streaming: map resolved capturePath/encoder
 * to dmabuf|vaapi|cpu. Idle UIs should use the preference instead.
 */
function miracastCaptureEncodeActive(capturePath, encoder, preference) {
  var path = String(capturePath || "").toLowerCase()
  var enc = String(encoder || "").toLowerCase()
  if (path === "dmabuf") return "dmabuf"
  if (enc === "libx264" || enc === "x264" || enc === "software" || enc === "sw")
    return "cpu"
  if (path === "pipe") {
    if (enc.indexOf("vaapi") >= 0 || enc.indexOf("qsv") >= 0 || enc === "vaapi" || enc === "qsv")
      return "vaapi"
    if (enc) return "cpu"
    return "vaapi"
  }
  var pref = String(preference || "dmabuf")
  if (pref === "dmabuf" || pref === "vaapi" || pref === "cpu") return pref
  return "dmabuf"
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
    inferExtendPosition: inferExtendPosition,
    miracastPhaseLabel: miracastPhaseLabel,
    miracastPhaseHint: miracastPhaseHint,
    miracastIsActive: miracastIsActive,
    miracastDoctorSummary: miracastDoctorSummary,
    miracastFirewallNeedsOpen: miracastFirewallNeedsOpen,
    miracastPeerTitle: miracastPeerTitle,
    miracastPeerSubtitle: miracastPeerSubtitle,
    miracastBarGlyph: miracastBarGlyph,
    miracastConnectionSummary: miracastConnectionSummary,
    miracastCaptureEncodeValues: miracastCaptureEncodeValues,
    miracastCaptureEncodeLabel: miracastCaptureEncodeLabel,
    miracastCaptureEncodeActive: miracastCaptureEncodeActive
  }
}
