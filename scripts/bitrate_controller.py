"""Samsung Smart View–style BitrateController (reverse-engineered).

Derived from ``libremotedisplay_wfd.so`` symbols/strings
(``vendor/samsung/.../BitrateController.cpp``):

* ``updateConfiguration(min, max, init, minQP, maxQP)``
* ``decideUDPBitrate(RTCP RR)`` / ``calculateFractionLost``
* ``increaseUDPBitrate`` / ``decreaseUDPBitrate``
* ``updateBitrateByNetStall``
* ``LOSS case`` vs ``No loss case`` logging
* Codec: MediaCodec ``bitrate`` + ``bitrate-mode`` + QP range
  (QTI: ``vendor.qti-ext-enc-qp-range.*``, ``content-adaptive-mode``)
* ``Turn on CAC mode in VBR`` → quality-defined VBR with a hard ceiling

Without sink RTCP (hotyeah advertised RTCP port 0), loss is proxied by
iw ``tx_failed`` / retry%% and stall by sender fps sag — same *roles* as
Samsung's RR + NetworkStall inputs.

Pure functions / small state object — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Defaults tuned for Miracast 1080p60 on 5 GHz HT20 (~72 Mbps MCS).
# Samsung strings: updateConfiguration min/max/init + minQP/maxQP.
DEFAULT_INIT_KBPS = 20_000
DEFAULT_MIN_KBPS = 1_500
DEFAULT_MAX_KBPS = 32_000
DEFAULT_QP_MIN = 18
DEFAULT_QP_MAX = 42

# Fraction-lost style thresholds (RTCP RR proxy).
LOSS_FRACTION_HIGH = 0.02  # ≥2% → decrease (Samsung "LOSS case")
LOSS_FRACTION_CLEAR = 0.005  # <0.5% and healthy → may increase

# Step factors (Samsung logs absolute bps changes; ratios match AOSP-ish feel).
DECREASE_FACTOR = 0.80
INCREASE_FACTOR = 1.10
STALL_DECREASE_FACTOR = 0.65  # NetworkStall path is more aggressive

# Encode target vs iw MCS capacity — ~half the PHY leaves room for Wi‑Fi overhead.
CAPACITY_FRAC = 0.50


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
    good_streak: int = 0
    bad_streak: int = 0


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
    # iw tx_failed delta this tick.
    tx_failed_delta: Optional[int] = None
    retry_percent: Optional[float] = None


@dataclass(frozen=True)
class BitrateDecision:
    kbps: int
    changed: bool
    reason: str
    qp_min: int
    qp_max: int
    rc_mode: str
    good_streak: int
    bad_streak: int


def config_for_band(
    *,
    band_5ghz: bool,
    width_mhz: Optional[float] = None,
) -> BitrateConfig:
    """Samsung BitrateController is constructed with WLAN_BAND."""
    if band_5ghz:
        # 5 GHz HT20 MCS≈72 → target up to ~half (~32–36 Mbps). Wider GO higher.
        wide = (width_mhz or 20) >= 40
        return BitrateConfig(
            init_kbps=22_000 if wide else 20_000,
            min_kbps=2_000,
            max_kbps=40_000 if wide else 32_000,
            qp_min=16,
            qp_max=40,
            rc_mode="QVBR",
        )
    # 2.4 GHz / 20 MHz MCC — keep a lower ceiling (interference / MCC tax).
    return BitrateConfig(
        init_kbps=12_000,
        min_kbps=1_500,
        max_kbps=18_000,
        qp_min=18,
        qp_max=42,
        rc_mode="QVBR",
    )


def _clamp_kbps(kbps: int, cfg: BitrateConfig, capacity_mbps: Optional[float]) -> int:
    lo, hi = int(cfg.min_kbps), int(cfg.max_kbps)
    if capacity_mbps is not None and capacity_mbps > 1.0:
        # Cap encode near half of iw MCS (Wi‑Fi/RTP overhead + ABR headroom).
        hi = min(hi, int(capacity_mbps * 1000 * CAPACITY_FRAC))
        hi = max(hi, lo)
    return max(lo, min(hi, int(kbps)))


def _estimate_loss_fraction(sig: LinkSignals) -> Optional[float]:
    if sig.loss_fraction is not None:
        return max(0.0, min(1.0, float(sig.loss_fraction)))
    # Proxies when RTCP RR is unavailable (our sink often advertises RTCP port 0).
    if sig.tx_failed_delta is not None and sig.tx_failed_delta > 0:
        # Map small failure bursts into a high loss fraction.
        return min(0.2, 0.02 * float(sig.tx_failed_delta))
    if sig.retry_percent is not None and sig.retry_percent >= 0.0:
        # iw retry% is not identical to RTP fraction lost, but same role.
        return min(1.0, float(sig.retry_percent) / 100.0)
    return None


def _is_stall(sig: LinkSignals) -> bool:
    if sig.stalled:
        return True
    if sig.video_fps is not None and 5.0 <= float(sig.video_fps) < 28.0:
        return True
    return False


def decide(
    state: BitrateState,
    sig: LinkSignals,
    cfg: BitrateConfig,
    *,
    cooldown_ok: bool,
) -> BitrateDecision:
    """One control tick — Samsung-style increase/decrease under min/max/QP."""
    kbps = _clamp_kbps(state.kbps, cfg, sig.link_capacity_mbps)
    good = int(state.good_streak)
    bad = int(state.bad_streak)
    loss = _estimate_loss_fraction(sig)
    stall = _is_stall(sig)

    if not cooldown_ok:
        return BitrateDecision(
            kbps=kbps,
            changed=False,
            reason="hold:cooldown",
            qp_min=cfg.qp_min,
            qp_max=cfg.qp_max,
            rc_mode=cfg.rc_mode,
            good_streak=good,
            bad_streak=bad,
        )

    # NetworkStall path (updateBitrateByNetStall) — aggressive decrease.
    if stall:
        nxt = _clamp_kbps(int(kbps * STALL_DECREASE_FACTOR), cfg, sig.link_capacity_mbps)
        return BitrateDecision(
            kbps=nxt,
            changed=nxt != kbps,
            reason=f"net_stall:fps={sig.video_fps}",
            qp_min=cfg.qp_min,
            qp_max=cfg.qp_max,
            rc_mode=cfg.rc_mode,
            good_streak=0,
            bad_streak=bad + 1,
        )

    # LOSS case (decideUDPBitrate from RR fraction lost).
    if loss is not None and loss >= LOSS_FRACTION_HIGH:
        nxt = _clamp_kbps(int(kbps * DECREASE_FACTOR), cfg, sig.link_capacity_mbps)
        return BitrateDecision(
            kbps=nxt,
            changed=nxt != kbps,
            reason=f"loss:{loss:.3f}",
            qp_min=cfg.qp_min,
            qp_max=cfg.qp_max,
            rc_mode=cfg.rc_mode,
            good_streak=0,
            bad_streak=bad + 1,
        )

    # No-loss case — may increase after a short healthy streak.
    clear = loss is None or loss < LOSS_FRACTION_CLEAR
    fps_ok = sig.video_fps is None or float(sig.video_fps) >= 29.0
    if clear and fps_ok:
        good += 1
        # Require a few clean ticks before climb (avoid flap).
        if good >= 3:
            nxt = _clamp_kbps(int(kbps * INCREASE_FACTOR), cfg, sig.link_capacity_mbps)
            return BitrateDecision(
                kbps=nxt,
                changed=nxt != kbps,
                reason="no_loss_increase",
                qp_min=cfg.qp_min,
                qp_max=cfg.qp_max,
                rc_mode=cfg.rc_mode,
                good_streak=0 if nxt != kbps else good,
                bad_streak=0,
            )
        return BitrateDecision(
            kbps=kbps,
            changed=False,
            reason=f"hold:good_streak={good}/3",
            qp_min=cfg.qp_min,
            qp_max=cfg.qp_max,
            rc_mode=cfg.rc_mode,
            good_streak=good,
            bad_streak=0,
        )

    return BitrateDecision(
        kbps=kbps,
        changed=False,
        reason="hold:marginal",
        qp_min=cfg.qp_min,
        qp_max=cfg.qp_max,
        rc_mode=cfg.rc_mode,
        good_streak=0,
        bad_streak=bad,
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
