#!/usr/bin/env python3
"""Miracast auto-tune: offline probes + optional short live sample.

Combines:
  - capability probes (VAAPI / wf-recorder / ffmpeg)
  - optional docs/benchmarks/results.tsv via recommend-cast-profile
  - list_p2p_radios idle P2P-GO preference
  - validated encode knobs (quality 5, VBV 0.5, SCC)
  - if a cast is streaming: ~25s TX / retry / CPU sample to refine advice

Usage:
  scripts/auto_tune_miracast.py                 # offline + live if streaming
  scripts/auto_tune_miracast.py --offline-only  # skip live sample
  scripts/auto_tune_miracast.py --live-seconds 30
  scripts/auto_tune_miracast.py --apply
  scripts/auto_tune_miracast.py --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(cmd: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _has_vaapi_device() -> bool:
    dri = Path("/dev/dri")
    if not dri.is_dir():
        return False
    return any(dri.glob("renderD*"))


def _ffmpeg_has_h264_vaapi() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        r = _run([ffmpeg, "-hide_banner", "-encoders"], timeout=8.0)
    except Exception:
        return False
    blob = (r.stdout or "") + (r.stderr or "")
    return "h264_vaapi" in blob


def _wf_recorder_path() -> str:
    return shutil.which("wf-recorder") or ""


def _icc_capable(path: str) -> bool:
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return False
    try:
        r = _run([path, "--help"], timeout=3.0)
        blob = (r.stdout or "") + (r.stderr or "")
        return "--toplevel" in blob or "ext-copy-capture" in blob or "ext_image_copy" in blob
    except Exception:
        return False


@dataclass
class EngineProbe:
    dmabuf: bool
    vaapi_pipe: bool
    cpu: bool
    ffmpeg: str
    wf_recorder: str
    vaapi_device: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class LiveSample:
    ok: bool
    skipped: bool
    reason: str
    seconds: float = 0.0
    p2p_iface: str = ""
    tx_mbps_mean: Optional[float] = None
    tx_cv: Optional[float] = None
    retry_per_s: Optional[float] = None
    retry_pct: Optional[float] = None
    wf_cpu_mean: Optional[float] = None
    ffmpeg_cpu_mean: Optional[float] = None
    signal_avg_dbm: Optional[int] = None
    phy_mbps: Optional[float] = None
    cast_log_hard_errors: int = 0
    current_capture_path: str = ""  # dmabuf | pipe | ""
    current_capture_encode: str = ""


@dataclass
class TuneResult:
    capture_encode: str  # dmabuf | vaapi | cpu
    p2p_wifi_interface: str  # auto | iface
    p2p_wifi_resolved: str
    p2p_quiet_csa: bool
    vaapi_quality: str
    vbv_multiplier: str
    wf_recorder_bin: str
    wf_recorder_proto: str
    video_encoder: str
    wf_recorder_damage: str
    engines: dict[str, Any]
    radios: list[dict[str, Any]]
    rationale: list[str]
    source: str  # capability | benchmarks+capability | …+live
    live: Optional[dict[str, Any]] = None


def probe_engines() -> EngineProbe:
    notes: list[str] = []
    ffmpeg = shutil.which("ffmpeg") or ""
    wf = _wf_recorder_path()
    has_dev = _has_vaapi_device()
    has_enc = _ffmpeg_has_h264_vaapi()
    vaapi_pipe = bool(has_dev and has_enc and ffmpeg)
    # DMA-BUF path needs wf-recorder + VAAPI encode on device
    dmabuf = bool(vaapi_pipe and wf)
    if not has_dev:
        notes.append("no /dev/dri/renderD* — VAAPI unavailable")
    if has_dev and not has_enc:
        notes.append("ffmpeg lacks h264_vaapi encoder")
    if not wf:
        notes.append("wf-recorder not on PATH — dmabuf path unavailable")
    if not ffmpeg:
        notes.append("ffmpeg not on PATH")
    return EngineProbe(
        dmabuf=dmabuf,
        vaapi_pipe=vaapi_pipe,
        cpu=True,
        ffmpeg=ffmpeg,
        wf_recorder=wf,
        vaapi_device=has_dev,
        notes=notes,
    )


def pick_encode(engines: EngineProbe, bench_encode: Optional[str] = None) -> tuple[str, str]:
    """Return (captureEncode, rationale). Prefer bench winner if still supported."""
    order = ["dmabuf", "vaapi", "cpu"]
    supported = {
        "dmabuf": engines.dmabuf,
        "vaapi": engines.vaapi_pipe,
        "cpu": engines.cpu,
    }
    if bench_encode in supported and supported[bench_encode]:
        return bench_encode, f"benchmark winner {bench_encode} still supported"
    for enc in order:
        if supported[enc]:
            why = {
                "dmabuf": "VAAPI + wf-recorder available (DMA-BUF preferred)",
                "vaapi": "VAAPI pipe available (no DMA-BUF)",
                "cpu": "software encode fallback",
            }[enc]
            if bench_encode and bench_encode != enc:
                why += f" (bench wanted {bench_encode}, unsupported here)"
            return enc, why
    return "cpu", "fallback cpu"


def pick_radio() -> tuple[str, str, list[dict[str, Any]], str]:
    """Return (setting, resolved, radios, rationale). Setting stays 'auto' when resolved from auto."""
    radios_mod = _load("list_p2p_radios", SCRIPTS / "list_p2p_radios.py")
    radios = radios_mod.list_radios()
    resolved = radios_mod.resolve_iface("auto", radios) or ""
    if not radios:
        return "auto", "", [], "no Wi-Fi radios found; leave auto"
    go_idle = [r for r in radios if r.get("p2pGo") and not r.get("inUse")]
    go = [r for r in radios if r.get("p2pGo")]
    if go_idle:
        r = go_idle[0]
        return (
            "auto",
            r["iface"],
            radios,
            f"auto → {r['iface']} (P2P-GO, idle"
            + (f", {r.get('adapterName')}" if r.get("adapterName") else "")
            + ")",
        )
    if go:
        r = go[0]
        return (
            "auto",
            r["iface"],
            radios,
            f"auto → {r['iface']} (P2P-GO, STA busy — only GO radio)",
        )
    return (
        "auto",
        resolved,
        radios,
        f"auto → {resolved or '?'} (no P2P-GO advertised; best effort)",
    )


def try_benchmark_profile() -> Optional[Any]:
    results = ROOT / "docs" / "benchmarks" / "results.tsv"
    if not results.is_file():
        return None
    try:
        mod = _load("recommend_cast_profile", SCRIPTS / "recommend-cast-profile.py")
        damage = ROOT / "docs" / "benchmarks" / "lpcm_damage_ab.tsv"
        damage_path = damage if damage.is_file() else None
        return mod.recommend(results, damage_path, "")
    except SystemExit:
        return None
    except Exception:
        return None


def _state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "omarchy-miracast"


def cast_phase() -> str:
    try:
        r = _run(["miracast-ctl", "status"], timeout=5.0)
        data = json.loads(r.stdout or "{}")
        return str(data.get("phase") or "")
    except Exception:
        return ""


def _status_fields() -> dict[str, Any]:
    try:
        r = _run(["miracast-ctl", "status"], timeout=5.0)
        data = json.loads(r.stdout or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _p2p_ifaces() -> list[str]:
    net = Path("/sys/class/net")
    if not net.is_dir():
        return []
    return sorted(
        p.name
        for p in net.iterdir()
        if p.name.startswith("p2p-") and (p / "statistics" / "tx_bytes").is_file()
    )


def _jiffies(pid: int) -> Optional[int]:
    try:
        parts = Path(f"/proc/{pid}/stat").read_text().split()
        return int(parts[13]) + int(parts[14])
    except Exception:
        return None


def _find_pids() -> tuple[Optional[int], Optional[int]]:
    wf = ff = None
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except Exception:
            continue
        if "wf-recorder" in cmd and "rawvideo" in cmd:
            wf = int(p.name)
        elif "ffmpeg" in cmd and "h264_vaapi" in cmd:
            ff = int(p.name)
        elif "wf-recorder" in cmd and "h264_vaapi" in cmd:
            # DMA-BUF path: wf-recorder encodes itself
            wf = int(p.name)
    return wf, ff


def _station_stats(iface: str) -> dict[str, Any]:
    try:
        r = _run(["iw", "dev", iface, "station", "dump"], timeout=3.0)
    except Exception:
        return {}
    out = r.stdout or ""

    def g(pat: str, cast=str):
        m = re.search(pat, out)
        if not m:
            return None
        try:
            return cast(m.group(1))
        except Exception:
            return m.group(1)

    br = g(r"tx bitrate:\s*([0-9.]+)")
    return {
        "tx_retries": g(r"tx retries:\s*(\d+)", int),
        "tx_packets": g(r"tx packets:\s*(\d+)", int),
        "signal_avg_dbm": g(r"signal avg:\s*(-?\d+)", int),
        "tx_bitrate_mbps": float(br) if br else None,
    }


def _cast_log_hard_errors(tail_bytes: int = 64_000) -> int:
    log = _state_dir() / "logs" / "cast.log"
    if not log.is_file():
        return 0
    try:
        data = log.read_bytes()[-tail_bytes:]
        text = data.decode("utf-8", errors="replace")
    except OSError:
        return 0
    pat = re.compile(
        r"buffer pool full|RTP TX stagnant|Capture restart failed|RTP sender is not healthy",
        re.I,
    )
    return len(pat.findall(text))


def sample_live(seconds: float = 25.0) -> LiveSample:
    """Sample P2P TX / retries / capture CPU while streaming."""
    status = _status_fields()
    phase = str(status.get("phase") or "")
    if phase != "streaming":
        return LiveSample(
            ok=False,
            skipped=True,
            reason=f"not streaming (phase={phase or 'unknown'})",
            current_capture_path=str(status.get("capturePath") or ""),
            current_capture_encode=str(status.get("captureEncode") or ""),
        )

    ifaces = _p2p_ifaces()
    if not ifaces:
        return LiveSample(
            ok=False,
            skipped=True,
            reason="streaming but no p2p-* iface",
            current_capture_path=str(status.get("capturePath") or ""),
            current_capture_encode=str(status.get("captureEncode") or ""),
        )
    iface = ifaces[0]
    tx_path = Path(f"/sys/class/net/{iface}/statistics/tx_bytes")
    hz = os.sysconf("SC_CLK_TCK")
    wf_pid, ff_pid = _find_pids()

    def read_tx() -> Optional[int]:
        try:
            return int(tx_path.read_text())
        except OSError:
            return None

    samples_tx: list[float] = []
    wf_cpus: list[float] = []
    ff_cpus: list[float] = []
    st0 = _station_stats(iface)
    t0 = time.monotonic()
    tx0 = read_tx()
    w0 = _jiffies(wf_pid) if wf_pid else None
    f0 = _jiffies(ff_pid) if ff_pid else None

    # Progress on stderr so miracast-ctl users see activity
    print(f"Live sample: {seconds:.0f}s on {iface}…", file=sys.stderr, flush=True)

    n = max(1, int(seconds))
    for i in range(n):
        time.sleep(1.0)
        t1 = time.monotonic()
        tx1 = read_tx()
        w1 = _jiffies(wf_pid) if wf_pid else None
        f1 = _jiffies(ff_pid) if ff_pid else None
        dt = max(t1 - t0, 1e-6)
        if tx0 is not None and tx1 is not None:
            samples_tx.append((tx1 - tx0) * 8.0 / dt / 1e6)  # Mbps
        if w0 is not None and w1 is not None:
            wf_cpus.append((w1 - w0) / hz / dt * 100.0)
        if f0 is not None and f1 is not None:
            ff_cpus.append((f1 - f0) / hz / dt * 100.0)
        t0, tx0, w0, f0 = t1, tx1, w1, f1
        if (i + 1) % 5 == 0 or i + 1 == n:
            print(f"  … {i + 1}/{n}s", file=sys.stderr, flush=True)

    st1 = _station_stats(iface)
    retries0 = st0.get("tx_retries")
    retries1 = st1.get("tx_retries")
    pkts0 = st0.get("tx_packets")
    pkts1 = st1.get("tx_packets")
    elapsed = float(n)
    retry_delta = (
        (retries1 - retries0)
        if isinstance(retries0, int) and isinstance(retries1, int)
        else None
    )
    pkt_delta = (
        (pkts1 - pkts0) if isinstance(pkts0, int) and isinstance(pkts1, int) else None
    )
    retry_per_s = (retry_delta / elapsed) if retry_delta is not None else None
    retry_pct = (
        (100.0 * retry_delta / pkt_delta)
        if retry_delta is not None and pkt_delta and pkt_delta > 0
        else None
    )

    tx_mean = statistics.mean(samples_tx) if samples_tx else None
    tx_cv = (
        statistics.pstdev(samples_tx) / tx_mean
        if samples_tx and tx_mean and tx_mean > 0
        else None
    )

    return LiveSample(
        ok=True,
        skipped=False,
        reason="ok",
        seconds=elapsed,
        p2p_iface=iface,
        tx_mbps_mean=round(tx_mean, 3) if tx_mean is not None else None,
        tx_cv=round(tx_cv, 3) if tx_cv is not None else None,
        retry_per_s=round(retry_per_s, 2) if retry_per_s is not None else None,
        retry_pct=round(retry_pct, 2) if retry_pct is not None else None,
        wf_cpu_mean=round(statistics.mean(wf_cpus), 1) if wf_cpus else None,
        ffmpeg_cpu_mean=round(statistics.mean(ff_cpus), 1) if ff_cpus else None,
        signal_avg_dbm=st1.get("signal_avg_dbm"),
        phy_mbps=st1.get("tx_bitrate_mbps"),
        cast_log_hard_errors=_cast_log_hard_errors(),
        current_capture_path=str(status.get("capturePath") or ""),
        current_capture_encode=str(status.get("captureEncode") or ""),
    )


def refine_from_live(
    encode: str,
    engines: EngineProbe,
    live: LiveSample,
    quiet_csa: bool,
) -> tuple[str, bool, list[str]]:
    """Adjust encode / CSA from live sample. Returns (encode, quiet_csa, notes)."""
    notes: list[str] = []
    if live.skipped or not live.ok:
        notes.append(f"live sample skipped: {live.reason}")
        return encode, quiet_csa, notes

    notes.append(
        f"live {live.seconds:.0f}s on {live.p2p_iface}: "
        f"tx≈{live.tx_mbps_mean} Mbps cv={live.tx_cv} "
        f"retries/s={live.retry_per_s} wf_cpu≈{live.wf_cpu_mean}% "
        f"ff_cpu≈{live.ffmpeg_cpu_mean}% path={live.current_capture_path or '?'}"
    )

    # High pipe capture CPU + dmabuf available → prefer dmabuf
    if (
        engines.dmabuf
        and encode != "dmabuf"
        and (live.current_capture_path == "pipe" or live.current_capture_encode == "vaapi")
        and live.wf_cpu_mean is not None
        and live.wf_cpu_mean >= 20.0
    ):
        notes.append(
            f"live: pipe wf-recorder CPU {live.wf_cpu_mean}% ≥20 — prefer dmabuf"
        )
        encode = "dmabuf"

    # Unstable TX or high retries → keep SCC (quiet CSA off)
    if live.retry_per_s is not None and live.retry_per_s >= 2.0:
        notes.append(
            f"live: retries/s {live.retry_per_s} ≥2 — keep SCC (p2pQuietCsa=false)"
        )
        quiet_csa = False
    if live.tx_cv is not None and live.tx_cv >= 0.15:
        notes.append(f"live: TX CV {live.tx_cv} ≥0.15 — delivery jitter; prefer SCC")
        quiet_csa = False

    if live.cast_log_hard_errors:
        notes.append(
            f"live: {live.cast_log_hard_errors} recent hard error(s) in cast.log"
        )

    if live.tx_mbps_mean is not None and live.tx_mbps_mean < 2.0:
        notes.append(
            f"live: TX {live.tx_mbps_mean} Mbps looks low for 1080p CBR — check radio/path"
        )

    return encode, quiet_csa, notes


def tune(*, live_seconds: float = 25.0, offline_only: bool = False) -> TuneResult:
    engines = probe_engines()
    rationale: list[str] = []
    rationale.extend(engines.notes)

    bench = try_benchmark_profile()
    source = "capability"
    wf_bin = engines.wf_recorder
    wf_proto = "auto"
    video_encoder = "auto"
    damage = "1"
    bench_encode = None

    if bench is not None:
        source = "benchmarks+capability"
        bench_encode = bench.encode
        wf_bin = bench.wf_recorder_bin or wf_bin
        wf_proto = bench.wf_recorder_proto or wf_proto
        video_encoder = bench.video_encoder or video_encoder
        damage = bench.damage or damage
        rationale.append(
            f"benchmark profile: case={bench.case} encode={bench.encode} "
            f"score={bench.score} ({bench.notes})"
        )
        if bench.damage_rationale:
            rationale.append(f"damage: {bench.damage_rationale}")

    # Prefer ICC binary when capable even without TSV
    if not bench:
        for hint in (
            str(Path.home() / "src" / "wf-recorder" / "build" / "wf-recorder"),
            wf_bin,
        ):
            if _icc_capable(hint):
                wf_bin = hint
                wf_proto = "icc"
                rationale.append(f"ICC-capable wf-recorder: {hint}")
                break

    encode, enc_why = pick_encode(engines, bench_encode)
    rationale.append(f"render engine: {encode} — {enc_why}")

    radio_setting, radio_resolved, radios, radio_why = pick_radio()
    rationale.append(f"radio: {radio_why}")

    # Validated pipe/session knobs (not from live A/B matrix TSV)
    quality = "5"
    vbv = "0.5"
    quiet_csa = False
    rationale.append("encode knobs: vaapiQuality=5, vbvMultiplier=0.5 (pipe-stable)")
    rationale.append("p2pQuietCsa=false (SCC default; MCC retry storms on 20 MHz CSA)")

    if offline_only:
        live = LiveSample(ok=False, skipped=True, reason="--offline-only")
        rationale.append("live sample skipped (--offline-only)")
    else:
        live = sample_live(live_seconds)
        encode, quiet_csa, live_notes = refine_from_live(
            encode, engines, live, quiet_csa
        )
        rationale.extend(live_notes)
        if live.ok and not live.skipped:
            source = source + "+live"
    live_dict = asdict(live)

    if encode == "dmabuf":
        video_encoder = video_encoder if video_encoder != "software" else "auto"
    elif encode == "vaapi":
        video_encoder = "vaapi"
    else:
        video_encoder = "software"

    return TuneResult(
        capture_encode=encode,
        p2p_wifi_interface=radio_setting,
        p2p_wifi_resolved=radio_resolved,
        p2p_quiet_csa=quiet_csa,
        vaapi_quality=quality,
        vbv_multiplier=vbv,
        wf_recorder_bin=wf_bin,
        wf_recorder_proto=wf_proto,
        video_encoder=video_encoder,
        wf_recorder_damage=damage,
        engines={
            "dmabuf": engines.dmabuf,
            "vaapi": engines.vaapi_pipe,
            "cpu": engines.cpu,
            "ffmpeg": engines.ffmpeg,
            "wf_recorder": engines.wf_recorder,
            "vaapi_device": engines.vaapi_device,
        },
        radios=radios,
        rationale=rationale,
        source=source,
        live=live_dict,
    )


def _home_tilde(path: str) -> str:
    """Show $HOME as ~ in human output (no absolute home path dump)."""
    if not path:
        return path
    home = str(Path.home())
    if path == home or path.startswith(home + "/"):
        return "~" + path[len(home) :]
    return path


def _encode_plain(encode: str) -> str:
    return {
        "dmabuf": "DMA-BUF → VAAPI (lowest compositor CPU when it works)",
        "vaapi": "pipe → VAAPI (screencopy frames, then GPU encode)",
        "cpu": "pipe → software encode (fallback)",
    }.get(encode, encode)


def _source_plain(source: str) -> str:
    parts = []
    if "benchmarks" in source:
        parts.append("checked-in benchmark scores")
    if "capability" in source:
        parts.append("what this machine supports")
    if "live" in source:
        parts.append("live cast sample")
    return " + ".join(parts) if parts else source


def _fmt_pct(v: Optional[float]) -> str:
    return f"{v:.0f}%" if v is not None else "n/a"


def _fmt_num(v: Optional[float], digits: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"


def format_human(t: TuneResult, applied: Optional[dict[str, str]] = None) -> str:
    """Human-readable auto-tune report (stdout for miracast-ctl benchmark)."""
    eng = t.engines
    yes = lambda b: "yes" if b else "no"
    lines: list[str] = [
        "Miracast auto-tune",
        "==================",
        f"Based on: {_source_plain(t.source)}",
        "",
        "Recommendation",
        "--------------",
        f"  Capture path   {t.capture_encode}",
        f"                 {_encode_plain(t.capture_encode)}",
        f"  Supported      dmabuf={yes(eng.get('dmabuf'))}  "
        f"vaapi-pipe={yes(eng.get('vaapi'))}  cpu={yes(eng.get('cpu'))}",
    ]

    radio = t.p2p_wifi_interface
    if t.p2p_wifi_resolved and t.p2p_wifi_interface == "auto":
        radio = f"auto → {t.p2p_wifi_resolved}"
    elif t.p2p_wifi_resolved and t.p2p_wifi_resolved != t.p2p_wifi_interface:
        radio = f"{t.p2p_wifi_interface} → {t.p2p_wifi_resolved}"
    lines.append(f"  Wi-Fi radio    {radio}")
    lines.append(
        "                 Auto prefers an idle P2P-GO adapter when more than one exists."
    )
    lines.append(
        f"  Encode knobs   quality={t.vaapi_quality}  VBV={t.vbv_multiplier}s"
    )
    lines.append(
        "                 Higher quality = faster/worse VAAPI; VBV is CBR buffer depth."
    )

    if t.p2p_quiet_csa:
        lines.append("  Channel mode   quiet CSA (MCC) — P2P may hop off the STA channel")
    else:
        lines.append(
            "  Channel mode   SCC — stay on the same channel as normal Wi‑Fi (default)"
        )
        lines.append(
            "                 Quiet-channel CSA is opt-in; MCC can cause retry storms."
        )

    wf = _home_tilde(t.wf_recorder_bin) if t.wf_recorder_bin else "(PATH wf-recorder)"
    lines.append(f"  wf-recorder    {wf}")
    lines.append(f"                 protocol={t.wf_recorder_proto}  damage={t.wf_recorder_damage}")

    # Live block
    lines.append("")
    lines.append("Live sample")
    lines.append("-----------")
    live = t.live
    if not live:
        lines.append("  (not run)")
    elif live.get("skipped"):
        reason = str(live.get("reason") or "skipped")
        if reason.startswith("not streaming"):
            lines.append("  Skipped — no active cast (phase is not streaming).")
            lines.append(
                "  Tip: start casting, then re-run without --offline-only to measure"
            )
            lines.append("       TX stability, retries, and capture CPU (~25s).")
        elif "--offline-only" in reason:
            lines.append("  Skipped — you passed --offline-only.")
        else:
            lines.append(f"  Skipped — {reason}")
    else:
        cur = live.get("current_capture_path") or "?"
        cur_enc = live.get("current_capture_encode") or "?"
        lines.append(
            f"  Window         {live.get('seconds')}s on {live.get('p2p_iface') or '?'}"
        )
        lines.append(f"  Current path   {cur} / encode={cur_enc}")
        tx = live.get("tx_mbps_mean")
        cv = live.get("tx_cv")
        tx_note = ""
        if cv is not None:
            if cv < 0.08:
                tx_note = " — stable"
            elif cv < 0.15:
                tx_note = " — mild jitter"
            else:
                tx_note = " — unstable (delivery jitter)"
        lines.append(
            f"  TX rate        ≈{_fmt_num(tx)} Mbps  (variation CV={_fmt_num(cv, 3)}){tx_note}"
        )
        retries = live.get("retry_per_s")
        retry_note = ""
        if retries is not None:
            if retries < 1.0:
                retry_note = " — healthy"
            elif retries < 2.0:
                retry_note = " — elevated"
            else:
                retry_note = " — high (prefer SCC / check radio)"
        lines.append(f"  Wi‑Fi retries  {_fmt_num(retries, 2)} /s{retry_note}")
        lines.append(
            f"  Capture CPU    wf-recorder {_fmt_pct(live.get('wf_cpu_mean'))}   "
            f"ffmpeg {_fmt_pct(live.get('ffmpeg_cpu_mean'))}"
        )
        sig = live.get("signal_avg_dbm")
        phy = live.get("phy_mbps")
        if sig is not None or phy is not None:
            sig_s = f"{sig} dBm" if sig is not None else "n/a"
            phy_s = f"{phy} Mb/s PHY" if phy is not None else "n/a"
            lines.append(f"  Link          {sig_s}, {phy_s}")
        hard = int(live.get("cast_log_hard_errors") or 0)
        if hard:
            lines.append(f"  Cast log      {hard} recent hard error(s) — see cast.log")

    lines.append("")
    lines.append("Why")
    lines.append("---")
    for item in t.rationale:
        lines.append(f"  • {item}")

    lines.append("")
    lines.append("Next step")
    lines.append("---------")
    if applied:
        lines.append("  Settings written:")
        for key, path in applied.items():
            lines.append(f"    {key}: {_home_tilde(path)}")
        lines.append("  Reconnect (or restart capture) for encode/radio changes to apply.")
    else:
        lines.append("  Dry-run only — nothing was written.")
        lines.append("  To save these settings:")
        lines.append("    miracast-ctl benchmark --apply")
        if live and live.get("skipped") and "not streaming" in str(live.get("reason") or ""):
            lines.append("  For a live TX/CPU sample first:")
            lines.append("    start a cast, then: miracast-ctl benchmark")

    lines.append("")
    lines.append("Env preview (exported on connect / written by --apply)")
    lines.append("-------------------------------------------------------")
    # format_env already ends with newline
    lines.append(format_env(t).rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


def format_env(t: TuneResult) -> str:
    legacy = {"dmabuf": "vaapi", "vaapi": "pipe", "cpu": "pipe"}.get(
        t.capture_encode, "pipe"
    )
    lines = [
        "# Generated by scripts/auto_tune_miracast.py",
        f"# source={t.source} encode={t.capture_encode} radio={t.p2p_wifi_resolved or t.p2p_wifi_interface}",
        f"export FLUXCAST_WFD_CAPTURE_ENCODE_PREF={t.capture_encode}",
        f"export FLUXCAST_WFD_CAPTURE_ENCODE={legacy}",
        f"export FLUXCAST_WFD_ENCODER={t.video_encoder}",
        f"export FLUXCAST_WFD_VAAPI_QUALITY={t.vaapi_quality}",
        f"export FLUXCAST_WFD_VBV_MULTIPLIER={t.vbv_multiplier}",
        f"export FLUXCAST_WFD_WF_RECORDER_DAMAGE={t.wf_recorder_damage}",
    ]
    if t.p2p_wifi_resolved:
        lines.append(f"export FLUXCAST_WFD_INTERFACE={t.p2p_wifi_resolved}")
    if t.wf_recorder_bin:
        lines.append(f"export FLUXCAST_WFD_WF_RECORDER_BIN={t.wf_recorder_bin}")
    lines.append(f"export FLUXCAST_WFD_WF_RECORDER_PROTO={t.wf_recorder_proto}")
    lines.append("# PIPE_LOW_LATENCY intentionally unset")
    lines.append("")
    return "\n".join(lines)


def apply_tune(t: TuneResult) -> dict[str, str]:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    settings_path = cfg / "omarchy-miracast" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text())
        except Exception:
            data = {}
    data["captureEncode"] = t.capture_encode
    data["videoEncoder"] = t.video_encoder
    data["vaapiQuality"] = t.vaapi_quality
    data["vbvMultiplier"] = t.vbv_multiplier
    data["p2pWifiInterface"] = t.p2p_wifi_interface
    data["p2pQuietCsa"] = bool(t.p2p_quiet_csa)
    data["wfRecorderDamage"] = t.wf_recorder_damage
    if t.wf_recorder_bin:
        data["wfRecorderBin"] = t.wf_recorder_bin
    data["wfRecorderProto"] = t.wf_recorder_proto
    settings_path.write_text(json.dumps(data, indent=2) + "\n")

    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    state_dir = state / "omarchy-miracast"
    state_dir.mkdir(parents=True, exist_ok=True)
    env_path = state_dir / "recommended-cast.env"
    env_path.write_text(format_env(t))
    # Prefer file read by FluxCast / miracast-ctl start
    (state_dir / "capture-encode").write_text(t.capture_encode + "\n")

    report = state_dir / "auto-tune.json"
    report.write_text(json.dumps(asdict(t), indent=2) + "\n")

    return {
        "settings": str(settings_path),
        "state_env": str(env_path),
        "capture_encode_file": str(state_dir / "capture-encode"),
        "report": str(report),
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write settings + state env")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--offline-only",
        action="store_true",
        help="skip live TX/CPU sample even if streaming",
    )
    ap.add_argument(
        "--live-seconds",
        type=float,
        default=25.0,
        help="live sample duration when streaming (default 25)",
    )
    args = ap.parse_args(argv)

    result = tune(live_seconds=args.live_seconds, offline_only=args.offline_only)
    applied = apply_tune(result) if args.apply else None
    payload = asdict(result)
    payload["applied"] = applied
    payload["env"] = format_env(result)

    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    sys.stdout.write(format_human(result, applied))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
