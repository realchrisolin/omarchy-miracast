# AOSP Wi-Fi Display source — architecture that is known to work

Design notes from Android Open Source `frameworks/av` wifi-display
(**inspiration for Linux**, not a port of Android runtime code).

Primary tree (Lineage 18.1 / Android 11-era copy of the same design):

- `media/libstagefright/wifi-display/source/WifiDisplaySource.*`
- `…/PlaybackSession.*`
- `…/Converter.*` (PCM AU framing)
- `…/TSPacketizer.*` (PAT/PMT/PES/TS)
- `…/MediaSender.*` + `RTPSender` (RTP/UDP)
- Capture: `AUDIO_SOURCE_REMOTE_SUBMIX`, video via `Surface` / MediaCodec

## Layered pipeline (successful shape)

```
┌─────────────────────────────────────────────────────────────┐
│ WifiDisplaySource                                           │
│  RTSP server (M1–M7, M16 keepalive), codec negotiation      │
└───────────────────────────┬─────────────────────────────────┘
                            │ PLAY
┌───────────────────────────▼─────────────────────────────────┐
│ PlaybackSession                                             │
│  owns tracks; pulls capture; feeds MediaSender              │
├─────────────────┬───────────────────────────────────────────┤
│ Video track     │ Audio track                               │
│ Surface/encoder │ REMOTE_SUBMIX → 48 kHz stereo PCM         │
│ → H.264 AUs     │ → Converter (AAC encode OR raw PCM frame) │
└────────┬────────┴─────────────┬─────────────────────────────┘
         │                      │
         └──────────┬───────────┘
                    ▼
┌─────────────────────────────────────────────────────────────┐
│ MediaSender + TSPacketizer  (ONE mux for both tracks)       │
│  Interleave by PTS; ~100 ms PAT/PMT/PCR; RTP PT=33 (MP2T)   │
└─────────────────────────────────────────────────────────────┘
```

Linux mapping:

| AOSP | FluxCast / Omarchy |
|------|--------------------|
| `REMOTE_SUBMIX` | PipeWire null sink `miracast` + **`.monitor`** |
| MediaCodec H.264 | `wf-recorder` VAAPI → annex-B H.264 |
| `Converter` AAC | ffmpeg/wf-recorder AAC (stable path) |
| `Converter` PCM | `WFDLPCMMuxer` LPCM packer |
| `TSPacketizer` | ffmpeg `rtp_mpegts` **or** `WFDLPCMMuxer` |
| RTSP in `WifiDisplaySource` | FluxCast `wfd/rtsp` |

## Codec policy (important)

AOSP does **not** default to LPCM when the sink also has AAC:

1. If sink has LPCM **and** `media.wfd.use-pcm-audio=true` → LPCM  
2. Else if sink has AAC → **AAC** (normal case)  
3. Else if sink has LPCM only → LPCM  
4. Else → fail / no audio  

So LPCM-only dongles (like this TV) **must** get real WFD LPCM in the TS.
Advertising AAC “for picture” is a Linux workaround, not the AOSP design.

## MPEG-TS layout AOSP actually emits

| Role | PID | Notes |
|------|-----|--------|
| PAT | `0x0000` | `version_number = 1` (`0xc3`) |
| PMT | `0x0100` | same version 1 |
| PCR | `0x1000` | **dedicated** adaptation-only packets |
| H.264 video | **`0x1011`** | PES `stream_id` `0xE0`, stream_type `0x1B` |
| AAC audio | `0x1100` | stream_type `0x0F`, PES `0xC0` |
| LPCM audio | `0x1100` | stream_type **`0x83`**, PES **`0xBD`** |

PMT elementary-stream info:

- H.264: AVC video descriptor (tag 40) + AVC timing/HRD (tag 42)
- LPCM: LPCM audio stream descriptor (tag `0x83`, length 2)

PAT/PMT/PCR are refreshed about every **100 ms** with media (not only on
video frame count).

## LPCM PES payload (Wi-Fi Display)

From `Converter::feedRawAudioInputBuffers` (spec framing, not Android-only):

1. Host PCM → **big-endian** s16 (`htons`)
2. Body size fixed: **6 × 80 × 4 = 1920** bytes
3. 4-byte header only (no DVD frame header):

| Byte | 48 kHz stereo 16-bit |
|------|----------------------|
| 0 | `0xA0` |
| 1 | `0x06` (AU count) |
| 2 | `0x00` |
| 3 | `0x11` (quant/rate/channels) |

Audio PES also uses **2 stuffing bytes** in the PES header
(`numStuffingBytes = 2` for audio in `MediaSender`).

## Video details that matter for “obtaining stream”

- SPS/PPS prepended to IDR (`PREPEND_SPS_PPS_TO_IDR_FRAMES` /
  `FLAG_MANUALLY_PREPEND_SPS_PPS`)
- Video on **`0x1011`**, not on the PCR PID
- PCR on **`0x1000`** as its own packets when `EMIT_PCR` is set

A mux that puts H.264 on `0x1000` while sinks look for `0x1011` (AOSP /
ffmpeg `--mpegts_start_pid 4113`) will TX happily while the TV stays on
**“obtaining stream”**.

## What FluxCast should mirror (checklist)

1. **Negotiate** like AOSP: AAC if present; LPCM only when that is all the sink has (or forced).
2. **Capture** display mix from a dedicated sink monitor (REMOTE_SUBMIX analogue).
3. **One TS** with AOSP PIDs: PMT `0x100`, PCR `0x1000`, video `0x1011`, audio `0x1100`.
4. **PSI version 1** on PAT/PMT when talking to picky / Android-like sinks.
5. **LPCM**: `0x83` + `0xBD` + `A0 06 00 11` + 1920-byte BE PCM; LPCM ES descriptor.
6. **H.264**: AU-aligned annex-B, SPS/PPS on IDR, AVC descriptors in PMT.
7. **TS stuffing**: short packets use an adaptation field + `0xFF` stuffing — never pad
   unused bytes as PES payload (that looks like “corrupted” video on sinks).
8. Do **not** copy ALooper/MediaCodec/Binder — only the wire contract above.

## References in this repo

- Implementation: `fluxcast/src/drivers/wfd_lpcm_mux.py`
- Stable AAC path PIDs: `fluxcast/src/wfd/media/pipeline.py` (`-mpegts_start_pid 4113`)
- Shorter LPCM note: `docs/aosp-wfd-audio-notes.md`
