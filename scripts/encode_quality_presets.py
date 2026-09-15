#!/usr/bin/env python3
"""Per-render-engine encode quality tiers (high | medium | low).

Render engine (dmabuf | vaapi | cpu) and quality tier are orthogonal. The same
tier label resolves to different knobs per engine — DMA-BUF high is not the
same recipe as VAAPI-pipe high.

## 20 MHz Miracast P2P bitrate budget

All tiers are capped for a **20 MHz** Wi‑Fi Direct link (common when P2P
stays SCC next to a busy 5 GHz STA):

  - 802.11n/ac 20 MHz 1SS PHY ceiling ≈ 65–72 Mbps
  - Practical UDP throughput in good indoor tests ≈ 50–60 Mbps (1SS);
    optimistic MAC efficiency ≈ 50–65% of PHY → ~35–45 Mbps
  - Miracast also needs LPCM audio (~1.5 Mbps), MPEG‑TS/RTP overhead,
    retries, and often STA coexistence on the same channel
  - On-device: uncapped DMA-BUF **CQP** peaked ~22–28 Mbps and produced
    visible TV blockiness/corruption on 20 MHz

Reliable video envelope used here (1080p30):

  | Tier   | Target | Peak (maxrate) |
  |--------|--------|----------------|
  | high   | 10 Mbps | 12 Mbps       |
  | medium |  8 Mbps | 10 Mbps       |
  | low    |  5 Mbps |  8 Mbps       |

High uses **QVBR** (not CQP): CQP cannot honor maxrate on Intel VAAPI.
``bitrate`` = target; ``vaapiBitrate`` = peak cap (FLUXCAST_WFD_VAAPI_BITRATE).
"""

from __future__ import annotations

from typing import Any, Optional

ENGINES = ("dmabuf", "vaapi", "cpu")
TIERS = ("high", "medium", "low")

# Knobs written into settings.json / exported to FluxCast.
# bitrate = stream/target; vaapiBitrate = QVBR/CBR peak (FLUXCAST_WFD_VAAPI_BITRATE).
PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "dmabuf": {
        # QVBR + hard maxrate — CQP ignored maxrate and blew past 20 MHz.
        "high": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 18,
            "vaapiQuality": "4",
            "vaapiBitrate": "12M",
            "bitrate": "10M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "medium": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 20,
            "vaapiQuality": "5",
            "vaapiBitrate": "10M",
            "bitrate": "8M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "low": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 22,
            "vaapiQuality": "6",
            "vaapiBitrate": "8M",
            "bitrate": "5M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
    },
    "vaapi": {
        # Pipe path — same 20 MHz envelope as DMA-BUF.
        "high": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 18,
            "vaapiQuality": "4",
            "vaapiBitrate": "12M",
            "bitrate": "10M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "medium": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 20,
            "vaapiQuality": "5",
            "vaapiBitrate": "10M",
            "bitrate": "8M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "low": {
            "vaapiRcMode": "QVBR",
            "vaapiQp": 22,
            "vaapiQuality": "6",
            "vaapiBitrate": "8M",
            "bitrate": "5M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
    },
    "cpu": {
        # libx264: hard CBR at the same caps (no QVBR).
        "high": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 18,
            "vaapiQuality": "4",
            "vaapiBitrate": "10M",
            "bitrate": "10M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "medium": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 20,
            "vaapiQuality": "5",
            "vaapiBitrate": "8M",
            "bitrate": "8M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "low": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 22,
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
