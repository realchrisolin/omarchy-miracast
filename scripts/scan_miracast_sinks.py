#!/usr/bin/env python3
"""Fast Miracast sink discovery for the Display dropdown.

Avoids cold-starting full FluxCast. Prefers NetworkManager WifiP2P D-Bus, then
``wpa_cli p2p_find`` (sudo -n). Returns WFD-capable peers when possible, with
full device names and WPS manufacturer/model when exposed.

Safe while a cast is active: never ``p2p_flush`` / ``iw scan``. A short
``p2p_find`` (no flush) is used and aborted if the live P2P group drops.

Usage:
  ./scripts/scan_miracast_sinks.py --timeout 5
  ./scripts/scan_miracast_sinks.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
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


def _nm_p2p_path(prefer_iface: Optional[str] = None) -> Optional[str]:
    """Return WifiP2P device path, preferring p2p-dev-<prefer_iface> when set."""
    try:
        raw = _gdbus_get(NM_DEST, NM_PATH, NM_DEST, "Devices")
    except Exception:
        return None
    paths = []
    for path in _object_paths(raw):
        try:
            dtype = _gdbus_get(NM_DEST, path, "org.freedesktop.NetworkManager.Device", "DeviceType")
        except Exception:
            continue
        # NM_DEVICE_TYPE_WIFI_P2P = 30
        if re.search(r"\b30\b|uint32 30", dtype):
            paths.append(path)
    if not paths:
        return None
    if prefer_iface:
        needle = f"p2p-dev-{prefer_iface}"
        for path in paths:
            try:
                iface = _parse_string(
                    _gdbus_get(NM_DEST, path, "org.freedesktop.NetworkManager.Device", "Interface")
                )
            except Exception:
                continue
            if iface == needle or (iface and prefer_iface in iface):
                return path
    return paths[0]


def scan_nm(timeout: int, wifi_iface: Optional[str] = None) -> list[dict[str, Any]]:
    path = _nm_p2p_path(wifi_iface)
    if not path:
        raise RuntimeError("No NetworkManager Wi-Fi P2P device")
    iface = _parse_string(
        _gdbus_get(NM_DEST, path, "org.freedesktop.NetworkManager.Device", "Interface")
    ) or "p2p-dev"
    # StartFind(a{sv}). gdbus CLI rejects common timeout variant spellings;
    # empty options works and we enforce duration with our own poll deadline.
    _gdbus_call(
        [
            "--dest",
            NM_DEST,
            "--object-path",
            path,
            "--method",
            "org.freedesktop.NetworkManager.Device.WifiP2P.StartFind",
            "{}",
        ],
        timeout=5.0,
    )
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


def _wifi_iface(prefer: Optional[str] = None) -> Optional[str]:
    """Resolve managed Wi-Fi iface (P2P-GO preferred, idle preferred)."""
    prefer = (prefer or os.environ.get("FLUXCAST_WFD_INTERFACE") or "").strip()
    try:
        from list_p2p_radios import resolve_iface

        return resolve_iface(prefer or "auto")
    except Exception:
        pass
    if prefer and prefer.lower() not in ("auto", "default"):
        return prefer
    for path in sorted(os.listdir("/sys/class/net")):
        base = f"/sys/class/net/{path}"
        if os.path.exists(f"{base}/wireless") or os.path.exists(f"{base}/phy80211"):
            if path.startswith("p2p-"):
                continue
            return path
    return None


def scan_wpa(timeout: int, wifi_iface: Optional[str] = None) -> list[dict[str, Any]]:
    iface = wifi_iface or _wifi_iface()
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


def _group_ifaces() -> list[str]:
    out = []
    for name in os.listdir("/sys/class/net"):
        if name.startswith("p2p-") and not name.startswith("p2p-dev"):
            out.append(name)
    return out


def _group_still_up(before: list[str]) -> bool:
    """True if we still have a P2P group iface (or none was required)."""
    if not before:
        return True
    now = set(_group_ifaces())
    return any(g in now for g in before)


def collect_peers_readonly() -> list[dict[str, Any]]:
    """Peers already known to NM/wpa/omarchy — no discovery traffic."""
    by_mac: dict[str, dict[str, Any]] = {}

    def upsert(peer: dict[str, Any]) -> None:
        mac = str(peer.get("mac") or "").upper()
        if not re.fullmatch(r"[0-9A-F:]{17}", mac):
            return
        cur = by_mac.get(mac, {})
        merged = {**cur, **{k: v for k, v in peer.items() if v not in (None, "", [])}}
        merged["mac"] = mac
        by_mac[mac] = merged

    # NM cache (no StartFind)
    try:
        path = _nm_p2p_path()
        if path:
            peers_raw = _gdbus_get(
                NM_DEST, path, "org.freedesktop.NetworkManager.Device.WifiP2P", "Peers"
            )
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
                upsert(
                    {
                        "mac": (address or "").upper(),
                        "name": name or address,
                        "manufacturer": manufacturer or None,
                        "model": model or None,
                        "wfd": bool(wfd_ies),
                        "rtsp_port": _wfd_rtsp_port(wfd_ies) if wfd_ies else None,
                        "source": "nm-cache",
                    }
                )
    except Exception:
        pass

    # wpa peer table (no p2p_find)
    iface = _wifi_iface()
    if iface:
        for prefix in (
            ["sudo", "-n", "wpa_cli", "-i", iface],
            ["wpa_cli", "-i", iface],
        ):
            try:
                listed = _run([*prefix, "p2p_peers"], timeout=5.0)
            except Exception:
                continue
            macs = [
                ln.strip()
                for ln in (listed.stdout or "").splitlines()
                if re.fullmatch(r"[0-9a-fA-F:]{17}", ln.strip())
            ]
            for mac in macs:
                try:
                    details = _run([*prefix, "p2p_peer", mac.lower()], timeout=5.0)
                except Exception:
                    upsert({"mac": mac.upper(), "name": mac.upper(), "source": "wpa-cache"})
                    continue
                fields = {}
                for line in (details.stdout or "").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        fields[k.strip()] = v.strip()
                has_wfd = "wfd_subelems" in fields or "wfd_dev_info" in fields
                upsert(
                    {
                        "mac": mac.upper(),
                        "name": fields.get("device_name") or mac.upper(),
                        "manufacturer": fields.get("manufacturer"),
                        "model": fields.get("model_name") or fields.get("model_number"),
                        "wfd": has_wfd,
                        "rtsp_port": 7236 if has_wfd else None,
                        "source": "wpa-cache",
                    }
                )
            break

    # omarchy peers.json + current status peer
    try:
        home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        peers_path = home / "omarchy-miracast" / "peers.json"
        status_path = home / "omarchy-miracast" / "status.json"
        if peers_path.is_file():
            for p in json.loads(peers_path.read_text()):
                # Never default wfd=True — printers are plain P2P and must not
                # appear as Miracast sinks (Wi-Fi Display IE is definitive).
                upsert(
                    {
                        "mac": str(p.get("mac") or "").upper(),
                        "name": p.get("name") or p.get("mac"),
                        "manufacturer": p.get("manufacturer"),
                        "model": p.get("model"),
                        "wfd": bool(p.get("wfd")) if "wfd" in p else None,
                        "source": "peers.json",
                    }
                )
        if status_path.is_file():
            st = json.loads(status_path.read_text())
            mac = str(st.get("peer") or "").upper()
            if mac:
                upsert(
                    {
                        "mac": mac,
                        "name": st.get("peerName") or mac,
                        "wfd": True,  # actively casting ⇒ Miracast-capable
                        "source": "active-session",
                    }
                )
    except Exception:
        pass

    return list(by_mac.values())


def scan_wpa_safe(
    timeout: int,
    *,
    watch_groups: Optional[list[str]] = None,
    wifi_iface: Optional[str] = None,
) -> list[dict[str, Any]]:
    """p2p_find without flush; abort if a watched P2P group iface disappears."""
    iface = wifi_iface or _wifi_iface()
    if not iface:
        raise RuntimeError("No Wi-Fi interface for wpa_cli")
    watch = list(watch_groups or [])

    def wpa(*args: str) -> subprocess.CompletedProcess:
        for prefix in (["sudo", "-n", "wpa_cli", "-i", iface], ["wpa_cli", "-i", iface]):
            try:
                r = _run([*prefix, *args], timeout=max(6.0, timeout + 2))
                if r.returncode == 0 or "FAIL" not in (r.stdout or ""):
                    return r
            except Exception:
                continue
        return _run(["sudo", "-n", "wpa_cli", "-i", iface, *args], timeout=max(6.0, timeout + 2))

    # Never flush. Short find only.
    start = wpa("p2p_find", str(timeout))
    if start.returncode != 0 and "OK" not in (start.stdout or ""):
        raise RuntimeError((start.stderr or start.stdout or "p2p_find failed").strip())
    try:
        deadline = time.monotonic() + max(1, timeout)
        peers: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            if not _group_still_up(watch):
                raise RuntimeError("P2P group dropped during scan — aborted")
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


def _merge_peers(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_mac: dict[str, dict[str, Any]] = {}
    for group in groups:
        for peer in group:
            mac = str(peer.get("mac") or "").upper()
            if not mac:
                continue
            cur = by_mac.get(mac, {})
            merged = {
                **cur,
                **{k: v for k, v in peer.items() if v not in (None, "", [])},
                "mac": mac,
            }
            # Live WFD IE evidence wins over stale cache.
            if cur.get("wfd") is True and peer.get("wfd") is False and peer.get("source") in (
                "wpa_cli",
                "NetworkManager",
            ):
                merged["wfd"] = False
            if peer.get("wfd") is True:
                merged["wfd"] = True
            by_mac[mac] = merged
    return list(by_mac.values())


def _filter_miracast_sinks(peers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep peers that advertise Wi-Fi Display (Miracast) capability.

    Spec: WFD IE in P2P probe/assoc (wpa: wfd_subelems; NM: WfdIEs). Plain
    Wi-Fi Direct printers/PCs lack it (often WPS category 3 = Printer).
    """
    return [p for p in peers if p.get("wfd") is True]


def scan(
    timeout: int = 5,
    *,
    wifi_iface: Optional[str] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return (peers, source, meta). Works while a cast is active."""
    active = _session_active()
    groups_before = _group_ifaces()
    cached = collect_peers_readonly()
    resolved = wifi_iface or _wifi_iface()
    meta: dict[str, Any] = {
        "session_active": active,
        "live_discovery": False,
        "group_ifaces": groups_before,
        "wifi_iface": resolved,
    }
    errors: list[str] = []

    # Idle: NM find first, then wpa find.
    # Active: still allow short finds (no flush); watch group iface; merge cache.
    if not active:
        try:
            peers = scan_nm(timeout, wifi_iface=resolved)
            return peers, "NetworkManager", {**meta, "live_discovery": True}
        except Exception as exc:
            errors.append(f"NM: {exc}")
        try:
            peers = scan_wpa_safe(timeout, watch_groups=[], wifi_iface=resolved)
            return peers, "wpa_cli", {**meta, "live_discovery": True}
        except Exception as exc:
            errors.append(f"wpa_cli: {exc}")
        if cached:
            return cached, "cache", meta
        raise RuntimeError("; ".join(errors) or "scan failed")

    # Active cast: prefer short wpa find (proven safe without flush on AX201),
    # also try NM StartFind, always merge readonly cache / current peer.
    discovered: list[dict[str, Any]] = []
    source = "cache+live"
    try:
        discovered = scan_wpa_safe(
            min(timeout, 5), watch_groups=groups_before, wifi_iface=resolved
        )
        meta["live_discovery"] = True
        source = "wpa_cli+cache"
    except Exception as exc:
        errors.append(f"wpa_cli: {exc}")
        try:
            discovered = scan_nm(min(timeout, 5), wifi_iface=resolved)
            if not _group_still_up(groups_before):
                raise RuntimeError("P2P group dropped during NM scan")
            meta["live_discovery"] = True
            source = "NetworkManager+cache"
        except Exception as exc2:
            errors.append(f"NM: {exc2}")

    peers = _merge_peers(cached, discovered)
    if not peers:
        raise RuntimeError(
            "; ".join(errors) or "no peers (cache empty and live discovery failed)"
        )
    if not _group_still_up(groups_before):
        meta["warning"] = "P2P group changed during scan"
    return peers, source, meta


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timeout", type=int, default=5, help="discovery seconds (default 5)")
    p.add_argument("--json", action="store_true", help="print JSON (default)")
    p.add_argument("--all", action="store_true", help="include non-WFD P2P peers")
    p.add_argument(
        "--iface",
        default="",
        help="managed Wi-Fi iface or auto (default: smart pick / FLUXCAST_WFD_INTERFACE)",
    )
    args = p.parse_args(argv)
    t0 = time.monotonic()
    prefer = (args.iface or "").strip() or None
    try:
        peers, source, meta = scan(args.timeout, wifi_iface=_wifi_iface(prefer) if prefer else None)
    except Exception as exc:
        print(json.dumps({"ok": False, "peers": [], "error": str(exc)}, separators=(",", ":")))
        return 1
    if not args.all:
        peers = _filter_miracast_sinks(peers)
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
                "session_active": meta.get("session_active"),
                "live_discovery": meta.get("live_discovery"),
                "wifi_iface": meta.get("wifi_iface"),
                "warning": meta.get("warning"),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
