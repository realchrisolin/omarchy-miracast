#!/usr/bin/env python3
"""Soak-test continuous -D VAAPI *pipe* Miracast for audio-only wedges.

Attach to a live Extend session (or rely on existing status.json streaming)
and sample cast.log / latency.jsonl / P2P TX for --duration seconds.

Fails (exit 1) if:
  * VIDEO_STALL / audio-only TX band persists without recovery
  * RENDER ENGINE falls back to CPU (libx264) while preference is vaapi
  * Dual wf-recorder pipelines appear
  * Stall count exceeds --max-stalls

Example:
  miracast-ctl set-render-engine vaapi
  miracast-ctl set-cast-preset movie   # DAMAGE=0 continuous -D
  # connect Miracast, then:
  ./scripts/soak_vaapi_pipe.py --duration 1800
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

STATE = Path.home() / ".local" / "state" / "omarchy-miracast"
DEFAULT_STATUS = STATE / "status.json"
DEFAULT_CAST_LOG = STATE / "logs" / "cast.log"
DEFAULT_LATENCY = STATE / "logs" / "latency.jsonl"
DEFAULT_CAPTURE_ENCODE = STATE / "capture-encode"


def _phase(status: Path) -> str:
    try:
        return str(json.loads(status.read_text()).get("phase") or "")
    except Exception:
        return ""


def _p2p_tx_bytes() -> int | None:
    total = 0
    found = False
    for p in Path("/sys/class/net").glob("p2p-*/statistics/tx_bytes"):
        try:
            total += int(p.read_text().strip())
            found = True
        except OSError:
            pass
    return total if found else None


def _wf_recorder_count() -> int:
    try:
        out = subprocess.check_output(["pgrep", "-a", "wf-recorder"], text=True)
    except Exception:
        return 0
    n = 0
    for line in out.splitlines():
        if "persistent-miracast" in line or "/dev/stdout" in line:
            n += 1
    return n


def _tail_new(path: Path, offset: int) -> tuple[str, int]:
    try:
        data = path.read_text(errors="ignore")
    except OSError:
        return "", offset
    if offset > len(data):
        offset = 0
    return data[offset:], len(data)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--duration", type=float, default=600.0, help="seconds")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    p.add_argument("--cast-log", type=Path, default=DEFAULT_CAST_LOG)
    p.add_argument("--latency", type=Path, default=DEFAULT_LATENCY)
    p.add_argument("--capture-encode", type=Path, default=DEFAULT_CAPTURE_ENCODE)
    p.add_argument("--max-stalls", type=int, default=6)
    p.add_argument("--audio-band-low", type=float, default=1.4)
    p.add_argument("--audio-band-high", type=float, default=2.2)
    p.add_argument(
        "--require-vaapi",
        action="store_true",
        default=True,
        help="fail if capture-encode file is not vaapi (default on)",
    )
    args = p.parse_args(argv)

    if args.require_vaapi:
        try:
            eng = args.capture_encode.read_text().strip().lower()
        except OSError:
            eng = ""
        if eng and eng != "vaapi":
            print(f"FAIL: capture-encode={eng!r} (want vaapi)", file=sys.stderr)
            return 2

    if _phase(args.status) != "streaming":
        print(
            f"FAIL: status phase={_phase(args.status)!r} (need streaming)",
            file=sys.stderr,
        )
        return 2

    print(
        f"soak_vaapi_pipe: duration={args.duration}s interval={args.interval}s",
        flush=True,
    )
    t0 = time.time()
    last_tx = _p2p_tx_bytes()
    last_t = t0
    cast_off = args.cast_log.stat().st_size if args.cast_log.exists() else 0
    lat_off = args.latency.stat().st_size if args.latency.exists() else 0
    stalls = 0
    audio_band_streak = 0
    cpu_fallback = 0
    dual_pipeline = 0
    video_stall_events = 0
    recoveries = 0

    while time.time() - t0 < args.duration:
        time.sleep(args.interval)
        now = time.time()
        if _phase(args.status) != "streaming":
            print(f"FAIL: left streaming at t={now - t0:.0f}s", file=sys.stderr)
            return 1

        tx = _p2p_tx_bytes()
        mbps = None
        if tx is not None and last_tx is not None and now > last_t:
            mbps = (tx - last_tx) * 8 / (now - last_t) / 1e6
        if tx is not None:
            last_tx = tx
            last_t = now

        chunk, cast_off = _tail_new(args.cast_log, cast_off)
        lat_chunk, lat_off = _tail_new(args.latency, lat_off)

        if "VIDEO_STALL" in chunk or '"event": "video_stall"' in lat_chunk:
            video_stall_events += 1
            stalls += 1
        if "bufs_size storm" in chunk or "bufs-size-storm" in chunk:
            stalls += 1
        if "fell back to CPU" in chunk or "libx264" in chunk and "RENDER ENGINE" in chunk:
            if "fell back to CPU" in chunk:
                cpu_fallback += 1
        if "Desktop capture pipeline restarted" in chunk:
            recoveries += 1

        n_wf = _wf_recorder_count()
        if n_wf > 1:
            dual_pipeline += 1
            print(f"WARN: {n_wf} wf-recorder pipelines at t={now - t0:.0f}s", flush=True)

        if mbps is not None and args.audio_band_low <= mbps <= args.audio_band_high:
            audio_band_streak += 1
        else:
            if audio_band_streak >= 3:
                stalls += 1
            audio_band_streak = 0

        if int(now - t0) % 30 < args.interval:
            print(
                f"t={now - t0:.0f}s mbps={mbps and round(mbps, 2)} "
                f"stalls={stalls} video_stall={video_stall_events} "
                f"recoveries={recoveries} wf={n_wf}",
                flush=True,
            )

        if stalls > args.max_stalls:
            print(f"FAIL: stalls={stalls} > max={args.max_stalls}", file=sys.stderr)
            return 1
        if cpu_fallback:
            print("FAIL: CPU (libx264) fallback observed", file=sys.stderr)
            return 1
        if dual_pipeline > 3:
            print("FAIL: dual wf-recorder pipelines persisted", file=sys.stderr)
            return 1

    print(
        f"PASS: duration={args.duration}s stalls={stalls} "
        f"video_stall_events={video_stall_events} recoveries={recoveries}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
