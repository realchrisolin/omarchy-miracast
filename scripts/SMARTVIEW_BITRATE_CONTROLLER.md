# Reverse-engineered Samsung Smart View BitrateController

Source: `libremotedisplay_wfd.so` on SM-S918U
(`vendor/samsung/frameworks/av/media/libremotedisplay/wifi-display/source/BitrateController.cpp`)

## Control loop
1. `updateConfiguration(minBitrate, maxBitrate, initBitrate, minQP, maxQP)`
2. On UDP: `decideUDPBitrate(RTCP RR)` via `calculateFractionLost`
   - LOSS → `decreaseUDPBitrate`
   - No loss → `increaseUDPBitrate`
3. `updateBitrateByNetStall` → aggressive decrease
4. TCP path: `decideTCPBitrate`
5. Apply via MediaCodec `setParameters(bitrate)` + `bitrate-mode` + QP range
6. Optional VBR: `"Turn on CAC mode in VBR"` + QTI content-adaptive

## Live Samsung ceilings (dig, 1080p / resolution=2)

```
min=2097152 (2 Mbps), init=8388608 (8 Mbps), max=14680064 (14 Mbps)
mMinQP=15, mMaxQP=44
```

## Linux port (`bitrate_controller.py` + QVBR)

Uses those Samsung min/init/max/QP values. Enabled when
**`encodeStrategy=smartview`** and **`encodeProfile=best`**. Prefers
**DMA-BUF + QVBR** (`prepare_dmabuf_settings`); session may fall back to pipe.
**`encodeStrategy=performance`** keeps DMA-BUF **CQP** (no `set-bitrate-kbps`).

### VAAPI QVBR must hit the bitrate target
Intel QVBR with fixed `qp=` undershoots to ~2–3 Mbps and looks pixelated.
Required encode flags (FluxCast + custom wf-recorder):

- `rc_mode=QVBR`, `b=` / `maxrate=` = target (e.g. 14M)
- **`qmin`/`qmax`** from Samsung bounds (15–44) — **do not** pass fixed `qp=` for QVBR
- **`minrate` ≈ 70% of target** (applied on `AVCodecContext.rc_min_rate` in the
  custom wf-recorder build)
- VBV `bufsize` ~2× peak (`vbvMultiplier=2.0`)
- Set **`wfRecorderBin`** to that build; stock PATH `wf-recorder` ignores the floor

| Samsung | Ours |
|---------|------|
| RTCP RR fraction lost | Only if real RR is available — **iw retry% is not used** |
| NetworkStall | sender fps sag / hard `tx_failed` |
| MediaCodec live setBitrate | coalesced SIGUSR1 + DMA sticky (`prepare_dmabuf_settings`) |
| CAC / quality VBR | `h264_vaapi` **QVBR** + qmin/qmax + minrate floor |
| QP min/max | `FLUXCAST_WFD_VAAPI_QMIN` / `QMAX` (not fixed `qp=` on QVBR) |

## Also present in Samsung (not all ported)
- `dropAFrame` / `setFrameSkip` (wf-recorder already drops on backlog)
- `requestIDRFrame` (we stamp + wait for GOP)
- Resolution downsize to 720p under stress
- Recovery RTP / TCP switch
