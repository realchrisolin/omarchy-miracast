#!/usr/bin/env python3
"""Miracast auto-tune: offline probes + optional short live sample.

Builds one report dict, then formats it for humans / JSON / settings apply.

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

# Live-sample assessment thresholds (also used by refine heuristics).
TX_CV_STABLE = 0.08
TX_CV_MILD = 0.15
RETRY_HEALTHY = 1.0
RETRY_ELEVATED = 2.0
PIPE_CPU_PREFER_DMABUF = 20.0
TX_MBPS_LOW = 2.0

ENCODE_SUMMARY = {
    "dmabuf": "DMA-BUF → VAAPI (lowest compositor CPU when it works)",
    "vaapi": "pipe → VAAPI (screencopy frames, then GPU encode)",
    "cpu": "pipe → software encode (fallback)",
}

# settings.captureEncode → FluxCast FLUXCAST_WFD_CAPTURE_ENCODE legacy value
ENCODE_LEGACY = {"dmabuf": "vaapi", "vaapi": "pipe", "cpu": "pipe"}


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

    def as_engines_dict(self) -> dict[str, Any]:
        return {
            "dmabuf": self.dmabuf,
            "vaapi": self.vaapi_pipe,
            "cpu": self.cpu,
            "ffmpeg": self.ffmpeg,
            "wf_recorder": self.wf_recorder,
            "vaapi_device": self.vaapi_device,
        }


@dataclass
class LiveSample:
    """Raw live metrics collected while streaming (pre-assessment)."""

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


def empty_report() -> dict[str, Any]:
    """Canonical auto-tune result. Settings are what --apply writes."""
    return {
        "source": "capability",
        "settings": {
            "captureEncode": "cpu",  # dmabuf | vaapi | cpu
            "videoEncoder": "auto",
            "vaapiQuality": "5",
            "vbvMultiplier": "0.5",
            "p2pWifiInterface": "auto",  # auto | iface
            "p2pWifiResolved": "",
            "p2pQuietCsa": False,
            "wfRecorderBin": "",
            "wfRecorderProto": "auto",
            "wfRecorderDamage": "1",
        },
        "engines": {},
        "radios": [],
        "live": None,
        "rationale": [],
    }


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
        detail = f"auto → {r['iface']} (P2P-GO, idle"
        if r.get("adapterName"):
            detail += f", {r['adapterName']}"
        detail += ")"
        return "auto", r["iface"], radios, detail
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


def classify_live_skip(reason: str) -> str:
    if reason.startswith("not streaming"):
        return "not_streaming"
    if "--offline-only" in reason:
        return "offline_only"
    if "no p2p" in reason:
        return "no_p2p_iface"
    return "other"


def assess_tx_stability(tx_cv: Optional[float]) -> Optional[str]:
    if tx_cv is None:
        return None
    if tx_cv < TX_CV_STABLE:
        return "stable"
    if tx_cv < TX_CV_MILD:
        return "mild_jitter"
    return "unstable"


def assess_retry_health(retry_per_s: Optional[float]) -> Optional[str]:
    if retry_per_s is None:
        return None
    if retry_per_s < RETRY_HEALTHY:
        return "healthy"
    if retry_per_s < RETRY_ELEVATED:
        return "elevated"
    return "high"


def live_report(sample: LiveSample) -> dict[str, Any]:
    """Store raw metrics + assessments in one dict for formatters / JSON."""
    out: dict[str, Any] = asdict(sample)
    if sample.skipped or not sample.ok:
        out["skip_kind"] = classify_live_skip(sample.reason)
        out["assessments"] = {}
        return out
    out["skip_kind"] = None
    out["assessments"] = {
        "tx_stability": assess_tx_stability(sample.tx_cv),
        "retry_health": assess_retry_health(sample.retry_per_s),
        "tx_low": bool(
            sample.tx_mbps_mean is not None and sample.tx_mbps_mean < TX_MBPS_LOW
        ),
        "pipe_cpu_high": bool(
            sample.wf_cpu_mean is not None
            and sample.wf_cpu_mean >= PIPE_CPU_PREFER_DMABUF
            and (
                sample.current_capture_path == "pipe"
                or sample.current_capture_encode == "vaapi"
            )
        ),
    }
    return out


def refine_from_live(
    encode: str,
    engines: EngineProbe,
    live: LiveSample,
    quiet_csa: bool,
    live_block: Optional[dict[str, Any]] = None,
) -> tuple[str, bool, list[str]]:
    """Adjust encode / CSA from live sample. Returns (encode, quiet_csa, notes).

    Pass live_block from live_report(live) to avoid assessing twice.
    """
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

    block = live_block if live_block is not None else live_report(live)
    assessed = block.get("assessments") or {}

    # High pipe capture CPU + dmabuf available → prefer dmabuf
    if engines.dmabuf and encode != "dmabuf" and assessed.get("pipe_cpu_high"):
        notes.append(
            f"live: pipe wf-recorder CPU {live.wf_cpu_mean}% "
            f"≥{PIPE_CPU_PREFER_DMABUF:g} — prefer dmabuf"
        )
        encode = "dmabuf"

    # Unstable TX or high retries → keep SCC (quiet CSA off)
    if assessed.get("retry_health") == "high":
        notes.append(
            f"live: retries/s {live.retry_per_s} ≥{RETRY_ELEVATED:g} — "
            "keep SCC (p2pQuietCsa=false)"
        )
        quiet_csa = False
    if assessed.get("tx_stability") == "unstable":
        notes.append(
            f"live: TX CV {live.tx_cv} ≥{TX_CV_MILD:g} — delivery jitter; prefer SCC"
        )
        quiet_csa = False

    if live.cast_log_hard_errors:
        notes.append(
            f"live: {live.cast_log_hard_errors} recent hard error(s) in cast.log"
        )

    if assessed.get("tx_low"):
        notes.append(
            f"live: TX {live.tx_mbps_mean} Mbps looks low for 1080p CBR — check radio/path"
        )

    return encode, quiet_csa, notes


def _video_encoder_for(encode: str, preferred: str) -> str:
    if encode == "dmabuf":
        return preferred if preferred != "software" else "auto"
    if encode == "vaapi":
        return "vaapi"
    return "software"


def tune(*, live_seconds: float = 25.0, offline_only: bool = False) -> dict[str, Any]:
    """Probe + decide. Returns one report dict (settings / live / rationale)."""
    report = empty_report()
    engines = probe_engines()
    report["engines"] = engines.as_engines_dict()
    report["rationale"].extend(engines.notes)

    bench = try_benchmark_profile()
    settings = report["settings"]
    settings["wfRecorderBin"] = engines.wf_recorder
    bench_encode = None

    if bench is not None:
        report["source"] = "benchmarks+capability"
        bench_encode = bench.encode
        settings["wfRecorderBin"] = bench.wf_recorder_bin or settings["wfRecorderBin"]
        settings["wfRecorderProto"] = bench.wf_recorder_proto or settings["wfRecorderProto"]
        settings["videoEncoder"] = bench.video_encoder or settings["videoEncoder"]
        settings["wfRecorderDamage"] = bench.damage or settings["wfRecorderDamage"]
        report["rationale"].append(
            f"benchmark profile: case={bench.case} encode={bench.encode} "
            f"score={bench.score} ({bench.notes})"
        )
        if bench.damage_rationale:
            report["rationale"].append(f"damage: {bench.damage_rationale}")
    else:
        # Prefer ICC binary when capable even without TSV
        for hint in (
            str(Path.home() / "src" / "wf-recorder" / "build" / "wf-recorder"),
            settings["wfRecorderBin"],
        ):
            if _icc_capable(hint):
                settings["wfRecorderBin"] = hint
                settings["wfRecorderProto"] = "icc"
                report["rationale"].append(
                    f"ICC-capable wf-recorder: {_home_tilde(hint)}"
                )
                break

    encode, enc_why = pick_encode(engines, bench_encode)
    report["rationale"].append(f"render engine: {encode} — {enc_why}")

    radio_setting, radio_resolved, radios, radio_why = pick_radio()
    report["radios"] = radios
    settings["p2pWifiInterface"] = radio_setting
    settings["p2pWifiResolved"] = radio_resolved
    report["rationale"].append(f"radio: {radio_why}")

    # Validated pipe/session knobs (not from live A/B matrix TSV)
    settings["vaapiQuality"] = "5"
    settings["vbvMultiplier"] = "0.5"
    settings["p2pQuietCsa"] = False
    report["rationale"].append(
        "encode knobs: vaapiQuality=5, vbvMultiplier=0.5 (pipe-stable)"
    )
    report["rationale"].append(
        "p2pQuietCsa=false (SCC default; MCC retry storms on 20 MHz CSA)"
    )

    if offline_only:
        live = LiveSample(ok=False, skipped=True, reason="--offline-only")
        report["rationale"].append("live sample skipped (--offline-only)")
    else:
        live = sample_live(live_seconds)

    # Assess once; refine reuses the same live_block.
    live_block = live_report(live)
    if not offline_only:
        encode, quiet_csa, live_notes = refine_from_live(
            encode,
            engines,
            live,
            bool(settings["p2pQuietCsa"]),
            live_block=live_block,
        )
        settings["p2pQuietCsa"] = quiet_csa
        report["rationale"].extend(live_notes)
        if live.ok and not live.skipped:
            report["source"] = report["source"] + "+live"

    report["live"] = live_block
    settings["captureEncode"] = encode
    settings["videoEncoder"] = _video_encoder_for(encode, settings["videoEncoder"])
    return report


# --- formatting (read the report dict; do not re-decide) -------------------


def _home_tilde(path: str) -> str:
    """Show $HOME as ~ in human output (no absolute home path dump)."""
    if not path:
        return path
    home = str(Path.home())
    if path == home or path.startswith(home + "/"):
        return "~" + path[len(home) :]
    return path


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


def _yes(b: Any) -> str:
    return "yes" if b else "no"


def _radio_display(settings: dict[str, Any]) -> str:
    setting = settings.get("p2pWifiInterface") or "auto"
    resolved = settings.get("p2pWifiResolved") or ""
    if resolved and setting == "auto":
        return f"auto → {resolved}"
    if resolved and resolved != setting:
        return f"{setting} → {resolved}"
    return str(setting)


def _live_lines(live: Optional[dict[str, Any]]) -> list[str]:
    if not live:
        return ["  (not run)"]
    if live.get("skipped"):
        kind = live.get("skip_kind") or classify_live_skip(str(live.get("reason") or ""))
        if kind == "not_streaming":
            return [
                "  Skipped — no active cast (phase is not streaming).",
                "  Tip: start casting, then re-run without --offline-only to measure",
                "       TX stability, retries, and capture CPU (~25s).",
            ]
        if kind == "offline_only":
            return ["  Skipped — you passed --offline-only."]
        return [f"  Skipped — {live.get('reason') or kind}"]

    assessments = live.get("assessments") or {}
    stability = assessments.get("tx_stability")
    retry_h = assessments.get("retry_health")
    stability_note = {
        "stable": " — stable",
        "mild_jitter": " — mild jitter",
        "unstable": " — unstable (delivery jitter)",
    }.get(stability or "", "")
    retry_note = {
        "healthy": " — healthy",
        "elevated": " — elevated",
        "high": " — high (prefer SCC / check radio)",
    }.get(retry_h or "", "")

    lines = [
        f"  Window         {live.get('seconds')}s on {live.get('p2p_iface') or '?'}",
        f"  Current path   {live.get('current_capture_path') or '?'} / "
        f"encode={live.get('current_capture_encode') or '?'}",
        f"  TX rate        ≈{_fmt_num(live.get('tx_mbps_mean'))} Mbps  "
        f"(variation CV={_fmt_num(live.get('tx_cv'), 3)}){stability_note}",
        f"  Wi‑Fi retries  {_fmt_num(live.get('retry_per_s'), 2)} /s{retry_note}",
        f"  Capture CPU    wf-recorder {_fmt_pct(live.get('wf_cpu_mean'))}   "
        f"ffmpeg {_fmt_pct(live.get('ffmpeg_cpu_mean'))}",
    ]
    sig = live.get("signal_avg_dbm")
    phy = live.get("phy_mbps")
    if sig is not None or phy is not None:
        sig_s = f"{sig} dBm" if sig is not None else "n/a"
        phy_s = f"{phy} Mb/s PHY" if phy is not None else "n/a"
        lines.append(f"  Link           {sig_s}, {phy_s}")
    hard = int(live.get("cast_log_hard_errors") or 0)
    if hard:
        lines.append(f"  Cast log       {hard} recent hard error(s) — see cast.log")
    return lines


def _next_step_lines(
    report: dict[str, Any], applied: Optional[dict[str, str]]
) -> list[str]:
    if applied:
        lines = ["  Settings written:"]
        for key, path in applied.items():
            lines.append(f"    {key}: {_home_tilde(path)}")
        lines.append(
            "  Reconnect (or restart capture) for encode/radio changes to apply."
        )
        return lines

    lines = [
        "  Dry-run only — nothing was written.",
        "  To save these settings:",
        "    miracast-ctl benchmark --apply",
    ]
    live = report.get("live") or {}
    if live.get("skip_kind") == "not_streaming":
        lines.append("  For a live TX/CPU sample first:")
        lines.append("    start a cast, then: miracast-ctl benchmark")
    return lines


def format_human(
    report: dict[str, Any], applied: Optional[dict[str, str]] = None
) -> str:
    """Render a tune() report dict for the terminal. No decision logic here."""
    settings = report.get("settings") or {}
    engines = report.get("engines") or {}
    encode = settings.get("captureEncode") or "cpu"
    quiet = bool(settings.get("p2pQuietCsa"))

    lines: list[str] = [
        "Miracast auto-tune",
        "==================",
        f"Based on: {_source_plain(str(report.get('source') or ''))}",
        "",
        "Recommendation",
        "--------------",
        f"  Capture path   {encode}",
        f"                 {ENCODE_SUMMARY.get(encode, encode)}",
        f"  Supported      dmabuf={_yes(engines.get('dmabuf'))}  "
        f"vaapi-pipe={_yes(engines.get('vaapi'))}  cpu={_yes(engines.get('cpu'))}",
        f"  Wi-Fi radio    {_radio_display(settings)}",
        "                 Auto prefers an idle P2P-GO adapter when more than one exists.",
        f"  Encode knobs   quality={settings.get('vaapiQuality')}  "
        f"VBV={settings.get('vbvMultiplier')}s",
        "                 Higher quality = faster/worse VAAPI; VBV is CBR buffer depth.",
    ]
    if quiet:
        lines.append(
            "  Channel mode   quiet CSA (MCC) — P2P may hop off the STA channel"
        )
    else:
        lines.append(
            "  Channel mode   SCC — stay on the same channel as normal Wi‑Fi (default)"
        )
        lines.append(
            "                 Quiet-channel CSA is opt-in; MCC can cause retry storms."
        )

    wf_bin = settings.get("wfRecorderBin") or ""
    wf = _home_tilde(wf_bin) if wf_bin else "(PATH wf-recorder)"
    lines.append(f"  wf-recorder    {wf}")
    lines.append(
        f"                 protocol={settings.get('wfRecorderProto')}  "
        f"damage={settings.get('wfRecorderDamage')}"
    )

    lines.append("")
    lines.append("Live sample")
    lines.append("-----------")
    lines.extend(_live_lines(report.get("live")))

    lines.append("")
    lines.append("Why")
    lines.append("---")
    for item in report.get("rationale") or []:
        lines.append(f"  • {item}")

    lines.append("")
    lines.append("Next step")
    lines.append("---------")
    lines.extend(_next_step_lines(report, applied))

    lines.append("")
    lines.append("Env preview (exported on connect / written by --apply)")
    lines.append("-------------------------------------------------------")
    lines.append(format_env(report).rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


def format_env(report: dict[str, Any]) -> str:
    settings = report.get("settings") or {}
    encode = settings.get("captureEncode") or "cpu"
    legacy = ENCODE_LEGACY.get(encode, "pipe")
    resolved = settings.get("p2pWifiResolved") or settings.get("p2pWifiInterface") or ""
    lines = [
        "# Generated by scripts/auto_tune_miracast.py",
        f"# source={report.get('source')} encode={encode} radio={resolved}",
        f"export FLUXCAST_WFD_CAPTURE_ENCODE_PREF={encode}",
        f"export FLUXCAST_WFD_CAPTURE_ENCODE={legacy}",
        f"export FLUXCAST_WFD_ENCODER={settings.get('videoEncoder') or 'auto'}",
        f"export FLUXCAST_WFD_VAAPI_QUALITY={settings.get('vaapiQuality')}",
        f"export FLUXCAST_WFD_VBV_MULTIPLIER={settings.get('vbvMultiplier')}",
        f"export FLUXCAST_WFD_WF_RECORDER_DAMAGE={settings.get('wfRecorderDamage')}",
    ]
    if settings.get("p2pWifiResolved"):
        lines.append(
            f"export FLUXCAST_WFD_INTERFACE={settings['p2pWifiResolved']}"
        )
    if settings.get("wfRecorderBin"):
        lines.append(
            f"export FLUXCAST_WFD_WF_RECORDER_BIN={settings['wfRecorderBin']}"
        )
    lines.append(
        f"export FLUXCAST_WFD_WF_RECORDER_PROTO={settings.get('wfRecorderProto')}"
    )
    lines.append("# PIPE_LOW_LATENCY intentionally unset")
    lines.append("")
    return "\n".join(lines)


def apply_tune(report: dict[str, Any]) -> dict[str, str]:
    settings = report.get("settings") or {}
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    settings_path = cfg / "omarchy-miracast" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text())
        except Exception:
            data = {}
    data["captureEncode"] = settings.get("captureEncode")
    data["videoEncoder"] = settings.get("videoEncoder")
    data["vaapiQuality"] = settings.get("vaapiQuality")
    data["vbvMultiplier"] = settings.get("vbvMultiplier")
    data["p2pWifiInterface"] = settings.get("p2pWifiInterface")
    data["p2pQuietCsa"] = bool(settings.get("p2pQuietCsa"))
    data["wfRecorderDamage"] = settings.get("wfRecorderDamage")
    if settings.get("wfRecorderBin"):
        data["wfRecorderBin"] = settings["wfRecorderBin"]
    data["wfRecorderProto"] = settings.get("wfRecorderProto")
    settings_path.write_text(json.dumps(data, indent=2) + "\n")

    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    state_dir = state / "omarchy-miracast"
    state_dir.mkdir(parents=True, exist_ok=True)
    env_path = state_dir / "recommended-cast.env"
    env_path.write_text(format_env(report))
    # Prefer file read by FluxCast / miracast-ctl start
    encode = str(settings.get("captureEncode") or "cpu")
    (state_dir / "capture-encode").write_text(encode + "\n")

    report_path = state_dir / "auto-tune.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")

    return {
        "settings": str(settings_path),
        "state_env": str(env_path),
        "capture_encode_file": str(state_dir / "capture-encode"),
        "report": str(report_path),
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

    report = tune(live_seconds=args.live_seconds, offline_only=args.offline_only)
    applied = apply_tune(report) if args.apply else None
    payload = dict(report)
    payload["applied"] = applied
    payload["env"] = format_env(report)

    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    sys.stdout.write(format_human(report, applied))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
