"""Samsung Smart View–style BitrateController (reverse-engineered).

Derived from ``libremotedisplay_wfd.so`` symbols/strings
(``vendor/samsung/.../BitrateController.cpp``):

* ``updateConfiguration(min, max, init, minQP, maxQP)``
* ``decideUDPBitrate(RTCP RR)`` / ``calculateFractionLost``
* ``increaseUDPBitrate`` / ``decreaseUDPBitrate``
* ``updateBitrateByNetStall``
* ``LOSS case`` vs ``No loss case`` logging
* Codec: MediaCodec ``bitrate`` + ``bitrate-mode`` + QP range
* ``Turn on CAC mode in VBR`` → quality-defined VBR with a hard ceiling

Extension for Linux VAAPI QVBR: when the encode *target* is already near the
75% MCS ceiling but measured air TX under-fills that setpoint, lower QP so
QVBR spends more bits (maxrate alone does not force fill).

Without sink RTCP (hotyeah advertised RTCP port 0), loss is proxied by
iw ``tx_failed`` / retry%% and stall by sender fps sag — same *roles* as
Samsung's RR + NetworkStall inputs.

Pure functions / small state object — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Defaults tuned for Miracast 1080p60 on 5 GHz HT20 (~72 Mbps MCS).
DEFAULT_INIT_KBPS = 36_000
DEFAULT_MIN_KBPS = 1_500
DEFAULT_MAX_KBPS = 55_000
DEFAULT_QP_MIN = 12
DEFAULT_QP_MAX = 42
DEFAULT_QP = 22

# Fraction-lost style thresholds (RTCP RR proxy).
LOSS_FRACTION_HIGH = 0.02  # ≥2% → decrease (Samsung "LOSS case")
LOSS_FRACTION_CLEAR = 0.005  # <0.5% and healthy → may increase

# Step factors (Samsung logs absolute bps changes; ratios match AOSP-ish feel).
DECREASE_FACTOR = 0.80
INCREASE_FACTOR = 1.10
STALL_DECREASE_FACTOR = 0.65  # NetworkStall path is more aggressive

# Encode target vs iw MCS capacity (RTP/Wi‑Fi overhead still needs the remaining).
CAPACITY_FRAC = 0.75

# QVBR fill: when air TX is below this fraction of the desired setpoint
# (min(target, 75% MCS)), sharpen QP. Above this → hold / soften on pain.
FILL_LOW_FRAC = 0.55
FILL_HIGH_FRAC = 0.92
QP_STEP = 1
# Need this many clean underfill ticks before lowering QP (avoid flap).
QP_SHARPEN_STREAK = 2


@dataclass
class BitrateConfig:
    init_kbps: int = DEFAULT_INIT_KBPS
    min_kbps: int = DEFAULT_MIN_KBPS
    max_kbps: int = DEFAULT_MAX_KBPS
    qp_min: int = DEFAULT_QP_MIN
    qp_max: int = DEFAULT_QP_MAX
    # Prefer QVBR (Samsung "CAC mode in VBR") with maxrate = target.
    rc_mode: str = "QVBR"


@dataclass
class BitrateState:
    kbps: int
    qp: int = DEFAULT_QP
    good_streak: int = 0
    bad_streak: int = 0
    underfill_streak: int = 0


@dataclass(frozen=True)
class LinkSignals:
    """Inputs analogous to Samsung RR + NetworkStall."""

    # Proxy for RTCP fraction lost (0.0–1.0). None = unknown.
    loss_fraction: Optional[float] = None
    # NetworkStall / encoder backlog proxy.
    stalled: bool = False
    # Sender video fps; sag ⇒ treat like stall when usable.
    video_fps: Optional[float] = None
    # Optional capacity (iw MCS Mbps) to clamp max.
    link_capacity_mbps: Optional[float] = None
    # Measured air / delivery rate (Mbps). Used for QVBR QP fill.
    air_tx_mbps: Optional[float] = None
    # iw tx_failed delta this tick.
    tx_failed_delta: Optional[int] = None
    retry_percent: Optional[float] = None


@dataclass(frozen=True)
class BitrateDecision:
    kbps: int
    qp: int
    changed: bool
    reason: str
    qp_min: int
    qp_max: int
    rc_mode: str
    good_streak: int
    bad_streak: int
    underfill_streak: int


def config_for_band(
    *,
    band_5ghz: bool,
    width_mhz: Optional[float] = None,
) -> BitrateConfig:
    """Samsung BitrateController is constructed with WLAN_BAND."""
    if band_5ghz:
        # 5 GHz HT20 MCS≈72 → allow up to ~75% (~54 Mbps). Wider GO higher.
        wide = (width_mhz or 20) >= 40
        return BitrateConfig(
            init_kbps=40_000 if wide else 36_000,
            min_kbps=2_000,
            max_kbps=80_000 if wide else 55_000,
            qp_min=12,
            qp_max=40,
            rc_mode="QVBR",
        )
    # 2.4 GHz / 20 MHz MCC — keep a lower ceiling (interference / MCC tax).
    return BitrateConfig(
        init_kbps=12_000,
        min_kbps=1_500,
        max_kbps=22_000,
        qp_min=14,
        qp_max=42,
        rc_mode="QVBR",
    )


def desired_fill_mbps(kbps: int, capacity_mbps: Optional[float]) -> float:
    """Air setpoint: min(encode target, 75% of MCS)."""
    target = max(0.0, float(kbps) / 1000.0)
    if capacity_mbps is not None and capacity_mbps > 1.0:
        return min(target, float(capacity_mbps) * CAPACITY_FRAC)
    return target


def _clamp_kbps(kbps: int, cfg: BitrateConfig, capacity_mbps: Optional[float]) -> int:
    lo, hi = int(cfg.min_kbps), int(cfg.max_kbps)
    if capacity_mbps is not None and capacity_mbps > 1.0:
        hi = min(hi, int(capacity_mbps * 1000 * CAPACITY_FRAC))
        hi = max(hi, lo)
    return max(lo, min(hi, int(kbps)))


def _clamp_qp(qp: int, cfg: BitrateConfig) -> int:
    return max(int(cfg.qp_min), min(int(cfg.qp_max), int(qp)))


def _estimate_loss_fraction(sig: LinkSignals) -> Optional[float]:
    if sig.loss_fraction is not None:
        return max(0.0, min(1.0, float(sig.loss_fraction)))
    if sig.tx_failed_delta is not None and sig.tx_failed_delta > 0:
        return min(0.2, 0.02 * float(sig.tx_failed_delta))
    if sig.retry_percent is not None and sig.retry_percent >= 0.0:
        return min(1.0, float(sig.retry_percent) / 100.0)
    return None


def _is_stall(sig: LinkSignals) -> bool:
    if sig.stalled:
        return True
    if sig.video_fps is not None:
        v = float(sig.video_fps)
        if 5.0 <= v < 28.0:
            return True
    return False


def _decision(
    *,
    kbps: int,
    qp: int,
    changed: bool,
    reason: str,
    cfg: BitrateConfig,
    good: int,
    bad: int,
    underfill: int,
) -> BitrateDecision:
    return BitrateDecision(
        kbps=kbps,
        qp=_clamp_qp(qp, cfg),
        changed=changed,
        reason=reason,
        qp_min=cfg.qp_min,
        qp_max=cfg.qp_max,
        rc_mode=cfg.rc_mode,
        good_streak=good,
        bad_streak=bad,
        underfill_streak=underfill,
    )


def decide(
    state: BitrateState,
    sig: LinkSignals,
    cfg: BitrateConfig,
    *,
    cooldown_ok: bool,
) -> BitrateDecision:
    """One control tick — bitrate target + QVBR QP fill toward 75% MCS."""
    kbps = _clamp_kbps(state.kbps, cfg, sig.link_capacity_mbps)
    qp = _clamp_qp(getattr(state, "qp", DEFAULT_QP), cfg)
    good = int(state.good_streak)
    bad = int(state.bad_streak)
    underfill = int(getattr(state, "underfill_streak", 0) or 0)
    loss = _estimate_loss_fraction(sig)
    stall = _is_stall(sig)
    fill = desired_fill_mbps(kbps, sig.link_capacity_mbps)
    air = sig.air_tx_mbps

    if not cooldown_ok:
        return _decision(
            kbps=kbps,
            qp=qp,
            changed=False,
            reason="hold:cooldown",
            cfg=cfg,
            good=good,
            bad=bad,
            underfill=underfill,
        )

    # NetworkStall — cut bitrate and soften QP.
    if stall:
        nxt = _clamp_kbps(int(kbps * STALL_DECREASE_FACTOR), cfg, sig.link_capacity_mbps)
        nqp = _clamp_qp(qp + QP_STEP, cfg)
        return _decision(
            kbps=nxt,
            qp=nqp,
            changed=(nxt != kbps) or (nqp != qp),
            reason=f"net_stall:fps={sig.video_fps}",
            cfg=cfg,
            good=0,
            bad=bad + 1,
            underfill=0,
        )

    # LOSS case — cut bitrate; soften QP slightly.
    if loss is not None and loss >= LOSS_FRACTION_HIGH:
        nxt = _clamp_kbps(int(kbps * DECREASE_FACTOR), cfg, sig.link_capacity_mbps)
        nqp = _clamp_qp(qp + QP_STEP, cfg)
        return _decision(
            kbps=nxt,
            qp=nqp,
            changed=(nxt != kbps) or (nqp != qp),
            reason=f"loss:{loss:.3f}",
            cfg=cfg,
            good=0,
            bad=bad + 1,
            underfill=0,
        )

    # Over-fill vs 75% of *MCS capacity* (not the possibly-demoted target) —
    # soften QP before the air hits the Wi‑Fi cliff.
    cap = sig.link_capacity_mbps
    if (
        air is not None
        and cap is not None
        and float(cap) > 1.0
        and float(air) >= float(cap) * CAPACITY_FRAC * FILL_HIGH_FRAC
        and qp < cfg.qp_max
    ):
        nqp = _clamp_qp(qp + QP_STEP, cfg)
        return _decision(
            kbps=kbps,
            qp=nqp,
            changed=nqp != qp,
            reason=(
                f"fill_high:air={air:.1f}>="
                f"{FILL_HIGH_FRAC:.0%}×{CAPACITY_FRAC:.0%}×{cap:.1f}"
            ),
            cfg=cfg,
            good=0,
            bad=0,
            underfill=0,
        )

    clear = loss is None or loss < LOSS_FRACTION_CLEAR
    fps_ok = sig.video_fps is None or float(sig.video_fps) >= 29.0
    if not (clear and fps_ok):
        return _decision(
            kbps=kbps,
            qp=qp,
            changed=False,
            reason="hold:marginal",
            cfg=cfg,
            good=0,
            bad=bad,
            underfill=0,
        )

    good += 1

    # 1) Climb bitrate target while below the 75% MCS clamp.
    at_ceiling = kbps >= _clamp_kbps(10**9, cfg, sig.link_capacity_mbps)
    if good >= 3 and not at_ceiling:
        nxt = _clamp_kbps(int(kbps * INCREASE_FACTOR), cfg, sig.link_capacity_mbps)
        if nxt != kbps:
            return _decision(
                kbps=nxt,
                qp=qp,
                changed=True,
                reason="no_loss_increase",
                cfg=cfg,
                good=0,
                bad=0,
                underfill=0,
            )

    # 2) Target already at ceiling but QVBR under-fills → sharpen QP so more
    #    bits are spent (this is the Smart View gap on VAAPI QVBR).
    if (
        air is not None
        and fill >= 8.0
        and float(air) < fill * FILL_LOW_FRAC
        and qp > cfg.qp_min
    ):
        underfill += 1
        if underfill >= QP_SHARPEN_STREAK and good >= 2:
            nqp = _clamp_qp(qp - QP_STEP, cfg)
            return _decision(
                kbps=kbps,
                qp=nqp,
                changed=nqp != qp,
                reason=(
                    f"qp_fill:air={air:.1f}<{FILL_LOW_FRAC:.0%}×{fill:.1f}"
                    f" qp{qp}→{nqp}"
                ),
                cfg=cfg,
                good=0,
                bad=0,
                underfill=0,
            )
        return _decision(
            kbps=kbps,
            qp=qp,
            changed=False,
            reason=f"hold:underfill={underfill}/{QP_SHARPEN_STREAK}",
            cfg=cfg,
            good=good,
            bad=0,
            underfill=underfill,
        )

    if good < 3:
        return _decision(
            kbps=kbps,
            qp=qp,
            changed=False,
            reason=f"hold:good_streak={good}/3",
            cfg=cfg,
            good=good,
            bad=0,
            underfill=0,
        )

    return _decision(
        kbps=kbps,
        qp=qp,
        changed=False,
        reason="hold:at_ceiling_filled",
        cfg=cfg,
        good=good,
        bad=0,
        underfill=0,
    )


def kbps_to_ffmpeg(kbps: int) -> str:
    if kbps >= 1000:
        mbps = kbps / 1000.0
        if mbps >= 10:
            return f"{mbps:.0f}M"
        return f"{mbps:.1f}M"
    return f"{int(kbps)}k"


def ffmpeg_to_kbps(text: str) -> int:
    import re

    m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*([KkMm])?\s*$", str(text or ""))
    if not m:
        return DEFAULT_INIT_KBPS
    v = float(m.group(1))
    u = (m.group(2) or "M").upper()
    if u == "K":
        return max(1, int(v))
    return max(1, int(v * 1000))
