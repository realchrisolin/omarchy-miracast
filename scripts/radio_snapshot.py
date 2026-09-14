#!/usr/bin/env python3
"""Capture public-safe Wi-Fi / P2P radio details for Miracast benchmarks.

Includes channels, bandwidth, role, MCC/SCC, signal, negotiated bitrates, and
throughput samples. Scrubs SSIDs, BSSIDs, and MAC addresses from any raw text.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

_MAC_RE = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
_SSID_LINE_RE = re.compile(r"(?i)^\s*SSID:")
_CONNECTED_RE = re.compile(r"(?i)^\s*Connected to\s+")


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception:
        return ""


def _scrub_iw_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if _SSID_LINE_RE.match(line) or _CONNECTED_RE.match(line):
            continue
        lines.append(_MAC_RE.sub("<mac>", line))
    return "\n".join(lines)


def _parse_bitrate(line: str) -> Optional[float]:
    # "tx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz ..."
    m = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\s*MBit/s", line, re.I)
    return float(m.group(1)) if m else None


def _parse_signal(line: str) -> Optional[int]:
    m = re.search(r"signal:\s*(-?\d+)\s*dBm", line, re.I)
    return int(m.group(1)) if m else None


def _link_stats(iface: str) -> dict[str, Any]:
    raw = _run(["iw", "dev", iface, "link"])
    out: dict[str, Any] = {"iface": iface}
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("freq:"):
            try:
                out["freq_mhz"] = float(s.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif s.startswith("signal:"):
            out["signal_dbm"] = _parse_signal(s)
        elif s.lower().startswith("tx bitrate:"):
            out["tx_bitrate_mbps"] = _parse_bitrate(s)
            out["tx_bitrate_raw"] = _MAC_RE.sub("<mac>", s.split(":", 1)[1].strip())
        elif s.lower().startswith("rx bitrate:"):
            out["rx_bitrate_mbps"] = _parse_bitrate(s)
            out["rx_bitrate_raw"] = _MAC_RE.sub("<mac>", s.split(":", 1)[1].strip())
    info = _run(["iw", "dev", iface, "info"])
    for line in info.splitlines():
        s = line.strip()
        if s.startswith("type "):
            out["type"] = s[5:].strip()
        if s.startswith("channel "):
            # channel 149 (5745 MHz), width: 80 MHz, center1: 5775 MHz
            out["channel_line"] = s
            m = re.match(
                r"channel\s+(\d+)\s+\((\d+(?:\.\d+)?)\s*MHz\).*width:\s*(\d+)\s*MHz",
                s,
            )
            if m:
                out["channel"] = int(m.group(1))
                out["freq_mhz"] = float(m.group(2))
                out["width_mhz"] = int(m.group(3))
    out["iw_link_scrubbed"] = _scrub_iw_text(raw) or None
    return out


def _tx_kbps(iface: str, seconds: float = 2.0) -> Optional[float]:
    path = Path(f"/sys/class/net/{iface}/statistics/tx_bytes")
    try:
        b0 = int(path.read_text())
        time.sleep(seconds)
        b1 = int(path.read_text())
        return round((b1 - b0) * 8 / (seconds * 1000.0), 1)
    except Exception:
        return None


def _status() -> dict[str, Any]:
    ctl = Path(__file__).resolve().parents[1] / "bin" / "miracast-ctl"
    raw = _run(["bash", str(ctl), "status"], timeout=12)
    try:
        return json.loads(raw)
    except Exception:
        return {}


def collect_radio(
    *,
    status: Optional[dict[str, Any]] = None,
    tx_sample_s: float = 2.0,
) -> dict[str, Any]:
    """Public-safe radio/negotiation snapshot for benchmarks."""
    st = status if status is not None else _status()
    sta_iface = st.get("staIface") or "wlp0s20f3"
    p2p_iface = st.get("p2pIface")

    sta = _link_stats(str(sta_iface)) if sta_iface else {}
    p2p = _link_stats(str(p2p_iface)) if p2p_iface else {}

    # Prefer miracast-ctl attach_radio fields when present (already scrubbed).
    sta_ch = st.get("staChannel") or sta.get("channel")
    p2p_ch = st.get("p2pChannel") or p2p.get("channel")
    sta_w = st.get("staWidthMHz") or sta.get("width_mhz")
    p2p_w = st.get("p2pWidthMHz") or p2p.get("width_mhz")
    sta_f = st.get("staFreqMHz") or sta.get("freq_mhz")
    p2p_f = st.get("p2pFreqMHz") or p2p.get("freq_mhz")
    mcc = st.get("radioMcc")
    if mcc is None and sta_ch is not None and p2p_ch is not None:
        try:
            mcc = int(sta_ch) != int(p2p_ch)
        except Exception:
            mcc = None

    tx = _tx_kbps(str(p2p_iface), tx_sample_s) if p2p_iface else None

    # Negotiation / session radio policy hints (no secrets).
    negotiation = {
        "p2p_role": st.get("p2pRole") or p2p.get("type"),
        "radio_mcc": mcc,
        "quiet_channel_csa": bool(mcc) if mcc is not None else None,
        "stream_mode": st.get("streamMode"),
        "mode": st.get("mode"),
        "go_intent_setting": None,  # filled by caller if known
        "oper_channel_soft_pin": p2p_ch,
    }
    # Env used by product path
    if os.environ.get("MIRACAST_SKIP_P2P_CSA", "").strip() in ("1", "true", "yes"):
        negotiation["quiet_channel_csa"] = False
        negotiation["csa_skipped"] = True

    return {
        "sta": {
            "iface": sta_iface,
            "channel": sta_ch,
            "freq_mhz": sta_f,
            "width_mhz": sta_w,
            "signal_dbm": sta.get("signal_dbm"),
            "tx_bitrate_mbps": sta.get("tx_bitrate_mbps"),
            "rx_bitrate_mbps": sta.get("rx_bitrate_mbps"),
            "tx_bitrate_raw": sta.get("tx_bitrate_raw"),
            "rx_bitrate_raw": sta.get("rx_bitrate_raw"),
        },
        "p2p": {
            "iface": p2p_iface,
            "channel": p2p_ch,
            "freq_mhz": p2p_f,
            "width_mhz": p2p_w,
            "role": st.get("p2pRole") or p2p.get("type"),
            "signal_dbm": p2p.get("signal_dbm"),
            "tx_bitrate_mbps": p2p.get("tx_bitrate_mbps"),
            "rx_bitrate_mbps": p2p.get("rx_bitrate_mbps"),
            "tx_bitrate_raw": p2p.get("tx_bitrate_raw"),
            "rx_bitrate_raw": p2p.get("rx_bitrate_raw"),
            "tx_kbps_sample": tx,
            "tx_sample_s": tx_sample_s if tx is not None else None,
        },
        "negotiation": negotiation,
    }


def public_radio(radio: dict[str, Any]) -> dict[str, Any]:
    """Drop scrubbed raw blobs; keep numeric/negotiated fields for crowdsource."""
    if not radio:
        return {}
    sta = dict(radio.get("sta") or {})
    p2p = dict(radio.get("p2p") or {})
    for blob in (sta, p2p):
        blob.pop("iw_link_scrubbed", None)
    return {
        "sta": sta,
        "p2p": p2p,
        "negotiation": radio.get("negotiation") or {},
    }


def main() -> int:
    print(json.dumps(collect_radio(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
