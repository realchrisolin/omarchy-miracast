#!/usr/bin/env python3
"""Merge STA / P2P channel + link stats into miracast-ctl status JSON.

Reads status JSON on stdin, writes updated JSON on stdout.
Parses ``iw dev`` (channel/width) and ``iw station dump`` on the P2P iface
(signal, bitrate, retries). Override dumps via MIRACAST_IW_DEV_TEXT /
MIRACAST_IW_STATION_TEXT or ``--iw-text`` / ``--station-text`` for tests.

Does not include SSIDs or MAC addresses.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Optional


def parse_iw_dev(text: str) -> dict[str, Any]:
    sta: Optional[dict[str, Any]] = None
    p2p: Optional[dict[str, Any]] = None
    block: dict[str, Any] = {
        "iface": None,
        "type": None,
        "channel": None,
        "freq_mhz": None,
        "width_mhz": None,
    }

    def flush() -> None:
        nonlocal sta, p2p, block
        t = (block.get("type") or "").lower()
        ch = block.get("channel")
        if t == "managed" and ch is not None and sta is None:
            sta = dict(block)
        elif t in ("p2p-go", "p2p-client") and ch is not None:
            if p2p is None or (
                t == "p2p-go" and (p2p.get("type") or "").lower() != "p2p-go"
            ):
                p2p = dict(block)
        block = {
            "iface": block.get("iface"),
            "type": None,
            "channel": None,
            "freq_mhz": None,
            "width_mhz": None,
        }

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Interface ") or line.startswith("Unnamed/"):
            flush()
            block["iface"] = line.split()[1] if line.startswith("Interface ") else None
            continue
        if line.startswith("type "):
            block["type"] = line.split()[1]
            continue
        if line.startswith("channel "):
            m = re.match(
                r"channel\s+(\d+)\s+\((\d+)\s*MHz\)(?:,\s*width:\s*(\d+)\s*MHz)?",
                line,
            )
            if m:
                block["channel"] = int(m.group(1))
                block["freq_mhz"] = int(m.group(2))
                if m.group(3):
                    block["width_mhz"] = int(m.group(3))
    flush()

    out: dict[str, Any] = {}
    if sta and sta.get("channel") is not None:
        out["staChannel"] = sta["channel"]
        out["staFreqMHz"] = sta.get("freq_mhz")
        out["staWidthMHz"] = sta.get("width_mhz")
        out["staIface"] = sta.get("iface")
    if p2p and p2p.get("channel") is not None:
        out["p2pChannel"] = p2p["channel"]
        out["p2pFreqMHz"] = p2p.get("freq_mhz")
        out["p2pWidthMHz"] = p2p.get("width_mhz")
        out["p2pIface"] = p2p.get("iface")
        out["p2pRole"] = p2p.get("type")
    out.update(_concurrency_fields(out))
    return out


def _band_ghz(freq_mhz: Optional[float]) -> Optional[str]:
    if freq_mhz is None:
        return None
    try:
        f = float(freq_mhz)
    except (TypeError, ValueError):
        return None
    if 2400 <= f < 2500:
        return "2.4"
    if 4900 <= f < 5925:
        return "5"
    if 5925 <= f < 7125:
        return "6"
    return None


def _channel_from_freq(freq_mhz: Optional[float]) -> Optional[int]:
    if freq_mhz is None:
        return None
    try:
        f = int(round(float(freq_mhz)))
    except (TypeError, ValueError):
        return None
    if 2412 <= f <= 2484:
        return 1 + (f - 2412) // 5
    if 5000 <= f <= 5900:
        return (f - 5000) // 5
    return None


def _concurrency_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Decide MCC and which P2P frequency to trust for UI / ABR.

    Priority for the *air* frequency:
      1. ``oper_freq`` when it is a valid 5 GHz operating channel (sink accepted
         5 GHz GO — Smart View / hotyeah often listen on 2.4 but oper on 5220).
      2. Else ``listen_freq`` when iw GO merely mirrors STA 5 GHz while the peer
         only operates/listens on 2.4 (classic Intel MCC “iw lie”).
      3. Else iw-reported GO frequency.

    Discovery ``listen_freq`` stays in ``p2pListenFreqMHz`` either way.
    """
    out: dict[str, Any] = {}
    sta_f = fields.get("staFreqMHz")
    p2p_f = fields.get("p2pFreqMHz")
    listen_f = fields.get("p2pListenFreqMHz")
    oper_f = fields.get("p2pOperFreqMHz")
    sta_band = _band_ghz(sta_f)
    p2p_band = _band_ghz(p2p_f)
    listen_band = _band_ghz(listen_f)
    oper_band = _band_ghz(oper_f)

    # 1) Sink accepted a 5 GHz operating channel — trust that for air/UI.
    if oper_band == "5" and oper_f is not None:
        out["p2pFreqMHz"] = int(oper_f)
        ch = _channel_from_freq(oper_f)
        if ch is not None:
            out["p2pChannel"] = ch
        out["p2pFreqSource"] = "peer_oper"
        if sta_f is not None:
            out["radioMcc"] = int(sta_f) != int(oper_f)
        else:
            out["radioMcc"] = False
        return out

    # 2) Intel MCC lie: iw GO == STA 5 GHz, but peer has no 5 GHz oper and
    #    only listens on 2.4 → treat air as 2.4 listen.
    if (
        listen_band == "2.4"
        and sta_band == "5"
        and p2p_band == "5"
        and (oper_f is None or int(oper_f or 0) <= 0 or oper_band == "2.4")
        and sta_f is not None
        and p2p_f is not None
        and int(sta_f) == int(p2p_f)
    ):
        out["p2pFreqMHz"] = int(listen_f)
        ch = _channel_from_freq(listen_f)
        if ch is not None:
            out["p2pChannel"] = ch
        out["radioMcc"] = True
        out["p2pFreqSource"] = "peer_listen"
        return out

    # 3) Normal path: different channel or frequency ⇒ MCC.
    sta_ch = fields.get("staChannel")
    p2p_ch = fields.get("p2pChannel")
    if sta_f is not None and p2p_f is not None:
        out["radioMcc"] = int(sta_f) != int(p2p_f)
    elif sta_ch is not None and p2p_ch is not None:
        out["radioMcc"] = int(sta_ch) != int(p2p_ch)
    return out


def parse_p2p_peer_freqs(text: str) -> dict[str, Any]:
    """Parse ``wpa_cli p2p_peer`` for listen/oper freq (no identity fields)."""
    out: dict[str, Any] = {}
    if not text:
        return out
    m = re.search(r"^listen_freq=(\d+)\s*$", text, re.M)
    if m:
        out["p2pListenFreqMHz"] = int(m.group(1))
    m = re.search(r"^oper_freq=(\d+)\s*$", text, re.M)
    if m:
        out["p2pOperFreqMHz"] = int(m.group(1))
    return out


def parse_station_dump(text: str) -> dict[str, Any]:
    """Parse ``iw dev <p2p> station dump`` (no MACs in output)."""
    out: dict[str, Any] = {}
    if not text:
        return out

    def _f(pat: str, cast=float):
        # iw indents with tabs; allow leading whitespace on every field line.
        m = re.search(r"^\s*" + pat, text, re.I | re.M)
        if not m:
            return None
        try:
            return cast(m.group(1))
        except Exception:
            return None

    # Prefer averaged signal when present.
    sig = _f(r"signal\s+avg:\s*(-?\d+)", int)
    if sig is None:
        sig = _f(r"signal:\s*(-?\d+)", int)
    if sig is not None:
        out["p2pSignalDbm"] = sig

    # "tx bitrate: 72.2 MBit/s MCS 7 short GI"
    br = _f(r"tx bitrate:\s*([\d.]+)")
    if br is not None:
        out["p2pTxBitrateMbps"] = br
    rxbr = _f(r"rx bitrate:\s*([\d.]+)")
    if rxbr is not None:
        out["p2pRxBitrateMbps"] = rxbr

    for key, pat in (
        ("p2pTxBytes", r"tx bytes:\s*(\d+)"),
        ("p2pTxPackets", r"tx packets:\s*(\d+)"),
        ("p2pTxRetries", r"tx retries:\s*(\d+)"),
        ("p2pTxFailed", r"tx failed:\s*(\d+)"),
        ("p2pRxBytes", r"rx bytes:\s*(\d+)"),
        ("p2pRxPackets", r"rx packets:\s*(\d+)"),
        ("p2pRxDropMisc", r"rx drop misc:\s*(\d+)"),
    ):
        v = _f(pat, int)
        if v is not None:
            out[key] = v

    # Cumulative retry rate for UI when QML has no prior sample yet.
    pk = out.get("p2pTxPackets")
    rt = out.get("p2pTxRetries")
    if isinstance(pk, int) and pk > 0 and isinstance(rt, int):
        out["p2pTxRetryPercent"] = round(100.0 * rt / pk, 3)
    return out


_LINK_KEYS = (
    "p2pSignalDbm",
    "p2pTxBitrateMbps",
    "p2pRxBitrateMbps",
    "p2pTxBytes",
    "p2pTxPackets",
    "p2pTxRetries",
    "p2pTxFailed",
    "p2pRxBytes",
    "p2pRxPackets",
    "p2pRxDropMisc",
    "p2pTxRetryPercent",
    "p2pListenFreqMHz",
    "p2pOperFreqMHz",
    "p2pFreqSource",
)


def merge_radio_fields(
    data: dict[str, Any],
    iw_text: str,
    station_text: str = "",
    peer_text: str = "",
) -> dict[str, Any]:
    keys = (
        "staChannel",
        "staFreqMHz",
        "staWidthMHz",
        "staIface",
        "p2pChannel",
        "p2pFreqMHz",
        "p2pWidthMHz",
        "p2pIface",
        "p2pRole",
        "radioMcc",
    ) + _LINK_KEYS
    for k in keys:
        data.pop(k, None)
    data.update(parse_iw_dev(iw_text))
    if peer_text:
        data.update(parse_p2p_peer_freqs(peer_text))
    # Recompute MCC / maybe rewrite p2p freq after listen_freq is known.
    data.update(_concurrency_fields(data))
    if station_text:
        data.update(parse_station_dump(station_text))
    return data


def _iw_dev_text() -> str:
    env = os.environ.get("MIRACAST_IW_DEV_TEXT")
    if env is not None:
        return env
    try:
        return subprocess.check_output(["iw", "dev"], text=True, timeout=5.0)
    except Exception:
        return ""


def _station_dump_text(p2p_iface: Optional[str]) -> str:
    env = os.environ.get("MIRACAST_IW_STATION_TEXT")
    if env is not None:
        return env
    if not p2p_iface:
        return ""
    try:
        return subprocess.check_output(
            ["iw", "dev", p2p_iface, "station", "dump"],
            text=True,
            timeout=3.0,
        )
    except Exception:
        return ""


def _p2p_peer_text(peer_mac: Optional[str], sta_iface: Optional[str] = None) -> str:
    env = os.environ.get("MIRACAST_P2P_PEER_TEXT")
    if env is not None:
        return env
    if not peer_mac:
        return ""
    ifaces: list[str] = []
    if sta_iface:
        ifaces.append(sta_iface)
    try:
        import glob

        for sock in glob.glob("/run/wpa_supplicant/*") + glob.glob(
            "/var/run/wpa_supplicant/*"
        ):
            iface = os.path.basename(sock)
            if iface.startswith("p2p-") or iface in ifaces:
                continue
            ifaces.append(iface)
    except Exception:
        pass
    for iface in ifaces or ["wlp0s20f3", "wlan0"]:
        # ctrl socket is root-owned; Omarchy allows passwordless ``sudo -n wpa_cli``.
        for prefix in (["sudo", "-n"], []):
            cmd = prefix + ["wpa_cli", "-i", iface, "p2p_peer", peer_mac]
            try:
                out = subprocess.check_output(
                    cmd, text=True, timeout=2.0, stderr=subprocess.DEVNULL
                )
                if "listen_freq=" in out or "oper_freq=" in out:
                    return out
            except Exception:
                continue
    return ""


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--iw-text",
        default=None,
        help="iw dev dump (default: MIRACAST_IW_DEV_TEXT or live `iw dev`)",
    )
    p.add_argument(
        "--station-text",
        default=None,
        help="iw station dump (default: MIRACAST_IW_STATION_TEXT or live dump)",
    )
    p.add_argument(
        "--peer-text",
        default=None,
        help="wpa_cli p2p_peer dump (default: MIRACAST_P2P_PEER_TEXT or live)",
    )
    args = p.parse_args(argv)
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    iw_text = args.iw_text if args.iw_text is not None else _iw_dev_text()
    # Need iw first so we know staIface for wpa_cli.
    prelim = merge_radio_fields(data, iw_text, station_text="", peer_text="")
    peer_mac = str(data.get("peer") or "").strip() or None
    peer = (
        args.peer_text
        if args.peer_text is not None
        else _p2p_peer_text(peer_mac, sta_iface=prelim.get("staIface"))
    )
    merged = merge_radio_fields(data, iw_text, station_text="", peer_text=peer)
    station = (
        args.station_text
        if args.station_text is not None
        else _station_dump_text(merged.get("p2pIface"))
    )
    if station:
        link_only = (
            "p2pSignalDbm",
            "p2pTxBitrateMbps",
            "p2pRxBitrateMbps",
            "p2pTxBytes",
            "p2pTxPackets",
            "p2pTxRetries",
            "p2pTxFailed",
            "p2pRxBytes",
            "p2pRxPackets",
            "p2pRxDropMisc",
            "p2pTxRetryPercent",
        )
        for k in link_only:
            merged.pop(k, None)
        merged.update(parse_station_dump(station))
    print(json.dumps(merged, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
