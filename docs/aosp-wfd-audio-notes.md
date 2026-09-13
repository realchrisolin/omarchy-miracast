# AOSP Wi-Fi Display audio — design notes for Linux Miracast

Android’s Miracast *source* (Wi-Fi Display) already ships a working LPCM path.
We are **not** porting that code. It is Android-specific (AudioFlinger,
`MediaCodec`, Binder). Use it as **design inspiration** for FluxCast on Linux
(PipeWire + custom MPEG-TS mux).

For the full source architecture (PIDs, PCR, codec policy, pipeline diagram),
see **`aosp-wfd-architecture.md`**.

Canonical AOSP tree (read-only reference):

- `frameworks/av/media/libstagefright/wifi-display/source/WifiDisplaySource.cpp`
- `…/PlaybackSession.cpp` (`addAudioSource`)
- `…/Converter.cpp` (`feedRawAudioInputBuffers`)
- `…/TSPacketizer.cpp` (stream type `0x83` + LPCM descriptor; video PID `0x1011`)

Older Lineage mirrors of the same files are fine if googlesource is awkward.

## What AOSP does (the ideas that matter)

### 1. Capture is a dedicated mix, not the speaker

`PlaybackSession::addAudioSource` pulls PCM from **`AUDIO_SOURCE_REMOTE_SUBMIX`**
at **48 kHz stereo**. That is a virtual sink the system routes “display audio”
into — parallel to how we expose a **PipeWire/Pulse “Miracast” null sink** and
read its `.monitor`.

Linux mapping:

| AOSP | FluxCast / Omarchy |
|------|--------------------|
| `REMOTE_SUBMIX` | PipeWire null sink `Miracast` + `.monitor` |
| User picks WFD as output | Sound settings default → Miracast sink |
| 48 kHz / 2 ch fixed | Same caps on the GStreamer `pipewiresrc` path |

Do not reinvent a second capture graph; keep feeding the mux from that monitor.

### 2. Codec choice: AAC first, LPCM when the sink forces it

`WifiDisplaySource` advertises:

- `AAC 00000001 00` when the sink supports AAC (default)
- `LPCM 00000002 00` when PCM is selected

Selection roughly:

1. Prefer AAC if the sink lists it
2. Else use LPCM if the sink lists LPCM
3. Optional property `media.wfd.use-pcm-audio` forces PCM even when AAC exists

FluxCast already negotiates LPCM when the TV advertises LPCM-only (and for the
Microsoft adapter). Keep that policy; do not invent a third codec for WFD.

### 3. LPCM PES payload shape (Wi-Fi Display, not Blu-ray / DVD)

When PCM is used, AOSP does **not** run an AAC encoder. `Converter` packetizes
raw PCM for MPEG-TS:

- Byte-swap samples to **network order** (big-endian s16) if the capture was LE
- Fixed access-unit size: **6 AUs × 80 audio frames × 4 bytes** (stereo s16)
  → **1920 bytes of PCM per PES payload body**
- Prepend a **4-byte** WFD LPCM header (only — no DVD “frame header”):

| Byte | Value (48 kHz stereo 16-bit) | Meaning |
|------|------------------------------|---------|
| 0 | `0xA0` | Private-stream style marker used by WFD LPCM |
| 1 | `0x06` | Number of AUs in this PES packet |
| 2 | `0x00` | Reserved / emphasis off |
| 3 | `0x11` | quant=16-bit (`0`), rate=48 kHz (`2`), channels=stereo (`1`) |

Incomplete tails are held until a full 1920-byte body is ready, then emitted as
one access unit into the TS packetizer.

GStreamer `mpegtsmux` emits Blu-ray LPCM (`stream_type=0x8b`). WFD sinks want
**`stream_type=0x83`**. That is why FluxCast keeps a small custom muxer instead
of forcing gst.

### 4. PMT describes LPCM explicitly

`TSPacketizer` registers raw audio as:

- `stream_type = 0x83`
- PES `stream_id = 0xBD` (private_stream_1)
- Optional **LPCM audio stream descriptor** (tag `0x83`, length 2) carrying
  sampling frequency and channel count for the sink’s parser

FluxCast should advertise the same stream type and descriptor so cheap TVs that
only list LPCM can find the track.

### 5. Transport is still MPEG-TS over RTP

Video H.264 + audio LPCM share one TS, RTP payload type 33 (MP2T), same RTSP
session. Timing stays 90 kHz. Audio must keep flowing even on a static desktop —
AOSP’s capture is continuous; our LPCM path should likewise not depend on
damage-only screen capture starving the mux.

## What we deliberately do *not* copy

- Android `ALooper` / `AHandler` / `MediaCodec` plumbing
- HDCP / `IRemoteDisplay` Binder APIs
- `AudioSource` C++ and AudioFlinger internals
- Property service (`media.wfd.*`) — use env / Omarchy settings instead
- Verbatim C++ from Converter — reimplement the **framing contract** in Python

## FluxCast checklist (inspired by the above)

1. **Capture**: Miracast null-sink **`.monitor`** via Pulse/ffmpeg (not
   `pipewiresrc`, which can latch onto the mic and cause speaker feedback)
2. **Negotiate**: LPCM when sink is LPCM-only (AAC gives picture but silent
   speakers on those TVs); `FLUXCAST_WFD_FORCE_AAC=1` escapes to DMA+AAC
3. **Frame**: accumulate PCM into 1920-byte bodies; prepend `A0 06 00 11`; no
   DVD/Blu-ray extra header
4. **Mux**: PMT `stream_type=0x83`, PES `0xBD`; LPCM ES descriptor opt-in via
   `FLUXCAST_WFD_LPCM_PMT_DESC=1` (some dongles black-screen with ES_info)
5. **Lifecycle**: stop the muxer (and release the RTP bind) before restart so
   reconnects do not hit `EADDRINUSE` / orphan recorders

## Primary code pointers in this tree

- Muxer: `fluxcast` `src/drivers/wfd_lpcm_mux.py` (and the Omarchy patch mirror)
- Capture + LPCM start: `src/wfd/media/wlroots.py` (`_start_wf_recorder_lpcm`)
- RTSP negotiate: `src/wfd/rtsp/handler.py` (`negotiated_lpcm`)
- Sink / silence feeder: Omarchy `miracast-ctl` Miracast null sink
