#!/usr/bin/env python3
"""Encoding strategy: Smart View QVBR (default) vs Performance CQP.

Orthogonal to RENDER ENGINE (`captureEncode`) and PRESET QUALITY (`encodeProfile`).

* ``smartview`` — QVBR + BitrateController ABR; prefer DMA-BUF, fall back to
  VAAPI pipe if DMA BRC starves (Intel historically collapsed to ~0.5–3 Mbps).
* ``performance`` — DMA-BUF + CQP; Best uses the QP ladder, not set-bitrate QVBR.
"""

from __future__ import annotations

from typing import Any, Optional

STRATEGIES = ("smartview", "performance")
DEFAULT_STRATEGY = "smartview"

# Starve detector: air TX << target while video is healthy.
STARVE_TARGET_MIN_MBPS = 8.0
STARVE_FRAC = 0.25
STARVE_TICKS = 3


def normalize_strategy(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "smartview": "smartview",
        "smart-view": "smartview",
        "sv": "smartview",
        "qvbr": "smartview",
        "default": "smartview",
        "performance": "performance",
        "perf": "performance",
        "cqp": "performance",
    }
    return aliases.get(raw, DEFAULT_STRATEGY if not raw else raw)


def is_valid_strategy(value: Any) -> bool:
    return normalize_strategy(value) in STRATEGIES


def uses_sv_abr(settings: dict[str, Any]) -> bool:
    """Smart View BitrateController path (Best + smartview strategy)."""
    strategy = normalize_strategy(settings.get("encodeStrategy"))
    profile = str(settings.get("encodeProfile") or "").strip().lower()
    return strategy == "smartview" and profile == "best"


def apply_to_settings(
    settings: dict[str, Any],
    strategy: str,
    *,
    retarget_engine: bool = True,
) -> dict[str, Any]:
    """Mutate settings for the chosen strategy. Returns a small summary dict."""
    if not isinstance(settings, dict):
        raise TypeError("settings must be a dict")
    strat = normalize_strategy(strategy)
    if strat not in STRATEGIES:
        raise ValueError(f"unknown encodeStrategy {strategy!r}")

    settings["encodeStrategy"] = strat
    # Explicit strategy pick clears sticky fallback and engine pin so retarget applies.
    settings["encodeStrategyFallback"] = ""
    settings["encodeStrategyPinnedEngine"] = False

    if strat == "smartview":
        settings["vaapiRcMode"] = "QVBR"
        settings["vaapiAsyncDepth"] = 1
        try:
            q = int(str(settings.get("vaapiQuality") or "3"))
        except ValueError:
            q = 3
        if q < 3:
            settings["vaapiQuality"] = "3"
        if settings.get("encodeQpMin") is None:
            settings["encodeQpMin"] = 16
        if settings.get("encodeQpMax") is None:
            settings["encodeQpMax"] = 40
        if settings.get("vaapiQp") is None:
            settings["vaapiQp"] = 22
        settings["vbvMultiplier"] = "1.0"
        if retarget_engine and not settings.get("encodeStrategyPinnedEngine"):
            # Attempt DMA-BUF first; starve watchdog may flip to vaapi.
            settings["captureEncode"] = "dmabuf"
        settings["encodeStrategyAbr"] = "sv"
    else:
        # Performance: sharp CQP on DMA-BUF, no QVBR bitrate churn.
        settings["vaapiRcMode"] = "CQP"
        if retarget_engine and not settings.get("encodeStrategyPinnedEngine"):
            settings["captureEncode"] = "dmabuf"
        settings["encodeStrategyAbr"] = "cqp_ladder"
        # Drop SV congestion ceilings so CQP presets own the knobs.
        settings.pop("encodeWfdBitrateMbps", None)
        settings.pop("encodeCongestionBitrateMbps", None)

    return {
        "encodeStrategy": strat,
        "captureEncode": settings.get("captureEncode"),
        "vaapiRcMode": settings.get("vaapiRcMode"),
        "encodeStrategyAbr": settings.get("encodeStrategyAbr"),
    }


def dmabuf_qvbr_starving(
    *,
    capture_path: Optional[str],
    rc_mode: Optional[str],
    strategy: Optional[str],
    air_tx_mbps: Optional[float],
    target_mbps: Optional[float],
    video_fps: Optional[float],
    streak: int,
) -> tuple[bool, int, str]:
    """Return (should_fallback, new_streak, reason).

    Counts consecutive ticks where DMA-BUF QVBR delivers ≪ target while fps is OK.
    """
    strat = normalize_strategy(strategy)
    path = str(capture_path or "").strip().lower()
    rc = str(rc_mode or "").strip().upper()
    if strat != "smartview" or path != "dmabuf" or rc not in ("QVBR", "VBR", "CBR"):
        return False, 0, "inactive"
    if target_mbps is None or air_tx_mbps is None:
        return False, 0, "no_samples"
    if float(target_mbps) < STARVE_TARGET_MIN_MBPS:
        return False, 0, "target_low"
    # Require known healthy fps so quiet UI / rebind does not false-trigger.
    if video_fps is None or not (28.0 <= float(video_fps) <= 120.0):
        return False, 0, "fps_unknown"
    threshold = float(target_mbps) * STARVE_FRAC
    if float(air_tx_mbps) < threshold:
        new_streak = int(streak) + 1
        if new_streak >= STARVE_TICKS:
            return (
                True,
                new_streak,
                f"starve:{air_tx_mbps:.2f}<{STARVE_FRAC:.0%}×{target_mbps:.1f}",
            )
        return False, new_streak, f"streak:{new_streak}/{STARVE_TICKS}"
    return False, 0, "ok"
