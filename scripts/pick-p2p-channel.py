#!/usr/bin/env python3
"""Pick a quiet Wi-Fi channel for Miracast P2P GO / post-connect CSA.

Policy:
  1. Prefer candidates *outside* the STA's 80 MHz block (e.g. UNII-3
     149+ when the home AP is on UNII-1 ch 44 @ 80 MHz).
  2. Among those, prefer quiet (score <= threshold), else quietest.
  3. Only fall back into the STA block if every off-block candidate is
     unavailable; then quiet / quietest within the block.

Scoring (what "quiet" actually means):
  "Quiet" is only a threshold label (score <= DEFAULT_QUIET_THRESHOLD).
  The useful quantity is the numeric **score**: lower = less estimated
  co-/adjacent-channel energy from visible APs.

  - Co-channel APs count fully; neighbors are down-weighted
    (``_neighbor_weight``: 2.4 GHz ±1..4, 5 GHz ±4/±8).
  - Stronger APs add more to the score than weak ones.
  - When no candidate is under the quiet threshold, pick the **lowest
    score** ("quietest") — that ranking is what matters in dense RF.

  On-device check (2026-09-15, AX201 + 2.4-only Miracast dongle, MCC with
  5 GHz STA): among 2.4 candidates {1,6,11}, score order matched measured
  Miracast TX retry rate (ch 11 best, ch 1 worst). So the model is not just
  a naming convention — it tracked real delivery cost in that environment.

Reads `nmcli -t device wifi list` by default. Pure scoring helpers are
unit-tested without nmcli.

Usage:
  scripts/pick-p2p-channel.py
  scripts/pick-p2p-channel.py --json
  scripts/pick-p2p-channel.py --band 5
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

# Non-DFS 20 MHz channels commonly usable for P2P GO.
CANDIDATES_5GHZ = (36, 40, 44, 48, 149, 153, 157, 161)
CANDIDATES_24GHZ = (1, 6, 11)

# Global operating classes (IEEE 802.11 Annex E / WFA).
REG_CLASS_24 = 81
REG_CLASS_5_UNII1 = 115  # 36-48
REG_CLASS_5_UNII2A = 118  # 52-64
REG_CLASS_5_UNII2C = 121  # 100-144
REG_CLASS_5_UNII3 = 125  # 149-165

# Quiet if score is at or below this. Score is AP-weighted; 0 means empty.
DEFAULT_QUIET_THRESHOLD = 5.0


@dataclass
class ApSighting:
    channel: int
    freq_mhz: float
    signal: float  # dBm, higher (less negative) = stronger
    ssid: str = ""
    in_use: bool = False


@dataclass
class ChannelScore:
    channel: int
    freq_mhz: float
    reg_class: int
    score: float
    ap_count: int
    max_signal: float
    quiet: bool
    notes: str = ""


@dataclass
class PickResult:
    channel: int
    freq_mhz: float
    reg_class: int
    quiet: bool
    score: float
    reason: str
    sta_channel: Optional[int] = None
    sta_width_mhz: Optional[int] = None
    sta_block: list[int] = field(default_factory=list)
    candidates: list[ChannelScore] = field(default_factory=list)


def reg_class_for_channel(channel: int) -> int:
    if 1 <= channel <= 13:
        return REG_CLASS_24
    if channel in (36, 40, 44, 48):
        return REG_CLASS_5_UNII1
    if channel in (52, 56, 60, 64):
        return REG_CLASS_5_UNII2A
    if 100 <= channel <= 144:
        return REG_CLASS_5_UNII2C
    if channel in (149, 153, 157, 161, 165):
        return REG_CLASS_5_UNII3
    raise ValueError(f"unsupported P2P channel: {channel}")


def channel_to_freq_mhz(channel: int) -> float:
    if 1 <= channel <= 13:
        return 2407.0 + 5.0 * channel
    if 14 == channel:
        return 2484.0
    if 32 <= channel <= 177:
        return 5000.0 + 5.0 * channel
    raise ValueError(f"unknown channel: {channel}")


# Non-DFS 80 MHz primary sets (20 MHz steps) used by home APs / P2P CSA.
_UNII1_80 = (36, 40, 44, 48)
_UNII3_80 = (149, 153, 157, 161)


def sta_80mhz_block(sta_channel: int) -> tuple[int, ...]:
    """20 MHz primaries sharing an 80 MHz block with sta_channel."""
    if sta_channel in _UNII1_80:
        return _UNII1_80
    if sta_channel in _UNII3_80:
        return _UNII3_80
    if 52 <= sta_channel <= 64:
        return (52, 56, 60, 64)
    if 100 <= sta_channel <= 112:
        return (100, 104, 108, 112)
    if 116 <= sta_channel <= 128:
        return (116, 120, 124, 128)
    if 132 <= sta_channel <= 144:
        return (132, 136, 140, 144)
    return (sta_channel,)


def sta_occupied_channels(
    sta_channel: Optional[int],
    sta_width_mhz: int = 80,
) -> set[int]:
    """Channels to treat as occupied/overlapping with the live STA link."""
    if sta_channel is None:
        return set()
    if sta_channel <= 14:
        # 2.4 GHz: treat ±2 as overlap for ranking.
        return {c for c in range(sta_channel - 2, sta_channel + 3) if 1 <= c <= 13}
    if sta_width_mhz >= 80:
        return set(sta_80mhz_block(sta_channel))
    if sta_width_mhz >= 40:
        block = sta_80mhz_block(sta_channel)
        # Nearest pair within the 80 MHz block.
        idx = block.index(sta_channel) if sta_channel in block else 0
        lo = max(0, idx - 1)
        return set(block[lo : lo + 2])
    return {sta_channel}


def unii_band(channel: int) -> str:
    if channel <= 14:
        return "2.4"
    if channel in _UNII1_80:
        return "unii-1"
    if 52 <= channel <= 64:
        return "unii-2a"
    if 100 <= channel <= 144:
        return "unii-2c"
    if channel in _UNII3_80 or channel == 165:
        return "unii-3"
    return "other"


def parse_nmcli_wifi_list(text: str) -> list[ApSighting]:
    """Parse `nmcli -t -f IN-USE,SSID,CHAN,FREQ,SIGNAL device wifi list`."""
    out: list[ApSighting] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # nmcli -t escapes ':' as '\\:'; split carefully.
        parts: list[str] = []
        buf = ""
        escaped = False
        for ch in line:
            if escaped:
                buf += ch
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == ":":
                parts.append(buf)
                buf = ""
                continue
            buf += ch
        parts.append(buf)
        if len(parts) < 5:
            continue
        in_use = parts[0].strip() == "*"
        ssid = parts[1]
        try:
            channel = int(parts[2])
        except ValueError:
            continue
        freq_s = parts[3].replace("MHz", "").strip()
        try:
            freq = float(freq_s)
        except ValueError:
            try:
                freq = channel_to_freq_mhz(channel)
            except ValueError:
                continue
        try:
            signal = float(parts[4])
        except ValueError:
            signal = -100.0
        # nmcli SIGNAL is 0-100 quality; map roughly to dBm-ish for scoring.
        # Keep raw if already negative (iw-style); otherwise convert quality.
        if signal > 0:
            # quality 0..100 → approx -100..-30 dBm
            signal = (signal / 2.0) - 100.0
        out.append(
            ApSighting(
                channel=channel,
                freq_mhz=freq,
                signal=signal,
                ssid=ssid,
                in_use=in_use,
            )
        )
    return out


def sta_channel_from_sightings(sightings: Iterable[ApSighting]) -> Optional[int]:
    for ap in sightings:
        if ap.in_use:
            return ap.channel
    return None


def _neighbor_weight(candidate: int, ap_channel: int) -> float:
    """How much an AP on ap_channel bleeds into candidate (0..1)."""
    if candidate == ap_channel:
        return 1.0
    # 5 GHz 20 MHz: adjacent ±4 channels share some energy.
    if candidate >= 36 and ap_channel >= 36:
        dist = abs(candidate - ap_channel)
        if dist == 4:
            return 0.35
        if dist == 8:
            return 0.1
        return 0.0
    # 2.4 GHz: overlapping 20 MHz channels (±1..4).
    if candidate <= 14 and ap_channel <= 14:
        dist = abs(candidate - ap_channel)
        if dist == 0:
            return 1.0
        if dist <= 2:
            return 0.75
        if dist <= 4:
            return 0.35
        return 0.0
    return 0.0


def score_channel(
    channel: int,
    sightings: Iterable[ApSighting],
    *,
    quiet_threshold: float = DEFAULT_QUIET_THRESHOLD,
) -> ChannelScore:
    sightings = list(sightings)
    ap_count = 0
    max_signal = -120.0
    score = 0.0
    for ap in sightings:
        w = _neighbor_weight(channel, ap.channel)
        if w <= 0:
            continue
        if w >= 1.0:
            ap_count += 1
            max_signal = max(max_signal, ap.signal)
        # Stronger (less negative) signal → higher score (worse).
        strength = max(0.0, ap.signal + 95.0)  # -95 → 0, -45 → 50
        score += w * (10.0 + strength * 0.5)
    if ap_count == 0:
        max_signal = -120.0
    quiet = score <= quiet_threshold
    notes = "empty" if ap_count == 0 and score == 0 else f"aps={ap_count}"
    return ChannelScore(
        channel=channel,
        freq_mhz=channel_to_freq_mhz(channel),
        reg_class=reg_class_for_channel(channel),
        score=round(score, 2),
        ap_count=ap_count,
        max_signal=max_signal,
        quiet=quiet,
        notes=notes,
    )


def pick_channel(
    sightings: list[ApSighting],
    *,
    candidates: Iterable[int] = CANDIDATES_5GHZ,
    quiet_threshold: float = DEFAULT_QUIET_THRESHOLD,
    sta_channel: Optional[int] = None,
    sta_width_mhz: int = 80,
) -> PickResult:
    cand_list = list(candidates)
    if not cand_list:
        raise ValueError("no candidate channels")
    if sta_channel is None:
        sta_channel = sta_channel_from_sightings(sightings)

    occupied = sta_occupied_channels(sta_channel, sta_width_mhz)
    sta_band = unii_band(sta_channel) if sta_channel is not None else ""

    scored = [
        score_channel(ch, sightings, quiet_threshold=quiet_threshold)
        for ch in cand_list
    ]

    def sort_key(cs: ChannelScore) -> tuple:
        # Off STA 80 MHz block first (real MCC airtime), then quiet /
        # quietest, then different UNII band, then farther primary.
        in_sta_block = 1 if cs.channel in occupied else 0
        same_band = (
            1
            if sta_band and unii_band(cs.channel) == sta_band
            else 0
        )
        dist = abs(cs.channel - sta_channel) if sta_channel is not None else 0
        return (
            in_sta_block,
            0 if cs.quiet else 1,
            cs.score,
            same_band,
            -dist,
            cs.channel,
        )

    ranked = sorted(scored, key=sort_key)
    best = ranked[0]
    quiet_ones = [c for c in scored if c.quiet]
    if quiet_ones:
        reason = (
            f"quiet channel {best.channel} "
            f"(score={best.score}, {len(quiet_ones)} quiet of {len(scored)})"
        )
    else:
        reason = (
            f"no quiet channels; quietest is {best.channel} "
            f"(score={best.score}, aps={best.ap_count})"
        )
    if sta_channel is not None and best.channel == sta_channel:
        reason += f"; same as STA ch {sta_channel} (SCC likely)"
    elif sta_channel is not None and best.channel in occupied:
        reason += (
            f"; STA ch {sta_channel} @{sta_width_mhz}MHz block "
            f"{sorted(occupied)} — no off-block quiet option"
        )
    elif sta_channel is not None:
        reason += (
            f"; STA ch {sta_channel} @{sta_width_mhz}MHz "
            f"(avoided block {sorted(occupied)})"
        )

    return PickResult(
        channel=best.channel,
        freq_mhz=best.freq_mhz,
        reg_class=best.reg_class,
        quiet=best.quiet,
        score=best.score,
        reason=reason,
        sta_channel=sta_channel,
        sta_width_mhz=sta_width_mhz,
        sta_block=sorted(occupied),
        candidates=ranked,
    )


def fetch_nmcli_wifi_list(iface: Optional[str] = None) -> str:
    cmd = [
        "nmcli",
        "-t",
        "-f",
        "IN-USE,SSID,CHAN,FREQ,SIGNAL",
        "device",
        "wifi",
        "list",
    ]
    if iface:
        cmd.extend(["ifname", iface])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20.0)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "nmcli wifi list failed").strip())
    return r.stdout


def detect_sta_width_mhz(iface: Optional[str] = None) -> int:
    """Best-effort STA channel width from `iw` (default 80 for 5 GHz home APs)."""
    ifaces = [iface] if iface else []
    if not ifaces:
        try:
            out = subprocess.check_output(["iw", "dev"], text=True, timeout=5.0)
        except Exception:
            return 80
        current = None
        for line in out.splitlines():
            if line.startswith("Interface "):
                current = line.split()[1]
            elif current and "type managed" in line:
                ifaces.append(current)
    for name in ifaces:
        try:
            info = subprocess.check_output(
                ["iw", "dev", name, "info"], text=True, timeout=5.0
            )
        except Exception:
            continue
        # e.g. channel 44 (5220 MHz), width: 80 MHz, center1: 5210 MHz
        for line in info.splitlines():
            if "width:" in line and "channel" in line:
                if "160 MHz" in line:
                    return 160
                if "80 MHz" in line:
                    return 80
                if "40 MHz" in line:
                    return 40
                if "20 MHz" in line:
                    return 20
    return 80


def candidates_for_band(band: str) -> tuple[int, ...]:
    band = (band or "5").lower()
    if band in ("5", "5ghz", "5g"):
        return CANDIDATES_5GHZ
    if band in ("2.4", "24", "2.4ghz", "2g"):
        return CANDIDATES_24GHZ
    if band in ("both", "all"):
        return CANDIDATES_5GHZ + CANDIDATES_24GHZ
    raise ValueError(f"unknown band: {band}")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--band",
        default="5",
        help="candidate set: 5 (default), 2.4, or both",
    )
    p.add_argument(
        "--quiet-threshold",
        type=float,
        default=DEFAULT_QUIET_THRESHOLD,
        help=f"score <= this counts as quiet (default {DEFAULT_QUIET_THRESHOLD})",
    )
    p.add_argument("--iface", default=None, help="wifi ifname for nmcli")
    p.add_argument(
        "--from-file",
        default=None,
        help="read nmcli -t wifi list text from file instead of scanning",
    )
    p.add_argument(
        "--sta-width",
        type=int,
        default=None,
        help="STA channel width MHz (default: detect via iw, else 80)",
    )
    args = p.parse_args(argv)

    try:
        cands = candidates_for_band(args.band)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        if args.from_file:
            text = open(args.from_file, encoding="utf-8").read()
            sta_width = args.sta_width if args.sta_width is not None else 80
        else:
            text = fetch_nmcli_wifi_list(args.iface)
            sta_width = (
                args.sta_width
                if args.sta_width is not None
                else detect_sta_width_mhz(args.iface)
            )
        sightings = parse_nmcli_wifi_list(text)
        result = pick_channel(
            sightings,
            candidates=cands,
            quiet_threshold=args.quiet_threshold,
            sta_width_mhz=sta_width,
        )
    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"pick-p2p-channel: {e}", file=sys.stderr)
        return 1

    payload = {
        "ok": True,
        "channel": result.channel,
        "freq_mhz": result.freq_mhz,
        "reg_class": result.reg_class,
        "quiet": result.quiet,
        "score": result.score,
        "reason": result.reason,
        "sta_channel": result.sta_channel,
        "sta_width_mhz": result.sta_width_mhz,
        "sta_block": result.sta_block,
        "candidates": [asdict(c) for c in result.candidates],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        tag = "quiet" if result.quiet else "quietest"
        print(
            f"{tag} ch={result.channel} freq={result.freq_mhz:.0f} "
            f"reg={result.reg_class} score={result.score} — {result.reason}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
