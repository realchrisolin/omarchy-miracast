#!/usr/bin/env python3
"""Per-render-engine encode quality tiers (veryhigh | high | medium | low).

Render engine (dmabuf | vaapi | cpu) and quality tier are orthogonal. The same
tier label resolves to different knobs per engine — DMA-BUF high is not the
same recipe as VAAPI-pipe high.

## 20 MHz Miracast P2P bitrate budget

**high / medium / low** are capped for a **20 MHz** Wi‑Fi Direct link (common
when P2P stays SCC next to a busy 5 GHz STA, or on 2.4‑only sinks):

  - 802.11n/ac 20 MHz 1SS PHY ceiling ≈ 65–72 Mbps
  - Practical UDP throughput in good indoor tests ≈ 50–60 Mbps (1SS);
    optimistic MAC efficiency ≈ 50–65% of PHY → ~35–45 Mbps
  - Miracast also needs LPCM audio (~1.5 Mbps), MPEG‑TS/RTP overhead,
    retries, and often STA coexistence on the same channel
  - On-device: uncapped DMA-BUF **CQP** peaked ~22–28 Mbps and produced
    visible TV blockiness/corruption on 20 MHz

Reliable maximize-quality envelope (1080p30 + ~1.5 Mbps LPCM):

  Research / industry anchors:
  - Intel WiDi adaptive BRC: 1080p range **3–20 Mbps** (good channel → high end)
  - Fixed WiDi deployments often used **~8 Mbps**; lower-bandwidth mode for congestion
  - 802.11n 20 MHz practical UDP ≫ 20 Mbps in clean tests; on-device CQP peaks of
    **22–28 Mbps** still corrupted the TV → keep video **≤ ~14–16 Mbps**
  - ~0.15–0.2 bits/pixel × 1080p30 ≈ **9–12 Mbps** “high” desktop/screen content

  On-device failure modes:
  - Uncapped **CQP qp=16** → good looks, peaks **22–28 Mbps** (corrupts on 20 MHz)
  - **CBR/QVBR/VBR/AVBR on DMA-BUF wf-recorder** → starved **~0.5–3 Mbps** even with
    bit_rate/bufsize set on the codec context (Intel BRC + DMA-BUF path)

  Presets:
  - **DMA-BUF**: **CQP** (only RC that fills bits on this path). High uses **qp=18**
    quality=2 + i_qfactor=1.3 + CHP/high — 5 min soak peaked ~19 Mbps (under ~22
    corruption). Avoid quality=1 with low qp (old 22–28 Mbps TV corruption).
  - **Very High** (5 GHz+ only): intentionally above the 2.4 / 20 MHz budget
    (DMA-BUF qp=16 peaks that corrupted 20 MHz; pipe CBR ~28M). Gate on band —
    not an arbitrary UI lock.
  - **VAAPI pipe named tiers** (high/medium/low): **hard CBR** (ffmpeg BRC
    works). Do **not** use QVBR here — on-device Intel QVBR undershot to ~5–7
    Mbps. High is **CBR 15M + quality=3 + async=1 + VBV 1.0**. quality=1 hung
    the pipe; clamp floor is quality≥3.
  - **VAAPI pipe Best (Dynamic)**: **CQP QP 1–51** with pipe-safe knobs
    (async=1, quality≥3). Named CBR was a one-step Mbps nudge under oversat
    (JScreenFix); CQP demotes actually soften the encode. Same QP index map
    as DMA-BUF Best.
  - **CPU**: CBR ladder (libx264); independent of VAAPI pipe hang constraints.

  ``bitrate`` / ``vaapiBitrate`` are the pipe named-tier CBR target (=maxrate);
  informational under Best CQP. ``vbvMultiplier`` is CBR HRD depth in seconds
  of bitrate.
"""

from __future__ import annotations

import math
from typing import Any, Optional

ENGINES = ("dmabuf", "vaapi", "cpu")
# Concrete encode ladders the dynamic controller steps through.
TIERS = ("veryhigh", "high", "medium", "low")
# 20 MHz / 2.4-safe concrete tiers (bitrate labels ≤ ~16M).
TIERS_20MHZ = ("high", "medium", "low")
# User-facing profile ids (includes Best (Dynamic) sentinel).
PROFILES = ("best", "veryhigh", "high", "medium", "low")
# When encodeProfile=best, first apply / default effective tier (named map).
BEST_START_TIER = "high"
# Very High wire rate exceeds 2.4 GHz / 20 MHz Miracast headroom.
TIER_REQUIRES_5GHZ = frozenset({"veryhigh"})

# Fine Best (Dynamic) ladder — sharp (0) → soft (N). Named presets stay separate;
# Best walks these steps with SIGUSR1 rebind (not live encoder ABR).
# No Mbps hard-cap: retries / stalls / delivery failures (and CBR under-delivery)
# demote when the air cannot hold a sharp step. DMA-BUF covers CQP QP 1–51.

BEST_CQP_QP_MIN = 1
BEST_CQP_QP_MAX = 51


def _cqp_companion(qp: int) -> tuple[str, str, str]:
    """quality, i_qfactor, informational bitrate label for a CQP qp."""
    if qp <= 10:
        quality, iqf = "1", "1.1"
    elif qp <= 14:
        quality, iqf = "1", "1.15"
    elif qp <= 16:
        quality, iqf = "2", "1.2"
    elif qp <= 18:
        quality, iqf = "2", "1.3"
    elif qp <= 20:
        quality, iqf = "3", "1.2"
    elif qp <= 23:
        quality, iqf = "4", "1.1"
    elif qp <= 28:
        quality, iqf = "5", "1.05"
    elif qp <= 35:
        quality, iqf = "6", "1.0"
    else:
        quality, iqf = "7", "1.0"
    # Rough label anchored at qp18 ≈ 14 Mbps (informational under CQP).
    mbps = max(1.0, min(120.0, 14.0 * (2.0 ** ((18 - qp) / 6.0))))
    if mbps >= 10:
        br = f"{int(round(mbps))}M"
    else:
        br = f"{mbps:.1f}".rstrip("0").rstrip(".") + "M"
    return quality, iqf, br


def _build_cqp_best_steps(*, async_depth: int, quality_floor: int = 1) -> list[dict[str, Any]]:
    """CQP QP 1–51 ladder. Pipe uses async=1 and quality_floor=3 (hang floor)."""
    steps: list[dict[str, Any]] = []
    floor = max(1, int(quality_floor))
    for qp in range(BEST_CQP_QP_MIN, BEST_CQP_QP_MAX + 1):
        quality, iqf, br = _cqp_companion(qp)
        if int(quality) < floor:
            quality = str(floor)
        steps.append(
            {
                "vaapiRcMode": "CQP",
                "vaapiQp": qp,
                "vaapiQuality": quality,
                "vaapiIQfactor": iqf,
                "vaapiBitrate": br,
                "bitrate": br,
                "vaapiGop": 30,
                "vaapiAsyncDepth": async_depth,
            }
        )
    return steps


def _build_dmabuf_best_steps() -> list[dict[str, Any]]:
    return _build_cqp_best_steps(async_depth=2, quality_floor=1)


def _build_pipe_cqp_best_steps() -> list[dict[str, Any]]:
    """VAAPI-pipe Best: CQP with pipe hang floor (async=1, quality≥3)."""
    return _build_cqp_best_steps(async_depth=1, quality_floor=3)


def _build_cbr_best_steps(
    *,
    async_depth: int,
    quality_sharp: str,
    quality_soft: str,
) -> list[dict[str, Any]]:
    """Dense CBR ladder (Mbps), sharp → soft. Pipe keeps quality ≥ 3."""
    # 48→3 Mbps in 1 Mbps steps — negotiation finds what the link holds.
    rates = list(range(48, 2, -1))  # 48 .. 3
    steps: list[dict[str, Any]] = []
    for mbps in rates:
        # Map bitrate to a nominal qp for status/display only.
        # qp18 ≈ 15M, lower qp ↔ higher rate.
        qp = int(round(18 - 6 * math.log2(max(mbps, 1) / 14.0)))
        qp = max(BEST_CQP_QP_MIN, min(BEST_CQP_QP_MAX, qp))
        if mbps >= 28:
            quality = quality_sharp
        elif mbps >= 12:
            quality = "3" if async_depth == 1 else quality_sharp
        elif mbps >= 8:
            quality = "4" if async_depth == 1 else "4"
        else:
            quality = quality_soft
        if async_depth == 1 and int(quality) < 3:
            quality = "3"  # pipe hang floor
        br = f"{mbps}M"
        step: dict[str, Any] = {
            "vaapiRcMode": "CBR",
            "vaapiQp": qp,
            "vaapiQuality": quality,
            "vaapiBitrate": br,
            "bitrate": br,
            "vaapiGop": 30,
            "vaapiAsyncDepth": async_depth,
        }
        if async_depth == 1:
            step["vbvMultiplier"] = "1.0" if mbps >= 15 else ("0.75" if mbps >= 10 else "0.5")
        steps.append(step)
    return steps


BEST_STEPS: dict[str, list[dict[str, Any]]] = {
    "dmabuf": _build_dmabuf_best_steps(),
    # AOSP WifiDisplay uses Constant bitrate (OMX_Video_ControlRateConstant),
    # not unbounded CQP. Keep a CBR Mbps ladder for Best on pipe; named tiers
    # below stay CBR as well.
    "vaapi": _build_cbr_best_steps(async_depth=1, quality_sharp="3", quality_soft="4"),
    "cpu": _build_cbr_best_steps(async_depth=2, quality_sharp="1", quality_soft="6"),
}


def best_step_for_qp(qp: int) -> int:
    """DMA-BUF step index for an H.264 QP (1–51)."""
    q = max(BEST_CQP_QP_MIN, min(BEST_CQP_QP_MAX, int(qp)))
    return q - BEST_CQP_QP_MIN


def best_step_for_bitrate_mbps(engine: str, mbps: float) -> int:
    """Nearest CBR Best step for a target Mbps (vaapi/cpu)."""
    eng = engine if engine in BEST_STEPS else "vaapi"
    steps = BEST_STEPS[eng]
    best_i, best_err = 0, 1e9
    for i, step in enumerate(steps):
        raw = str(step.get("bitrate") or "0").strip().lower().rstrip("m")
        try:
            v = float(raw)
        except ValueError:
            continue
        err = abs(v - mbps)
        if err < best_err:
            best_err, best_i = err, i
    return best_i


# Named preset → Best step (qp for dmabuf/vaapi Best; Mbps for cpu).
BEST_TIER_TO_STEP: dict[str, dict[str, int]] = {
    "dmabuf": {
        "veryhigh": best_step_for_qp(16),
        "high": best_step_for_qp(18),
        "medium": best_step_for_qp(20),
        "low": best_step_for_qp(23),
    },
    "vaapi": {
        # AOSP default ~5 Mbps; high named preset historically 15M CBR.
        "veryhigh": best_step_for_bitrate_mbps("vaapi", 10),
        "high": best_step_for_bitrate_mbps("vaapi", 5),
        "medium": best_step_for_bitrate_mbps("vaapi", 4),
        "low": best_step_for_bitrate_mbps("vaapi", 3),
    },
    "cpu": {
        "veryhigh": best_step_for_bitrate_mbps("cpu", 28),
        "high": best_step_for_bitrate_mbps("cpu", 16),
        "medium": best_step_for_bitrate_mbps("cpu", 10),
        "low": best_step_for_bitrate_mbps("cpu", 6),
    },
}

# Knobs written into settings.json / exported to FluxCast.
# bitrate = stream/target; vaapiBitrate = peak (FLUXCAST_WFD_VAAPI_BITRATE).
# For CBR both are equal so HRD maxrate == target.
PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "dmabuf": {
        # CQP only — bitrate RC undershoots ~0.5–3 Mbps on DMA-BUF+Intel.
        # qp=18/quality=1 looked sharp but peaks hit ~22–28 Mbps and corrupted
        # the TV on 20 MHz. High uses qp=18/quality=2 + i_qfactor=1.3: sharper
        # than qp=19, with quality/i_qfactor holding peaks under the old blow-up
        # (5 min soak at qp=19 peaked ~13.6 Mbps — headroom to try 18).
        # Very High reuses the sharper qp=16 envelope that blew past 2.4/20 MHz
        # (~22–28 Mbps peaks) — only safe on 5 GHz+ P2P.
        "veryhigh": {
            "vaapiRcMode": "CQP",
            "vaapiQp": 16,
            "vaapiQuality": "2",
            "vaapiIQfactor": "1.2",
            "vaapiBitrate": "28M",
            "bitrate": "28M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "high": {
            "vaapiRcMode": "CQP",
            "vaapiQp": 18,
            "vaapiQuality": "2",
            "vaapiIQfactor": "1.3",
            "vaapiBitrate": "14M",
            "bitrate": "14M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "medium": {
            "vaapiRcMode": "CQP",
            "vaapiQp": 20,
            "vaapiQuality": "4",
            "vaapiIQfactor": "1.1",
            "vaapiBitrate": "10M",
            "bitrate": "10M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "low": {
            "vaapiRcMode": "CQP",
            "vaapiQp": 23,
            "vaapiQuality": "6",
            "vaapiIQfactor": "1.0",
            "vaapiBitrate": "6M",
            "bitrate": "6M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
    },
    "vaapi": {
        # Pipe — hard CBR only (QVBR undershot ~5–7 Mbps on Intel+hotyeah).
        # quality=1 / async>1 hung the pipe (bufs_size → audio-only). CHP/high
        # profile is negotiated separately when the sink offers it.
        #
        # Ladder (1080p30 + LPCM):
        #   veryhigh ≈ 5 GHz+ only (above 2.4/20 MHz corruption line)
        #   high     ≈ max quality under soft 20 MHz envelope
        #   medium   ≈ stable daily driver with RF headroom
        #   low      ≈ poor RF / battery / busy channel
        "veryhigh": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 16,
            "vaapiQuality": "3",
            "vaapiBitrate": "28M",
            "bitrate": "28M",
            "vbvMultiplier": "1.0",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 1,
        },
        "high": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 18,
            "vaapiQuality": "3",
            "vaapiBitrate": "15M",
            "bitrate": "15M",
            "vbvMultiplier": "1.0",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 1,
        },
        "medium": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 20,
            "vaapiQuality": "3",
            "vaapiBitrate": "12M",
            "bitrate": "12M",
            "vbvMultiplier": "0.75",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 1,
        },
        "low": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 22,
            "vaapiQuality": "4",
            "vaapiBitrate": "8M",
            "bitrate": "8M",
            "vbvMultiplier": "0.5",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 1,
        },
    },
    "cpu": {
        "veryhigh": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 16,
            "vaapiQuality": "1",
            "vaapiBitrate": "28M",
            "bitrate": "28M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "high": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 18,
            "vaapiQuality": "1",
            "vaapiBitrate": "16M",
            "bitrate": "16M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "medium": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 20,
            "vaapiQuality": "4",
            "vaapiBitrate": "10M",
            "bitrate": "10M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "low": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 22,
            "vaapiQuality": "6",
            "vaapiBitrate": "6M",
            "bitrate": "6M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
    },
}


def normalize_engine(engine: Optional[str]) -> str:
    e = str(engine or "dmabuf").strip().lower()
    return e if e in ENGINES else "dmabuf"


def _alias_tier(raw: str) -> str:
    t = str(raw or "").strip().lower().replace("-", "").replace("_", "")
    if t in ("veryhigh", "vh"):
        return "veryhigh"
    return t


def normalize_tier(tier: Optional[str]) -> str:
    """Concrete ladder step (never ``best``)."""
    t = _alias_tier(tier or "medium")
    if t == "best":
        return BEST_START_TIER
    return t if t in TIERS else "medium"


def normalize_profile(profile: Optional[str]) -> str:
    """User-facing profile id including ``best``."""
    p = _alias_tier(profile or "medium")
    if p == "best":
        return "best"
    return p if p in PROFILES else "medium"


def tier_requires_5ghz(tier: Optional[str]) -> bool:
    """True when the concrete tier's wire rate exceeds 2.4 GHz / 20 MHz budget."""
    return normalize_tier(tier) in TIER_REQUIRES_5GHZ


def freq_allows_veryhigh(freq_mhz: Any) -> bool:
    """5 GHz or 6 GHz P2P — enough airtime for Very High bitrate."""
    try:
        f = float(freq_mhz)
    except (TypeError, ValueError):
        return False
    return f >= 4900.0


def best_steps_for(engine: Optional[str]) -> list[dict[str, Any]]:
    return BEST_STEPS[normalize_engine(engine)]


def best_step_count(engine: Optional[str]) -> int:
    return len(best_steps_for(engine))


def min_best_step(engine: Optional[str], *, band_5ghz: bool = True) -> int:
    """Sharpest allowed step (always 0 — link health demotes; no band hard-cap)."""
    return 0


def clamp_best_step(
    engine: Optional[str], step: int, *, band_5ghz: bool = True
) -> int:
    steps = best_steps_for(engine)
    hi = len(steps) - 1
    try:
        s = int(step)
    except (TypeError, ValueError):
        s = best_start_step(engine)
    return max(0, min(hi, s))


def step_target_mbps(engine: Optional[str], step: int) -> float:
    """Nominal bitrate label for CBR ratio checks."""
    knobs = resolve_best_step(engine, step, band_5ghz=True)
    raw = str(knobs.get("bitrate") or "12M").strip().lower()
    if raw.endswith("m"):
        return float(raw[:-1])
    if raw.endswith("k"):
        return float(raw[:-1]) / 1000.0
    try:
        return float(raw)
    except ValueError:
        return 12.0


def step_display_label(engine: Optional[str], step: int) -> str:
    """Short UI label: qp18 (CQP) or 15M (CBR)."""
    knobs = resolve_best_step(engine, step, band_5ghz=True)
    rc = str(knobs.get("vaapiRcMode") or "").upper()
    if rc == "CQP":
        return f"qp{knobs.get('vaapiQp', '?')}"
    br = str(knobs.get("bitrate") or "").strip()
    return br or f"step{step}"


def nearest_named_tier(engine: Optional[str], step: int) -> str:
    """Map fine step → legacy named tier for encodeProfileEffective."""
    eng = normalize_engine(engine)
    s = clamp_best_step(eng, step)
    table = BEST_TIER_TO_STEP.get(eng) or BEST_TIER_TO_STEP["dmabuf"]
    # Pick the sharpest named tier whose step index is ≤ current (or softest).
    named = "low"
    for name in ("veryhigh", "high", "medium", "low"):
        if s <= int(table[name]):
            return name
        named = name
    return named


def named_tier_to_step(tier: Optional[str], engine: Optional[str] = None) -> int:
    eng = normalize_engine(engine)
    t = normalize_tier(tier)
    table = BEST_TIER_TO_STEP.get(eng) or BEST_TIER_TO_STEP["dmabuf"]
    if t in table:
        return int(table[t])
    return int(table.get(BEST_START_TIER, min_best_step(eng, band_5ghz=False)))


def best_start_step(engine: Optional[str] = None) -> int:
    return named_tier_to_step(BEST_START_TIER, engine)


def resolve_best_step(
    engine: Optional[str],
    step: int,
    *,
    band_5ghz: bool = False,
) -> dict[str, Any]:
    """Knobs for Best fine-step (requires_5ghz key stripped)."""
    eng = normalize_engine(engine)
    idx = clamp_best_step(eng, step, band_5ghz=band_5ghz)
    raw = dict(best_steps_for(eng)[idx])
    raw.pop("requires_5ghz", None)
    return raw


def resolve_preset(
    engine: Optional[str], tier: Optional[str]
) -> dict[str, Any]:
    """Return a copy of knobs for engine×tier (concrete named tier only)."""
    eng = normalize_engine(engine)
    ti = normalize_tier(tier)
    return dict(PRESETS[eng][ti])


def apply_to_settings(
    data: dict[str, Any],
    *,
    tier: Optional[str] = None,
    engine: Optional[str] = None,
    effective_tier: Optional[str] = None,
    effective_step: Optional[int] = None,
    band_5ghz: Optional[bool] = None,
) -> dict[str, Any]:
    """Mutate settings dict: set encodeProfile + knobs for current/new engine.

    ``tier`` may be ``best`` (stores profile=best). For Best, prefer
    ``effective_step`` (fine ladder); ``effective_tier`` maps to a step.
    ``band_5ghz`` clamps sharp steps when False/None-with-unknown → treat as
    False only when explicitly False; None means allow stored/requested step
    but still clamp via ``band_5ghz or False`` for safety when applying Best.
    """
    if not isinstance(data, dict):
        raise TypeError("settings must be a dict")
    eng = normalize_engine(engine if engine is not None else data.get("captureEncode"))
    requested = tier if tier is not None else data.get("encodeProfile")
    profile = normalize_profile(requested)
    # True when caller explicitly selected Best (not engine retarget / effective step).
    fresh_best = tier is not None and normalize_profile(tier) == "best"

    if profile == "best":
        if effective_step is not None:
            step = int(effective_step)
        elif effective_tier is not None:
            step = named_tier_to_step(effective_tier, eng)
        elif fresh_best:
            # Clicking Best starts at High-equivalent; don't inherit a demoted step.
            step = best_start_step(eng)
            data["encodeBestDemoteCount"] = 0
            data.pop("encodeBestSignalFloorDbm", None)
            data.pop("encodeBestClimbFloorStep", None)
        elif data.get("encodeProfileEffectiveStep") is not None:
            try:
                step = int(data.get("encodeProfileEffectiveStep"))
            except (TypeError, ValueError):
                step = named_tier_to_step(data.get("encodeProfileEffective"), eng)
        else:
            step = best_start_step(eng)
        # Best is not band-clamped; ABR demotes when the link cannot hold a step.
        step = clamp_best_step(eng, step, band_5ghz=True)
        knobs = resolve_best_step(eng, step, band_5ghz=True)
        ti = nearest_named_tier(eng, step)
        data["encodeProfileEffectiveStep"] = step
        data["encodeProfileEffectiveLabel"] = step_display_label(eng, step)
    else:
        ti = normalize_tier(profile)
        knobs = resolve_preset(eng, ti)
        data.pop("encodeProfileEffectiveStep", None)
        data.pop("encodeProfileEffectiveLabel", None)
        # Lock Best only applies while Best is selected.
        data["encodeBestLocked"] = False

    data["captureEncode"] = eng
    data["encodeProfile"] = profile
    data["encodeProfileEffective"] = ti
    for key, value in knobs.items():
        data[key] = value
    out = {
        "captureEncode": eng,
        "encodeProfile": profile,
        "encodeProfileEffective": ti,
        **knobs,
    }
    if profile == "best":
        out["encodeProfileEffectiveStep"] = data["encodeProfileEffectiveStep"]
        out["encodeProfileEffectiveLabel"] = data["encodeProfileEffectiveLabel"]
        out["encodeBestLocked"] = bool(data.get("encodeBestLocked"))
    else:
        out["encodeBestLocked"] = False
    return out


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", default="dmabuf", choices=ENGINES)
    ap.add_argument("--tier", default="medium", choices=PROFILES)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    knobs = resolve_preset(args.engine, args.tier)
    payload = {"captureEncode": args.engine, "encodeProfile": args.tier, **knobs}
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"{args.engine} / {args.tier}")
        for k, v in knobs.items():
            print(f"  {k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
