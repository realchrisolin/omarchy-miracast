#!/usr/bin/env python3
"""Collect benchmark equipment metadata — useful, not overly identifying.

Includes: CPU, GPU, Wi-Fi chip/driver/firmware, kernel, compositor, monitor
display names/modes, optional Miracast sink display name.

Excludes: Wi-Fi SSIDs/BSSIDs, interface MAC addresses, IPs, usernames/home paths.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

_MAC_RE = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") if r.returncode == 0 else ""
    except Exception:
        return ""


def _scrub(text: str) -> str:
    text = _MAC_RE.sub("<mac>", text)
    text = _IPV4_RE.sub("<ip>", text)
    home = os.environ.get("HOME") or ""
    if home:
        text = text.replace(home, "$HOME")
    return text


def _cpu() -> Optional[str]:
    for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


def _lspci_lines(*needles: str) -> list[str]:
    out = _run(["lspci", "-nn"])
    lines = []
    for line in out.splitlines():
        low = line.lower()
        if any(n in low for n in needles):
            # Drop any accidental MAC-looking substrings; keep PCI IDs.
            lines.append(_scrub(line.strip()))
    return lines


def _wifi_netdev() -> Optional[str]:
    """First non-p2p wireless netdev name (not a MAC)."""
    for path in sorted(Path("/sys/class/net").glob("*")):
        name = path.name
        if name.startswith("p2p") or name.startswith("lo"):
            continue
        if (path / "wireless").exists() or (path / "phy80211").exists():
            return name
    return None


def _wifi_info() -> dict[str, Any]:
    info: dict[str, Any] = {}
    pci = _lspci_lines("network controller", "wireless")
    if pci:
        info["pci"] = pci
    dev = _wifi_netdev()
    if not dev:
        return info
    info["iface"] = dev  # wlp… is fine; not a MAC
    driver_link = Path(f"/sys/class/net/{dev}/device/driver")
    if driver_link.exists() or driver_link.is_symlink():
        try:
            info["driver"] = Path(os.readlink(driver_link)).name
        except OSError:
            pass
    et = _run(["ethtool", "-i", dev])
    for key in ("driver", "version", "firmware-version", "bus-info"):
        for line in et.splitlines():
            if line.startswith(key + ":"):
                val = line.split(":", 1)[1].strip()
                # bus-info can be PCI BDF — OK; skip if it looks like a MAC
                if _MAC_RE.search(val):
                    continue
                info[key.replace("-", "_")] = val
    # Channel/width only (no SSID) from iw
    iw = _run(["iw", "dev", dev, "info"])
    for line in iw.splitlines():
        line = line.strip()
        if line.startswith("channel "):
            info["sta_channel"] = _scrub(line)
            break
    return info


def _gpu() -> list[str]:
    return _lspci_lines("vga compatible", "3d controller", "display controller")


def _monitors() -> list[dict[str, Any]]:
    raw = _run(["hyprctl", "-j", "monitors"])
    if not raw:
        return []
    try:
        monitors = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for m in monitors:
        out.append(
            {
                "name": m.get("name"),
                "description": _scrub(str(m.get("description") or "")),
                "make": m.get("make") or None,
                "model": m.get("model") or None,
                "width": m.get("width"),
                "height": m.get("height"),
                "refresh_hz": m.get("refreshRate"),
                "scale": m.get("scale"),
            }
        )
    return out


def _sink_display_name(settings_path: Optional[Path] = None) -> Optional[str]:
    path = settings_path or Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ) / "omarchy-miracast" / "settings.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    name = data.get("lastPeerName") or None
    if not name:
        return None
    # Never echo a MAC if someone stored one in the name field.
    if _MAC_RE.fullmatch(str(name).strip()):
        return None
    return _scrub(str(name))


def collect(*, settings: Optional[Path] = None) -> dict[str, Any]:
    return {
        "kernel": _scrub(_run(["uname", "-r"]).strip() or os.uname().release),
        "cpu": _cpu(),
        "gpu": _gpu(),
        "wifi": _wifi_info(),
        "monitors": _monitors(),
        "compositor": _scrub(
            (_run(["hyprctl", "version"]).splitlines() or [""])[0]
        )
        or None,
        "sink_display_name": _sink_display_name(settings),
        # Explicitly omitted: SSIDs, BSSIDs, MACs, IPs, $HOME paths, username
        "omitted": ["wifi_ssid", "wifi_bssid", "mac_addresses", "ip_addresses"],
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="print JSON (default)")
    p.add_argument(
        "--settings",
        type=Path,
        default=None,
        help="omarchy-miracast settings.json for sink display name",
    )
    args = p.parse_args(argv)
    print(json.dumps(collect(settings=args.settings), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
