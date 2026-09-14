#!/usr/bin/env python3
"""Fingerprint a Miracast sink for manufacturer / model (and related IDs).

Sources (best → fallback):

1. **WPS / P2P peer record** via ``wpa_cli p2p_peer <mac>`` (needs privileges) —
   ``manufacturer``, ``model_name``, ``model_number``, ``device_name``,
   ``pri_dev_type``, ``wfd_subelems``, ``vendor_elems``
2. **FluxCast mode-state** (``sink-modes.json``) — often has the full P2P
   device name after RTSP, plus CEA/VESA capability masks
3. **omarchy-miracast** settings / status / peers.json display names
4. **MAC OUI** lookup when the address is universally administered (many P2P
   addrs are locally administered / randomized and yield nothing)
5. Heuristic parse of ``device_name`` (e.g. ``[LG] webOS TV SM8600PUA``)

Private benches should store the full record. Crowdsource sanitization keeps
manufacturer / model / device category / capability masks — never MAC/SSID.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

_MAC_RE = re.compile(r"(?i)^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")

# WPS primary device type category (Wi-Fi Alliance).
_WPS_CATEGORY = {
    1: "Computer",
    2: "Input",
    3: "Printer",
    4: "Camera",
    5: "Storage",
    6: "Network Infrastructure",
    7: "Display",
    8: "Multimedia",
    9: "Gaming",
    10: "Telephone",
    11: "Audio",
}


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") if r.returncode == 0 else (r.stderr or "")
    except Exception:
        return ""


def _state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "omarchy-miracast"


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy-miracast"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_mac(mac: Optional[str]) -> Optional[str]:
    if not mac:
        return None
    mac = str(mac).strip().upper().replace("-", ":")
    if not _MAC_RE.match(mac):
        return None
    return mac


def mac_is_locally_administered(mac: str) -> bool:
    return bool(int(mac.split(":")[0], 16) & 0x02)


def parse_wps_primary_device_type(value: Optional[str]) -> dict[str, Any]:
    """Parse ``7-0050F204-1`` → category Display, WFA OUI, subcategory."""
    out: dict[str, Any] = {"raw": value}
    if not value:
        return out
    m = re.fullmatch(r"(\d+)-([0-9A-Fa-f]{8})-(\d+)", str(value).strip())
    if not m:
        return out
    cat = int(m.group(1))
    out.update(
        {
            "category_id": cat,
            "category": _WPS_CATEGORY.get(cat, f"category_{cat}"),
            "oui": m.group(2).upper(),
            "subcategory_id": int(m.group(3)),
        }
    )
    return out


def parse_device_name(name: Optional[str]) -> dict[str, Any]:
    """Split common P2P names into brand / product / model tokens."""
    out: dict[str, Any] = {"raw": name}
    if not name:
        return out
    s = str(name).strip()
    out["display_name"] = s
    # [LG] webOS TV SM8600PUA
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", s)
    if m:
        out["bracket_brand"] = m.group(1).strip()
        rest = m.group(2).strip()
        out["product_rest"] = rest or None
        # Trailing model-like token: letters+digits (SM8600PUA, UN55…)
        m2 = re.search(r"\b([A-Z]{1,5}\d{2,}[A-Z0-9]*)\b", rest)
        if m2:
            out["model_code"] = m2.group(1)
        if rest:
            # Drop model code from product line
            product = rest
            if out.get("model_code"):
                product = product.replace(out["model_code"], "").strip(" -")
            out["product_line"] = product or None
        return out
    # hotyeah-AABB12_P2P / DIRECT-xx-HP
    if s.upper().startswith("DIRECT-"):
        out["direct_suffix"] = s.split("-", 2)[-1] if s.count("-") >= 2 else s
    m = re.match(r"^([A-Za-z][A-Za-z0-9+]{1,24})([-_].*)?$", s)
    if m:
        out["name_prefix"] = m.group(1)
    return out


def oui_vendor(mac: str) -> Optional[str]:
    if mac_is_locally_administered(mac):
        return None
    oui = "".join(mac.split(":")[:3]).upper()
    for path in (
        Path("/usr/share/hwdata/oui.txt"),
        Path("/usr/share/misc/oui.txt"),
        Path("/var/lib/ieee-data/oui.txt"),
    ):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        # hwdata format: "AA-BB-CC   (hex)\t\tVendor"
        for line in text.splitlines():
            if oui[:6] in line.replace(":", "").replace("-", "").upper() and "\t" in line:
                parts = line.split("\t")
                vendor = parts[-1].strip()
                if vendor:
                    return vendor
    return None


def _wpa_cli_p2p_peer(mac: str) -> dict[str, str]:
    """Return key=value map from ``wpa_cli p2p_peer`` (empty if unavailable)."""
    mac_l = mac.lower()
    # Prefer sudo -n (miracast benches often already have keep-alive sudo).
    candidates = [
        ["sudo", "-n", "wpa_cli", "-i", "wlp0s20f3", "p2p_peer", mac_l],
        ["sudo", "-n", "wpa_cli", "-i", "wlp0s20f3", "p2p_peer", mac],
    ]
    # Discover wireless ifaces
    for path in Path("/sys/class/net").glob("*"):
        if (path / "wireless").exists() or (path / "phy80211").exists():
            if path.name.startswith("p2p-"):
                continue
            candidates.insert(
                0, ["sudo", "-n", "wpa_cli", "-i", path.name, "p2p_peer", mac_l]
            )
            break
    raw = ""
    for cmd in candidates:
        raw = _run(cmd, timeout=4.0)
        if "device_name=" in raw or "manufacturer=" in raw:
            break
    out: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _from_mode_state(mac: Optional[str]) -> dict[str, Any]:
    data = _load_json(_state_dir() / "sink-modes.json")
    if not data:
        return {}
    stored = normalize_mac(str(data.get("peer") or ""))
    # Only use RTSP/mode-state when it matches the requested peer (or no MAC asked).
    if mac and stored and stored != mac:
        return {}
    if mac and not stored:
        return {}
    return {
        "device_name": data.get("peerName"),
        "cea_mask": data.get("ceaMask"),
        "vesa_mask": data.get("vesaMask"),
        "current_mode": data.get("current"),
        "supported_modes": data.get("supported"),
        "peer_mac": data.get("peer"),
    }


def _from_omarchy(mac: Optional[str]) -> dict[str, Any]:
    settings = _load_json(_config_dir() / "settings.json")
    status = _load_json(_state_dir() / "status.json")
    peers_path = _state_dir() / "peers.json"
    peers = []
    if peers_path.is_file():
        try:
            peers = json.loads(peers_path.read_text())
        except Exception:
            peers = []
    status_mac = normalize_mac(status.get("peer") or settings.get("lastPeerMac"))
    name = None
    peer_mac = mac or status_mac
    # Prefer peers.json name for the requested MAC; do not mix in another peer's status.
    if mac and peers:
        for p in peers:
            if normalize_mac(str(p.get("mac") or "")) == mac:
                name = p.get("name") or name
                peer_mac = mac
                break
    if not mac or status_mac == mac:
        name = name or status.get("peerName") or settings.get("lastPeerName")
        peer_mac = status_mac or peer_mac
        return {
            "device_name": name,
            "peer_mac": peer_mac,
            "stream_mode": status.get("streamMode"),
            "capture_path": status.get("capturePath"),
            "encoder": status.get("encoder"),
            "p2p_role": status.get("p2pRole"),
        }
    return {
        "device_name": name,
        "peer_mac": peer_mac,
        "stream_mode": None,
        "capture_path": None,
        "encoder": None,
        "p2p_role": None,
    }


def fingerprint_sink(mac: Optional[str] = None, *, use_wpa: bool = True) -> dict[str, Any]:
    """Aggregate identity for a Miracast sink.

    Returns a structure suitable for private benchmark dumps. Public sanitization
    should keep ``identity`` / ``capabilities`` and drop ``mac`` / raw wpa maps.
    """
    omarchy = _from_omarchy(normalize_mac(mac) if mac else None)
    mac_n = normalize_mac(mac) or normalize_mac(str(omarchy.get("peer_mac") or ""))
    mode = _from_mode_state(mac_n)
    wpa: dict[str, str] = {}
    if use_wpa and mac_n:
        wpa = _wpa_cli_p2p_peer(mac_n)

    device_name = (
        wpa.get("device_name")
        or mode.get("device_name")
        or omarchy.get("device_name")
    )
    parsed = parse_device_name(device_name)
    manufacturer = wpa.get("manufacturer") or parsed.get("bracket_brand") or parsed.get("name_prefix")
    model_name = wpa.get("model_name")
    model_number = wpa.get("model_number")
    model_code = parsed.get("model_code")
    # Prefer explicit model code from device_name when WPS model_* is generic.
    model_hint = model_code
    if not model_hint and model_name and model_name.lower() not in ("webos tv", "tv", "miracast"):
        model_hint = model_name
    if not model_hint and parsed.get("product_line"):
        model_hint = parsed.get("product_line")

    pri = parse_wps_primary_device_type(wpa.get("pri_dev_type"))
    oui = oui_vendor(mac_n) if mac_n else None

    identity = {
        "manufacturer": manufacturer,
        "manufacturer_normalized": (
            "LG" if manufacturer and str(manufacturer).upper() in ("LGE", "LG") else manufacturer
        ),
        "model_name": model_name,
        "model_number": model_number,
        "model_code": model_code,
        "model_hint": model_hint,
        "device_name": device_name,
        "product_line": parsed.get("product_line"),
        "serial_number": wpa.get("serial_number"),
        "primary_device_type": pri,
        "mac_oui_vendor": oui,
        "mac_locally_administered": mac_is_locally_administered(mac_n) if mac_n else None,
    }

    capabilities = {
        "cea_mask": mode.get("cea_mask"),
        "vesa_mask": mode.get("vesa_mask"),
        "current_mode": mode.get("current_mode") or omarchy.get("stream_mode"),
        "supported_modes": mode.get("supported_modes"),
        "wfd_subelems": wpa.get("wfd_subelems"),
        "vendor_elems": wpa.get("vendor_elems"),
        "config_methods": wpa.get("config_methods"),
        "dev_capab": wpa.get("dev_capab"),
        "oper_freq": wpa.get("oper_freq"),
        "signal_level": wpa.get("level"),
    }

    sources = {
        "wpa_cli_p2p_peer": bool(wpa.get("device_name") or wpa.get("manufacturer")),
        "sink_modes_json": bool(mode.get("device_name") or mode.get("cea_mask")),
        "omarchy_status_settings": bool(omarchy.get("device_name")),
        "oui_lookup": bool(oui),
    }

    return {
        "mac": mac_n,
        "identity": identity,
        "capabilities": capabilities,
        "sources": sources,
        "raw": {
            "wpa_cli": wpa or None,
            "mode_state": mode or None,
            "omarchy": omarchy or None,
            "device_name_parsed": parsed,
        },
    }


# Chip-vendor strings that are usually the Wi-Fi SoC OEM, not the retail brand.
_CHIP_OEMS = frozenset(
    {
        "realtek",
        "ralink",
        "mediatek",
        "mtk",
        "broadcom",
        "qualcomm",
        "atheros",
        "intel",
        "marvell",
        "cypress",
        "espressif",
    }
)


def public_identity(fp: dict[str, Any]) -> dict[str, Any]:
    """Crowdsource-safe subset (no MAC / raw dumps)."""
    ident = fp.get("identity") or {}
    caps = fp.get("capabilities") or {}
    parsed = (fp.get("raw") or {}).get("device_name_parsed") or {}
    wps_mfr = ident.get("manufacturer_normalized") or ident.get("manufacturer")
    retail = parsed.get("bracket_brand") or parsed.get("name_prefix")
    # Prefer retail P2P name brand when WPS manufacturer is a chip OEM (e.g. Realtek).
    if wps_mfr and str(wps_mfr).lower() in _CHIP_OEMS and retail:
        manufacturer = retail
        chipset = f"{wps_mfr} {ident.get('model_name') or ''}".strip()
    else:
        manufacturer = wps_mfr or retail
        chipset = None
        if wps_mfr and str(wps_mfr).lower() in _CHIP_OEMS:
            chipset = f"{wps_mfr} {ident.get('model_name') or ''}".strip()
    model_hint = (
        ident.get("model_code")
        or (None if chipset else (ident.get("model_hint") or ident.get("model_name")))
        or parsed.get("product_line")
    )
    return {
        "manufacturer": manufacturer,
        "model_hint": model_hint,
        "model_name": ident.get("model_name"),
        "model_code": ident.get("model_code"),
        "chipset": chipset,
        "device_name": ident.get("device_name"),
        "product_line": ident.get("product_line"),
        "device_category": (ident.get("primary_device_type") or {}).get("category"),
        "cea_mask": caps.get("cea_mask"),
        "vesa_mask": caps.get("vesa_mask"),
        "mac_oui_vendor": ident.get("mac_oui_vendor"),
        "sources": fp.get("sources"),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mac", default=None, help="P2P peer MAC (default: from miracast status)")
    p.add_argument("--json", action="store_true", default=True)
    p.add_argument("--public", action="store_true", help="print crowdsource-safe identity only")
    p.add_argument("--no-wpa", action="store_true", help="skip wpa_cli (no sudo)")
    args = p.parse_args(argv)
    fp = fingerprint_sink(args.mac, use_wpa=not args.no_wpa)
    out = public_identity(fp) if args.public else fp
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
