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
    quality=2 — on-device ~7–14 Mbps typical; sharper than starved CBR, calmer than qp=16.
  - **VAAPI pipe / CPU**: **CBR 14/10/6 Mbps** (ffmpeg BRC works; hard 20 MHz cap).

  ``bitrate`` / ``vaapiBitrate`` document the 20 MHz budget (used by pipe CBR;
  informational for DMA-BUF CQP).
"""

from __future__ import annotations

from typing import Any, Optional

ENGINES = ("dmabuf", "vaapi", "cpu")
TIERS = ("high", "medium", "low")

# Knobs written into settings.json / exported to FluxCast.
# bitrate = stream/target; vaapiBitrate = peak (FLUXCAST_WFD_VAAPI_BITRATE).
# For CBR both are equal so HRD maxrate == target.
PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "dmabuf": {
        # CQP only — bitrate RC undershoots ~0.5–3 Mbps on DMA-BUF+Intel.
        # qp=18 + quality=1: max encode effort at the qp that stays ~12–17 Mbps
        # live. qp=17 peaked ~32 Mbps on-device (over 20 MHz); qp=16 worse.
        "high": {
            "vaapiRcMode": "CQP",
            "vaapiQp": 18,
            "vaapiQuality": "1",
            "vaapiBitrate": "14M",
            "bitrate": "14M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "medium": {
            "vaapiRcMode": "CQP",
            "vaapiQp": 20,
            "vaapiQuality": "4",
            "vaapiBitrate": "10M",
            "bitrate": "10M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
        "low": {
            "vaapiRcMode": "CQP",
            "vaapiQp": 23,
            "vaapiQuality": "6",
            "vaapiBitrate": "6M",
            "bitrate": "6M",
            "vaapiGop": 30,
            "vaapiAsyncDepth": 2,
        },
    },
    "vaapi": {
        # Pipe — same CBR envelope (maximize within 20 MHz).
        "high": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 18,
            "vaapiQuality": "2",
            "vaapiBitrate": "14M",
            "bitrate": "14M",
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
    "cpu": {
        "high": {
            "vaapiRcMode": "CBR",
            "vaapiQp": 18,
            "vaapiQuality": "2",
            "vaapiBitrate": "14M",
            "bitrate": "14M",
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
