#!/usr/bin/env python3
"""Run QUALITY × RENDER ENGINE live matrix benchmarks.

For each engine (dmabuf|vaapi|cpu) and tier (high|medium|low):
  1. Apply per-engine encode profile knobs
  2. Reconnect Miracast (encode env is process-start only)
  3. Record a short live private benchmark

Restores the original settings (+ reconnect) at the end or on interrupt.

Usage (while casting, or with a known last peer):
  scripts/benchmark_encode_matrix.py
  scripts/benchmark_encode_matrix.py --engines dmabuf,vaapi --tiers high,medium
  miracast-ctl benchmark all
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
CTL = ROOT / "bin" / "miracast-ctl"

ENGINES_DEFAULT = ("dmabuf", "vaapi", "cpu")
TIERS_DEFAULT = ("high", "medium", "low")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(cmd: list[str], timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        # Kill the hung child so the next cell can reconnect cleanly.
        if exc.process is not None:
            try:
                exc.process.kill()
                exc.process.wait(timeout=5)
            except Exception:
                pass
        return subprocess.CompletedProcess(
            cmd,
            returncode=124,
            stdout=(exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=(
                ((exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""))
                + f"\n[matrix] timed out after {timeout}s"
            ),
        )


def _ctl(*args: str, timeout: Optional[float] = 120.0) -> subprocess.CompletedProcess:
    return _run(["bash", str(CTL), *args], timeout=timeout)


def _status() -> dict[str, Any]:
    r = _ctl("status", timeout=20)
    try:
        data = json.loads(r.stdout or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _settings_path() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return cfg / "omarchy-miracast" / "settings.json"


def _state_dir() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state / "omarchy-miracast"


def _read_settings() -> dict[str, Any]:
    path = _settings_path()
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_settings(data: dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _write_capture_encode(engine: str) -> None:
    path = _state_dir() / "capture-encode"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(engine + "\n")


def _wait_streaming(timeout_s: float = 120.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _status().get("phase") == "streaming":
            return True
        time.sleep(2.0)
    return False


def _reconnect_countdown(seconds: int = 10) -> None:
    """Warn before tearing down an active cast; Ctrl+C cancels."""
    print(
        "\n"
        "WARNING: An active Miracast session is streaming.\n"
        "This benchmark reconnects between cells (encode settings load at\n"
        "FluxCast start), so the current cast will drop briefly and then\n"
        "restore your previous settings when finished.\n"
        "\n"
        "Press Ctrl+C to cancel.\n",
        file=sys.stderr,
        flush=True,
    )
    try:
        for left in range(seconds, 0, -1):
            print(
                f"\rStarting matrix in {left}s… (Ctrl+C to cancel)   ",
                end="",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(1.0)
        print("\rStarting matrix now.                    ", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr, flush=True)
        raise SystemExit(130) from None


def _apply_cell(presets_mod: Any, engine: str, tier: str) -> dict[str, Any]:
    data = _read_settings()
    applied = presets_mod.apply_to_settings(data, tier=tier, engine=engine)
    # Keep movie continuous -D if that was the content hint; profile table
    # does not own damage — preserve castPreset / wfRecorderDamage.
    _write_settings(data)
    _write_capture_encode(engine)
    return applied


def _reconnect(peer: str, peer_name: str, mode: str, with_audio: bool) -> bool:
    """Full stop/start so FluxCast reloads encode env.

    ``miracast-ctl start`` can block longer than a hard timeout even after
    streaming is up. Launch it in the background and key success off
    ``status.phase == streaming`` instead.
    """
    args = [
        "bash",
        str(CTL),
        "start",
        "--peer",
        peer,
        "--peer-name",
        peer_name or "sink",
        "--mode",
        mode or "extend",
        "--go-intent",
        "15",
    ]
    if with_audio:
        args.append("--with-audio")
    print("[matrix] reconnecting Miracast…", file=sys.stderr, flush=True)
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    ok = _wait_streaming(180.0)
    if ok:
        # Prefer a clean start exit, but do not fail the cell if start lingers.
        try:
            proc.wait(timeout=45)
        except subprocess.TimeoutExpired:
            print(
                "[matrix] start still running after streaming — continuing",
                file=sys.stderr,
                flush=True,
            )
        return True

    print("[matrix] WARNING: not streaming after reconnect", file=sys.stderr)
    try:
        proc.kill()
        proc.wait(timeout=10)
    except Exception:
        pass
    _ctl("stop", "--keep-workspaces", timeout=60.0)
    return False


def _record_cell(
    *,
    engine: str,
    tier: str,
    warmup: float,
    sample: float,
    tx_sample: float,
) -> dict[str, Any]:
    case = f"{engine}_{tier}"
    cmd = [
        sys.executable,
        str(SCRIPTS / "record_live_benchmark.py"),
        "--case",
        case,
        "--warmup",
        str(warmup),
        "--sample",
        str(sample),
        "--tx-sample",
        str(tx_sample),
    ]
    print(
        f"[matrix] record {case} (warmup={warmup}s sample={sample}s tx={tx_sample}s)…",
        file=sys.stderr,
        flush=True,
    )
    r = _run(cmd, timeout=max(300.0, warmup + sample + tx_sample + 120.0))
    out: dict[str, Any] = {
        "engine": engine,
        "tier": tier,
        "case": case,
        "returncode": r.returncode,
        "stderr_tail": (r.stderr or "")[-500:],
    }
    # record_live prints JSON summary on stdout
    text = (r.stdout or "").strip()
    try:
        # last JSON object in stdout
        start = text.rfind("{")
        if start >= 0:
            out["summary"] = json.loads(text[start:])
    except Exception:
        out["summary_raw"] = text[-1000:]
    return out


def run_matrix(
    *,
    engines: list[str],
    tiers: list[str],
    warmup: float,
    sample: float,
    tx_sample: float,
    settle: float,
    with_audio: bool,
    skip_countdown: bool = False,
) -> dict[str, Any]:
    presets = _load("encode_quality_presets", SCRIPTS / "encode_quality_presets.py")
    status0 = _status()
    peer = str(status0.get("peer") or "")
    peer_name = str(status0.get("peerName") or "")
    mode = str(status0.get("mode") or "extend")
    settings0 = _read_settings()
    if not peer:
        peer = str(settings0.get("lastPeerMac") or "")
    if not peer_name:
        peer_name = str(settings0.get("lastPeerName") or "sink")
    if not peer:
        raise SystemExit("no peer MAC in status/settings — start a cast first")

    audio = with_audio

    # Matrix always reconnects per cell; warn if a cast is live.
    if status0.get("phase") == "streaming" and not skip_countdown:
        _reconnect_countdown(10)

    snapshot = {
        "settings": dict(settings0),
        "capture_encode": str(settings0.get("captureEncode") or "dmabuf"),
        "peer": peer,
        "peer_name": peer_name,
        "mode": mode,
    }

    cells: list[dict[str, Any]] = []
    started = datetime.now().isoformat(timespec="seconds")
    total = len(engines) * len(tiers)
    n = 0

    def restore() -> None:
        print("[matrix] restoring original settings + reconnect…", file=sys.stderr)
        try:
            _write_settings(snapshot["settings"])
            _write_capture_encode(snapshot["capture_encode"])
            if not _reconnect(
                snapshot["peer"], snapshot["peer_name"], snapshot["mode"], audio
            ):
                print(
                    "[matrix] restore reconnect did not reach streaming "
                    "(settings files were still rewritten)",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(f"[matrix] restore failed: {exc}", file=sys.stderr)

    try:
        for engine in engines:
            for tier in tiers:
                n += 1
                print(
                    f"[matrix] ({n}/{total}) engine={engine} tier={tier}",
                    file=sys.stderr,
                    flush=True,
                )
                applied = _apply_cell(presets, engine, tier)
                if not _reconnect(peer, peer_name, mode, audio):
                    cells.append(
                        {
                            "engine": engine,
                            "tier": tier,
                            "ok": False,
                            "error": "reconnect/streaming timeout",
                            "applied": applied,
                        }
                    )
                    continue
                if settle > 0:
                    time.sleep(settle)
                cell = _record_cell(
                    engine=engine,
                    tier=tier,
                    warmup=warmup,
                    sample=sample,
                    tx_sample=tx_sample,
                )
                cell["ok"] = cell.get("returncode") == 0 and bool(cell.get("summary"))
                cell["applied"] = applied
                cells.append(cell)
    except KeyboardInterrupt:
        print("[matrix] interrupted", file=sys.stderr)
        cells.append({"ok": False, "error": "interrupted"})
    finally:
        restore()

    # Private matrix index (no MAC in top-level; per-cell summaries already scrubbed)
    priv = (
        Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    )
    # Prefer repo private dir via benchmark_privacy
    try:
        bp = _load("benchmark_privacy", SCRIPTS / "benchmark_privacy.py")
        out_dir = bp.PRIVATE_DIR
    except Exception:
        out_dir = ROOT / "docs" / "benchmarks" / "private"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"matrix-encode-{ts}.json"
    report = {
        "kind": "encode_matrix",
        "started": started,
        "finished": datetime.now().isoformat(timespec="seconds"),
        "engines": engines,
        "tiers": tiers,
        "warmup_s": warmup,
        "sample_s": sample,
        "tx_sample_s": tx_sample,
        "settle_s": settle,
        "cells": cells,
        "restored": {
            "captureEncode": snapshot["capture_encode"],
            "encodeProfile": (snapshot["settings"] or {}).get("encodeProfile"),
        },
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    report["matrix_path"] = str(out_path)
    return report


def _print_table(report: dict[str, Any]) -> None:
    print("Encode matrix results")
    print("=====================")
    print(f"Saved: {report.get('matrix_path')}")
    print()
    hdr = f"{'engine':8} {'tier':7} {'ok':3} {'tx_max':7} {'retries/s':9} {'quality':8} {'private':40}"
    print(hdr)
    print("-" * len(hdr))
    for cell in report.get("cells") or []:
        eng = cell.get("engine") or "?"
        tier = cell.get("tier") or "?"
        ok = "yes" if cell.get("ok") else "no"
        summ = cell.get("summary") or {}
        tx = summ.get("tx_mbps_max")
        q = summ.get("quality")
        priv = summ.get("private") or summ.get("stem") or ""
        if isinstance(priv, str) and "/" in priv:
            priv = Path(priv).name
        # retries from nested file would need reload; keep summary fields only
        print(
            f"{eng:8} {tier:7} {ok:3} {tx if tx is not None else '-':>7} "
            f"{'-':>9} {q if q else '-':>8} {str(priv)[:40]:40}"
        )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--engines",
        default=",".join(ENGINES_DEFAULT),
        help="comma list: dmabuf,vaapi,cpu",
    )
    ap.add_argument(
        "--tiers",
        default=",".join(TIERS_DEFAULT),
        help="comma list: high,medium,low",
    )
    ap.add_argument("--warmup", type=float, default=5.0)
    ap.add_argument("--sample", type=float, default=15.0)
    ap.add_argument("--tx-sample", type=float, default=10.0)
    ap.add_argument(
        "--settle",
        type=float,
        default=3.0,
        help="seconds to wait after streaming before sampling",
    )
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument(
        "-y",
        "--yes",
        "--force",
        action="store_true",
        help="skip the 10s countdown when a cast is already streaming",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    for e in engines:
        if e not in ENGINES_DEFAULT:
            print(f"unknown engine: {e}", file=sys.stderr)
            return 1
    for t in tiers:
        if t not in TIERS_DEFAULT:
            print(f"unknown tier: {t}", file=sys.stderr)
            return 1

    n = len(engines) * len(tiers)
    est = n * (args.warmup + args.sample + args.tx_sample + args.settle + 35)
    print(
        f"[matrix] {n} cells (~{est/60:.0f} min with reconnects). "
        "Original settings restored at end.",
        file=sys.stderr,
        flush=True,
    )

    report = run_matrix(
        engines=engines,
        tiers=tiers,
        warmup=args.warmup,
        sample=args.sample,
        tx_sample=args.tx_sample,
        settle=args.settle,
        with_audio=not args.no_audio,
        skip_countdown=bool(args.yes),
    )
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_table(report)
    return 0 if all(c.get("ok") for c in report.get("cells") or []) else 1


if __name__ == "__main__":
    raise SystemExit(main())
