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
    quality=2 + i_qfactor=1.3 + CHP/high — 5 min soak peaked ~19 Mbps (under ~22
    corruption). Avoid quality=1 with low qp (old 22–28 Mbps TV corruption).
  - **VAAPI pipe**: **hard CBR** (ffmpeg BRC works). Do **not** use QVBR here —
    on-device Intel QVBR undershot to ~5–7 Mbps (worse on lighting). High is
    **CBR 15M + quality=3 + async=1 + VBV 1.0** (closest-to-lossless on 20 MHz
    after hotyeah soaks; wire ~18 Mbps with LPCM; ~3–4 Mbps under the ~22 Mbps
    corruption line). quality=1 hung the pipe; clamp floor is quality≥3.
  - **CPU**: CBR ladder (libx264); independent of VAAPI pipe hang constraints.

  ``bitrate`` / ``vaapiBitrate`` are the pipe CBR target (=maxrate); informational
  for DMA-BUF CQP. ``vbvMultiplier`` is CBR HRD depth in seconds of bitrate.
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
        # qp=18/quality=1 looked sharp but peaks hit ~22–28 Mbps and corrupted
        # the TV on 20 MHz. High uses qp=18/quality=2 + i_qfactor=1.3: sharper
        # than qp=19, with quality/i_qfactor holding peaks under the old blow-up
        # (5 min soak at qp=19 peaked ~13.6 Mbps — headroom to try 18).
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
        # Ladder (1080p30 + LPCM on 20 MHz):
        #   high   ≈ max quality under soft envelope (wire ~18 Mbps)
        #   medium ≈ stable daily driver with RF headroom
        #   low    ≈ poor RF / battery / busy channel
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
