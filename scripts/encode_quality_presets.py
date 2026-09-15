#!/usr/bin/env python3
"""Per-render-engine encode quality tiers (high | medium | low).

Render engine (dmabuf | vaapi | cpu) and quality tier are orthogonal. The same
tier label resolves to different knobs per engine — DMA-BUF high is not the
same recipe as VAAPI-pipe high.

Vetted starting points (1080p30, ~20 MHz Miracast P2P):
  - pipe high ≈ QVBR qp18 quality4 max 16M (on-device A/B)
  - DMA-BUF high uses sharper QP / more encode effort at the same peak cap
  - pure CQP avoided as a default (Intel ignores maxrate; spikes on 20 MHz)
"""

from __future__ import annotations

from typing import Any, Optional

ENGINES = ("dmabuf", "vaapi", "cpu")
TIERS = ("high", "medium", "low")

# Knobs written into settings.json / exported to FluxCast.
# bitrate = stream/target; vaapiBitrate = QVBR/CBR peak (FLUXCAST_WFD_VAAPI_BITRATE).
PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "dmabuf": {
        "high": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 16,
            "vaapiQuality": "3",
            "vaapiBitrate": "16M",
            "bitrate": "12M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "medium": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 18,
            "vaapiQuality": "4",
            "vaapiBitrate": "14M",
            "bitrate": "11M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "low": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 20,
            "vaapiQuality": "6",
            "vaapiBitrate": "12M",
            "bitrate": "8M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
    },
    "vaapi": {
        # Pipe path — vetted QVBR envelope; high uses quality=4 from A/B.
        "high": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 18,
            "vaapiQuality": "4",
            "vaapiBitrate": "16M",
            "bitrate": "12M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "medium": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 18,
            "vaapiQuality": "5",
            "vaapiBitrate": "14M",
            "bitrate": "11M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "low": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 20,
            "vaapiQuality": "6",
            "vaapiBitrate": "12M",
            "bitrate": "8M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
    },
    "cpu": {
        # libx264 path: bitrate/GOP matter; VAAPI fields unused but kept coherent.
        "high": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 18,
            "vaapiQuality": "4",
            "vaapiBitrate": "12M",
            "bitrate": "12M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "medium": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 18,
            "vaapiQuality": "5",
            "vaapiBitrate": "8M",
            "bitrate": "8M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "low": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 20,
            "vaapiQuality": "6",
            "vaapiBitrate": "5M",
            "bitrate": "5M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
    },
}


def normalize_engine(engine: Optional[str]) -> str:
    e = str(engine or "dmabuf").strip().lower()
    return e if e in ENGINES else "dmabuf"


def normalize_tier(tier: Optional[str]) -> str:
    t = str(tier or "medium").strip().lower()
    return t if t in TIERS else "medium"


def resolve_preset(
    engine: Optional[str], tier: Optional[str]
) -> dict[str, Any]:
    """Return a copy of knobs for engine×tier."""
    eng = normalize_engine(engine)
    ti = normalize_tier(tier)
    return dict(PRESETS[eng][ti])


def apply_to_settings(
    data: dict[str, Any],
    *,
    tier: Optional[str] = None,
    engine: Optional[str] = None,
) -> dict[str, Any]:
    """Mutate settings dict: set encodeProfile + knobs for current/new engine.

    Returns the knobs applied (including encodeProfile / captureEncode echo).
    """
    if not isinstance(data, dict):
        raise TypeError("settings must be a dict")
    eng = normalize_engine(engine if engine is not None else data.get("captureEncode"))
    ti = normalize_tier(tier if tier is not None else data.get("encodeProfile"))
    knobs = resolve_preset(eng, ti)
    data["captureEncode"] = eng
    data["encodeProfile"] = ti
    for key, value in knobs.items():
        data[key] = value
    out = {"captureEncode": eng, "encodeProfile": ti, **knobs}
    return out


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", default="dmabuf", choices=ENGINES)
    ap.add_argument("--tier", default="medium", choices=TIERS)
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
