#!/usr/bin/env python3
"""Fast Miracast sink discovery for the Display dropdown.

Avoids cold-starting full FluxCast. Prefers NetworkManager WifiP2P D-Bus, then
``wpa_cli p2p_find`` (sudo -n). Returns WFD-capable peers when possible, with
full device names and WPS manufacturer/model when exposed.

Usage:
  ./scripts/scan_miracast_sinks.py --timeout 5
  ./scripts/scan_miracast_sinks.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Optional

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

NM_DEST = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"


def _run(cmd: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _gdbus_call(args: list[str], timeout: float = 5.0) -> str:
    result = _run(["gdbus", "call", "--system", *args], timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "").strip())
    return result.stdout or ""


def _gdbus_get(dest: str, path: str, iface: str, prop: str) -> str:
    return _gdbus_call(
        [
            "--dest",
            dest,
            "--object-path",
            path,
            "--method",
            "org.freedesktop.DBus.Properties.Get",
            iface,
            prop,
        ]
    )


def _parse_string(blob: str) -> str:
    m = re.search(r"<'([^']*)'>", blob) or re.search(r'"([^"]*)"', blob)
    return m.group(1) if m else ""


def _object_paths(blob: str) -> list[str]:
    return re.findall(r"(/org/freedesktop/NetworkManager/[A-Za-z0-9/_-]+)", blob)


def _parse_ay(blob: str) -> list[int]:
    return [int(x, 0) for x in re.findall(r"0x[0-9a-fA-F]+|\b\d+\b", blob)]


def _wfd_rtsp_port(ies: list[int]) -> int:
    # Subelement 0 (Device Info): 6 bytes; RTSP port is last 2 bytes BE.
    i = 0
    while i + 2 <= len(ies):
        sub_id, length = ies[i], ies[i + 1]
        i += 2
        if i + length > len(ies):
            break
        body = ies[i : i + length]
        i += length
        if sub_id == 0 and length >= 6:
            return (body[4] << 8) | body[5]
    return 7236


def _nm_p2p_path() -> Optional[str]:
    try:
        raw = _gdbus_get(NM_DEST, NM_PATH, NM_DEST, "Devices")
    except Exception:
        return None
    for path in _object_paths(raw):
        try:
            dtype = _gdbus_get(NM_DEST, path, "org.freedesktop.NetworkManager.Device", "DeviceType")
        except Exception:
            continue
        # NM_DEVICE_TYPE_WIFI_P2P = 30
        if re.search(r"\b30\b|uint32 30", dtype):
            return path
    return None


def scan_nm(timeout: int) -> list[dict[str, Any]]:
    path = _nm_p2p_path()
    if not path:
        raise RuntimeError("No NetworkManager Wi-Fi P2P device")
    iface = _parse_string(
        _gdbus_get(NM_DEST, path, "org.freedesktop.NetworkManager.Device", "Interface")
    ) or "p2p-dev"
    # StartFind(a{sv}). NM wants timeout as signed int "i", not uint32.
    # Empty options also works; we enforce duration with our own deadline.
    started = False
    for opts in (
        f"{{'timeout': <i {max(1, int(timeout))}>}}",
        "{}",
    ):
        try:
            _gdbus_call(
                [
                    "--dest",
                    NM_DEST,
                    "--object-path",
                    path,
                    "--method",
                    "org.freedesktop.NetworkManager.Device.WifiP2P.StartFind",
                    opts,
                ],
                timeout=5.0,
            )
            started = True
            break
        except Exception:
            continue
    if not started:
        raise RuntimeError("WifiP2P.StartFind failed")
    try:
        # Poll peers every 0.75s; return early once we have WFD peers or any peers late.
        deadline = time.monotonic() + max(1, timeout)
        peers: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            time.sleep(0.75)
            peers_raw = _gdbus_get(
                NM_DEST, path, "org.freedesktop.NetworkManager.Device.WifiP2P", "Peers"
            )
            peers = []
            for peer_path in _object_paths(peers_raw):
                name = _parse_string(
                    _gdbus_get(
                        NM_DEST,
                        peer_path,
                        "org.freedesktop.NetworkManager.WifiP2PPeer",
                        "Name",
                    )
                )
                address = _parse_string(
                    _gdbus_get(
                        NM_DEST,
                        peer_path,
                        "org.freedesktop.NetworkManager.WifiP2PPeer",
                        "HwAddress",
                    )
                )
                model = _parse_string(
                    _gdbus_get(
                        NM_DEST,
                        peer_path,
                        "org.freedesktop.NetworkManager.WifiP2PPeer",
                        "Model",
                    )
                )
                manufacturer = _parse_string(
                    _gdbus_get(
                        NM_DEST,
                        peer_path,
                        "org.freedesktop.NetworkManager.WifiP2PPeer",
                        "Manufacturer",
                    )
                )
                wfd_blob = _gdbus_get(
                    NM_DEST,
                    peer_path,
                    "org.freedesktop.NetworkManager.WifiP2PPeer",
                    "WfdIEs",
                )
                wfd_ies = _parse_ay(wfd_blob)
                has_wfd = bool(wfd_ies)
                peers.append(
                    {
                        "mac": (address or "").upper(),
                        "name": name or address or "unknown",
                        "manufacturer": manufacturer or None,
                        "model": model or None,
                        "wfd": has_wfd,
                        "rtsp_port": _wfd_rtsp_port(wfd_ies) if has_wfd else None,
                        "source": "NetworkManager",
                        "iface": iface,
                    }
                )
            wfd_peers = [p for p in peers if p.get("wfd")]
            elapsed = time.monotonic() - (deadline - timeout)
            # Early exit once we have WFD sinks and have searched ≥3s (or 60% of budget).
            if wfd_peers and elapsed >= min(3.0, max(2.0, timeout * 0.6)):
                return wfd_peers
        wfd_peers = [p for p in peers if p.get("wfd")]
        return wfd_peers if wfd_peers else peers
    finally:
        try:
            _gdbus_call(
                [
                    "--dest",
                    NM_DEST,
                    "--object-path",
                    path,
                    "--method",
                    "org.freedesktop.NetworkManager.Device.WifiP2P.StopFind",
                ],
                timeout=3.0,
            )
        except Exception:
            pass


def _wifi_iface() -> Optional[str]:
    for path in sorted(os.listdir("/sys/class/net")):
        base = f"/sys/class/net/{path}"
        if os.path.exists(f"{base}/wireless") or os.path.exists(f"{base}/phy80211"):
            if path.startswith("p2p-"):
                continue
            return path
    return None


def scan_wpa(timeout: int) -> list[dict[str, Any]]:
    iface = _wifi_iface()
    if not iface:
        raise RuntimeError("No Wi-Fi interface for wpa_cli")
    # Prefer sudo -n (same as fingerprint); fall back to plain wpa_cli.
    def wpa(*args: str) -> subprocess.CompletedProcess:
        for prefix in (["sudo", "-n", "wpa_cli", "-i", iface], ["wpa_cli", "-i", iface]):
            try:
                r = _run([*prefix, *args], timeout=max(6.0, timeout + 2))
                if r.returncode == 0 or "FAIL" not in (r.stdout or ""):
                    return r
            except Exception:
                continue
        return _run(["sudo", "-n", "wpa_cli", "-i", iface, *args], timeout=max(6.0, timeout + 2))

    start = wpa("p2p_find", str(timeout))
    if start.returncode != 0 and "OK" not in (start.stdout or ""):
        raise RuntimeError((start.stderr or start.stdout or "p2p_find failed").strip())
    try:
        deadline = time.monotonic() + max(1, timeout)
        peers: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            time.sleep(0.75)
            listed = wpa("p2p_peers")
            macs = [
                ln.strip()
                for ln in (listed.stdout or "").splitlines()
                if re.fullmatch(r"[0-9a-fA-F:]{17}", ln.strip())
            ]
            peers = []
            for mac in macs:
                details = wpa("p2p_peer", mac.lower())
                text = details.stdout or ""
                fields = {}
                for line in text.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        fields[k.strip()] = v.strip()
                name = fields.get("device_name") or mac
                has_wfd = "wfd_subelems" in fields or "wfd_dev_info" in fields
                peers.append(
                    {
                        "mac": mac.upper(),
                        "name": name,
                        "manufacturer": fields.get("manufacturer"),
                        "model": fields.get("model_name") or fields.get("model_number"),
                        "wfd": has_wfd,
                        "rtsp_port": 7236 if has_wfd else None,
                        "source": "wpa_cli",
                        "iface": iface,
                    }
                )
            wfd_peers = [p for p in peers if p.get("wfd")]
            elapsed = timeout - (deadline - time.monotonic())
            if wfd_peers and elapsed >= min(3.0, timeout * 0.45):
                return wfd_peers
        wfd_peers = [p for p in peers if p.get("wfd")]
        return wfd_peers if wfd_peers else peers
    finally:
        try:
            wpa("p2p_stop_find")
        except Exception:
            pass


def _session_active() -> bool:
    try:
        from fingerprint_miracast_sink import miracast_session_active

        return bool(miracast_session_active())
    except Exception:
        return False


def scan(timeout: int = 5) -> tuple[list[dict[str, Any]], str]:
    if _session_active():
        raise RuntimeError(
            "scan refused while Miracast session is active (would disrupt P2P)"
        )
    errors: list[str] = []
    try:
        peers = scan_nm(timeout)
        return peers, "NetworkManager"
    except Exception as exc:
        errors.append(f"NM: {exc}")
    try:
        peers = scan_wpa(timeout)
        return peers, "wpa_cli"
    except Exception as exc:
        errors.append(f"wpa_cli: {exc}")
    raise RuntimeError("; ".join(errors) or "scan failed")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timeout", type=int, default=5, help="discovery seconds (default 5)")
    p.add_argument("--json", action="store_true", help="print JSON (default)")
    p.add_argument("--all", action="store_true", help="include non-WFD P2P peers")
    args = p.parse_args(argv)
    t0 = time.monotonic()
    try:
        peers, source = scan(args.timeout)
    except Exception as exc:
        print(json.dumps({"ok": False, "peers": [], "error": str(exc)}, separators=(",", ":")))
        return 1
    if not args.all:
        wfd = [x for x in peers if x.get("wfd")]
        if wfd:
            peers = wfd
    # Stable order: WFD first, then name
    peers.sort(key=lambda x: (0 if x.get("wfd") else 1, str(x.get("name") or "").lower()))
    for i, peer in enumerate(peers):
        peer["index"] = i
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    print(
        json.dumps(
            {
                "ok": True,
                "peers": peers,
                "source": source,
                "timeout_s": args.timeout,
                "elapsed_ms": elapsed_ms,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
