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

Live Smart View dig (S23→hotyeah, 1080p, resolution=2)::

    BitrateController: init, min=2097152, init=8388608, max=14680064
    BitrateController: mMinQP=15, mMaxQP=44

(= 2 / 8 / 14 Mbps). Linux VAAPI cannot live-setParameters; we coalesce
SIGUSR1 applies. When target is at the Samsung max but air under-fills,
sharpen QP so QVBR spends more bits.

Without sink RTCP (hotyeah RTCP port 0), loss is proxied by iw tx_failed /
retry%% and stall by sender fps sag — same *roles* as Samsung RR + NetStall.

Pure functions / small state object — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Samsung BitrateController (live dig, resolution=2 / 1080p), values in kbps.
# bps: min=2097152, init=8388608, max=14680064; QP 15–44.
DEFAULT_INIT_KBPS = 8_192  # 8 * 1024
DEFAULT_MIN_KBPS = 2_048  # 2 * 1024
DEFAULT_MAX_KBPS = 14_336  # 14 * 1024
DEFAULT_QP_MIN = 15
DEFAULT_QP_MAX = 44
DEFAULT_QP = 22

# Fraction-lost style thresholds (RTCP RR proxy / iw retry%).
# Dig session ran fine with modest retries; 2% was too hair-trigger and
# demoted 14M→11M after a single rebind pause (loss:0.025).
LOSS_FRACTION_HIGH = 0.08  # ≥8% → decrease (Samsung "LOSS case")
LOSS_FRACTION_CLEAR = 0.02  # <2% and healthy → may increase
# Require this many consecutive loss ticks before cutting bitrate.
LOSS_STREAK_BEFORE_CUT = 2

# Step factors (Samsung logs absolute bps changes; ratios match AOSP-ish feel).
DECREASE_FACTOR = 0.80
INCREASE_FACTOR = 1.10
STALL_DECREASE_FACTOR = 0.65  # NetworkStall path is more aggressive

# Extra safety vs iw MCS (Samsung max ≪ MCS on HT20; rarely binds).
CAPACITY_FRAC = 0.75

# QVBR fill: when air TX is below this fraction of the desired setpoint
# (min(target, 75% MCS)), sharpen QP. Above this → hold / soften on pain.
FILL_LOW_FRAC = 0.55
FILL_HIGH_FRAC = 0.92
QP_STEP = 1
# Need this many clean underfill ticks before lowering QP (avoid flap).
QP_SHARPEN_STREAK = 2

# Apply coalescing — Samsung decides often; we only SIGUSR1-restart when the
# pending encode knobs moved enough (VAAPI cannot live-setParameters yet).
APPLY_MIN_QP_DELTA = 2
APPLY_MIN_KBPS_FRAC = 0.15
APPLY_MIN_INTERVAL_S = 45.0
# Smaller *bitrate* drifts may flush after this idle interval.
# QP±1 alone never flushes. 10% climbs (8.0→8.8) must not restart either —
# wait for a full 15% step or a long idle flush.
APPLY_FLUSH_INTERVAL_S = 120.0
APPLY_FLUSH_QP_DELTA = 2  # same as min; no single-step QP flush
APPLY_FLUSH_KBPS_FRAC = 0.15


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
    """Samsung BitrateController is constructed with WLAN_BAND.

    Dig only captured resolution=2 (1080p) on 5 GHz; use those exact
    min/init/max/QP. 2.4 GHz keeps a slightly lower max (no live table).
    ``width_mhz`` reserved for future WLAN_BAND tables.
    """
    del width_mhz  # only one Samsung resolution table captured so far
    if band_5ghz:
        return BitrateConfig(
            init_kbps=DEFAULT_INIT_KBPS,
            min_kbps=DEFAULT_MIN_KBPS,
            max_kbps=DEFAULT_MAX_KBPS,
            qp_min=DEFAULT_QP_MIN,
            qp_max=DEFAULT_QP_MAX,
            rc_mode="QVBR",
        )
    # 2.4 GHz — no dig table; keep under the 1080p 5 GHz max.
    return BitrateConfig(
        init_kbps=6_144,  # 6 Mbps
        min_kbps=DEFAULT_MIN_KBPS,
        max_kbps=10_240,  # 10 Mbps
        qp_min=DEFAULT_QP_MIN,
        qp_max=DEFAULT_QP_MAX,
        rc_mode="QVBR",
    )


def desired_fill_mbps(kbps: int, capacity_mbps: Optional[float]) -> float:
    """Air setpoint: min(encode target, CAPACITY_FRAC × MCS)."""
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
    """One control tick — bitrate target + QVBR QP fill toward encode setpoint."""
    kbps = _clamp_kbps(state.kbps, cfg, sig.link_capacity_mbps)
    qp = _clamp_qp(getattr(state, "qp", DEFAULT_QP), cfg)
    good = int(state.good_streak)
    bad = int(state.bad_streak)
    underfill = int(getattr(state, "underfill_streak", 0) or 0)
    loss = _estimate_loss_fraction(sig)
    stall = _is_stall(sig)
    fill = desired_fill_mbps(kbps, sig.link_capacity_mbps)
    # Sysfs / cumulative counters can spike across rebinds (e.g. 126 Mbps on a
    # 72 Mbps MCS). Cap air to MCS before fill_high / qp_fill decisions.
    air = sig.air_tx_mbps
    if air is not None and sig.link_capacity_mbps is not None:
        cap = float(sig.link_capacity_mbps)
        if cap > 1.0:
            air = min(float(air), cap)

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

    # LOSS case — cut bitrate only after sustained elevated loss. A single
    # 2–3% retry tick after SIGUSR1 must not yank Samsung max (14M) down.
    if loss is not None and loss >= LOSS_FRACTION_HIGH:
        bad_n = bad + 1
        if bad_n < LOSS_STREAK_BEFORE_CUT:
            return _decision(
                kbps=kbps,
                qp=qp,
                changed=False,
                reason=f"hold:loss_streak={bad_n}/{LOSS_STREAK_BEFORE_CUT}:{loss:.3f}",
                cfg=cfg,
                good=0,
                bad=bad_n,
                underfill=0,
            )
        nxt = _clamp_kbps(int(kbps * DECREASE_FACTOR), cfg, sig.link_capacity_mbps)
        nqp = qp
        if nxt < kbps:
            nqp = _clamp_qp(qp + QP_STEP, cfg)
            bad_n = 0
        elif bad_n >= LOSS_STREAK_BEFORE_CUT + 2 and qp < cfg.qp_max:
            nqp = _clamp_qp(qp + QP_STEP, cfg)
            bad_n = 0
        changed = (nxt != kbps) or (nqp != qp)
        return _decision(
            kbps=nxt,
            qp=nqp,
            changed=changed,
            reason=f"loss:{loss:.3f}",
            cfg=cfg,
            good=0,
            bad=bad_n,
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
    # Ignore TX≈0 / tiny samples (sysfs counter glitches) — those are not
    # "QVBR under-fill", and sharpening QP on them causes rebind storms.
    if (
        air is not None
        and float(air) >= 2.0
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


def is_urgent_apply(reason: str) -> bool:
    """Whether decide() reason may skip the long coalesce window.

    Sticky iw retry%% ``loss:`` is **not** urgent — that path caused ~17s
    SIGUSR1 pause storms (even qp-only at the 2M floor). Only hard encoder
    stalls may use the shorter urgent interval, and only with a real knob Δ.
    """
    return str(reason or "").startswith("net_stall:")


def should_apply_encode(
    *,
    applied_kbps: int,
    applied_qp: int,
    desired_kbps: int,
    desired_qp: int,
    reason: str,
    now: float,
    last_apply_ts: float,
) -> tuple[bool, str]:
    """Gate SIGUSR1 encode restarts while keeping Samsung-style decide() hot.

    Returns (apply_now, gate_reason).
    """
    dq = abs(int(desired_qp) - int(applied_qp))
    base = max(1, int(applied_kbps))
    dfrac = abs(int(desired_kbps) - int(applied_kbps)) / float(base)
    elapsed = float(now) - float(last_apply_ts or 0.0)
    first = not last_apply_ts or last_apply_ts <= 0.0

    # No restart if encode knobs are unchanged (loss at floor used to qp+1
    # every tick and still force urgent applies).
    if dq == 0 and dfrac < 0.01:
        return False, "hold:noop"

    # QP-only demotes never bypass coalesce — bitrate already at floor.
    if dfrac < 0.01 and dq < APPLY_MIN_QP_DELTA:
        return (
            False,
            f"hold:qp_only Δqp={dq} (need ≥{APPLY_MIN_QP_DELTA})",
        )

    # net_stall: shorter interval, but still require meaningful Δ + cooldown.
    if is_urgent_apply(reason):
        urgent_interval = min(30.0, APPLY_MIN_INTERVAL_S)
        if dq >= APPLY_MIN_QP_DELTA or dfrac >= APPLY_MIN_KBPS_FRAC:
            if first or elapsed >= urgent_interval:
                return True, f"urgent:{reason}"
            return (
                False,
                f"hold:urgent_cooldown {elapsed:.0f}/{urgent_interval:.0f}s",
            )
        return False, f"hold:urgent_small_delta Δqp={dq} Δkbps={dfrac:.0%}"

    # loss: / qp_fill / climbs — normal coalesce (no immediate restart).
    if dq >= APPLY_MIN_QP_DELTA or dfrac >= APPLY_MIN_KBPS_FRAC:
        if first or elapsed >= APPLY_MIN_INTERVAL_S:
            return (
                True,
                f"ready:Δqp={dq} Δkbps={dfrac:.0%} wait={elapsed:.0f}s",
            )
        return (
            False,
            f"hold:min_interval {elapsed:.0f}/{APPLY_MIN_INTERVAL_S:.0f}s "
            f"(Δqp={dq} Δkbps={dfrac:.0%})",
        )

    # Small drift — flush only after a longer idle so tiny qp±1 steps batch.
    if (dq >= APPLY_FLUSH_QP_DELTA or dfrac >= APPLY_FLUSH_KBPS_FRAC) and (
        first or elapsed >= APPLY_FLUSH_INTERVAL_S
    ):
        return (
            True,
            f"flush:Δqp={dq} Δkbps={dfrac:.0%} wait={elapsed:.0f}s",
        )

    return (
        False,
        f"hold:coalesce Δqp={dq} Δkbps={dfrac:.0%} wait={elapsed:.0f}s",
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
