#!/usr/bin/env python3
"""Lightweight Miracast quality probes (no VMAF/SSIM).

Collects delivery / encode-path health during a live cast:

- Radio signal quality (dBm, negotiated bitrate) via ``radio_snapshot``
- TX throughput stability from FluxCast ``sender_health`` log lines / latency.jsonl
- Cast-log incidents: capture restarts, buffer-pool / DTS errors, unhealthy sender

Emits pass/warn/fail criteria suitable for crowdsource benchmarks (no MACs/SSIDs).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "omarchy-miracast"
_LOG = _STATE / "logs" / "cast.log"
_LATENCY = _STATE / "logs" / "latency.jsonl"

_TX_KIB = re.compile(
    r"Sender health:.*tx\+(\d+)\s*KiB",
    re.I,
)
_RESTART = re.compile(
    r"Desktop capture pipeline restarted|Capture restart finished|SIGUSR1: capture restart",
    re.I,
)
_HARD = re.compile(
    r"Capture restart failed|RTP sender is not healthy|buffer pool full|DTS|Invalid DTS|Error opening output",
    re.I,
)
_HEALTH_LINE = re.compile(r"Sender health:", re.I)


def _parse_ts(line: str) -> Optional[float]:
    # 2026-09-14T13:11:22-0400 ...
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", line)
    if not m:
        return None
    try:
        # Prefer mono from latency jsonl; for cast.log use naive local parse via fromisoformat
        dt = datetime.fromisoformat(m.group(1))
        return dt.timestamp()
    except Exception:
        return None


def _read_lines(path: Path, max_bytes: int = 512_000) -> list[str]:
    if not path.is_file():
        return []
    try:
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


def probe_cast_log(window_s: float = 20.0) -> dict[str, Any]:
    lines = _read_lines(_LOG)
    now = time.time()
    # cast.log timestamps are local without tz in fromisoformat — treat as recent-only via tail count
    # Use last N health lines as the window proxy when tz parsing is ambiguous.
    health_tx: list[int] = []
    health_gaps: list[float] = []
    last_health_ts: Optional[float] = None
    restarts = 0
    hard_errors = 0
    health_events = 0

    # Prefer the last ~window worth of health lines (~5s cadence → window/5)
    for line in lines:
        if _RESTART.search(line):
            restarts += 1
        if _HARD.search(line):
            hard_errors += 1
        m = _TX_KIB.search(line)
        if m:
            health_events += 1
            health_tx.append(int(m.group(1)))
            ts = _parse_ts(line)
            if ts is not None and last_health_ts is not None:
                health_gaps.append(ts - last_health_ts)
            if ts is not None:
                last_health_ts = ts

    # Restrict to last window_s of health samples if timestamps available
    # Fall back: last max(3, window/5) samples
    max_samples = max(3, int(window_s / 5) + 1)
    recent_tx = health_tx[-max_samples:]
    deltas = [b - a for a, b in zip(recent_tx, recent_tx[1:]) if b >= a]
    tx_kib_per_interval = deltas
    out: dict[str, Any] = {
        "health_events": health_events,
        "health_samples_used": len(recent_tx),
        "capture_restarts": restarts,
        "hard_error_hits": hard_errors,
        "tx_kib_deltas": tx_kib_per_interval,
    }
    if tx_kib_per_interval:
        mean = statistics.mean(tx_kib_per_interval)
        out["tx_kib_delta_mean"] = round(mean, 2)
        out["tx_kib_delta_min"] = min(tx_kib_per_interval)
        out["tx_kib_delta_max"] = max(tx_kib_per_interval)
        if len(tx_kib_per_interval) >= 2:
            stdev = statistics.pstdev(tx_kib_per_interval)
            out["tx_kib_delta_stdev"] = round(stdev, 2)
            out["tx_stability_cv"] = round(stdev / mean, 3) if mean > 0 else None
        # Rough kbps assuming ~5s health cadence
        out["tx_kbps_from_health_approx"] = round(mean * 8 / 5.0, 1)
    if health_gaps:
        recent_gaps = health_gaps[-max_samples:]
        out["health_interval_s_mean"] = round(statistics.mean(recent_gaps), 2)
        out["health_interval_s_max"] = round(max(recent_gaps), 2)
    return out


def probe_latency_jsonl(window_s: float = 20.0) -> dict[str, Any]:
    lines = _read_lines(_LATENCY, max_bytes=256_000)
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    if not events:
        return {}
    # Window by mono clock if present
    last_mono = None
    for e in reversed(events):
        if isinstance(e.get("mono"), (int, float)):
            last_mono = float(e["mono"])
            break
    recent = events
    if last_mono is not None:
        recent = [
            e
            for e in events
            if isinstance(e.get("mono"), (int, float))
            and last_mono - float(e["mono"]) <= window_s
        ] or events[-20:]

    setup = next((e for e in events if e.get("event") == "play_accepted"), None)
    probe = next((e for e in events if e.get("event") == "latency_probe"), None)
    health = [e for e in recent if e.get("event") == "sender_health"]
    restarts = sum(
        1
        for e in recent
        if str(e.get("event") or "").startswith("capture") and "restart" in str(e.get("event"))
    )

    tx_vals: list[int] = []
    for e in health:
        m = re.search(r"tx\+(\d+)\s*KiB", str(e.get("tx_summary") or ""))
        if m:
            tx_vals.append(int(m.group(1)))
    deltas = [b - a for a, b in zip(tx_vals, tx_vals[1:]) if b >= a]

    out: dict[str, Any] = {
        "events_in_window": len(recent),
        "sender_health_events": len(health),
        "capture_restart_events": restarts,
    }
    if setup:
        out["setup_ms"] = setup.get("setup_ms")
    if probe:
        out["sender_startup_ms"] = probe.get("sender_startup_ms")
        out["sender_path_latency_ms"] = probe.get("sender_path_latency_ms")
    if deltas:
        mean = statistics.mean(deltas)
        out["tx_kib_delta_mean"] = round(mean, 2)
        out["tx_kib_delta_min"] = min(deltas)
        out["tx_kib_delta_max"] = max(deltas)
        if len(deltas) >= 2:
            stdev = statistics.pstdev(deltas)
            out["tx_kib_delta_stdev"] = round(stdev, 2)
            out["tx_stability_cv"] = round(stdev / mean, 3) if mean > 0 else None
    return out


def evaluate_criteria(
    *,
    radio: Optional[dict[str, Any]] = None,
    cast: Optional[dict[str, Any]] = None,
    latency: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return pass/warn/fail with reasons.

    Criteria (lightweight, not perceptual):
    - signal: P2P RSSI warn < -70 dBm, fail < -80 dBm
    - delivery: hard encode/RTP errors → fail; capture restarts → warn
    - stability: TX health delta CV warn > 0.5, fail > 1.0 (or zero deltas)
    - bitrate: P2P negotiated TX bitrate warn < 150 Mb/s for 1080p-class (soft)
    """
    radio = radio or {}
    cast = cast or {}
    latency = latency or {}
    p2p = radio.get("p2p") or {}
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    sig = p2p.get("signal_dbm")
    if isinstance(sig, (int, float)):
        if sig < -80:
            add("p2p_signal", "fail", f"{sig} dBm (< -80)")
        elif sig < -70:
            add("p2p_signal", "warn", f"{sig} dBm (< -70)")
        else:
            add("p2p_signal", "pass", f"{sig} dBm")
    else:
        add("p2p_signal", "warn", "unavailable")

    hard = int(cast.get("hard_error_hits") or 0)
    restarts = int(cast.get("capture_restarts") or latency.get("capture_restart_events") or 0)
    if hard > 0:
        add("delivery_errors", "fail", f"hard_error_hits={hard}")
    elif restarts > 0:
        add("delivery_errors", "warn", f"capture_restarts={restarts}")
    else:
        add("delivery_errors", "pass", "no restarts/hard errors in window")

    cv = cast.get("tx_stability_cv")
    if cv is None:
        cv = latency.get("tx_stability_cv")
    if cv is None:
        add("tx_stability", "warn", "insufficient samples")
    elif cv > 1.0:
        add("tx_stability", "fail", f"cv={cv}")
    elif cv > 0.5:
        add("tx_stability", "warn", f"cv={cv}")
    else:
        add("tx_stability", "pass", f"cv={cv}")

    rate = p2p.get("tx_bitrate_mbps")
    if isinstance(rate, (int, float)):
        if rate < 50:
            add("p2p_tx_bitrate", "fail", f"{rate} Mb/s")
        elif rate < 150:
            add("p2p_tx_bitrate", "warn", f"{rate} Mb/s (soft 1080p floor)")
        else:
            add("p2p_tx_bitrate", "pass", f"{rate} Mb/s")
    else:
        add("p2p_tx_bitrate", "warn", "unavailable")

    order = {"pass": 0, "warn": 1, "fail": 2}
    overall = "pass"
    for c in checks:
        if order[c["status"]] > order[overall]:
            overall = c["status"]

    return {
        "overall": overall,
        "checks": checks,
        "note": (
            "Lightweight delivery/radio criteria only — not perceptual video quality "
            "(no VMAF/SSIM/blockiness)."
        ),
    }


def collect_quality(
    *,
    radio: Optional[dict[str, Any]] = None,
    window_s: float = 20.0,
) -> dict[str, Any]:
    cast = probe_cast_log(window_s=window_s)
    latency = probe_latency_jsonl(window_s=window_s)
    if radio is None:
        try:
            from radio_snapshot import collect_radio

            radio = collect_radio(tx_sample_s=2.0)
        except Exception:
            radio = {}
    criteria = evaluate_criteria(radio=radio, cast=cast, latency=latency)
    return {
        "window_s": window_s,
        "cast_log": cast,
        "latency_log": latency,
        "criteria": criteria,
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window", type=float, default=20.0)
    args = p.parse_args(argv)
    print(json.dumps(collect_quality(window_s=args.window), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
