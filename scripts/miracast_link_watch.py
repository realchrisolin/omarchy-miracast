#!/usr/bin/env python3
"""Low-cost Miracast link watcher (stall recovery + rare RF CSA).

Design (keep radio/CPU cheap):
  * Every ``--interval`` seconds: read P2P TX bytes from sysfs (negligible).
  * Every N ticks: ``iw station dump`` for retry rate (cheap).
  * Stall = TX below ``--stall-mbps`` for ``--stall-ticks`` samples →
    ``miracast-ctl restart-capture`` (cooldown ``--restart-cooldown``).
  * RF path: only after sustained high retries / IDR burst, and at most every
    ``--score-cooldown`` seconds, score channels via pick-p2p-channel using
    **cached** ``nmcli wifi list`` (no forced rescan). CSA only with score
    hysteresis + ``--csa-cooldown``.

Exits when status.json phase is no longer streaming (or status missing).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def _log(path: Path, msg: str) -> None:
    line = f"[link-watch] {msg}"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, file=sys.stderr, flush=True)


def _phase(status_path: Path) -> str:
    try:
        import json

        return str(json.loads(status_path.read_text()).get("phase") or "")
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


def _go_iface() -> str:
    try:
        out = subprocess.check_output(["iw", "dev"], text=True, timeout=3.0)
    except Exception:
        return ""
    iface = ""
    for raw in out.splitlines():
        line = raw.strip()
        if line.startswith("Interface p2p-"):
            iface = line.split()[1]
        elif iface and line.startswith("type "):
            t = line.split()[1]
            if t in ("P2P-GO", "P2P-client"):
                return iface
            iface = ""
    return ""


def _go_channel(iface: str) -> int | None:
    if not iface:
        return None
    try:
        info = subprocess.check_output(
            ["iw", "dev", iface, "info"], text=True, timeout=3.0
        )
    except Exception:
        return None
    m = re.search(r"channel\s+(\d+)", info)
    return int(m.group(1)) if m else None


def _station_dump(iface: str) -> str:
    if not iface:
        return ""
    try:
        return subprocess.check_output(
            ["iw", "dev", iface, "station", "dump"], text=True, timeout=3.0
        )
    except Exception:
        return ""


def _station_retries_from_dump(dump: str) -> tuple[int, int, int] | None:
    """Return (tx_packets, tx_retries, tx_failed) or None."""
    if not dump:
        return None
    pk = re.search(r"tx packets:\s*(\d+)", dump)
    rt = re.search(r"tx retries:\s*(\d+)", dump)
    if not pk or not rt:
        return None
    fail_m = re.search(r"tx failed:\s*(\d+)", dump)
    failed = int(fail_m.group(1)) if fail_m else 0
    return int(pk.group(1)), int(rt.group(1)), failed


def _station_retries(iface: str) -> tuple[int, int, int] | None:
    return _station_retries_from_dump(_station_dump(iface))


def _station_signal_dbm_from_dump(dump: str) -> float | None:
    """Peer signal (dBm) from iw station dump text, or None."""
    if not dump:
        return None
    m = re.search(r"signal:\s*(-?\d+)", dump)
    return float(m.group(1)) if m else None


def _station_signal_dbm(iface: str) -> float | None:
    return _station_signal_dbm_from_dump(_station_dump(iface))


def _station_tx_bitrate_mbps_from_dump(dump: str) -> float | None:
    """Peer TX bitrate (MCS capacity) in Mbps from dump text, or None."""
    if not dump:
        return None
    # e.g. "tx bitrate:	72.2 MBit/s MCS 7 short GI"
    m = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit", dump, re.I)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return val if val > 0 else None


def _station_tx_bitrate_mbps(iface: str) -> float | None:
    return _station_tx_bitrate_mbps_from_dump(_station_dump(iface))


def _go_width_mhz(iface: str) -> float | None:
    """P2P GO channel width (MHz) from ``iw dev INFO``, or None."""
    try:
        out = subprocess.check_output(
            ["iw", "dev", iface, "info"], text=True, timeout=2.0
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"width:\s*(\d+)\s*MHz", out)
    return float(m.group(1)) if m else None


def _station_inactive_ms(iface: str) -> int | None:
    """Peer inactive time in ms (iw station dump), or None if no station."""
    dump = _station_dump(iface)
    if not dump.strip():
        return None
    m = re.search(r"inactive time:\s*(\d+)\s*ms", dump)
    return int(m.group(1)) if m else None


def _udp_send_errors(chunk: str) -> int:
    return len(re.findall(r"UDP send error", chunk))


def _tail_text(path: Path, max_bytes: int = 65536) -> str:
    """Read only the end of a growing log (avoid O(file) every tick)."""
    try:
        size = path.stat().st_size
        with path.open("r", errors="ignore") as fh:
            if size > max_bytes:
                fh.seek(max(0, size - max_bytes))
                fh.readline()  # drop partial first line
            return fh.read()
    except OSError:
        return ""


def _recent_video_fps(cast_log: Path) -> float | None:
    """Estimate video fps from the last two Sender health lines (None if unknown)."""
    try:
        lines = [
            ln
            for ln in _tail_text(cast_log).splitlines()
            if "Sender health" in ln and "video_frames=" in ln
        ][-4:]
    except OSError:
        return None
    samples: list[tuple[float, int]] = []
    for ln in lines:
        m = re.search(
            r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}).*video_frames=(\d+)", ln
        )
        if not m:
            continue
        try:
            # Accept with or without TZ suffix by truncating to seconds.
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"))
            samples.append((ts, int(m.group(2))))
        except Exception:
            continue
    if len(samples) < 2:
        return None
    (t0, f0), (t1, f1) = samples[-2], samples[-1]
    dt = t1 - t0
    if dt <= 0 or dt > 30:
        return None
    # Counter reset after capture rebind → negative delta; not a real fps.
    if f1 < f0:
        return None
    fps = (f1 - f0) / dt
    if fps > 120.0:
        return None
    return fps


def _video_fps_healthy(vfps: float | None, *, min_fps: float = 20.0) -> bool:
    """True when Sender health shows video still advancing (Waydroid/UI OK).

    Low positive fps (0 < fps < 15) is treated as a counter-reset unknown —
    not dead — so soft TX after capture rebind does not false-stall ABR.
    Absurd/negative readings are also unknown (never a demote signal).
    Missing fps (None) still counts as unhealthy for soft-TX gating.
    """
    if vfps is None:
        return False
    v = float(vfps)
    if v < 0.0 or v > 120.0:
        return True
    if 0.0 < v < 15.0:
        return True
    return v >= float(min_fps)


def _soft_tx_counts_as_stall(
    mbps: float | None,
    vfps: float | None,
    *,
    stall_mbps: float,
) -> bool:
    """Soft TX is a stall only if video fps is unhealthy (or TX≈0)."""
    if mbps is None:
        return False
    if mbps < 0.15:
        return True
    if mbps < stall_mbps and not _video_fps_healthy(vfps):
        return True
    return False


def _idr_count_since(cast_log: Path, start_size: int) -> int:
    try:
        with cast_log.open("r", errors="ignore") as fh:
            fh.seek(max(0, start_size))
            chunk = fh.read()
    except OSError:
        return 0
    return len(re.findall(r"Sink requested IDR", chunk))


def _cast_log_chunk(cast_log: Path, start_size: int) -> str:
    try:
        with cast_log.open("r", errors="ignore") as fh:
            fh.seek(max(0, start_size))
            return fh.read()
    except OSError:
        return ""


def _bufs_size_storm(chunk: str) -> bool:
    """True if bufs_size climbed sharply (encode backlog / VAAPI pipe wedge)."""
    vals = [int(n) for n in re.findall(r"bufs_size:\s*(\d+)", chunk)]
    if len(vals) < 3:
        return False
    peak = max(vals)
    start = vals[0]
    # Storm: climb of ≥6 within the tick window, or peak near pool cap.
    return peak >= 14 or (peak - start) >= 6


def _video_stall_logged(chunk: str) -> bool:
    return "VIDEO_STALL" in chunk


def _load_pick(plugin: Path):
    path = plugin / "scripts" / "pick-p2p-channel.py"
    spec = importlib.util.spec_from_file_location("pick_p2p_channel", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("pick-p2p-channel missing")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pick_p2p_channel"] = mod
    spec.loader.exec_module(mod)
    return mod


def _score_24_snapshot(pick_mod, cur_ch: int) -> tuple[int, float, float, int] | None:
    """One cached nmcli list → (best_ch, best_score, cur_score, ap_count).

    Uses ``nmcli wifi list`` without ``--rescan`` so the rare RF path stays cheap.
    """
    text = pick_mod.fetch_nmcli_wifi_list()
    sightings = pick_mod.parse_nmcli_wifi_list(text)
    sight_24 = [s for s in sightings if getattr(s, "freq_mhz", 3000) < 3000]
    if not sight_24:
        return None
    best = None
    for ch in pick_mod.CANDIDATES_24GHZ:
        cs = pick_mod.score_channel(ch, sight_24)
        if best is None or cs.score < best.score:
            best = cs
    if best is None:
        return None
    cur_score = float(pick_mod.score_channel(cur_ch, sight_24).score)
    return best.channel, float(best.score), cur_score, len(sight_24)


def _chan_to_freq(ch: int) -> int | None:
    if 1 <= ch <= 14:
        return 2407 + 5 * ch
    table = {
        36: 5180,
        40: 5200,
        44: 5220,
        48: 5240,
        149: 5745,
        153: 5765,
        157: 5785,
        161: 5805,
        165: 5825,
    }
    return table.get(ch)


def _csa(iface: str, want_ch: int, cast_log: Path) -> bool:
    freq = _chan_to_freq(want_ch)
    if not freq:
        return False
    for attempt in (1, 2, 3):
        try:
            out = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "wpa_cli",
                    "-i",
                    iface,
                    "chan_switch",
                    "5",
                    str(freq),
                    "bandwidth=20",
                ],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            _log(cast_log, f"CSA try={attempt} → ch {want_ch}: {(out.stdout or out.stderr or '').strip()}")
        except Exception as exc:
            _log(cast_log, f"CSA try={attempt} error: {exc}")
        time.sleep(1.0)
        if _go_channel(iface) == want_ch:
            return True
        time.sleep(1.0)
    return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ctl", required=True, help="path to miracast-ctl")
    p.add_argument("--plugin", required=True, help="plugin root (for pick-p2p-channel)")
    p.add_argument("--status", required=True)
    p.add_argument("--cast-log", required=True)
    p.add_argument("--interval", type=float, default=8.0)
    p.add_argument("--stall-mbps", type=float, default=3.0)
    p.add_argument("--stall-ticks", type=int, default=3)
    p.add_argument(
        "--audio-band-low",
        type=float,
        default=1.4,
        help="Mbps floor of audio-only TX band (LPCM ~1.5 + overhead)",
    )
    p.add_argument(
        "--audio-band-high",
        type=float,
        default=2.2,
        help="Mbps ceiling of audio-only TX band",
    )
    p.add_argument(
        "--audio-band-ticks",
        type=int,
        default=2,
        help="ticks stuck in audio-only band before restart-capture",
    )
    p.add_argument("--restart-cooldown", type=float, default=45.0)
    p.add_argument(
        "--video-signal-cooldown",
        type=float,
        default=90.0,
        help="cooldown for TX/audio-band restarts that use the video-signal path "
        "(VIDEO_STALL/bufs no longer trigger link-watch restart)",
    )
    p.add_argument("--retry-sample-every", type=int, default=3, help="ticks between iw dumps")
    p.add_argument(
        "--retry-rate-threshold",
        type=float,
        default=5.0,
        help="percent; log/CSA only — ABR demote uses dynamic_encode.RETRY_PCT_DOWN",
    )
    p.add_argument(
        "--tx-failed-threshold",
        type=int,
        default=1,
        help="iw station tx-failed delta that counts as delivery pain for ABR",
    )
    p.add_argument("--score-cooldown", type=float, default=180.0)
    p.add_argument("--csa-cooldown", type=float, default=300.0)
    p.add_argument("--csa-max-per-hour", type=int, default=2)
    p.add_argument("--score-margin-ratio", type=float, default=1.25)
    p.add_argument("--score-margin-abs", type=float, default=15.0)
    p.add_argument(
        "--dynamic-cooldown",
        type=float,
        # Each apply = SIGUSR1 capture restart. 90s keeps headroom from
        # stacking rebinds (rebind_storm) after a big CQP jump.
        default=90.0,
        help="min seconds between Best (Dynamic) tier changes",
    )
    p.add_argument(
        "--abr-grace",
        type=float,
        default=90.0,
        help="seconds after first ABR tick before demote/climb applies "
        "(avoids SIGUSR1 restart storms right after PLAY)",
    )
    p.add_argument(
        "--peer-inactive-ms",
        type=int,
        default=45000,
        help="iw station inactive time (ms) before treating the sink as gone",
    )
    p.add_argument(
        "--peer-inactive-ticks",
        type=int,
        default=2,
        help="consecutive inactive samples before stop (avoids one-shot blips)",
    )
    p.add_argument(
        "--udp-error-threshold",
        type=int,
        default=8,
        help="UDP send error lines in one cast.log chunk → dead media path",
    )
    p.add_argument(
        "--zero-tx-stop-ticks",
        type=int,
        default=6,
        help="ticks with TX≈0 after stall restarts before stop (not endless rebind)",
    )
    p.add_argument(
        "--settings",
        default="",
        help="settings.json path (default: ~/.config/omarchy-miracast/settings.json)",
    )
    args = p.parse_args(argv)

    status_path = Path(args.status)
    cast_log = Path(args.cast_log)
    plugin = Path(args.plugin)
    ctl = args.ctl
    settings_path = Path(
        args.settings
        or (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "omarchy-miracast"
            / "settings.json"
        )
    )

    _log(cast_log, f"started interval={args.interval}s stall<{args.stall_mbps}Mbps")

    last_tx: int | None = None
    last_t = time.time()
    stall_streak = 0
    audio_band_streak = 0
    last_restart = 0.0
    tick = 0
    last_pk = last_rt = None
    high_retry_streak = 0
    last_score = 0.0
    last_csa = 0.0
    csa_times: list[float] = []
    peer_inactive_streak = 0
    zero_tx_streak = 0
    log_size = 0
    try:
        log_size = cast_log.stat().st_size
    except OSError:
        pass

    pick_mod = None
    dyn_mod = None
    dyn_presets = None  # encode_quality_presets module
    dyn_state = None  # dynamic_encode.LadderState (fine step index)
    sv_bc_state = None  # bitrate_controller.BitrateState (Smart View–style)
    # Last knobs actually applied via SIGUSR1 (coalesced vs decide() state).
    sv_applied_kbps: int | None = None
    sv_applied_qp: int | None = None
    sv_last_apply_ts = 0.0
    sv_pending = None  # last BitrateDecision waiting for coalesce gate
    bc_mod = None  # bitrate_controller module (load once)
    es_mod = None  # encode_strategy (DMA-BUF QVBR starve → pipe)
    starve_streak = 0
    try:
        bc_path = plugin / "scripts" / "bitrate_controller.py"
        bspec = importlib.util.spec_from_file_location(
            "bitrate_controller", bc_path
        )
        if bspec is not None and bspec.loader is not None:
            bc_mod = importlib.util.module_from_spec(bspec)
            import sys as _sys

            _sys.modules["bitrate_controller"] = bc_mod
            bspec.loader.exec_module(bc_mod)
            _log(cast_log, "bitrate_controller loaded (Smart View–style)")
        else:
            _log(cast_log, f"bitrate_controller unavailable: {bc_path}")
    except Exception as exc:
        _log(cast_log, f"bitrate_controller load error: {exc}")
        bc_mod = None
    try:
        es_path = plugin / "scripts" / "encode_strategy.py"
        espec = importlib.util.spec_from_file_location("encode_strategy", es_path)
        if espec is not None and espec.loader is not None:
            es_mod = importlib.util.module_from_spec(espec)
            import sys as _sys

            _sys.modules["encode_strategy"] = es_mod
            espec.loader.exec_module(es_mod)
        else:
            es_mod = None
    except Exception as exc:
        _log(cast_log, f"encode_strategy load error: {exc}")
        es_mod = None
    last_dyn_change = 0.0
    abr_ready_at = 0.0  # wall time when ABR may first apply changes
    # Also suppress stall/audio restart-capture during the same settle window —
    # stacked SIGUSR1 while RTP is coming up → UDP EBADF death.
    capture_grace_until = time.time() + float(args.abr_grace)
    # Bound UDP-during-rebind suppress so endless VIDEO_STALL loops can't leave
    # the UI stuck on "streaming" forever.
    udp_rebind_suppress_until = 0.0
    last_retry_pct: float | None = None
    last_station_dump = ""
    last_link_cap: float | None = None
    last_signal_dbm: float | None = None
    last_width_mhz: float | None = None
    last_retries_ps: float | None = None
    last_tx_failed: int | None = None
    last_tx_failed_delta = 0
    last_stats_t = 0.0
    delivery_fail_tick = False

    def _stop_session(reason: str) -> None:
        """End Miracast so the UI does not stay on streaming after a dead sink."""
        _log(cast_log, f"stop session: {reason}")
        try:
            subprocess.run(
                [ctl, "stop"],
                capture_output=True,
                text=True,
                timeout=60.0,
            )
        except Exception as exc:
            _log(cast_log, f"stop failed: {exc}")

    def _capture_paused() -> bool:
        """True when FluxCast/Omarchy is mid-rebind (pause file or status)."""
        pause = os.environ.get("FLUXCAST_CAPTURE_PAUSE_FILE", "").strip()
        if pause and Path(pause).exists():
            return True
        try:
            import json

            st = json.loads(status_path.read_text())
            if str(st.get("restarting") or "").lower() in ("1", "true", "yes"):
                return True
        except Exception:
            pass
        return False

    def _do_restart(reason: str, *, cooldown: float) -> bool:
        nonlocal last_restart, stall_streak, audio_band_streak, last_tx, tick
        now_r = time.time()
        if (now_r - last_restart) < cooldown:
            return False
        if _capture_paused():
            _log(cast_log, f"{reason} deferred (capture pause/restarting)")
            return False
        _log(cast_log, f"{reason} → restart-capture")
        try:
            subprocess.run(
                [ctl, "restart-capture"],
                capture_output=True,
                text=True,
                timeout=30.0,
            )
        except Exception as exc:
            _log(cast_log, f"restart-capture error: {exc}")
        last_restart = now_r
        stall_streak = 0
        audio_band_streak = 0
        last_tx = None  # re-baseline after pipeline churn
        # Grace: ignore TX until encode has time to ramp (avoid false stalls).
        time.sleep(max(args.interval, 16.0))
        tick += 1
        return True

    while True:
        phase = _phase(status_path)
        # Stay alive through rtsp/connecting flicker (air-TX UI gate). Only
        # exit when the cast is actually gone.
        if phase in ("", "idle", "error", "failed", None):
            _log(cast_log, f"exit phase={phase!r}")
            return 0
        if phase != "streaming":
            time.sleep(max(1.0, min(args.interval, 3.0)))
            continue

        now = time.time()
        tx = _p2p_tx_bytes()
        mbps = None
        if tx is not None and last_tx is not None and now > last_t:
            mbps = (tx - last_tx) * 8 / (now - last_t) / 1e6
        if tx is not None:
            last_tx = tx
            last_t = now

        # --- cast.log video signals (prefer over slow TX-only stall) ---
        chunk = _cast_log_chunk(cast_log, log_size)
        try:
            log_size = cast_log.stat().st_size
        except OSError:
            pass

        # UDP send errors usually mean a dead media socket — but VIDEO_STALL /
        # capture rebind closes the RTP fd briefly and floods EBADF. Suppress
        # stop only briefly around a rebind, not forever.
        udp_errs = _udp_send_errors(chunk)
        rebind_in_chunk = (
            _video_stall_logged(chunk)
            or _bufs_size_storm(chunk)
            or "Auto-rebind" in chunk
            or "SIGUSR1" in chunk
            or "capture restart already in flight" in chunk
            or "rebinding desktop capture" in chunk
        )
        if rebind_in_chunk:
            udp_rebind_suppress_until = time.time() + 20.0
        if udp_errs >= max(1, args.udp_error_threshold):
            if time.time() < udp_rebind_suppress_until or time.time() < capture_grace_until:
                _log(
                    cast_log,
                    f"UDP send errors×{udp_errs} during "
                    f"{'rebind window' if time.time() < udp_rebind_suppress_until else 'capture grace'} "
                    f"— not stopping (expected EBADF while socket recycles)",
                )
            else:
                _stop_session(
                    f"UDP send errors×{udp_errs} in cast.log (dead media path)"
                )
                return 0

        # FluxCast already auto-rebinds on VIDEO_STALL / bufs_size storms.
        # A second restart-capture here doubled the pause storm (same QP).
        # Log only — TX/UDP/audio-band paths below still restart when needed.
        if _video_stall_logged(chunk) or _bufs_size_storm(chunk):
            reason = (
                "VIDEO_STALL in cast.log"
                if _video_stall_logged(chunk)
                else "bufs_size storm in cast.log"
            )
            _log(
                cast_log,
                f"{reason} (FluxCast handles rebind; link-watch not double-restarting)",
            )

        # --- stall detection (cheap) ---
        # Under CQP, quiet UI (e.g. Waydroid) often sits ~1.5–3 Mbps at 30 fps.
        # That is NOT a stall — only treat soft TX as pain when video fps dies
        # or TX is essentially zero.
        vfps = _recent_video_fps(cast_log)
        video_ok = _video_fps_healthy(vfps)

        # AOSP never tears capture down while frames still encode. Soft/zero TX
        # with healthy sender fps is a counter glitch or quiet CBR interval —
        # not a stall (false TX≈0 storms after CBR 5M→3M rebinds).
        if mbps is not None and mbps < 0.15 and not video_ok:
            stall_streak += 1
            zero_tx_streak += 1
        elif _soft_tx_counts_as_stall(mbps, vfps, stall_mbps=args.stall_mbps):
            stall_streak += 1
            zero_tx_streak = 0
        else:
            stall_streak = 0
            zero_tx_streak = 0

        # Audio-only band ≈ LPCM while video RTP is dead — require unhealthy fps.
        if (
            mbps is not None
            and args.audio_band_low <= mbps <= args.audio_band_high
            and not video_ok
        ):
            audio_band_streak += 1
        else:
            audio_band_streak = 0

        # Endless restart-capture while TX stays ~0 leaves UI on "streaming".
        # During capture grace: allow ONE hard-zero restart (media path may not
        # have come up yet). Soft stalls stay suppressed. Don't stop for zero-TX
        # until grace ends — otherwise we kill a settling session.
        if zero_tx_streak >= max(1, args.zero_tx_stop_ticks):
            mbps_s = f"{mbps:.2f}" if mbps is not None else "?"
            if time.time() < capture_grace_until:
                # Do NOT restart-capture during grace when fps is still unknown
                # (Sender health not logged yet). That SIGUSR1 made FluxCast
                # abandon DMA-BUF for pipe before DMA QVBR could settle.
                if vfps is None:
                    _log(
                        cast_log,
                        f"TX≈0 for {zero_tx_streak} ticks (mbps={mbps_s}) during "
                        f"capture grace with fps=? — wait for Sender health "
                        f"(not restarting; protects DMA-BUF QVBR)",
                    )
                    zero_tx_streak = 0
                    stall_streak = 0
                    time.sleep(args.interval)
                    continue
                _log(
                    cast_log,
                    f"TX≈0 for {zero_tx_streak} ticks (mbps={mbps_s}) during "
                    f"capture grace — one recovery restart, not stop",
                )
                zero_tx_streak = 0
                stall_streak = 0
                if _do_restart(
                    f"TX≈0 recovery during capture grace (mbps={mbps_s})",
                    cooldown=max(30.0, float(args.restart_cooldown)),
                ):
                    continue
            else:
                _stop_session(
                    f"TX≈0 for {zero_tx_streak} ticks (mbps={mbps_s}) — sink gone"
                )
                return 0

        if stall_streak >= args.stall_ticks:
            fps_s = f"{vfps:.1f}" if vfps is not None else "?"
            if video_ok:
                _log(
                    cast_log,
                    f"stall TX={mbps:.2f}Mbps fps={fps_s} ignored "
                    f"(video healthy — AOSP would not restart)",
                )
                stall_streak = 0
            # During capture grace: never restart on unknown fps (DMA-BUF QVBR
            # settle). Soft stalls also wait. Hard TX≈0 with known-dead fps
            # still goes through zero_tx_streak above.
            elif time.time() < capture_grace_until:
                _log(
                    cast_log,
                    f"stall TX={mbps:.2f}Mbps fps={fps_s} for {stall_streak} ticks "
                    f"(capture grace — not restarting)",
                )
                stall_streak = 0
            elif _do_restart(
                f"stall TX={mbps:.2f}Mbps fps={fps_s} for {stall_streak} ticks",
                cooldown=args.restart_cooldown,
            ):
                continue

        if audio_band_streak >= args.audio_band_ticks:
            fps_s = f"{vfps:.1f}" if vfps is not None else "?"
            if time.time() < capture_grace_until:
                _log(
                    cast_log,
                    f"audio-only TX band={mbps:.2f}Mbps fps={fps_s} "
                    f"for {audio_band_streak} ticks (capture grace — not restarting)",
                )
                audio_band_streak = 0
            elif _do_restart(
                f"audio-only TX band={mbps:.2f}Mbps fps={fps_s} "
                f"for {audio_band_streak} ticks",
                cooldown=args.video_signal_cooldown,
            ):
                continue

        # --- retries / IDR / peer liveness (cheap-ish) ---
        tick += 1
        go = _go_iface()
        rf_pain = False
        if tick % max(1, args.retry_sample_every) == 0 and go:
            # Associated but idle peer (TV left) — P2P group can linger.
            dump = _station_dump(go)
            last_station_dump = dump or ""
            if dump:
                last_signal_dbm = _station_signal_dbm_from_dump(dump)
                last_link_cap = _station_tx_bitrate_mbps_from_dump(dump)
            if not dump.strip():
                peer_inactive_streak += 1
                _log(
                    cast_log,
                    f"no P2P stations on GO streak={peer_inactive_streak}",
                )
                if peer_inactive_streak >= max(1, args.peer_inactive_ticks):
                    _stop_session("no P2P stations on GO (peer left)")
                    return 0
            else:
                inactive_m = re.search(r"inactive time:\s*(\d+)\s*ms", dump)
                inactive = int(inactive_m.group(1)) if inactive_m else None
                if inactive is not None and inactive >= max(1000, args.peer_inactive_ms):
                    peer_inactive_streak += 1
                    _log(
                        cast_log,
                        f"peer inactive {inactive}ms streak={peer_inactive_streak}",
                    )
                    if peer_inactive_streak >= max(1, args.peer_inactive_ticks):
                        _stop_session(
                            f"peer inactive {inactive}ms "
                            f"(≥{args.peer_inactive_ms}ms ×{peer_inactive_streak})"
                        )
                        return 0
                else:
                    peer_inactive_streak = 0

            delivery_fail_tick = False
            stats = _station_retries_from_dump(dump)
            if stats and last_pk is not None:
                dpk = stats[0] - last_pk
                drt = stats[1] - last_rt
                dfail = stats[2] - (last_tx_failed if last_tx_failed is not None else stats[2])
                dt_s = max(0.001, now - last_stats_t) if last_stats_t else float(args.interval)
                last_retries_ps = drt / dt_s
                if dpk > 100:
                    rate = 100.0 * drt / dpk
                    last_retry_pct = rate
                    if rate >= args.retry_rate_threshold:
                        high_retry_streak += 1
                        rf_pain = True
                        _log(
                            cast_log,
                            f"high retries rate={rate:.2f}% Δrt={drt} Δpk={dpk} streak={high_retry_streak}",
                        )
                    else:
                        high_retry_streak = 0
                if dfail >= max(1, int(args.tx_failed_threshold)):
                    delivery_fail_tick = True
                    last_tx_failed_delta = dfail
                    rf_pain = True
                    _log(cast_log, f"tx failed Δ={dfail}")
                else:
                    last_tx_failed_delta = 0
            if stats:
                last_pk, last_rt = stats[0], stats[1]
                last_tx_failed = stats[2]
                last_stats_t = now
        elif tick % max(1, args.retry_sample_every) == 0 and not go:
            # Streaming status but no GO iface — reclaim.
            _stop_session("streaming status but no P2P GO/client iface")
            return 0

        idr = len(re.findall(r"Sink requested IDR", chunk))
        if idr >= 2:
            rf_pain = True

        # --- Best (Dynamic) encode ladder (only when profile=best) ---
        try:
            import json

            settings = json.loads(settings_path.read_text()) if settings_path.is_file() else {}
            profile = str(settings.get("encodeProfile") or "").strip().lower()
            if profile == "best" and bool(settings.get("encodeBestLocked")):
                # Freeze current fine-step QP/bitrate; stall recovery above still runs.
                dyn_state = None  # clear streaks so unlock starts clean
            elif profile == "best":
                if dyn_mod is None:
                    dyn_path = plugin / "scripts" / "dynamic_encode.py"
                    spec = importlib.util.spec_from_file_location("dynamic_encode", dyn_path)
                    dyn_mod = importlib.util.module_from_spec(spec)
                    assert spec.loader is not None
                    # Required on Python 3.14+ before exec_module (dataclasses).
                    sys.modules[spec.name] = dyn_mod
                    spec.loader.exec_module(dyn_mod)
                if dyn_presets is None:
                    presets_path = plugin / "scripts" / "encode_quality_presets.py"
                    pspec = importlib.util.spec_from_file_location(
                        "encode_quality_presets", presets_path
                    )
                    dyn_presets = importlib.util.module_from_spec(pspec)
                    assert pspec.loader is not None
                    sys.modules[pspec.name] = dyn_presets
                    pspec.loader.exec_module(dyn_presets)
                engine = str(settings.get("captureEncode") or "dmabuf")
                if dyn_state is None:
                    try:
                        step0 = int(settings.get("encodeProfileEffectiveStep"))
                    except (TypeError, ValueError):
                        step0 = dyn_presets.named_tier_to_step(
                            settings.get("encodeProfileEffective") or "high",
                            engine,
                        )
                    try:
                        demotes0 = int(settings.get("encodeBestDemoteCount") or 0)
                    except (TypeError, ValueError):
                        demotes0 = 0
                    sig_floor0 = None
                    try:
                        if settings.get("encodeBestSignalFloorDbm") is not None:
                            sig_floor0 = float(settings.get("encodeBestSignalFloorDbm"))
                    except (TypeError, ValueError):
                        sig_floor0 = None
                    climb_floor0 = None
                    try:
                        if settings.get("encodeBestClimbFloorStep") is not None:
                            climb_floor0 = int(settings.get("encodeBestClimbFloorStep"))
                    except (TypeError, ValueError):
                        climb_floor0 = None
                    dyn_state = dyn_mod.LadderState(
                        step=step0,
                        demote_count=demotes0,
                        signal_floor_dbm=sig_floor0,
                        climb_floor_step=climb_floor0,
                    )
                # ABR stall = real delivery failure or soft TX *with* dead fps.
                # Quiet Waydroid/UI under CQP at ~2 Mbps / 30 fps is healthy.
                # Do NOT treat FluxCast VIDEO_STALL / bufs_size log lines as ABR
                # stalls — those already trigger capture rebind; counting them
                # here caused a soft-QP demote spiral into UDP death.
                abr_stalled = (
                    (stall_streak >= max(1, int(args.stall_ticks) - 1) and not video_ok)
                    or (
                        audio_band_streak >= max(1, int(args.audio_band_ticks) - 1)
                        and not video_ok
                    )
                    or (mbps is not None and mbps < 0.15)
                )
                rc_mode = str(settings.get("vaapiRcMode") or "CQP")
                # Full QP/bitrate ladder — demote on retries / stalls / delivery
                # failures only (no hard Mbps ceiling).
                min_step = 0
                max_step = dyn_presets.best_step_count(engine) - 1
                target = dyn_presets.step_target_mbps(engine, dyn_state.step)
                band_5ghz = False
                # Prefer cached station dump / status — avoid extra iw per tick.
                width_mhz = last_width_mhz
                signal_dbm = last_signal_dbm
                link_cap = last_link_cap
                st_live: dict = {}
                try:
                    st_live = (
                        json.loads(status_path.read_text())
                        if status_path.is_file()
                        else {}
                    )
                    freq = st_live.get("p2pFreqMHz") or st_live.get("p2pListenFreqMHz")
                    if freq is not None and float(freq) >= 4900.0:
                        band_5ghz = True
                    elif go and tick % max(1, args.retry_sample_every) == 0:
                        # Fall back to iw channel when status lacks freq.
                        ch = _go_channel(go)
                        if ch is not None and int(ch) >= 36:
                            band_5ghz = True
                    if st_live.get("p2pWidthMHz") is not None:
                        width_mhz = float(st_live.get("p2pWidthMHz"))
                        last_width_mhz = width_mhz
                    elif go and width_mhz is None and tick % max(1, args.retry_sample_every) == 0:
                        width_mhz = _go_width_mhz(go)
                        last_width_mhz = width_mhz
                    if st_live.get("p2pSignalDbm") is not None and signal_dbm is None:
                        signal_dbm = float(st_live.get("p2pSignalDbm"))
                    if st_live.get("p2pTxBitrateMbps") is not None and link_cap is None:
                        link_cap = float(st_live.get("p2pTxBitrateMbps"))
                except (TypeError, ValueError, OSError, json.JSONDecodeError):
                    pass
                # Rebind UDP EBADF is not a link-quality demote signal.
                udp_is_delivery_fail = udp_errs > 0 and not rebind_in_chunk
                if link_cap is None and last_station_dump:
                    link_cap = _station_tx_bitrate_mbps_from_dump(last_station_dump)
                if signal_dbm is None and last_station_dump:
                    signal_dbm = _station_signal_dbm_from_dump(last_station_dump)
                # WFD path (Best + CBR): honor sink IDR as congestion; cut bitrate
                # instead of walking the old CQP QP ladder / inventing headroom %.
                # Samsung Smart View BitrateController (libremotedisplay_wfd.so):
                # loss/stall → decreaseUDPBitrate; no-loss streak → increase.
                # Smart View ABR only when strategy=smartview (Performance uses CQP ladder).
                _strat = str(settings.get("encodeStrategy") or "smartview").strip().lower()
                use_sv_bc = (
                    str(settings.get("encodeProfile") or "").lower() == "best"
                    and _strat == "smartview"
                )
                if abr_ready_at <= 0.0:
                    abr_ready_at = now + float(args.abr_grace)
                    _log(
                        cast_log,
                        f"dynamic: ABR grace {args.abr_grace:.0f}s "
                        f"(no quality changes until settle)",
                    )
                in_abr_grace = now < abr_ready_at
                cooldown_ok = (not in_abr_grace) and (
                    (now - last_dyn_change) >= args.dynamic_cooldown
                )
                # DMA-BUF QVBR starve → sticky pipe fallback (after ABR grace).
                if (
                    es_mod is not None
                    and use_sv_bc
                    and not in_abr_grace
                    and not str(settings.get("encodeStrategyFallback") or "")
                ):
                    live_cap = str(
                        (st_live or {}).get("capturePath")
                        or settings.get("captureEncode")
                        or ""
                    ).strip().lower()
                    path_for_starve = "dmabuf" if live_cap == "dmabuf" else live_cap
                    tgt = None
                    try:
                        if settings.get("encodeWfdBitrateMbps") is not None:
                            tgt = float(settings.get("encodeWfdBitrateMbps"))
                        elif settings.get("encodeCongestionBitrateMbps") is not None:
                            tgt = float(settings.get("encodeCongestionBitrateMbps"))
                    except (TypeError, ValueError):
                        tgt = None
                    if tgt is None:
                        try:
                            mbr = re.match(
                                r"^\s*([0-9]*\.?[0-9]+)\s*([KkMm])?",
                                str(
                                    settings.get("vaapiBitrate")
                                    or settings.get("bitrate")
                                    or ""
                                ),
                            )
                            if mbr:
                                tgt = float(mbr.group(1))
                                if (mbr.group(2) or "M").upper() == "K":
                                    tgt /= 1000.0
                        except (TypeError, ValueError):
                            tgt = None
                    hit, starve_streak, sreason = es_mod.dmabuf_qvbr_starving(
                        capture_path=path_for_starve,
                        rc_mode=rc_mode,
                        strategy=_strat,
                        air_tx_mbps=mbps,
                        target_mbps=tgt,
                        video_fps=vfps,
                        streak=starve_streak,
                    )
                    if hit:
                        _log(
                            cast_log,
                            f"encode-strategy: dmabuf-qvbr starve → fallback pipe ({sreason})",
                        )
                        try:
                            sf = Path.home() / ".config" / "omarchy-miracast" / "settings.json"
                            data = json.loads(sf.read_text()) if sf.is_file() else {}
                            if not isinstance(data, dict):
                                data = {}
                            # Session-only pipe fallback — keep settings
                            # captureEncode=dmabuf so the next start retries DMA.
                            data["encodeStrategyFallback"] = "pipe"
                            data["vaapiRcMode"] = "QVBR"
                            if str(data.get("captureEncode") or "") != "dmabuf":
                                data["captureEncode"] = "dmabuf"
                            sf.write_text(json.dumps(data, indent=2) + "\n")
                            subprocess.run(
                                [ctl, "restart-capture", "quick"],
                                capture_output=True,
                                text=True,
                                timeout=90.0,
                            )
                            starve_streak = 0
                            last_restart = now
                            last_tx = None
                            time.sleep(max(args.interval, 12.0))
                            continue
                        except Exception as exc:
                            _log(cast_log, f"encode-strategy fallback error: {exc}")
                if use_sv_bc:
                    if bc_mod is not None:
                        band_5 = bool(band_5ghz)
                        try:
                            # Prefer live status freq (settings.json has no p2pFreqMHz).
                            freq = (
                                (st_live or {}).get("p2pFreqMHz")
                                or (st_live or {}).get("p2pOperFreqMHz")
                                or settings.get("p2pFreqMHz")
                            )
                            if freq is not None and float(freq) >= 5000:
                                band_5 = True
                        except (TypeError, ValueError):
                            pass
                        # HT20 5 GHz MCS is often ~72 — not ≥150.
                        if not band_5 and link_cap is not None and float(link_cap) >= 50:
                            band_5 = True
                        cfg = bc_mod.config_for_band(
                            band_5ghz=band_5,
                            width_mhz=float(width_mhz) if width_mhz else 20.0,
                        )
                        cur_br = str(
                            settings.get("bitrate")
                            or settings.get("vaapiBitrate")
                            or "10M"
                        )
                        cur_kbps = bc_mod.ffmpeg_to_kbps(cur_br)
                        try:
                            cur_qp = int(settings.get("vaapiQp") or 22)
                        except (TypeError, ValueError):
                            cur_qp = 22
                        if sv_bc_state is None:
                            sv_bc_state = bc_mod.BitrateState(kbps=cur_kbps, qp=cur_qp)
                        else:
                            sv_bc_state = bc_mod.BitrateState(
                                kbps=cur_kbps,
                                qp=cur_qp,
                                good_streak=sv_bc_state.good_streak,
                                bad_streak=sv_bc_state.bad_streak,
                                underfill_streak=getattr(
                                    sv_bc_state, "underfill_streak", 0
                                )
                                or 0,
                            )
                        # Soft/zero TX is NetStall only with *known unhealthy* fps.
                        # fps=None after rebind must not demote (was 45M→12M).
                        bc_stall = (
                            bool(abr_stalled)
                            and vfps is not None
                            and not video_ok
                        )
                        # QVBR strategy: do not feed iw retry% as RTCP LOSS
                        # (Smart View uses RR; retry% demotes were invented).
                        sig = bc_mod.LinkSignals(
                            stalled=bc_stall,
                            video_fps=vfps,
                            link_capacity_mbps=link_cap,
                            air_tx_mbps=mbps,
                            tx_failed_delta=int(last_tx_failed_delta or 0)
                            if delivery_fail_tick
                            else 0,
                            retry_percent=None,
                        )
                        d = bc_mod.decide(
                            sv_bc_state, sig, cfg, cooldown_ok=cooldown_ok
                        )
                        sv_bc_state = bc_mod.BitrateState(
                            kbps=d.kbps,
                            qp=d.qp,
                            good_streak=d.good_streak,
                            bad_streak=d.bad_streak,
                            underfill_streak=d.underfill_streak,
                        )
                        if sv_applied_kbps is None:
                            sv_applied_kbps = cur_kbps
                            sv_applied_qp = cur_qp
                        if d.changed:
                            sv_pending = d
                        # Samsung decide() stays hot; coalesce VAAPI SIGUSR1 applies.
                        if sv_pending is not None:
                            pend = sv_pending
                            hard_fail = bool(delivery_fail_tick) or bool(
                                last_tx_failed_delta and last_tx_failed_delta > 0
                            )
                            if in_abr_grace and not hard_fail and not bc_mod.is_urgent_apply(
                                pend.reason
                            ):
                                if tick % 10 == 0:
                                    print(
                                        f"[link-watch] dynamic: hold:abr_grace "
                                        f"({pend.reason} want {pend.kbps}kbps qp{pend.qp})",
                                        file=sys.stderr,
                                        flush=True,
                                    )
                                time.sleep(args.interval)
                                continue
                            do_apply, gate = bc_mod.should_apply_encode(
                                applied_kbps=int(sv_applied_kbps),
                                applied_qp=int(sv_applied_qp or cur_qp),
                                desired_kbps=int(pend.kbps),
                                desired_qp=int(pend.qp),
                                reason=str(pend.reason),
                                now=now,
                                last_apply_ts=sv_last_apply_ts,
                            )
                            if not do_apply:
                                # Do not write coalesce holds into cast.log —
                                # that inflated the file and burned CPU on
                                # full-log fps scans. Never skip the tick sleep.
                                if d.changed and tick % 10 == 0:
                                    print(
                                        f"[link-watch] dynamic: coalesce {gate} "
                                        f"(applied {sv_applied_kbps}kbps qp{sv_applied_qp} "
                                        f"→ want {pend.kbps}kbps qp{pend.qp})",
                                        file=sys.stderr,
                                        flush=True,
                                    )
                                time.sleep(args.interval)
                                continue
                            # Keep DMA-BUF across ABR applies for QVBR strategy.
                            try:
                                import json as _json

                                sf = (
                                    Path.home()
                                    / ".config"
                                    / "omarchy-miracast"
                                    / "settings.json"
                                )
                                data = (
                                    _json.loads(sf.read_text())
                                    if sf.is_file()
                                    else {}
                                )
                                if isinstance(data, dict):
                                    data["captureEncode"] = "dmabuf"
                                    data["encodeStrategyFallback"] = ""
                                    data["encodeStrategy"] = "smartview"
                                    sf.write_text(
                                        _json.dumps(data, indent=2) + "\n"
                                    )
                                    (
                                        Path.home()
                                        / ".local"
                                        / "state"
                                        / "omarchy-miracast"
                                        / "capture-encode"
                                    ).write_text("dmabuf\n")
                            except Exception as exc:
                                _log(cast_log, f"dmabuf-sticky prep error: {exc}")
                            _log(
                                cast_log,
                                f"dynamic: sv-apply {sv_applied_kbps}→{pend.kbps}kbps "
                                f"qp{sv_applied_qp}→{pend.qp} ({pend.reason}) "
                                f"[{gate}] rc={pend.rc_mode} "
                                f"qpBounds={pend.qp_min}-{pend.qp_max}",
                            )
                            try:
                                subprocess.run(
                                    [
                                        ctl,
                                        "set-bitrate-kbps",
                                        str(pend.kbps),
                                        str(pend.qp),
                                        str(pend.qp_min),
                                        str(pend.qp_max),
                                    ],
                                    capture_output=True,
                                    text=True,
                                    timeout=60.0,
                                )
                            except Exception as exc:
                                _log(cast_log, f"set-bitrate-kbps error: {exc}")
                            sv_applied_kbps = int(pend.kbps)
                            sv_applied_qp = int(pend.qp)
                            sv_last_apply_ts = now
                            sv_pending = None
                            last_dyn_change = now
                            last_restart = now
                            last_tx = None
                            time.sleep(max(args.interval, 12.0))
                            continue
                        # No pending apply — skip legacy CQP ladder, but still
                        # sleep. A bare continue here spun at ~80% CPU.
                        time.sleep(args.interval)
                        continue

                sample = dyn_mod.LinkSample(
                    throughput_mbps=mbps,
                    link_capacity_mbps=link_cap,
                    retry_percent=last_retry_pct,
                    retries_per_sec=last_retries_ps,
                    signal_dbm=signal_dbm,
                    stalled=bool(abr_stalled),
                    delivery_fail=bool(delivery_fail_tick or udp_is_delivery_fail),
                    tx_failed_delta=int(last_tx_failed_delta or 0)
                    if delivery_fail_tick
                    else (1 if udp_is_delivery_fail else 0),
                    video_fps=vfps,
                )
                # Grace: block climbs / soft applies after rebind. Demotes still
                # apply — especially extreme_harbor (JScreenFix-class oversat).
                cooldown_ok = (not in_abr_grace) and (
                    (now - last_dyn_change) >= args.dynamic_cooldown
                )
                decision = dyn_mod.decide(
                    dyn_state,
                    sample,
                    cooldown_ok=True if in_abr_grace else cooldown_ok,
                    rc_mode=rc_mode,
                    min_step=min_step,
                    max_step=max_step,
                    target_mbps=target,
                    band_5ghz=band_5ghz,
                    width_mhz=width_mhz,
                )
                if decision.changed and in_abr_grace:
                    is_demote = decision.reason.startswith("down:")
                    if not is_demote:
                        decision = dyn_mod.LadderDecision(
                            step=dyn_state.step,
                            changed=False,
                            reason=f"hold:abr_grace ({decision.reason})",
                            bad_streak=decision.bad_streak,
                            good_streak=decision.good_streak,
                            demote_count=dyn_state.demote_count,
                            signal_floor_dbm=dyn_state.signal_floor_dbm,
                            climb_good_need=decision.climb_good_need,
                            climb_floor_step=getattr(
                                dyn_state, "climb_floor_step", None
                            ),
                        )
                # Never apply more than one quality change per dynamic_cooldown —
                # each set-quality restarts capture and stacked restarts → UDP death.
                elif (
                    decision.changed
                    and last_dyn_change > 0.0
                    and (now - last_dyn_change) < args.dynamic_cooldown
                ):
                    decision = dyn_mod.LadderDecision(
                        step=dyn_state.step,
                        changed=False,
                        reason=f"hold:apply_cooldown ({decision.reason})",
                        bad_streak=decision.bad_streak,
                        good_streak=decision.good_streak,
                        demote_count=decision.demote_count,
                        signal_floor_dbm=decision.signal_floor_dbm,
                        climb_good_need=decision.climb_good_need,
                        climb_floor_step=decision.climb_floor_step,
                    )
                prev_demotes = int(getattr(dyn_state, "demote_count", 0) or 0)
                prev_floor = getattr(dyn_state, "climb_floor_step", None)
                dyn_state = dyn_mod.LadderState(
                    step=decision.step,
                    bad_streak=decision.bad_streak,
                    good_streak=decision.good_streak,
                    demote_count=decision.demote_count,
                    signal_floor_dbm=decision.signal_floor_dbm,
                    climb_good_need=getattr(
                        decision, "climb_good_need", dyn_mod.GOOD_STREAK_UP
                    ),
                    climb_floor_step=getattr(decision, "climb_floor_step", None),
                )
                # Persist demote settle + soft climb ceiling across watcher restarts.
                if (
                    decision.demote_count != prev_demotes
                    or decision.climb_floor_step != prev_floor
                    or decision.reason.startswith("signal_recover")
                    or "signal_recover" in decision.reason
                ):
                    try:
                        settings["encodeBestDemoteCount"] = int(decision.demote_count)
                        if decision.climb_floor_step is None:
                            settings.pop("encodeBestClimbFloorStep", None)
                        else:
                            settings["encodeBestClimbFloorStep"] = int(
                                decision.climb_floor_step
                            )
                        if decision.signal_floor_dbm is None:
                            settings.pop("encodeBestSignalFloorDbm", None)
                        else:
                            settings["encodeBestSignalFloorDbm"] = float(
                                decision.signal_floor_dbm
                            )
                        settings_path.write_text(
                            json.dumps(settings, separators=(",", ":")) + "\n"
                        )
                    except Exception as exc:
                        _log(cast_log, f"dynamic persist demotes error: {exc}")
                if decision.changed:
                    label = dyn_presets.step_display_label(engine, decision.step)
                    _log(
                        cast_log,
                        f"dynamic: → step {decision.step} ({label}, {decision.reason})",
                    )
                    try:
                        subprocess.run(
                            [ctl, "set-quality-effective", f"step:{decision.step}"],
                            capture_output=True,
                            text=True,
                            timeout=45.0,
                        )
                    except Exception as exc:
                        _log(cast_log, f"dynamic apply error: {exc}")
                    last_dyn_change = now
                    last_restart = now  # share cooldown with capture restarts
                    last_tx = None
                    time.sleep(max(args.interval, 12.0))
                    continue
                if (
                    decision.reason.startswith("hold:no_climb")
                    or decision.reason.startswith("signal_recover")
                    or "signal_recover" in decision.reason
                ):
                    _log(cast_log, f"dynamic: {decision.reason}")
            else:
                dyn_state = None
        except Exception as exc:
            _log(cast_log, f"dynamic skipped: {exc}")
            _log(cast_log, f"IDR burst count={idr} since last check")

        # --- rare score + optional CSA ---
        cur_ch = _go_channel(go) if go else None
        want_score = (
            rf_pain
            and high_retry_streak >= 2
            and cur_ch is not None
            and cur_ch <= 14
            and (now - last_score) >= args.score_cooldown
        )
        if want_score and cur_ch is not None:
            last_score = now
            try:
                if pick_mod is None:
                    pick_mod = _load_pick(plugin)
                snap = _score_24_snapshot(pick_mod, cur_ch)
                if snap is not None:
                    best_ch, best_score, cur_score, n_aps = snap
                    _log(
                        cast_log,
                        f"score cur_ch={cur_ch} score={cur_score:.1f} best_ch={best_ch} "
                        f"best_score={best_score:.1f} aps={n_aps}",
                    )
                    better = (
                        best_ch != cur_ch
                        and cur_score > best_score * args.score_margin_ratio
                        and (cur_score - best_score) >= args.score_margin_abs
                    )
                    csa_times = [t for t in csa_times if now - t < 3600]
                    if (
                        better
                        and (now - last_csa) >= args.csa_cooldown
                        and len(csa_times) < args.csa_max_per_hour
                    ):
                        _log(
                            cast_log,
                            f"CSA {cur_ch} → {best_ch} (score {cur_score:.0f} → {best_score:.0f})",
                        )
                        if _csa(go, best_ch, cast_log):
                            last_csa = now
                            csa_times.append(now)
                            high_retry_streak = 0
                        else:
                            _log(cast_log, f"CSA to {best_ch} failed; staying on {cur_ch}")
            except Exception as exc:
                _log(cast_log, f"score/CSA skipped: {exc}")

        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
