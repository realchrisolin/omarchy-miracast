#!/usr/bin/env python3
"""Private vs public Miracast benchmark artifacts.

Private runs (gitignored) may include full sink name, MAC, SSIDs, BSSIDs, and
raw ``iw`` dumps needed for local analysis.

Public runs (committed under docs/benchmarks/public/) keep manufacturer/model
hints, host chipset info, and metrics only — never MACs, SSIDs, BSSIDs, IPs,
or $HOME paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "docs" / "benchmarks"
PRIVATE_DIR = BENCH_DIR / "private"
PUBLIC_DIR = BENCH_DIR / "public"

_MAC_RE = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Known Miracast / CE brands often appearing in P2P device names.
_BRANDS = (
    "LG",
    "Samsung",
    "Sony",
    "Panasonic",
    "Sharp",
    "TCL",
    "Hisense",
    "Philips",
    "Vizio",
    "Roku",
    "Amazon",
    "Fire",
    "Google",
    "Chromecast",
    "Microsoft",
    "Xbox",
    "Apple",
    "Belkin",
    "Netgear",
    "Actiontec",
    "Microsoft",
    "Miracast",
    "hotyeah",  # common USB dongle OEM prefix in P2P names
    "EZCast",
    "Anker",
)


def scrub_text(text: str) -> str:
    text = _MAC_RE.sub("<mac>", text)
    text = _IPV4_RE.sub("<ip>", text)
    home = os.environ.get("HOME") or ""
    if home:
        text = text.replace(home, "$HOME")
    # Redact SSID assignments but keep the key label.
    text = re.sub(r"(SSID:\s*)\S+", r"\1<redacted>", text, flags=re.I)
    return text


def looks_like_mac(value: str) -> bool:
    return bool(_MAC_RE.fullmatch((value or "").strip()))


def sanitize_display_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    name = str(name).strip()
    if looks_like_mac(name):
        return None
    # Drop trailing MAC-like / hex tails from dongle names: hotyeah-AABB12_P2P
    cleaned = re.sub(r"[-_][0-9A-Fa-f]{4,}(_P2P)?$", "", name)
    cleaned = cleaned.strip("-_ ") or name
    return scrub_text(cleaned)


def guess_manufacturer(display_name: Optional[str]) -> Optional[str]:
    if not display_name:
        return None
    raw = display_name.strip()
    # [LG] / (Samsung)
    m = re.match(r"^[\[(]?\s*([A-Za-z][A-Za-z0-9+.-]{1,32})\s*[\])]?", raw)
    token = (m.group(1) if m else raw).strip()
    for brand in _BRANDS:
        if re.search(rf"(?i)\b{re.escape(brand)}\b", raw) or token.lower() == brand.lower():
            return brand if brand != "Fire" else "Amazon"
    # First alphabetic token as weak hint (e.g. hotyeah-…).
    m = re.match(r"^([A-Za-z][A-Za-z0-9+]{1,24})", token)
    return m.group(1) if m else None


def guess_model_hint(display_name: Optional[str], manufacturer: Optional[str]) -> Optional[str]:
    if not display_name:
        return None
    name = sanitize_display_name(display_name) or display_name
    # Bracket-only / brand-only names like [LG] have no model.
    bare = name.strip(" []()_-")
    if manufacturer and bare.lower() == manufacturer.lower():
        return None
    if re.fullmatch(r"[\[(][^\]\)]{1,24}[\])]", name):
        return None
    if manufacturer and name.lower().startswith(manufacturer.lower()):
        rest = name[len(manufacturer) :].strip(" []()-_")
        return rest or None
    return name


def private_filename_stem(
    device_name: Optional[str],
    *,
    when: Optional[datetime] = None,
    case: Optional[str] = None,
) -> str:
    """Private JSON stem: ``<device_name>[-<case>]-<YYYYMMDD_HHMMSS>``.

    Device name is filesystem-sanitized (no MACs). Timestamp is local time.
    """
    when = when or datetime.now()
    ts = when.strftime("%Y%m%d_%H%M%S")
    raw = sanitize_display_name(device_name) or (device_name or "").strip() or "sink"
    device = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-_.")[:48] or "sink"
    parts = [device]
    if case:
        tag = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(case)).strip("-_.")[:40]
        if tag:
            parts.append(tag)
    parts.append(ts)
    return "-".join(parts)


def private_path(stem: str) -> Path:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    return PRIVATE_DIR / f"{stem}.json"


def public_path(stem: str) -> Path:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    return PUBLIC_DIR / f"{stem}.json"


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def stable_public_id(kind: str, sink_name: str, ts: str, metrics: dict[str, Any]) -> str:
    raw = f"{kind}|{sink_name}|{ts}|{json.dumps(metrics, sort_keys=True)}"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:10]
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", (sink_name or "sink")).strip("-").lower()[:24] or "sink"
    day = (ts or "")[:10].replace("-", "") or "unknown"
    return f"{day}_{safe}_{kind}_{digest}"


def sanitize_private_run(private: dict[str, Any]) -> dict[str, Any]:
    """Build a crowdsource-safe public record from a private run."""
    sink = private.get("sink") or {}
    host = private.get("host") or {}
    session = private.get("session") or {}
    metrics = private.get("metrics") or {}
    fp_pub = sink.get("fingerprint_public") or {}
    display = sanitize_display_name(
        fp_pub.get("device_name") or sink.get("display_name") or sink.get("name")
    )
    manufacturer = (
        fp_pub.get("manufacturer")
        or sink.get("manufacturer")
        or guess_manufacturer(display)
    )
    model = (
        fp_pub.get("model_hint")
        or sink.get("model_code")
        or sink.get("model_hint")
        or guess_model_hint(display, manufacturer)
    )

    wifi = host.get("wifi") or {}
    public_wifi = {
        k: wifi.get(k)
        for k in ("pci", "driver", "firmware_version", "sta_channel")
        if wifi.get(k) is not None
    }

    public_metrics = {
        k: metrics[k]
        for k in (
            "hyprland_pct_one_core",
            "wf_recorder_pct_one_core",
            "ffmpeg_pct_one_core",
            "fluxcast_pct_one_core",
            "p2p_tx_kbps",
            "sta_channel",
            "p2p_channel",
            "sta_freq_mhz",
            "p2p_freq_mhz",
            "sta_width_mhz",
            "p2p_width_mhz",
            "radio_mcc",
            "p2p_role",
            "sample_s",
            "rcs_busy_pct_approx_median",
            "vcs_busy_pct_approx_median",
            "rows",  # e.g. p2p scc/mcc table rows without MACs
        )
        if k in metrics and metrics[k] is not None
    }

    radio = private.get("radio_public") or private.get("radio") or {}
    # Prefer structured radio; fold flat metrics into radio if missing.
    if radio:
        try:
            from radio_snapshot import public_radio

            radio = public_radio(radio) if "sta" in radio or "p2p" in radio else radio
        except Exception:
            pass
    else:
        radio = {
            "sta": {
                "channel": metrics.get("sta_channel"),
                "freq_mhz": metrics.get("sta_freq_mhz"),
                "width_mhz": metrics.get("sta_width_mhz"),
            },
            "p2p": {
                "channel": metrics.get("p2p_channel"),
                "freq_mhz": metrics.get("p2p_freq_mhz"),
                "width_mhz": metrics.get("p2p_width_mhz"),
                "role": metrics.get("p2p_role"),
                "tx_kbps_sample": metrics.get("p2p_tx_kbps"),
            },
            "negotiation": {
                "p2p_role": metrics.get("p2p_role"),
                "radio_mcc": metrics.get("radio_mcc"),
                "quiet_channel_csa": metrics.get("radio_mcc"),
                "oper_channel_soft_pin": metrics.get("p2p_channel"),
            },
        }

    kind = str(private.get("kind") or "benchmark")
    ts = str(private.get("ts") or datetime.now(timezone.utc).isoformat())
    pub_id = private.get("public_id") or stable_public_id(
        kind, display or manufacturer or "sink", ts, public_metrics
    )

    return {
        "id": pub_id,
        "ts": ts,
        "kind": kind,
        "sink": {
            "display_name": display,
            "manufacturer": manufacturer,
            "model_hint": model,
            "model_name": fp_pub.get("model_name") or sink.get("model_name"),
            "model_code": fp_pub.get("model_code") or sink.get("model_code"),
            "product_line": fp_pub.get("product_line") or sink.get("product_line"),
            "device_category": fp_pub.get("device_category"),
            "cea_mask": fp_pub.get("cea_mask"),
            "vesa_mask": fp_pub.get("vesa_mask"),
            "chipset": fp_pub.get("chipset") or sink.get("chipset"),
            "wfd_role": sink.get("wfd_role") or sink.get("p2p_role"),
            "fingerprint_sources": fp_pub.get("sources"),
        },
        "host": {
            "cpu": host.get("cpu"),
            "gpu": host.get("gpu"),
            "wifi": public_wifi or None,
            "kernel": host.get("kernel"),
            "compositor": host.get("compositor"),
        },
        "session": {
            "mode": session.get("mode"),
            "stream_mode": session.get("stream_mode") or session.get("streamMode"),
            "capture_path": session.get("capture_path") or session.get("capturePath"),
            "encoder": session.get("encoder"),
            "persist_display": session.get("persist_display"),
        },
        "radio": radio,
        "quality": private.get("quality_public")
        or {
            "criteria": (private.get("quality") or {}).get("criteria"),
            "window_s": (private.get("quality") or {}).get("window_s"),
        },
        "metrics": public_metrics,
        "notes": private.get("public_notes") or private.get("notes") or [],
        "omitted": [
            "sink_mac",
            "wifi_ssid",
            "wifi_bssid",
            "mac_addresses",
            "ip_addresses",
            "home_paths",
            "raw_iw",
        ],
        "source_private_stem": private.get("stem"),
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
