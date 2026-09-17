#!/usr/bin/env python3
"""Best (Dynamic) fine-step encode ladder decisions.

Pure functions — no I/O. The link watcher feeds metrics and receives the next
step index (0 = sharpest) plus a reason.

Reliability rules:
  - Start near High (qp18 / step 17 on DMA-BUF)
  - Climb freely when clean — **no hard QP floor**
  - **Primary control: air-TX headroom.** Keep measured air throughput under
    ``HEADROOM_FRAC`` (15%) of iw station ``tx bitrate``. Soft demotes are
    *our* ladder reacting to that ratio — not a Wi‑Fi “unhealthy” flag.
  - Soften QP (raise step) until air stays under the headroom target; climb
    slowly only while under target
  - **Backup:** TX failures / UDP / video fps cliff / true stalls still demote
    (fps/extreme catch what headroom has not yet)
  - Mild MCC retries alone do not demote
  - After **3 demotes**, block climbs until demotes decay (long clean streak)
    or signal recovers (≥10 dB). Demotes 1–2 still allow climbing
  - **Soft climb ceiling:** any demote sets a floor at the demote target —
    cannot re-climb sharper until demotes fully clear
  - Cliff climbs (≈qp≤10) need a **longer clean streak** per step
  - Mild demotes respect apply-cooldown (each apply can restart capture);
    extreme/severe may demote without waiting
  - Never recommend engine switches

CQP: soft *low* TX with healthy fps must not demote (quiet UI). Air above
the headroom target *does* demote even when fps still looks fine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Mild MCC retries (~1%) alone must not yank QP.
RETRY_PCT_DOWN = 5.0
RETRY_PCT_UP = 2.0
RETRIES_PER_SEC_DOWN = 80.0
THROUGHPUT_STALL_MBPS = 3.0
SIGNAL_WEAK_DBM = -70.0
# Was 6 dB — too easy to wipe settle on MCC signal bounce and re-climb the cliff.
SIGNAL_RECOVER_DB = 10.0
# Sender health fps below this while we expect ~30 = delivery stress / oversat.
VIDEO_FPS_PAIN = 28.0
# Tighter in cliff zone — catch soft fps before tx-failed/UDP.
VIDEO_FPS_PAIN_CLIFF = 29.0
# Harder sag: multi-step demote immediately (corruption / pre-UDP).
VIDEO_FPS_CRITICAL = 24.0
# Extreme oversat (JScreenFix-class) — jump toward safe harbor.
# Real drops showed fps≈22.4 right before TX death; keep the threshold
# just above that so we harbor before UDP fails, not after.
VIDEO_FPS_EXTREME = 23.0
VIDEO_FPS_HEALTHY = 20.0  # used with soft-TX stall gating elsewhere
# Block climbs while content is still hot (marginal fps / retries).
VIDEO_FPS_HOT = 29.5
RETRY_PCT_HOT = 1.5
RETRY_PCT_EXTREME = 8.0
TX_FAILED_EXTREME = 5

# Air TX vs iw station tx bitrate (MCS). Keep payload under this fraction.
HEADROOM_FRAC = 0.15
# Multi-step when air exceeds this multiple of the headroom target.
HEADROOM_SEVERE_MULT = 2.0   # ≥30% of capacity
HEADROOM_EXTREME_MULT = 3.0  # ≥45% of capacity
# One big CQP jump per apply (each apply = SIGUSR1). Tiny +3/+5 jumps left
# JScreenFix at ~40 Mbps across qp24→qp29 and stacked rebinds killed the link.
HEADROOM_JUMP_MILD = 4
HEADROOM_JUMP_SEVERE = 10
HEADROOM_JUMP_EXTREME = 18

# Mild retry%/signal bad needs 2 ticks; fps / tx-fail / stall demote on first tick.
BAD_STREAK_DOWN = 2
BAD_STREAK_DOWN_FAST = 1
GOOD_STREAK_UP = 6
# After signal_recover, require a longer clean run before climbing again.
GOOD_STREAK_UP_AFTER_RECOVER = 12
# DMA fine ladder: step 9 ≈ qp10. At/above this sharpness, demote harder.
CLIFF_STEP_MAX = 9
# Extra clean ticks required before each climb while already in the cliff.
GOOD_STREAK_UP_CLIFF = 12
# Sustained clean ticks to decay one demote (MCC often can't hit +10 dB).
DEMOTE_DECAY_STREAK = GOOD_STREAK_UP_AFTER_RECOVER
MAX_DEMOTES_BEFORE_NO_CLIMB = 3
SEVERE_RETRY_PCT = 15.0
SEVERE_DOWN_STEPS = 3
EXTREME_DOWN_STEPS = 5

_BITRATE_RC = frozenset({"cbr", "vbr", "qvbr", "avbr"})

DEFAULT_START_STEP = 17
# Soft landing after extreme oversat (≈qp18 on DMA fine ladder).
SAFE_HARBOR_STEP = DEFAULT_START_STEP
DEFAULT_MAX_STEP = 50


@dataclass(frozen=True)
class LinkSample:
    # Measured air TX (from p2p iface byte counters), Mbps.
    throughput_mbps: Optional[float] = None
    # iw station "tx bitrate" (MCS capacity), Mbps.
    link_capacity_mbps: Optional[float] = None
    retry_percent: Optional[float] = None
    retries_per_sec: Optional[float] = None
    signal_dbm: Optional[float] = None
    stalled: bool = False
    delivery_fail: bool = False
    tx_failed_delta: Optional[int] = None
    # Sender-health video fps; sag below VIDEO_FPS_PAIN precedes stream death.
    video_fps: Optional[float] = None


@dataclass(frozen=True)
class LadderState:
    step: int = DEFAULT_START_STEP
    bad_streak: int = 0
    good_streak: int = 0
    demote_count: int = 0
    signal_floor_dbm: Optional[float] = None
    # Raised after signal_recover so climb doesn't immediately re-enter the cliff.
    climb_good_need: int = GOOD_STREAK_UP
    # Soft ceiling: lowest step index (sharpest QP) allowed until demotes clear.
    # Higher step = softer. Climb must keep step >= climb_floor_step.
    climb_floor_step: Optional[int] = None


@dataclass(frozen=True)
class LadderDecision:
    step: int
    changed: bool
    reason: str
    bad_streak: int
    good_streak: int
    demote_count: int = 0
    signal_floor_dbm: Optional[float] = None
    climb_good_need: int = GOOD_STREAK_UP
    climb_floor_step: Optional[int] = None


def uses_throughput_ratio(rc_mode: Optional[str]) -> bool:
    return str(rc_mode or "").strip().lower() in _BITRATE_RC


def clamp_step(step: int, *, min_step: int, max_step: int) -> int:
    return max(int(min_step), min(int(max_step), int(step)))


def _fps_usable(sample: LinkSample) -> bool:
    """Ignore non-positive and low fps (Sender health counter reset after rebind).

    Real oversat sag on this path is ~20–27. Rebind noise has shown up as 0.2–10.6
    fps and must not severe-demote.
    """
    return sample.video_fps is not None and sample.video_fps >= 15.0


def _in_cliff(step: int) -> bool:
    return int(step) <= CLIFF_STEP_MAX


def _fps_pain(sample: LinkSample, *, cliff: bool = False) -> bool:
    threshold = VIDEO_FPS_PAIN_CLIFF if cliff else VIDEO_FPS_PAIN
    return _fps_usable(sample) and sample.video_fps < threshold


def _fps_critical(sample: LinkSample) -> bool:
    return _fps_usable(sample) and sample.video_fps < VIDEO_FPS_CRITICAL


def _fps_extreme(sample: LinkSample) -> bool:
    return _fps_usable(sample) and sample.video_fps < VIDEO_FPS_EXTREME


def headroom_target_mbps(sample: LinkSample) -> Optional[float]:
    """Max allowed air TX = HEADROOM_FRAC × iw tx bitrate."""
    cap = sample.link_capacity_mbps
    if cap is None or cap <= 0:
        return None
    return float(HEADROOM_FRAC) * float(cap)


def _headroom_ratio(sample: LinkSample) -> Optional[float]:
    """air / headroom_target; >1 means over budget. None if unknown/quiet."""
    target = headroom_target_mbps(sample)
    air = sample.throughput_mbps
    if target is None or air is None:
        return None
    # Quiet desktop must not look like a headroom violation.
    if air < THROUGHPUT_STALL_MBPS:
        return 0.0
    if target <= 0:
        return None
    return float(air) / float(target)


def _headroom_over(sample: LinkSample) -> bool:
    r = _headroom_ratio(sample)
    return r is not None and r > 1.0


def _headroom_severe(sample: LinkSample) -> bool:
    r = _headroom_ratio(sample)
    return r is not None and r >= HEADROOM_SEVERE_MULT


def _headroom_extreme(sample: LinkSample) -> bool:
    r = _headroom_ratio(sample)
    return r is not None and r >= HEADROOM_EXTREME_MULT


def headroom_down_steps(sample: LinkSample) -> int:
    """How many ladder steps to soften in one apply when over headroom."""
    r = _headroom_ratio(sample)
    if r is None or r <= 1.0:
        return 0
    if r >= HEADROOM_EXTREME_MULT:
        return int(HEADROOM_JUMP_EXTREME)
    if r >= HEADROOM_SEVERE_MULT:
        return int(HEADROOM_JUMP_SEVERE)
    return int(HEADROOM_JUMP_MILD)


def _content_extreme(sample: LinkSample) -> bool:
    """JScreenFix-class oversat: demote hard, keep the session."""
    if _headroom_extreme(sample):
        return True
    if _fps_extreme(sample):
        return True
    if sample.retry_percent is not None and sample.retry_percent >= RETRY_PCT_EXTREME:
        return True
    if sample.tx_failed_delta is not None and sample.tx_failed_delta >= TX_FAILED_EXTREME:
        return True
    return False


def _content_hot(sample: LinkSample) -> bool:
    """Marginal link/content — allow hold/demote but block climbs."""
    if _content_extreme(sample) or _fps_critical(sample):
        return True
    if _headroom_over(sample):
        return True
    if _fps_usable(sample) and sample.video_fps < VIDEO_FPS_HOT:
        return True
    if sample.retry_percent is not None and sample.retry_percent >= RETRY_PCT_HOT:
        return True
    if sample.tx_failed_delta is not None and sample.tx_failed_delta > 0:
        return True
    return False


def _is_fast_bad(sample: LinkSample, bad_reason: str) -> bool:
    """True when one bad tick should demote (don't wait for streak=2)."""
    if sample.stalled or sample.delivery_fail:
        return True
    if sample.tx_failed_delta is not None and sample.tx_failed_delta > 0:
        return True
    if bad_reason.startswith("fps"):
        return True
    if bad_reason.startswith("headroom"):
        return True
    # Extreme oversat (retry%/tx flood) — demote on the first tick.
    if _content_extreme(sample):
        return True
    return False


def _is_bad(
    sample: LinkSample,
    *,
    rc_mode: Optional[str],
    target_mbps: Optional[float],
    band_5ghz: bool = False,
    width_mhz: Optional[float] = None,
    cliff: bool = False,
) -> tuple[bool, str]:
    del band_5ghz, width_mhz
    if sample.stalled:
        return True, "stall"
    # Prefer hard failures — air can't deliver.
    if sample.delivery_fail or (
        sample.tx_failed_delta is not None and sample.tx_failed_delta > 0
    ):
        delta = sample.tx_failed_delta if sample.tx_failed_delta is not None else "?"
        return True, f"tx_failedΔ{delta}"
    # Primary: keep air under 15% of iw tx bitrate (headroom).
    if _headroom_over(sample):
        target = headroom_target_mbps(sample) or 0.0
        air = sample.throughput_mbps or 0.0
        cap = sample.link_capacity_mbps or 0.0
        return (
            True,
            f"headroom{air:.1f}>{HEADROOM_FRAC:.0%}×{cap:.0f}(={target:.1f})",
        )
    # Backup: encoder/sender fps sags before iw counters move.
    # Ignore near-zero fps (Sender health counter reset after capture rebind).
    if _fps_critical(sample):
        return True, f"fps{sample.video_fps:.1f}<{VIDEO_FPS_CRITICAL:.0f}"
    pain_thr = VIDEO_FPS_PAIN_CLIFF if cliff else VIDEO_FPS_PAIN
    if _fps_pain(sample, cliff=cliff):
        return True, f"fps{sample.video_fps:.1f}<{pain_thr:.0f}"
    # Severe retries alone.
    if sample.retry_percent is not None and sample.retry_percent >= RETRY_PCT_DOWN:
        return True, f"retry%{sample.retry_percent:.2f}"
    if sample.retries_per_sec is not None and sample.retries_per_sec >= RETRIES_PER_SEC_DOWN:
        return True, f"retries/s{sample.retries_per_sec:.1f}"
    # CBR only: soft vs configured target.
    if uses_throughput_ratio(rc_mode) and target_mbps is not None:
        if (
            sample.throughput_mbps is not None
            and sample.throughput_mbps >= THROUGHPUT_STALL_MBPS
            and sample.throughput_mbps < 0.5 * target_mbps
        ):
            return True, f"throughput{sample.throughput_mbps:.1f}<0.5×{target_mbps:.0f}"
    if sample.signal_dbm is not None and sample.signal_dbm <= SIGNAL_WEAK_DBM:
        return True, f"signal{sample.signal_dbm:.0f}"
    return False, ""


def _is_good(
    sample: LinkSample,
    *,
    rc_mode: Optional[str],
    target_mbps: Optional[float],
    band_5ghz: bool = False,
    width_mhz: Optional[float] = None,
    cliff: bool = False,
) -> bool:
    del band_5ghz, width_mhz
    if sample.stalled or sample.delivery_fail:
        return False
    if sample.tx_failed_delta is not None and sample.tx_failed_delta > 0:
        return False
    # Must be under headroom budget to climb.
    if _headroom_over(sample):
        return False
    if _fps_pain(sample, cliff=cliff):
        return False
    if sample.retry_percent is not None and sample.retry_percent >= RETRY_PCT_UP:
        return False
    if sample.retries_per_sec is not None and sample.retries_per_sec >= RETRIES_PER_SEC_DOWN * 0.5:
        return False
    if sample.signal_dbm is not None and sample.signal_dbm <= SIGNAL_WEAK_DBM:
        return False
    if uses_throughput_ratio(rc_mode):
        if target_mbps is None or sample.throughput_mbps is None:
            return False
        if sample.throughput_mbps < max(THROUGHPUT_STALL_MBPS, 0.7 * target_mbps):
            return False
    else:
        # CQP: only require video still flowing when TX is known.
        if (
            sample.throughput_mbps is not None
            and sample.throughput_mbps < THROUGHPUT_STALL_MBPS
        ):
            return False
    return True


def _track_signal_floor(
    demotes: int,
    floor: Optional[float],
    signal_dbm: Optional[float],
) -> tuple[int, Optional[float], Optional[str]]:
    if signal_dbm is None:
        return demotes, floor, None
    new_floor = floor
    if demotes > 0:
        if new_floor is None or signal_dbm < new_floor:
            new_floor = float(signal_dbm)
    if (
        demotes >= MAX_DEMOTES_BEFORE_NO_CLIMB
        and new_floor is not None
        and signal_dbm >= new_floor + SIGNAL_RECOVER_DB
    ):
        return (
            0,
            None,
            f"signal_recover:{signal_dbm:.0f}>{new_floor:.0f}+{SIGNAL_RECOVER_DB:.0f}",
        )
    return demotes, new_floor, None


def decide(
    state: LadderState,
    sample: LinkSample,
    *,
    cooldown_ok: bool,
    rc_mode: Optional[str] = "cqp",
    min_step: int = 0,
    max_step: int = DEFAULT_MAX_STEP,
    target_mbps: Optional[float] = None,
    band_5ghz: bool = False,
    width_mhz: Optional[float] = None,
) -> LadderDecision:
    """Return next fine-step decision."""
    demotes = max(0, int(state.demote_count or 0))
    sig_floor = state.signal_floor_dbm
    climb_need = max(GOOD_STREAK_UP, int(state.climb_good_need or GOOD_STREAK_UP))
    climb_floor = state.climb_floor_step
    if climb_floor is not None:
        climb_floor = int(climb_floor)
    demotes, sig_floor, recovered = _track_signal_floor(
        demotes, sig_floor, sample.signal_dbm
    )
    if recovered:
        # Wipe settle + soft ceiling; demand a longer clean run before climbing.
        climb_need = GOOD_STREAK_UP_AFTER_RECOVER
        climb_floor = None

    step = clamp_step(state.step, min_step=min_step, max_step=max_step)
    cliff = _in_cliff(step)

    if int(state.step) < min_step or int(state.step) > max_step:
        return LadderDecision(
            step=step,
            changed=step != int(state.step),
            reason=("clamp" if not recovered else f"clamp;{recovered}"),
            bad_streak=0,
            good_streak=0,
            demote_count=demotes,
            signal_floor_dbm=sig_floor,
            climb_good_need=climb_need,
            climb_floor_step=climb_floor,
        )

    bad, bad_reason = _is_bad(
        sample,
        rc_mode=rc_mode,
        target_mbps=target_mbps,
        band_5ghz=band_5ghz,
        width_mhz=width_mhz,
        cliff=cliff,
    )
    good = _is_good(
        sample,
        rc_mode=rc_mode,
        target_mbps=target_mbps,
        band_5ghz=band_5ghz,
        width_mhz=width_mhz,
        cliff=cliff,
    )

    if recovered:
        bad_streak = 1 if bad else 0
        good_streak = 1 if good else 0
    else:
        bad_streak = state.bad_streak + 1 if bad else 0
        good_streak = state.good_streak + 1 if good else 0

    # Cliff / critical / extreme / headroom blowout: multi-step.
    extreme = _content_extreme(sample)
    severe = bool(
        extreme
        or sample.stalled
        or _headroom_severe(sample)
        or _fps_critical(sample)
        or (sample.tx_failed_delta is not None and sample.tx_failed_delta >= 3)
        or (
            cliff
            and sample.tx_failed_delta is not None
            and sample.tx_failed_delta >= 1
        )
        or (cliff and _fps_pain(sample, cliff=True))
        or (
            sample.retry_percent is not None
            and sample.retry_percent >= SEVERE_RETRY_PCT
        )
    )
    streak_need = (
        BAD_STREAK_DOWN_FAST if (bad and _is_fast_bad(sample, bad_reason)) else BAD_STREAK_DOWN
    )

    if bad and bad_streak >= streak_need:
        # Mild fps demotes must respect cooldown — each apply restarts capture
        # (SIGUSR1). Stacking demotes every tick caused UDP EBADF death.
        # Extreme/severe may demote immediately (prefer ugly over drop).
        if not severe and not cooldown_ok:
            return LadderDecision(
                step=step,
                changed=False,
                reason=f"hold:down_cooldown ({bad_reason})",
                bad_streak=bad_streak,
                good_streak=0,
                demote_count=demotes,
                signal_floor_dbm=sig_floor,
                climb_good_need=climb_need,
                climb_floor_step=climb_floor,
            )
        if bad_reason.startswith("headroom"):
            # One large CQP jump sized to overshoot — avoid demote storms.
            jump = max(1, headroom_down_steps(sample))
            nxt = clamp_step(step + jump, min_step=min_step, max_step=max_step)
        elif extreme:
            # Hard multi-step demote under JScreenFix-class load.
            # Relative steps only — no absolute QP/step floor (no hard limits).
            nxt = clamp_step(
                step + EXTREME_DOWN_STEPS,
                min_step=min_step,
                max_step=max_step,
            )
        elif severe:
            nxt = clamp_step(
                step + SEVERE_DOWN_STEPS, min_step=min_step, max_step=max_step
            )
            # Leaving the cliff under severe stress is worth an extra push —
            # soft +3 from qp6 still lands in the cliff and re-dies.
            if cliff and nxt <= CLIFF_STEP_MAX:
                nxt = clamp_step(
                    CLIFF_STEP_MAX + 1, min_step=min_step, max_step=max_step
                )
        else:
            nxt = clamp_step(step + 1, min_step=min_step, max_step=max_step)
        drop = max(0, nxt - step)
        if nxt != step:
            new_demotes = demotes + 1
            new_sig_floor = sig_floor
            if sample.signal_dbm is not None:
                if new_sig_floor is None or sample.signal_dbm < new_sig_floor:
                    new_sig_floor = float(sample.signal_dbm)
            # Soft ceiling: do not re-climb sharper than this demote target.
            new_floor = nxt if climb_floor is None else max(int(climb_floor), nxt)
            # Extreme demotes force settle so we do not re-climb into the fire.
            if extreme:
                new_demotes = max(new_demotes, MAX_DEMOTES_BEFORE_NO_CLIMB)
            reason = f"down:{bad_reason}"
            if extreme:
                reason += ";extreme_harbor"
            if drop > 1:
                reason += f"+{drop - 1}"
            reason += f";demotes={new_demotes}/{MAX_DEMOTES_BEFORE_NO_CLIMB}"
            return LadderDecision(
                step=nxt,
                changed=True,
                reason=reason,
                bad_streak=0,
                good_streak=0,
                demote_count=new_demotes,
                signal_floor_dbm=new_sig_floor,
                # After demote, require a long clean run before any climb.
                climb_good_need=GOOD_STREAK_UP_AFTER_RECOVER,
                climb_floor_step=new_floor,
            )

    if not cooldown_ok:
        return LadderDecision(
            step=step,
            changed=False,
            reason=("cooldown" if not recovered else f"cooldown;{recovered}"),
            bad_streak=bad_streak,
            good_streak=good_streak,
            demote_count=demotes,
            signal_floor_dbm=sig_floor,
            climb_good_need=climb_need,
            climb_floor_step=climb_floor,
        )

    # Settle only after the demote limit (default 3). Demotes 1–2 still climb
    # (but soft ceiling blocks sharper than the last demote target).
    if demotes >= MAX_DEMOTES_BEFORE_NO_CLIMB:
        if good and good_streak >= DEMOTE_DECAY_STREAK:
            new_demotes = demotes - 1
            new_floor = climb_floor if new_demotes > 0 else None
            return LadderDecision(
                step=step,
                changed=False,
                reason=f"hold:demote_decay {demotes}->{new_demotes}",
                bad_streak=0,
                good_streak=0,
                demote_count=new_demotes,
                signal_floor_dbm=sig_floor if new_demotes > 0 else None,
                climb_good_need=climb_need,
                climb_floor_step=new_floor,
            )
        return LadderDecision(
            step=step,
            changed=False,
            reason=f"hold:no_climb_after_{demotes}_demotes",
            bad_streak=bad_streak,
            good_streak=good_streak,
            demote_count=demotes,
            signal_floor_dbm=sig_floor,
            climb_good_need=climb_need,
            climb_floor_step=climb_floor,
        )

    # Hot content (marginal fps / retries / fresh tx fails): hold, never climb.
    # JScreenFix-class load often oscillates — climbing back into it drops UDP.
    if _content_hot(sample):
        return LadderDecision(
            step=step,
            changed=False,
            reason="hold:content_hot",
            bad_streak=bad_streak,
            good_streak=0,
            demote_count=demotes,
            signal_floor_dbm=sig_floor,
            climb_good_need=climb_need,
            climb_floor_step=climb_floor,
        )

    # Cliff: each step toward sharper QP needs a longer clean streak.
    if _in_cliff(step - 1 if step > min_step else step):
        climb_need = max(climb_need, GOOD_STREAK_UP_CLIFF)

    if good and good_streak >= climb_need:
        nxt = clamp_step(step - 1, min_step=min_step, max_step=max_step)
        if climb_floor is not None and nxt < int(climb_floor):
            return LadderDecision(
                step=step,
                changed=False,
                reason=f"hold:climb_floor_step={climb_floor}",
                bad_streak=bad_streak,
                good_streak=good_streak,
                demote_count=demotes,
                signal_floor_dbm=sig_floor,
                climb_good_need=climb_need,
                climb_floor_step=climb_floor,
            )
        if nxt != step:
            reason = "up:sustained_clean"
            if recovered:
                reason = f"up:sustained_clean;{recovered}"
            elif climb_need > GOOD_STREAK_UP:
                reason = f"up:sustained_clean;post_recover×{climb_need}"
            # Pace: after a cliff step, keep the long streak requirement.
            next_need = (
                GOOD_STREAK_UP_CLIFF if _in_cliff(nxt) else GOOD_STREAK_UP
            )
            return LadderDecision(
                step=nxt,
                changed=True,
                reason=reason,
                bad_streak=0,
                good_streak=0,
                demote_count=demotes,
                signal_floor_dbm=sig_floor,
                climb_good_need=next_need,
                climb_floor_step=climb_floor,
            )

    return LadderDecision(
        step=step,
        changed=False,
        reason=(recovered or "hold"),
        bad_streak=bad_streak,
        good_streak=good_streak,
        demote_count=demotes,
        signal_floor_dbm=sig_floor,
        climb_good_need=climb_need,
        climb_floor_step=climb_floor,
    )


TIERS = ("low", "medium", "high", "veryhigh")


def normalize_effective(tier: Optional[str]) -> str:
    t = str(tier or "high").strip().lower().replace("-", "").replace("_", "")
    if t in ("veryhigh", "vh"):
        return "veryhigh"
    return t if t in TIERS else "high"
