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
  var parts = []
  var mac = String(peer.mac || "").trim()
  if (mac !== "") parts.push(mac)
  var brand = [peer.manufacturer, peer.model].filter(function (x) {
    return x !== undefined && x !== null && String(x).trim() !== ""
  }).map(function (x) { return String(x).trim() }).join(" ")
  if (brand !== "") parts.push(brand)
  var band = String(peer.band || "").trim()
  if (band === "")
    band = miracastBandLabel(peer.listenFreqMHz || peer.operFreqMHz)
  if (band !== "" && band !== "--") parts.push(band)
  var cat = String(peer.category || peer.device_type || "").trim()
  if (cat !== "") parts.push(cat)
  else if (peer.wfd) parts.push("Miracast")
  return parts.join(" · ")
}

function miracastBarGlyph(phase, multiDisplay) {
  var value = String(phase || "idle")
  if (value === "streaming") return "󰖩"      // wifi / connected
  if (value === "connecting" || value === "dhcp" || value === "rtsp") return "󰑋"  // cast / linking
  if (value === "scanning") return "󰍉"       // search
  return multiDisplay ? "󰍺" : "󰍹"
}

function miracastConnectionSummary(phase, peerName, peerMac, mode, p2pFreqMHz) {
  // Peer name lives in the hero title — one state line (mode + freq when known).
  var label = String(peerName || "").trim()
  if (label === "") label = String(peerMac || "").trim()
  var modeLabel = String(mode || "mirror") === "extend" ? "Extend" : "Mirror"
  var value = String(phase || "idle")
  if (value === "streaming") {
    var freq = miracastFormatFreq(p2pFreqMHz)
    if (freq !== "" && freq !== "--")
      return "Connected on " + freq + " · " + modeLabel
    return "Connected · " + modeLabel
  }
  if (value === "connecting" || value === "dhcp" || value === "rtsp")
    return "Connecting…"
  if (value === "scanning") return "Scanning…"
  if (value === "error") return "Miracast error"
  if (label !== "") return "Last sink"
  return ""
}

function miracastBandLabel(freqMHz) {
  var v = parseFloat(freqMHz)
  if (!v) return ""
  if (v >= 2400 && v < 2500) return "2.4 GHz"
  if (v >= 4900 && v < 5925) return "5 GHz"
  if (v >= 5925 && v < 7125) return "6 GHz"
  var ghz = v / 1000
  return ghz.toFixed(ghz % 1 === 0 ? 0 : 1) + " GHz"
}

function miracastFormatChannel(channel) {
  if (channel === undefined || channel === null || channel === "") return "--"
  return String(channel)
}

/** Frequency for UI: prefer band label (2.4 / 5 GHz); fall back to MHz. */
function miracastFormatFreq(freqMHz) {
  var band = miracastBandLabel(freqMHz)
  if (band !== "") return band
  var v = parseFloat(freqMHz)
  if (!v) return "--"
  return Math.round(v) + " MHz"
}

function miracastFormatBandwidth(widthMHz) {
  var v = parseInt(widthMHz, 10)
  if (!v) return "--"
  return String(v) + " MHz"
}

function miracastFormatLinkMode(radioMcc, p2pRole, p2pFreqMHz, staFreqMHz) {
  // Spell out bands — "same channel" alone is easy to misread when drivers lie.
  var mira = miracastBandLabel(p2pFreqMHz)
  var wifi = miracastBandLabel(staFreqMHz)
  if (mira !== "" && wifi !== "") {
    if (radioMcc === true || mira !== wifi)
      return "Miracast on " + mira + " · Wi‑Fi on " + wifi
    return "Miracast & Wi‑Fi both on " + mira
  }
  if (radioMcc === true)
    return "Miracast on a different channel than Wi‑Fi"
  if (radioMcc === false)
    return "Miracast sharing Wi‑Fi’s channel"
  return "--"
}

/** @deprecated — freq is folded into miracastConnectionSummary. */
function miracastRadioHeroSummary(radio) {
  return ""
}

function miracastFormatMbps(v) {
  var n = parseFloat(v)
  if (!n && n !== 0) return "--"
  if (n >= 100) return Math.round(n) + " Mbps"
  return n.toFixed(n >= 10 ? 1 : 2) + " Mbps"
}

function miracastFormatRetryPercent(v) {
  var n = parseFloat(v)
  if (!n && n !== 0) return "--"
  return n.toFixed(n >= 10 ? 1 : 2) + "%"
}

function miracastFormatPerSec(v) {
  var n = parseFloat(v)
  if (!n && n !== 0) return "--"
  if (n >= 100) return Math.round(n) + "/s"
  if (n >= 10) return n.toFixed(1) + "/s"
  return n.toFixed(n >= 1 ? 1 : 2) + "/s"
}

function miracastFormatSignal(v) {
  var n = parseInt(v, 10)
  if (!n && n !== 0) return "--"
  return String(n) + " dBm"
}

/** RENDER ENGINE preference / pill ids. */
function miracastCaptureEncodeValues() {
  return ["dmabuf", "vaapi", "cpu"]
}

/** Encode quality profile pill ids (includes Best (Dynamic)). */
function miracastEncodeProfileValues() {
  return ["best", "veryhigh", "high", "medium", "low"]
}

function miracastEncodeProfileLabel(id) {
  var v = String(id || "").toLowerCase().replace(/[-_]/g, "")
  if (v === "best") return "Best (Dynamic)"
  if (v === "veryhigh" || v === "vh") return "Very High"
  if (v === "high") return "High"
  if (v === "medium") return "Medium"
  if (v === "low") return "Low"
  return id || "Medium"
}

function miracastRcModeLabel(rc) {
  var r = String(rc || "").trim().toUpperCase()
  if (r === "CQP") return "CQP (constant quality)"
  if (r === "CBR") return "CBR (constant bitrate)"
  if (r === "VBR") return "VBR (variable bitrate)"
  if (r === "QVBR") return "QVBR (quality-defined VBR)"
  if (r === "") return "--"
  return r
}

function miracastEngineDetailLabel(captureEncode, capturePath, encoder) {
  var pref = String(captureEncode || "").toLowerCase()
  var path = String(capturePath || "").toLowerCase()
  var enc = String(encoder || "")
  var engine = "--"
  if (path === "dmabuf" || pref === "dmabuf") engine = "DMA-BUF (zero-copy)"
  else if (path === "pipe" || pref === "vaapi") engine = "VAAPI pipe (raw frames)"
  else if (pref === "cpu") engine = "CPU (libx264)"
  else if (pref) engine = pref
  var codec = enc !== "" ? enc : (pref === "cpu" ? "libx264" : "h264_vaapi")
  return "Engine: " + engine + " · Codec: " + codec
}

/** Human-readable encode snapshot lines for the bar dropdown. */
function miracastEncoderDetailLines(m) {
  if (!m) return []
  var lines = []
  lines.push(miracastEngineDetailLabel(m.captureEncode, m.capturePath, m.encoder))
  var rc = String(m.vaapiRcMode || "").trim().toUpperCase()
  lines.push("Rate control: " + miracastRcModeLabel(rc))

  var qp = m.vaapiQp
  var q = m.vaapiQuality
  var iqf = m.vaapiIQfactor
  var bits = []
  if (qp !== undefined && qp !== null && String(qp) !== "")
    bits.push("QP " + String(qp))
  if (q !== undefined && q !== null && String(q) !== "")
    bits.push("quality " + String(q))
  if (iqf !== undefined && iqf !== null && String(iqf) !== "")
    bits.push("I-frame factor " + String(iqf))
  if (bits.length > 0)
    lines.push(bits.join(" · "))

  var br = String(m.bitrate || "").trim()
  var peak = String(m.vaapiBitrate || "").trim()
  if (rc === "CQP") {
    if (br !== "")
      lines.push("Bitrate label: " + br + " (CQP — content decides wire rate)")
  } else if (br !== "" || peak !== "") {
    if (peak !== "" && peak !== br)
      lines.push("Target " + (br || "--") + " · Peak " + peak)
    else
      lines.push("Target bitrate: " + (br || peak))
  }

  var gop = m.vaapiGop
  var asyncDepth = m.vaapiAsyncDepth
  var vbv = m.vbvMultiplier
  var trail = []
  if (gop !== undefined && gop !== null && String(gop) !== "")
    trail.push("GOP " + String(gop))
  if (asyncDepth !== undefined && asyncDepth !== null && String(asyncDepth) !== "")
    trail.push("async " + String(asyncDepth))
  if (rc === "CBR" && vbv !== undefined && vbv !== null && String(vbv) !== "")
    trail.push("VBV " + String(vbv))
  if (trail.length > 0)
    lines.push(trail.join(" · "))

  var profile = String(m.encodeProfile || "").toLowerCase()
  if (profile === "best") {
    var lab = String(m.encodeProfileEffectiveLabel || "").trim()
    if (lab === "" && m.encodeProfileEffective)
      lab = miracastEncodeProfileLabel(m.encodeProfileEffective)
    var best = "Best"
    if (lab !== "") best += " (" + lab + ")"
    if (m.encodeBestLocked) best += " · locked"
    lines.push(best)
  } else if (profile !== "") {
    lines.push("Preset: " + miracastEncodeProfileLabel(profile))
  }
  return lines
}

/**
 * Very High bitrate exceeds 2.4 GHz / 20 MHz Miracast headroom — only when P2P
 * is on 5 GHz or 6 GHz.
 */
function miracastFreqAllowsVeryHigh(freqMHz) {
  var n = parseFloat(freqMHz)
  if (!n) return false
  return n >= 4900
}

function miracastCaptureEncodeLabel(value) {
  var v = String(value || "")
  if (v === "dmabuf") return "GPU + DMA-BUF"
  if (v === "vaapi") return "GPU"
  if (v === "cpu") return "CPU"
  return v
}

/** Two-line RENDER ENGINE pill: title + (card-reported device name). */
function miracastCaptureEncodePillTitle(value) {
  return miracastCaptureEncodeLabel(value)
}

/** Strip legal-entity prefixes; keep the card's own product text for pills. */
function miracastGpuPillDeviceName(name) {
  var s = String(name || "").trim()
  if (s === "") return ""
  s = s.replace(/^Intel Corporation\s+/i, "")
  s = s.replace(/^NVIDIA Corporation\s+/i, "")
  s = s.replace(/^Advanced Micro Devices,\s*Inc\.\s*\[AMD\/ATI\]\s+/i, "")
  s = s.replace(/^Advanced Micro Devices,\s*Inc\.\s*/i, "")
  return s
}

function miracastCaptureEncodePillSubtitle(value, gpuDeviceName) {
  var v = String(value || "")
  if (v === "cpu") return ""
  var name = miracastGpuPillDeviceName(gpuDeviceName)
  if (name === "") return ""
  // Break before "[Iris Xe Graphics]"-style tails so WordWrap never leaves
  // a last line that is only "])" / punctuation.
  name = name.replace(/\s+\[/g, "\n[")
  return "(" + name + ")"
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
    if (enc.indexOf("vaapi") >= 0 || enc.indexOf("qsv") >= 0 || enc.indexOf("nvenc") >= 0
        || enc.indexOf("amf") >= 0 || enc === "vaapi" || enc === "qsv" || enc === "nvenc")
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
    miracastBandLabel: miracastBandLabel,
    miracastFormatChannel: miracastFormatChannel,
    miracastFormatFreq: miracastFormatFreq,
    miracastFormatBandwidth: miracastFormatBandwidth,
    miracastFormatLinkMode: miracastFormatLinkMode,
    miracastRadioHeroSummary: miracastRadioHeroSummary,
    miracastFormatMbps: miracastFormatMbps,
    miracastFormatRetryPercent: miracastFormatRetryPercent,
    miracastFormatPerSec: miracastFormatPerSec,
    miracastFormatSignal: miracastFormatSignal,
    miracastCaptureEncodeValues: miracastCaptureEncodeValues,
    miracastCaptureEncodeLabel: miracastCaptureEncodeLabel,
    miracastCaptureEncodePillTitle: miracastCaptureEncodePillTitle,
    miracastGpuPillDeviceName: miracastGpuPillDeviceName,
    miracastCaptureEncodePillSubtitle: miracastCaptureEncodePillSubtitle,
    miracastCaptureEncodeActive: miracastCaptureEncodeActive,
    miracastEncodeProfileValues: miracastEncodeProfileValues,
    miracastEncodeProfileLabel: miracastEncodeProfileLabel,
    miracastFreqAllowsVeryHigh: miracastFreqAllowsVeryHigh,
    miracastRcModeLabel: miracastRcModeLabel,
    miracastEngineDetailLabel: miracastEngineDetailLabel,
    miracastEncoderDetailLines: miracastEncoderDetailLines
  }
}
