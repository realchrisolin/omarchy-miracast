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


def _station_retries(iface: str) -> tuple[int, int] | None:
    """Return (tx_packets, tx_retries) or None."""
    if not iface:
        return None
    try:
        dump = subprocess.check_output(
            ["iw", "dev", iface, "station", "dump"], text=True, timeout=3.0
        )
    except Exception:
        return None
    pk = re.search(r"tx packets:\s*(\d+)", dump)
    rt = re.search(r"tx retries:\s*(\d+)", dump)
    if not pk or not rt:
        return None
    return int(pk.group(1)), int(rt.group(1))


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
        default=20.0,
        help="shorter cooldown when cast.log shows VIDEO_STALL / bufs_size storm",
    )
    p.add_argument("--retry-sample-every", type=int, default=3, help="ticks between iw dumps")
    p.add_argument("--retry-rate-threshold", type=float, default=0.8, help="percent")
    p.add_argument("--score-cooldown", type=float, default=180.0)
    p.add_argument("--csa-cooldown", type=float, default=300.0)
    p.add_argument("--csa-max-per-hour", type=int, default=2)
    p.add_argument("--score-margin-ratio", type=float, default=1.25)
    p.add_argument("--score-margin-abs", type=float, default=15.0)
    args = p.parse_args(argv)

    status_path = Path(args.status)
    cast_log = Path(args.cast_log)
    plugin = Path(args.plugin)
    ctl = args.ctl

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
    log_size = 0
    try:
        log_size = cast_log.stat().st_size
    except OSError:
        pass

    pick_mod = None

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
        if phase != "streaming":
            _log(cast_log, f"exit phase={phase!r}")
            return 0

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
        if _video_stall_logged(chunk) or _bufs_size_storm(chunk):
            reason = (
                "VIDEO_STALL in cast.log"
                if _video_stall_logged(chunk)
                else "bufs_size storm in cast.log"
            )
            if _do_restart(reason, cooldown=args.video_signal_cooldown):
                continue

        # --- stall detection (cheap) ---
        if mbps is not None and mbps < args.stall_mbps:
            stall_streak += 1
        else:
            stall_streak = 0

        # Audio-only band: LPCM ~1.5 Mbps + RTP overhead ≈ 1.7–1.9 Mbps while
        # video RTP is dead. Catch this even when above --stall-mbps floor.
        if (
            mbps is not None
            and args.audio_band_low <= mbps <= args.audio_band_high
        ):
            audio_band_streak += 1
        else:
            audio_band_streak = 0

        if stall_streak >= args.stall_ticks:
            if _do_restart(
                f"stall TX={mbps:.2f}Mbps for {stall_streak} ticks",
                cooldown=args.restart_cooldown,
            ):
                continue

        if audio_band_streak >= args.audio_band_ticks:
            if _do_restart(
                f"audio-only TX band={mbps:.2f}Mbps for {audio_band_streak} ticks",
                cooldown=args.video_signal_cooldown,
            ):
                continue

        # --- retries / IDR (cheap-ish) ---
        tick += 1
        go = _go_iface()
        rf_pain = False
        if tick % max(1, args.retry_sample_every) == 0 and go:
            stats = _station_retries(go)
            if stats and last_pk is not None:
                dpk = stats[0] - last_pk
                drt = stats[1] - last_rt
                if dpk > 100:
                    rate = 100.0 * drt / dpk
                    if rate >= args.retry_rate_threshold:
                        high_retry_streak += 1
                        rf_pain = True
                        _log(
                            cast_log,
                            f"high retries rate={rate:.2f}% Δrt={drt} Δpk={dpk} streak={high_retry_streak}",
                        )
                    else:
                        high_retry_streak = 0
            if stats:
                last_pk, last_rt = stats

        idr = len(re.findall(r"Sink requested IDR", chunk))
        if idr >= 2:
            rf_pain = True
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
