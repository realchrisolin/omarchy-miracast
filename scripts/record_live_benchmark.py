#!/usr/bin/env python3
"""Record a live Miracast session benchmark into docs/benchmarks/private/.

Captures full sink name + MAC (and host Wi-Fi details) for local analysis.
Run ``./scripts/sanitize_benchmark_results.py`` afterward to publish a
crowdsource-safe row into ``docs/benchmarks/public/`` and ``BENCHMARKS.md``.

Usage (while casting):
  ./scripts/record_live_benchmark.py
  SAMPLE_S=18 ./scripts/record_live_benchmark.py --stem lg_1080p_dmabuf
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from bench_host_info import collect as collect_host  # noqa: E402
from benchmark_privacy import (  # noqa: E402
    guess_manufacturer,
    guess_model_hint,
    private_path,
    write_json,
)
from fingerprint_miracast_sink import fingerprint_sink, public_identity  # noqa: E402
from radio_snapshot import collect_radio, public_radio  # noqa: E402


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception:
        return ""


def _status() -> dict[str, Any]:
    ctl = Path(__file__).resolve().parents[1] / "bin" / "miracast-ctl"
    raw = _run(["bash", str(ctl), "status"], timeout=15)
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _cpu_ticks(pid: int) -> Optional[int]:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        return int(fields[13]) + int(fields[14])
    except Exception:
        return None


def _sample_hyprland(sample_s: float) -> Optional[float]:
    try:
        # Newest Hyprland process
        pids = _run(["pgrep", "-x", "Hyprland"]).split()
        if not pids:
            return None
        pid = int(pids[-1])
    except Exception:
        return None
    hz = os.sysconf("SC_CLK_TCK")
    t0 = _cpu_ticks(pid)
    if t0 is None:
        return None
    wall0 = time.time()
    time.sleep(sample_s)
    t1 = _cpu_ticks(pid)
    wall1 = time.time()
    if t1 is None:
        return None
    elapsed = max(wall1 - wall0, 1e-6)
    return round(100.0 * (t1 - t0) / (hz * elapsed), 2)


def _p2p_tx_kbps(iface: Optional[str], seconds: float = 2.0) -> Optional[float]:
    if not iface:
        return None
    path = Path(f"/sys/class/net/{iface}/statistics/tx_bytes")
    try:
        b0 = int(path.read_text())
        time.sleep(seconds)
        b1 = int(path.read_text())
        return round((b1 - b0) * 8 / (seconds * 1000.0), 1)
    except Exception:
        return None


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stem", default=None, help="private filename stem")
    p.add_argument("--warmup", type=float, default=6.0)
    p.add_argument("--sample", type=float, default=float(os.environ.get("SAMPLE_S", "18")))
    p.add_argument("--tx-sample", type=float, default=2.0)
    args = p.parse_args(argv)

    status = _status()
    if status.get("phase") != "streaming":
        print(
            f"[record-live] warning: phase={status.get('phase')!r} (expected streaming)",
            file=sys.stderr,
        )

    print(f"[record-live] warmup {args.warmup}s…", file=sys.stderr)
    time.sleep(args.warmup)
    print(f"[record-live] Hyprland CPU sample {args.sample}s…", file=sys.stderr)
    hypr = _sample_hyprland(args.sample)
    print(f"[record-live] radio snapshot (tx {args.tx_sample}s)…", file=sys.stderr)
    radio = collect_radio(status=status, tx_sample_s=args.tx_sample)
    tx = (radio.get("p2p") or {}).get("tx_kbps_sample")
    if tx is None:
        tx = _p2p_tx_kbps(status.get("p2pIface"), args.tx_sample)

    host_full = collect_host(full=True)
    sink = host_full.get("sink") or {}
    display = sink.get("display_name") or status.get("peerName")
    mac = sink.get("mac") or status.get("peer")
    fp = fingerprint_sink(mac, use_wpa=True)
    ident = fp.get("identity") or {}
    manufacturer = (
        ident.get("manufacturer_normalized")
        or ident.get("manufacturer")
        or guess_manufacturer(display)
    )
    model = (
        ident.get("model_hint")
        or ident.get("model_code")
        or guess_model_hint(display or ident.get("device_name"), manufacturer)
    )
    display = ident.get("device_name") or display

    ts = datetime.now(timezone.utc).isoformat()
    day = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"live_{day}"

    private = {
        "stem": stem,
        "privacy": "private",
        "kind": "live_session",
        "ts": ts,
        "sink": {
            "display_name": display,
            "mac": mac,
            "manufacturer": manufacturer,
            "model_hint": model,
            "model_name": ident.get("model_name"),
            "model_code": ident.get("model_code"),
            "product_line": ident.get("product_line"),
            "p2p_role": status.get("p2pRole"),
            "wfd_role": status.get("p2pRole"),
            "fingerprint": fp,
            "fingerprint_public": public_identity(fp),
        },
        "host": {
            "cpu": host_full.get("cpu"),
            "gpu": host_full.get("gpu"),
            "wifi": host_full.get("wifi"),
            "kernel": host_full.get("kernel"),
            "compositor": host_full.get("compositor"),
            "monitors": host_full.get("monitors"),
        },
        "session": {
            "mode": status.get("mode"),
            "stream_mode": status.get("streamMode"),
            "capture_path": status.get("capturePath"),
            "encoder": status.get("encoder"),
            "persist_display": status.get("preserveDisplayAcrossMonitors"),
        },
        "metrics": {
            "sample_s": args.sample,
            "hyprland_pct_one_core": hypr,
            "p2p_tx_kbps": tx,
            "sta_channel": status.get("staChannel"),
            "p2p_channel": status.get("p2pChannel"),
            "sta_freq_mhz": status.get("staFreqMHz"),
            "p2p_freq_mhz": status.get("p2pFreqMHz"),
            "sta_width_mhz": status.get("staWidthMHz"),
            "p2p_width_mhz": status.get("p2pWidthMHz"),
            "radio_mcc": status.get("radioMcc"),
            "p2p_role": status.get("p2pRole"),
        },
        "radio": radio,
        "radio_public": public_radio(radio),
        "status_raw": status,
        "public_notes": [
            "Live Extend spot-check; private dump includes sink MAC / Wi-Fi SSIDs.",
            "Public radio fields: channels/width/freq, MCC, role, signal, bitrates, TX sample.",
        ],
        "notes": [
            "PRIVATE — do not commit. Sanitize with scripts/sanitize_benchmark_results.py.",
        ],
    }

    path = write_json(private_path(stem), private)
    print(json.dumps({"ok": True, "private": str(path), "sink": display, "mac": private["sink"]["mac"]}, indent=2))
    print(
        "[record-live] next: ./scripts/sanitize_benchmark_results.py",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
