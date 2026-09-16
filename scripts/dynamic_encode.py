#!/usr/bin/env python3
"""Best (Dynamic) fine-step encode ladder decisions.

Pure functions — no I/O. The link watcher feeds metrics and receives the next
step index (0 = sharpest) plus a reason.

Reliability rules:
  - Start near High (qp18 / step 17 on DMA-BUF)
  - Demote on real delivery pain (retries, stalls, TX failures, UDP errors)
  - After **3 demotes**, stop raising quality (settle)
  - On demote, raise a **climb floor** so we never return to the QP that failed
  - If **signal improves ≥6 dB**, clear demote settle and allow climbs again,
    but only relax the climb floor by **one** step (probe) — never wipe it
  - Cooldown throttles ups only; downs ignore cooldown
  - Never recommend engine switches

CQP: soft *low* TX must not demote (content×QP). High measured TX alone must
not demote either — only retries / failures / stalls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

RETRY_PCT_DOWN = 0.8
RETRY_PCT_UP = 0.4
RETRIES_PER_SEC_DOWN = 20.0
THROUGHPUT_STALL_MBPS = 3.0
SIGNAL_WEAK_DBM = -70.0
SIGNAL_RECOVER_DB = 6.0

BAD_STREAK_DOWN = 2
GOOD_STREAK_UP = 6
MAX_DEMOTES_BEFORE_NO_CLIMB = 3
SEVERE_RETRY_PCT = 5.0
SEVERE_DOWN_STEPS = 3

_BITRATE_RC = frozenset({"cbr", "vbr", "qvbr", "avbr"})

DEFAULT_START_STEP = 17
DEFAULT_MAX_STEP = 50


@dataclass(frozen=True)
class LinkSample:
    throughput_mbps: Optional[float] = None
    retry_percent: Optional[float] = None
    retries_per_sec: Optional[float] = None
    signal_dbm: Optional[float] = None
    stalled: bool = False
    delivery_fail: bool = False


@dataclass(frozen=True)
class LadderState:
    step: int = DEFAULT_START_STEP
    bad_streak: int = 0
    good_streak: int = 0
    demote_count: int = 0
    signal_floor_dbm: Optional[float] = None
    # Sharpest step index allowed (0 = unrestricted). Raised on demote.
    climb_floor_step: int = 0


@dataclass(frozen=True)
class LadderDecision:
    step: int
    changed: bool
    reason: str
    bad_streak: int
    good_streak: int
    demote_count: int = 0
    signal_floor_dbm: Optional[float] = None
    climb_floor_step: int = 0


def uses_throughput_ratio(rc_mode: Optional[str]) -> bool:
    return str(rc_mode or "").strip().lower() in _BITRATE_RC


def clamp_step(step: int, *, min_step: int, max_step: int) -> int:
    return max(int(min_step), min(int(max_step), int(step)))


def _is_bad(
    sample: LinkSample,
    *,
    rc_mode: Optional[str],
    target_mbps: Optional[float],
    band_5ghz: bool = False,
    width_mhz: Optional[float] = None,
) -> tuple[bool, str]:
    del band_5ghz, width_mhz
    if sample.stalled:
        return True, "stall"
    if sample.delivery_fail:
        return True, "delivery_fail"
    if sample.retry_percent is not None and sample.retry_percent >= RETRY_PCT_DOWN:
        return True, f"retry%{sample.retry_percent:.2f}"
    if sample.retries_per_sec is not None and sample.retries_per_sec >= RETRIES_PER_SEC_DOWN:
        return True, f"retries/s{sample.retries_per_sec:.1f}"
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
) -> bool:
    del band_5ghz, width_mhz
    if sample.stalled or sample.delivery_fail:
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
    """Update demote settle vs signal. Returns (demotes, signal_floor, recover|None)."""
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
    climb_floor = max(int(min_step), int(state.climb_floor_step or 0))
    demotes, sig_floor, recovered = _track_signal_floor(
        demotes, sig_floor, sample.signal_dbm
    )

    # Signal recover re-opens climbs but only relaxes the pain floor by one step.
    if recovered:
        if climb_floor > min_step:
            climb_floor = max(min_step, climb_floor - 1)
        else:
            # No prior floor: don't climb sharper than where we are now.
            climb_floor = max(min_step, int(state.step))

    effective_min = max(min_step, climb_floor)
    step = clamp_step(state.step, min_step=effective_min, max_step=max_step)

    if int(state.step) < effective_min or int(state.step) > max_step:
        return LadderDecision(
            step=step,
            changed=step != int(state.step),
            reason=("clamp" if not recovered else f"clamp;{recovered};floor={climb_floor}"),
            bad_streak=0,
            good_streak=0,
            demote_count=demotes,
            signal_floor_dbm=sig_floor,
            climb_floor_step=climb_floor,
        )

    bad, bad_reason = _is_bad(
        sample,
        rc_mode=rc_mode,
        target_mbps=target_mbps,
        band_5ghz=band_5ghz,
        width_mhz=width_mhz,
    )
    good = _is_good(
        sample,
        rc_mode=rc_mode,
        target_mbps=target_mbps,
        band_5ghz=band_5ghz,
        width_mhz=width_mhz,
    )

    if recovered:
        bad_streak = 1 if bad else 0
        good_streak = 1 if good else 0
    else:
        bad_streak = state.bad_streak + 1 if bad else 0
        good_streak = state.good_streak + 1 if good else 0

    severe = bool(
        sample.stalled
        or sample.delivery_fail
        or (
            sample.retry_percent is not None
            and sample.retry_percent >= SEVERE_RETRY_PCT
        )
    )

    if bad and bad_streak >= BAD_STREAK_DOWN:
        drop = SEVERE_DOWN_STEPS if severe else 1
        nxt = clamp_step(step + drop, min_step=min_step, max_step=max_step)
        if nxt != step:
            new_demotes = demotes + 1
            new_sig_floor = sig_floor
            if sample.signal_dbm is not None:
                if new_sig_floor is None or sample.signal_dbm < new_sig_floor:
                    new_sig_floor = float(sample.signal_dbm)
            # Painful sharp step was ``step`` — never climb back to it.
            new_climb_floor = max(climb_floor, step + 1, min_step)
            new_climb_floor = min(new_climb_floor, max_step)
            reason = f"down:{bad_reason}"
            if drop > 1:
                reason += f"+{drop - 1}"
            reason += f";demotes={new_demotes}/{MAX_DEMOTES_BEFORE_NO_CLIMB}"
            if new_climb_floor > climb_floor:
                reason += f";floor={new_climb_floor}"
            return LadderDecision(
                step=nxt,
                changed=True,
                reason=reason,
                bad_streak=0,
                good_streak=0,
                demote_count=new_demotes,
                signal_floor_dbm=new_sig_floor,
                climb_floor_step=new_climb_floor,
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
            climb_floor_step=climb_floor,
        )

    if demotes >= MAX_DEMOTES_BEFORE_NO_CLIMB:
        return LadderDecision(
            step=step,
            changed=False,
            reason=f"hold:no_climb_after_{demotes}_demotes",
            bad_streak=bad_streak,
            good_streak=good_streak,
            demote_count=demotes,
            signal_floor_dbm=sig_floor,
            climb_floor_step=climb_floor,
        )

    if good and good_streak >= GOOD_STREAK_UP:
        nxt = clamp_step(step - 1, min_step=effective_min, max_step=max_step)
        if nxt != step:
            reason = "up:sustained_clean"
            if recovered:
                reason = f"up:probe;{recovered};floor={climb_floor}"
            return LadderDecision(
                step=nxt,
                changed=True,
                reason=reason,
                bad_streak=0,
                good_streak=0,
                demote_count=demotes,
                signal_floor_dbm=sig_floor,
                climb_floor_step=climb_floor,
            )
        if climb_floor > min_step and step <= climb_floor:
            return LadderDecision(
                step=step,
                changed=False,
                reason=f"hold:floor={climb_floor}",
                bad_streak=bad_streak,
                good_streak=good_streak,
                demote_count=demotes,
                signal_floor_dbm=sig_floor,
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
        climb_floor_step=climb_floor,
    )


TIERS = ("low", "medium", "high", "veryhigh")


def normalize_effective(tier: Optional[str]) -> str:
    t = str(tier or "high").strip().lower().replace("-", "").replace("_", "")
    if t in ("veryhigh", "vh"):
        return "veryhigh"
    return t if t in TIERS else "high"
