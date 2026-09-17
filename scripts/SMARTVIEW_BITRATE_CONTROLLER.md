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

## Linux port (`bitrate_controller.py` + QVBR)
| Samsung | Ours |
|---------|------|
| RTCP RR fraction lost | iw tx_failed / retry% |
| NetworkStall | sender fps &lt; 28 / ABR stall |
| MediaCodec live setBitrate | `set-bitrate-kbps` + capture restart (VAAPI limitation) |
| CAC VBR | ffmpeg `h264_vaapi` **QVBR** + maxrate |
| QP min/max | settings encodeQpMin/Max + vaapiQp mid |

## Also present in Samsung (not all ported)
- `dropAFrame` / `setFrameSkip` (wf-recorder already drops on backlog)
- `requestIDRFrame` (we stamp + wait for GOP)
- Resolution downsize to 720p under stress
- Recovery RTP / TCP switch
