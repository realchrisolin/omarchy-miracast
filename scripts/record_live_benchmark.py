#!/usr/bin/env python3
"""Record a live Miracast session benchmark into docs/benchmarks/private/.

Captures full sink name + MAC (and host Wi-Fi details) for local analysis.
Run ``./scripts/sanitize_benchmark_results.py`` afterward to publish a
crowdsource-safe row into ``docs/benchmarks/public/`` and ``BENCHMARKS.md``.

Private JSON filenames are always:
  ``<device_name>[-<case>]-<YYYYMMDD_HHMMSS>.json``
e.g. ``hotyeah-20260914_211205.json`` or ``hotyeah-pipe_cqp-20260914_211205.json``.

Also records encode preset/settings, live ffmpeg/wf-recorder argv (home scrubbed),
Hyprland + capture/encode CPU, and a 1 Hz TX/retry profile.

Usage (while casting):
  ./scripts/record_live_benchmark.py
  SAMPLE_S=30 ./scripts/record_live_benchmark.py --case pipe_qvbr_movie
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
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
    private_filename_stem,
    private_path,
    sanitize_display_name,
    scrub_text,
    write_json,
)
from fingerprint_miracast_sink import fingerprint_sink, public_identity  # noqa: E402
from radio_snapshot import collect_radio, public_radio  # noqa: E402
from quality_snapshot import collect_quality  # noqa: E402


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


def _settings() -> dict[str, Any]:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = cfg / "omarchy-miracast" / "settings.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            errors="replace"
        )
        return scrub_text(raw.strip())
    except Exception:
        return ""


def _find_encode_pids() -> dict[str, Optional[int]]:
    wf = ff = flux = None
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except Exception:
            continue
        if "src/main.py" in cmd and "--protocol wfd" in cmd:
            flux = int(p.name)
        elif "wf-recorder" in cmd and ("rawvideo" in cmd or "h264_vaapi" in cmd):
            wf = int(p.name)
        elif "ffmpeg" in cmd and "h264_vaapi" in cmd:
            ff = int(p.name)
    return {"fluxcast": flux, "wf_recorder": wf, "ffmpeg": ff}


def _parse_ffmpeg_vaapi(cmdline: str) -> dict[str, Any]:
    """Extract RC knobs from a live ffmpeg h264_vaapi argv string."""
    out: dict[str, Any] = {}
    if not cmdline:
        return out

    def after(flag: str) -> Optional[str]:
        m = re.search(rf"(?:^|\s){re.escape(flag)}\s+(\S+)", cmdline)
        return m.group(1) if m else None

    for key, flag in (
        ("rc_mode", "-rc_mode"),
        ("qp", "-qp"),
        ("bitrate", "-b:v"),
        ("maxrate", "-maxrate"),
        ("bufsize", "-bufsize"),
        ("quality", "-quality"),
        ("gop", "-g"),
        ("fps", "-r"),
        ("async_depth", "-async_depth"),
        ("profile", "-profile:v"),
        ("level", "-level"),
    ):
        val = after(flag)
        if val is not None:
            out[key] = val
    return out


def _collect_encode(settings: dict[str, Any]) -> dict[str, Any]:
    pids = _find_encode_pids()
    ff_cmd = _cmdline(pids["ffmpeg"]) if pids.get("ffmpeg") else ""
    wf_cmd = _cmdline(pids["wf_recorder"]) if pids.get("wf_recorder") else ""
    flux_cmd = _cmdline(pids["fluxcast"]) if pids.get("fluxcast") else ""
    live = _parse_ffmpeg_vaapi(ff_cmd)
    preset_keys = (
        "castPreset",
        "captureEncode",
        "vaapiRcMode",
        "vaapiQp",
        "vaapiGop",
        "vaapiBitrate",
        "vaapiQuality",
        "vbvMultiplier",
        "wfRecorderDamage",
        "bitrate",
        "fps",
        "outputRes",
    )
    return {
        "settings": {k: settings.get(k) for k in preset_keys if k in settings},
        "pids": pids,
        "live_ffmpeg": live,
        "cmdline": {
            "ffmpeg": ff_cmd or None,
            "wf_recorder": wf_cmd or None,
            "fluxcast": flux_cmd or None,
        },
    }


def _sample_cpu_bundle(sample_s: float, pids: dict[str, Optional[int]]) -> dict[str, Any]:
    """Sample Hyprland + capture/encode CPU over the same wall window."""
    hz = os.sysconf("SC_CLK_TCK")
    names: dict[str, Optional[int]] = dict(pids)
    try:
        hypr_pids = _run(["pgrep", "-x", "Hyprland"]).split()
        names["hyprland"] = int(hypr_pids[-1]) if hypr_pids else None
    except Exception:
        names["hyprland"] = None

    t0: dict[str, Optional[int]] = {k: _cpu_ticks(v) if v else None for k, v in names.items()}
    wall0 = time.time()
    time.sleep(sample_s)
    wall1 = time.time()
    elapsed = max(wall1 - wall0, 1e-6)
    out: dict[str, Any] = {"sample_s": round(sample_s, 3)}
    for key, pid in names.items():
        a, b = t0.get(key), _cpu_ticks(pid) if pid else None
        if a is None or b is None:
            out[f"{key}_pct_one_core"] = None
            continue
        out[f"{key}_pct_one_core"] = round(100.0 * (b - a) / (hz * elapsed), 2)
    return out


def _sample_tx_profile(iface: Optional[str], seconds: float) -> dict[str, Any]:
    """1 Hz P2P TX Mbps + station retry deltas."""
    if not iface or seconds <= 0:
        return {"skipped": True, "reason": "no iface or seconds<=0"}
    tx_path = Path(f"/sys/class/net/{iface}/statistics/tx_bytes")
    if not tx_path.is_file():
        return {"skipped": True, "reason": f"missing {tx_path}"}

    def station() -> dict[str, Any]:
        out = _run(["iw", "dev", iface, "station", "dump"], timeout=3.0)
        def g(pat: str, cast=float):
            m = re.search(pat, out)
            if not m:
                return None
            try:
                return cast(m.group(1))
            except Exception:
                return None
        return {
            "retries": g(r"tx retries:\s*(\d+)", int),
            "failed": g(r"tx failed:\s*(\d+)", int),
            "packets": g(r"tx packets:\s*(\d+)", int),
            "signal_avg_dbm": g(r"signal avg:\s*(-?\d+)", int),
            "tx_bitrate_mbps": g(r"tx bitrate:\s*([0-9.]+)"),
        }

    n = max(1, int(round(seconds)))
    st0 = station()
    t0 = time.monotonic()
    try:
        tx0 = int(tx_path.read_text())
    except OSError as exc:
        return {"skipped": True, "reason": str(exc)}
    r0 = st0.get("retries")
    rates: list[float] = []
    retry_deltas: list[int] = []
    for _ in range(n):
        time.sleep(1.0)
        t1 = time.monotonic()
        try:
            tx1 = int(tx_path.read_text())
        except OSError:
            break
        st = station()
        dt = max(t1 - t0, 1e-6)
        rates.append((tx1 - tx0) * 8.0 / dt / 1e6)
        if isinstance(r0, int) and isinstance(st.get("retries"), int):
            retry_deltas.append(int(st["retries"]) - int(r0))
            r0 = st["retries"]
        t0, tx0 = t1, tx1
    st1 = station()
    if not rates:
        return {"skipped": True, "reason": "no samples"}
    mean = statistics.mean(rates)
    return {
        "skipped": False,
        "seconds": float(n),
        "iface": iface,
        "tx_mbps_mean": round(mean, 3),
        "tx_mbps_min": round(min(rates), 3),
        "tx_mbps_max": round(max(rates), 3),
        "tx_mbps_p95": round(sorted(rates)[int(0.95 * (len(rates) - 1))], 3),
        "tx_cv": round(statistics.pstdev(rates) / mean, 3) if mean else None,
        "retry_per_s_mean": round(statistics.mean(retry_deltas), 3) if retry_deltas else None,
        "retry_per_s_max": max(retry_deltas) if retry_deltas else None,
        "retries_total": sum(retry_deltas) if retry_deltas else None,
        "signal_avg_dbm": st1.get("signal_avg_dbm"),
        "phy_mbps": st1.get("tx_bitrate_mbps"),
        "tx_failed_delta": (
            (st1.get("failed") - st0.get("failed"))
            if isinstance(st0.get("failed"), int) and isinstance(st1.get("failed"), int)
            else None
        ),
    }


def _sample_hyprland(sample_s: float) -> Optional[float]:
    bundle = _sample_cpu_bundle(sample_s, {})
    return bundle.get("hyprland_pct_one_core")


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
    p.add_argument(
        "--case",
        default=None,
        help="optional case tag in the filename (between device and timestamp)",
    )
    p.add_argument(
        "--stem",
        default=None,
        help="deprecated: treated as --case (device + timestamp are always applied)",
    )
    p.add_argument("--warmup", type=float, default=8.0)
    p.add_argument(
        "--sample",
        type=float,
        default=float(os.environ.get("SAMPLE_S", "30")),
        help="CPU + quality window seconds (default 30)",
    )
    p.add_argument(
        "--tx-sample",
        type=float,
        default=float(os.environ.get("TX_SAMPLE_S", "20")),
        help="radio TX kbps sample + 1 Hz TX/retry profile length (default 20)",
    )
    args = p.parse_args(argv)

    status = _status()
    if status.get("phase") != "streaming":
        print(
            f"[record-live] warning: phase={status.get('phase')!r} (expected streaming)",
            file=sys.stderr,
        )

    settings = _settings()
    encode = _collect_encode(settings)
    print(f"[record-live] warmup {args.warmup}s…", file=sys.stderr)
    time.sleep(args.warmup)

    print(f"[record-live] CPU sample {args.sample}s (Hyprland + capture/encode)…", file=sys.stderr)
    cpu = _sample_cpu_bundle(args.sample, encode.get("pids") or {})
    hypr = cpu.get("hyprland_pct_one_core")

    print(f"[record-live] radio snapshot (tx {args.tx_sample}s)…", file=sys.stderr)
    radio = collect_radio(status=status, tx_sample_s=min(args.tx_sample, 15.0))
    tx = (radio.get("p2p") or {}).get("tx_kbps_sample")
    if tx is None:
        tx = _p2p_tx_kbps(status.get("p2pIface"), min(args.tx_sample, 5.0))

    print(f"[record-live] TX/retry profile {args.tx_sample}s…", file=sys.stderr)
    tx_profile = _sample_tx_profile(status.get("p2pIface"), args.tx_sample)

    # Refresh encode cmdlines after samples (PIDs can restart)
    encode = _collect_encode(settings)

    print(f"[record-live] quality probes (window {args.sample}s)…", file=sys.stderr)
    quality = collect_quality(radio=radio, window_s=max(args.sample, 20.0))

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
    case = getattr(args, "case", None) or args.stem
    stem = private_filename_stem(display, case=case)

    live = encode.get("live_ffmpeg") or {}
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
            "capture_encode": status.get("captureEncode"),
            "encoder": status.get("encoder"),
            "persist_display": status.get("preserveDisplayAcrossMonitors"),
            "extend_position": status.get("extendPosition"),
            "sink_scale": (status.get("sinkScales") or {}).get(mac)
            if mac
            else None,
        },
        "encode": encode,
        "metrics": {
            "sample_s": args.sample,
            "tx_profile_s": args.tx_sample,
            "hyprland_pct_one_core": hypr,
            "wf_recorder_pct_one_core": cpu.get("wf_recorder_pct_one_core"),
            "ffmpeg_pct_one_core": cpu.get("ffmpeg_pct_one_core"),
            "fluxcast_pct_one_core": cpu.get("fluxcast_pct_one_core"),
            "p2p_tx_kbps": tx,
            "tx_mbps_mean": tx_profile.get("tx_mbps_mean"),
            "tx_mbps_min": tx_profile.get("tx_mbps_min"),
            "tx_mbps_max": tx_profile.get("tx_mbps_max"),
            "tx_mbps_p95": tx_profile.get("tx_mbps_p95"),
            "tx_cv": tx_profile.get("tx_cv"),
            "retry_per_s_mean": tx_profile.get("retry_per_s_mean"),
            "retry_per_s_max": tx_profile.get("retry_per_s_max"),
            "sta_channel": status.get("staChannel"),
            "p2p_channel": status.get("p2pChannel"),
            "sta_freq_mhz": status.get("staFreqMHz"),
            "p2p_freq_mhz": status.get("p2pFreqMHz"),
            "sta_width_mhz": status.get("staWidthMHz"),
            "p2p_width_mhz": status.get("p2pWidthMHz"),
            "radio_mcc": status.get("radioMcc"),
            "p2p_role": status.get("p2pRole"),
            "vaapi_rc_mode": live.get("rc_mode") or settings.get("vaapiRcMode"),
            "vaapi_qp": live.get("qp") or settings.get("vaapiQp"),
            "vaapi_gop": live.get("gop") or settings.get("vaapiGop"),
            "vaapi_maxrate": live.get("maxrate") or settings.get("vaapiBitrate"),
            "vaapi_quality": live.get("quality") or settings.get("vaapiQuality"),
            "cast_preset": settings.get("castPreset"),
        },
        "tx_profile": tx_profile,
        "cpu": cpu,
        "radio": radio,
        "radio_public": public_radio(radio),
        "quality": quality,
        "quality_public": {
            "criteria": (quality.get("criteria") or {}),
            "window_s": quality.get("window_s"),
            "capture_restarts": (quality.get("cast_log") or {}).get("capture_restarts"),
            "hard_error_hits": (quality.get("cast_log") or {}).get("hard_error_hits"),
            "tx_stability_cv": (quality.get("cast_log") or {}).get("tx_stability_cv")
            or ((quality.get("latency_log") or {}).get("tx_stability_cv")),
            "setup_ms": (quality.get("latency_log") or {}).get("setup_ms"),
            "sender_path_latency_ms": (quality.get("latency_log") or {}).get(
                "sender_path_latency_ms"
            ),
        },
        "status_raw": status,
        "public_notes": [
            "Full live Extend baseline; private dump includes sink MAC / Wi-Fi SSIDs.",
            "Encode block: settings + live ffmpeg RC knobs (home paths scrubbed).",
            "Public radio fields: channels/width/freq, MCC, role, signal, bitrates, TX sample.",
            "Quality: lightweight delivery/radio criteria (not VMAF/SSIM).",
        ],
        "notes": [
            "PRIVATE — do not commit. Sanitize with scripts/sanitize_benchmark_results.py.",
        ],
    }

    path = write_json(private_path(stem), private)
    safe_sink = sanitize_display_name(display) or "sink"
    print(
        json.dumps(
            {
                "ok": True,
                "private": str(path),
                "stem": stem,
                "sink": safe_sink,
                "encode": {
                    "preset": settings.get("castPreset"),
                    "rc": live.get("rc_mode") or settings.get("vaapiRcMode"),
                    "qp": live.get("qp") or settings.get("vaapiQp"),
                    "maxrate": live.get("maxrate") or settings.get("vaapiBitrate"),
                    "gop": live.get("gop") or settings.get("vaapiGop"),
                },
                "tx_mbps_max": tx_profile.get("tx_mbps_max"),
                "quality": (quality.get("criteria") or {}).get("overall"),
            },
            indent=2,
        )
    )
    print(
        "[record-live] next: ./scripts/sanitize_benchmark_results.py",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
