#!/usr/bin/env python3
"""Verify whether Miracast air is on 2.4 or 5 GHz.

Combines:
  1. Status/peer fields (``oper_freq`` vs ``listen_freq`` vs iw GO)
  2. Optional monitor-mode dwell on both bands (needs a second NIC)

Usage:
  ./scripts/verify_p2p_air_band.py
  ./scripts/verify_p2p_air_band.py --json
  ./scripts/verify_p2p_air_band.py --monitor wlan1 --seconds 8

Exit 0 when a conclusion is reached; 1 on error; 2 when inconclusive.
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
from pathlib import Path
from typing import Any, Optional


def _run(cmd: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _status() -> dict[str, Any]:
    ctl = Path(__file__).resolve().parents[1] / "bin" / "miracast-ctl"
    if not ctl.is_file():
        return {}
    try:
        out = _run([str(ctl), "status"], timeout=10.0)
        return json.loads(out.stdout or "{}")
    except Exception:
        return {}


def _iw_go_freq() -> Optional[int]:
    try:
        out = _run(["iw", "dev"]).stdout or ""
    except Exception:
        return None
    # Prefer P2P-GO block
    block = ""
    cur = []
    for ln in out.splitlines():
        if ln.startswith("Interface "):
            if cur and any("P2P-GO" in x for x in cur):
                block = "\n".join(cur)
                break
            cur = [ln]
        else:
            cur.append(ln)
    if not block and cur and any("P2P-GO" in x for x in cur):
        block = "\n".join(cur)
    m = re.search(r"channel\s+\d+\s+\((\d+)\s*MHz\)", block)
    return int(m.group(1)) if m else None


def _monitor_capture(iface: str, freq_mhz: int, seconds: float) -> dict[str, Any]:
    """Dwell on freq with tcpdump; count frames (best-effort)."""
    result: dict[str, Any] = {
        "iface": iface,
        "freqMHz": freq_mhz,
        "ok": False,
        "packets": 0,
        "error": "",
    }
    if not shutil.which("tcpdump"):
        result["error"] = "tcpdump not installed"
        return result
    # Bring monitor up on frequency (requires privileges).
    cmds = [
        ["sudo", "-n", "ip", "link", "set", iface, "down"],
        ["sudo", "-n", "iw", "dev", iface, "set", "type", "monitor"],
        ["sudo", "-n", "ip", "link", "set", iface, "up"],
        ["sudo", "-n", "iw", "dev", iface, "set", "freq", str(freq_mhz)],
    ]
    for c in cmds:
        try:
            r = _run(c, timeout=5.0)
            if r.returncode != 0:
                result["error"] = (r.stderr or r.stdout or "iw/ip failed").strip()[:200]
                return result
        except Exception as exc:
            result["error"] = str(exc)
            return result
    pcap = Path(f"/tmp/miracast-air-{freq_mhz}.pcap")
    try:
        proc = subprocess.Popen(
            [
                "sudo",
                "-n",
                "tcpdump",
                "-i",
                iface,
                "-w",
                str(pcap),
                "-c",
                "200",
                "type",
                "data",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(max(1.0, float(seconds)))
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        if pcap.is_file():
            result["packets"] = max(0, pcap.stat().st_size // 64)  # rough
            result["ok"] = True
            result["pcap"] = str(pcap)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def conclude(st: dict[str, Any], iw_freq: Optional[int]) -> dict[str, Any]:
    listen = st.get("p2pListenFreqMHz")
    oper = st.get("p2pOperFreqMHz")
    reported = st.get("p2pFreqMHz")
    source = st.get("p2pFreqSource")
    out: dict[str, Any] = {
        "phase": st.get("phase"),
        "linkUp": st.get("linkUp"),
        "staFreqMHz": st.get("staFreqMHz"),
        "iwGoFreqMHz": iw_freq,
        "p2pListenFreqMHz": listen,
        "p2pOperFreqMHz": oper,
        "p2pFreqMHz": reported,
        "p2pFreqSource": source,
        "radioMcc": st.get("radioMcc"),
        "conclusion": "inconclusive",
        "airBand": None,
        "notes": [],
    }
    notes = out["notes"]

    if oper and int(oper) >= 5000:
        out["conclusion"] = "oper_5ghz"
        out["airBand"] = "5"
        notes.append(
            f"Peer oper_freq={oper} — sink accepted 5 GHz GO (Smart View–class)."
        )
        if listen and int(listen) < 3000:
            notes.append(
                f"listen_freq={listen} is discovery-only; do not treat as air band."
            )
        if iw_freq and int(iw_freq) >= 5000:
            notes.append(f"iw GO also on {iw_freq} MHz — agrees with oper.")
        if st.get("staFreqMHz") and int(st["staFreqMHz"]) == int(oper):
            notes.append(
                "STA and P2P oper share the same primary — true SCC on one radio "
                "(HT20 Miracast vs HE STA width is the remaining tax)."
            )
        return out

    if oper and 2400 <= int(oper) < 2500:
        out["conclusion"] = "oper_2.4"
        out["airBand"] = "2.4"
        notes.append(f"Peer oper_freq={oper} — Miracast air is 2.4 GHz.")
        return out

    if source == "peer_listen" and listen:
        out["conclusion"] = "listen_fallback_2.4"
        out["airBand"] = "2.4"
        notes.append(
            "No valid 5 GHz oper; status fell back to listen_freq (Intel MCC lie path)."
        )
        return out

    if iw_freq and int(iw_freq) >= 5000:
        out["conclusion"] = "iw_5ghz_only"
        out["airBand"] = "5"
        notes.append("Only iw GO freq available; treat as 5 GHz pending peer oper.")
        return out

    notes.append("Need active cast + peer oper/listen fields or --monitor dwell.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--monitor",
        default="",
        help="optional second NIC for monitor-mode dwell (e.g. wlan1)",
    )
    ap.add_argument("--seconds", type=float, default=6.0)
    args = ap.parse_args()

    st = _status()
    iw_freq = _iw_go_freq()
    result = conclude(st, iw_freq)

    if args.monitor:
        mon = args.monitor.strip()
        dwells = []
        for freq in (2437, 5220):
            dwells.append(_monitor_capture(mon, freq, args.seconds))
        result["monitorDwells"] = dwells
        # Prefer band with more data frames if capture worked.
        ok = [d for d in dwells if d.get("ok")]
        if len(ok) >= 2:
            a, b = ok[0], ok[1]
            if a["packets"] > b["packets"] * 2:
                result["monitorHint"] = f"more traffic on {a['freqMHz']}"
            elif b["packets"] > a["packets"] * 2:
                result["monitorHint"] = f"more traffic on {b['freqMHz']}"
            else:
                result["monitorHint"] = "similar counts — inconclusive (encryption)"

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"conclusion: {result['conclusion']}  airBand={result['airBand']}")
        print(
            f"  sta={result['staFreqMHz']}  iwGO={result['iwGoFreqMHz']}  "
            f"listen={result['p2pListenFreqMHz']}  oper={result['p2pOperFreqMHz']}  "
            f"reported={result['p2pFreqMHz']} ({result['p2pFreqSource']})"
        )
        for n in result.get("notes") or []:
            print(f"  - {n}")
        if result.get("monitorHint"):
            print(f"  monitor: {result['monitorHint']}")

    if result["conclusion"] == "inconclusive":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
