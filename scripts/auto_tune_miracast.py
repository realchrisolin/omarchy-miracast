#!/usr/bin/env python3
"""Offline Miracast auto-tune: probe render engines + radios, apply settings.

No live cast required. Combines:
  - capability probes (VAAPI / wf-recorder / ffmpeg)
  - optional docs/benchmarks/results.tsv via recommend-cast-profile
  - list_p2p_radios idle P2P-GO preference
  - validated encode knobs (quality 5, VBV 0.5, SCC)

Usage:
  scripts/auto_tune_miracast.py              # dry-run JSON + env
  scripts/auto_tune_miracast.py --apply      # write settings + state env
  scripts/auto_tune_miracast.py --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
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
    source: str  # capability | benchmarks+capability


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


def tune() -> TuneResult:
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

    if encode == "dmabuf":
        video_encoder = video_encoder if video_encoder != "software" else "auto"
    elif encode == "vaapi":
        video_encoder = "vaapi"
    else:
        video_encoder = "software"

    radio_setting, radio_resolved, radios, radio_why = pick_radio()
    rationale.append(f"radio: {radio_why}")

    # Validated pipe/session knobs (not from live A/B matrix TSV)
    quality = "5"
    vbv = "0.5"
    quiet_csa = False
    rationale.append("encode knobs: vaapiQuality=5, vbvMultiplier=0.5 (pipe-stable)")
    rationale.append("p2pQuietCsa=false (SCC default; MCC retry storms on 20 MHz CSA)")

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
    )


def format_env(t: TuneResult) -> str:
    legacy = {"dmabuf": "vaapi", "vaapi": "pipe", "cpu": "pipe"}.get(
        t.capture_encode, "pipe"
    )
    lines = [
        "# Generated by scripts/auto_tune_miracast.py (offline probes)",
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
    args = ap.parse_args(argv)

    result = tune()
    applied = apply_tune(result) if args.apply else None
    payload = asdict(result)
    payload["applied"] = applied
    payload["env"] = format_env(result)

    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"Auto-tune ({result.source})")
    print(f"  RENDER ENGINE : {result.capture_encode}  (supported: "
          f"dmabuf={result.engines['dmabuf']} vaapi={result.engines['vaapi']} "
          f"cpu={result.engines['cpu']})")
    print(
        f"  RADIO         : {result.p2p_wifi_interface}"
        + (f" → {result.p2p_wifi_resolved}" if result.p2p_wifi_resolved else "")
    )
    print(f"  quality/VBV   : {result.vaapi_quality} / {result.vbv_multiplier}")
    print(f"  quiet CSA     : {result.p2p_quiet_csa} (SCC default)")
    print(f"  wf-recorder   : {result.wf_recorder_bin or '(PATH)'} proto={result.wf_recorder_proto}")
    print("  rationale:")
    for line in result.rationale:
        print(f"    - {line}")
    print()
    if applied:
        print("Applied:")
        for k, v in applied.items():
            print(f"  {k}: {v}")
        print()
    else:
        print("Dry-run only. Re-run with --apply to update settings.")
        print()
    print(format_env(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
