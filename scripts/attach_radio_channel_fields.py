#!/usr/bin/env python3
"""Merge STA / P2P-GO channel fields into miracast-ctl status JSON.

Reads status JSON on stdin, writes updated JSON on stdout.
Parses `iw dev` (or MIRACAST_IW_DEV_TEXT / --iw-text for tests).

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
    if out.get("staChannel") is not None and out.get("p2pChannel") is not None:
        out["radioMcc"] = out["staChannel"] != out["p2pChannel"]
    return out


def merge_radio_fields(data: dict[str, Any], iw_text: str) -> dict[str, Any]:
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
    )
    for k in keys:
        data.pop(k, None)
    data.update(parse_iw_dev(iw_text))
    return data


def _iw_dev_text() -> str:
    env = os.environ.get("MIRACAST_IW_DEV_TEXT")
    if env is not None:
        return env
    try:
        return subprocess.check_output(["iw", "dev"], text=True, timeout=5.0)
    except Exception:
        return ""


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--iw-text",
        default=None,
        help="iw dev dump (default: MIRACAST_IW_DEV_TEXT or live `iw dev`)",
    )
    args = p.parse_args(argv)
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    iw_text = args.iw_text if args.iw_text is not None else _iw_dev_text()
    print(json.dumps(merge_radio_fields(data, iw_text), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
