# Building the Miracast performance stack locally

This document is a **proof-of-concept guide**: how to reproduce the low-CPU
Miracast Extend path on Omarchy/Hyprland **today**, before (and regardless of)
upstream merges. The Display panel README covers day-to-day install and
settings; this file explains **why** the pieces matter and **how** to wire
them by hand.

If you only want “cast works,” install stock deps and use PATH `wf-recorder`.
If you want “cast works **and** Hyprland stays responsive,” you need the stack
below — especially a custom `wf-recorder` until
[ammen99/wf-recorder#347](https://github.com/ammen99/wf-recorder/pull/347) (and
related fixes) land in distro packages.

---

## Why this exists

Miracast Extend on Hyprland is not “one app.” It is a chain:

```
Display panel (this plugin)
    → miracast-ctl
        → FluxCast (WFD session / RTP)
            → wf-recorder (Wayland capture + optional VAAPI encode)
                → Hyprland (wlr-screencopy *or* ext-image-copy-capture)
```

Stock Arch `wf-recorder` **0.6.0** talks **wlr-screencopy**. That works, but on
a live Extend session it can pin **~80% of one CPU core inside Hyprland** while
you are “just” casting a desktop.

A `wf-recorder` build of the **ext-image-copy-capture (ICC)** protocol (PR #347,
plus a small SIGINT teardown fix) drops that to roughly **~15% of one core** on
the same machine, same encode recipe, same TV — about a **5×** compositor-CPU
cut in our A/B.

Those improvements are **not** in distro packages yet. Upstream may merge them;
it may not. This guide shows how to run the PoC locally so the win is
reproducible, and so it is obvious what needs to ship upstream for “everyone.”

---

## Two different “modes” people confuse

### 1. Capture **tool**: portal vs `wf-recorder`

| Backend | When | Notes |
|---------|------|--------|
| **`wf-recorder`** | Omarchy Miracast Extend (default) | Direct Wayland capture of a named output |
| **xdg-desktop-portal** | FluxCast `portal` backend | Fine for interactive share; not the Extend headless path |

Omarchy sets `--wfd-capture-backend wf-recorder`. Portal is out of scope here.

### 2. Capture **protocol** inside `wf-recorder`: wlr-screencopy vs ICC

This is the important fork for Hyprland CPU.

| | **Stock / wlr-screencopy** | **ICC / ext-image-copy-capture** |
|--|---------------------------|----------------------------------|
| **Binary** | Distro `/usr/bin/wf-recorder` (0.6.0) | Local build of PR #347 (+ SIGINT fix) |
| **Wayland API** | `zwlr_screencopy_manager_v1` | `ext_image_copy_capture_manager_v1` |
| **Hyprland** | Supported for years | Needs a recent Hyprland that **advertises** ICC (e.g. 0.56.x) |
| **Compositor cost** | High on busy/Extend sessions (~**80%** one core in our Miracast A/B) | Much lower (~**15%** one core) |
| **Availability** | Every Arch install with `wf-recorder` | **Manual build** until upstream + packaging |
| **Omarchy default** | **Yes** (`PATH`) | **Opt-in** via `wfRecorderBin` / env |

**RENDER ENGINE** (DMA-BUF / VAAPI pipe / CPU) is a third axis: how frames are
*encoded* after capture. It does **not** choose wlr vs ICC. Prefer **GPU ·
DMA-BUF** once capture is healthy.

**Damage-aware vs continuous (`-D`):** Omarchy defaults to damage-aware (omit
`-D`), including the **LPCM** path (FluxCast honors
`FLUXCAST_WFD_WF_RECORDER_DAMAGE`). With ICC, Hyprland may still wait for
content change after the first frame unless present-scheduling is patched;
force continuous with `FLUXCAST_WFD_WF_RECORDER_DAMAGE=0` if typing lags.
Cadence A/B (CPU + `intel_gpu_top`): [BENCHMARKS.md](BENCHMARKS.md).

**Auto-pick the best measured profile** (ICC vs stock × dmabuf/vaapi/cpu +
DAMAGE) and apply FluxCast-facing settings:

```bash
./scripts/recommend-cast-profile.py --apply
```

Dry-run without writing settings: omit `--apply`. Details in
[BENCHMARKS.md § Auto-recommend](BENCHMARKS.md).

**Encode presets** (desktop UI vs movie):

```bash
miracast-ctl set-cast-preset desktop   # CQP qp18 GOP30
miracast-ctl set-cast-preset movie     # CQP qp18 GOP60 quality2, continuous -D
# RC/env apply at FluxCast start — reconnect if already streaming
```

`extendRefresh` follows the stream mode fps (e.g. `1920x1080p30` → 30 Hz head).
FluxCast auto-rebinds capture on buffer-pool / DTS spikes (20s debounce).

---

## Measured performance (same laptop, live Miracast)

See **[BENCHMARKS.md](BENCHMARKS.md)** for the full capture × RENDER ENGINE
matrix (ICC/stock × DMA-BUF/VAAPI/CPU), charts, method notes, and
`scripts/recommend-cast-profile.py` to turn those numbers into settings.

Headline (DMA-BUF encode, ~18s samples, % of one core):

| Capture binary | Hyprland | ffmpeg | Notes |
|----------------|----------|--------|--------|
| ICC + DMA-BUF | **~16%** | ~0% | Target PoC path |
| Stock wlr + DMA-BUF | **~81%** | ~0% | PATH default |

So: **shipping ICC in distro `wf-recorder` is the large Hyprland win.** DMA-BUF
keeps encode on the iGPU; CPU/`libx264` is a costly fallback (~40% ffmpeg).

---

## What must be true before it “just works” for everyone

| Layer | Upstream / packaging status | Local PoC |
|-------|----------------------------|-----------|
| Hyprland advertises ICC | Recent releases (verify with `hyprctl` / Wayland globals) | Use a new enough Omarchy/Hyprland |
| `wf-recorder` ICC client | [PR #347](https://github.com/ammen99/wf-recorder/pull/347) open | Build fork branch + fixes |
| SIGINT teardown race on Hyprland | [soreau#1](https://github.com/soreau/wf-recorder/pull/1) | Include in local build |
| FFmpeg 7.1+/9 `avcodec_get_supported_config` | [ammen99#352](https://github.com/ammen99/wf-recorder/pull/352) | Dual-path patch or Arch `ffmpeg-9` patch |
| FluxCast DMA-BUF + CQP + BIN/PROTO | Fork branch / patches in this repo | Point `FLUXCAST_ROOT` at patched tree |
| FluxCast WFD LPCM (`0x83`) mux + Pulse monitor | Same patches (`wfd_lpcm_mux`, wlroots) | Needed for LPCM-only TVs; see §6 |
| Omarchy Display Miracast UI | This plugin / `feat/display-miracast` | Install plugin; set `wfRecorderBin` |

Until the **wf-recorder** rows are in official packages, every interested user
must build and configure by hand — that is the gap this PoC documents.

---

## Local setup (full performance path)

### 0. Prerequisites

```bash
omarchy pkg add dnsmasq wf-recorder xorg-xrandr ffmpeg meson ninja \
  wayland-protocols libdrm mesa
# Plus build deps your distro needs for wf-recorder (gbm, libpipewire optional, …)
```

- Wi‑Fi that can do P2P / Miracast with NetworkManager
- A TV/dongle that actually completes WFD (many “Miracast” sticks are flaky)
- Hyprland new enough for `ext_image_copy_capture_manager_v1`

### P2P quiet channel (MCC via CSA)

Home Wi‑Fi (STA) and Miracast (P2P-GO) share one radio on typical laptops
(e.g. Intel AX201 / `iwlwifi`). Soft `--wfd-p2p-channel` during GO negotiation
is overridden to the STA channel (SCC). The working approach:

1. `scripts/pick-p2p-channel.py` — prefer quiet channels **outside** the STA’s
   80 MHz block (UNII-3 when STA is on UNII-1 ch 36–48).
2. Connect with NetworkManager as usual (association succeeds on STA channel).
3. After PLAY, `miracast-ctl` runs `wpa_cli chan_switch` on the GO iface to the
   picked channel. Home Wi‑Fi stays up; do **not** unmanage `p2p-dev-*` (that
   can leave wifi-p2p unavailable until NetworkManager restarts).

```bash
./scripts/pick-p2p-channel.py --json --band 5
./scripts/test_pick_p2p_channel.py
./scripts/test_attach_radio_channel_fields.py
./scripts/test_p2p_channel_integration.sh   # reconnect + assert GO ch != STA ch
./scripts/bench_p2p_channel.sh              # SCC vs MCC TX A/B → docs/benchmarks/
miracast-ctl pick-channel
miracast-ctl status   # includes staChannel / p2pChannel / radioMcc
# Live check while streaming:
iw dev   # STA channel vs P2P-GO channel
```

### 1. FluxCast (encode + session)

```bash
git clone https://github.com/realchrisolin/fluxcast.git ~/code/other/fluxcast
# or IlyaP358/fluxcast + apply patches/fluxcast from this repo — see APPLY.md
cd ~/code/other/fluxcast
git checkout feat/wfd-hw-encode-modes   # if using the fork branch

export FLUXCAST_ROOT="$HOME/code/other/fluxcast"
```

If you stay on stock FluxCast, apply `patches/fluxcast/` from this plugin
(`APPLY.md`). You need at least DMA-BUF/CQP + `FLUXCAST_WFD_WF_RECORDER_BIN`
support for the full PoC.

### 2. Custom `wf-recorder` (ICC)

```bash
git clone https://github.com/soreau/wf-recorder.git ~/src/wf-recorder
cd ~/src/wf-recorder
git checkout ext-copy-capture
# Cherry-pick or merge:
#   - Hyprland SIGINT fix (soreau/wf-recorder#1 / realchrisolin fix/hyprland-sigint-teardown)
#   - FFmpeg 7.1+ dual-path (ammen99/wf-recorder#352) — required on Arch FFmpeg 9

meson setup build --buildtype=release
ninja -C build
./build/wf-recorder -v    # should mention ext-copy-capture / branch tip
```

On Arch FFmpeg 9, tip without the dual-path/`avcodec_get_supported_config` change
will not compile. Use #352 (or temporarily Arch’s packaging patch).

**Smoke test (no Miracast):**

```bash
./build/wf-recorder -o <some-output> -f /tmp/t.mp4 -c h264_vaapi -r 30 \
  -p bf=0 -p qp=18 -p rc_mode=CQP -F 'scale_vaapi=format=nv12:out_range=tv'
# Ctrl+C should exit 0 — no "Too many bits for size_t"
```

**FluxCast ICC integration tests** (Layer 4 — binary selection + `-D`/`-r` flags):

```bash
cd "${FLUXCAST_ROOT:-$HOME/code/other/fluxcast}"
export FLUXCAST_WFD_WF_RECORDER_BIN="${FLUXCAST_WFD_WF_RECORDER_BIN:-$HOME/src/wf-recorder/build/wf-recorder}"
python3 -m unittest tests.test_icc_integration -v
# wf-recorder client Layers 1–3:
meson test -C ~/src/wf-recorder/build --print-errorlogs
```

### 3. This Display + Miracast plugin

```bash
# Clone / copy into Omarchy plugins, validate, rescan — see README.md Install
export FLUXCAST_ROOT="${FLUXCAST_ROOT:-$HOME/code/other/fluxcast}"
```

### 4. Opt into the ICC binary (required — no auto-detect)

Default is portable: **PATH stock** `wf-recorder`. Point settings at your build:

`~/.config/omarchy-miracast/settings.json` (merge with your other keys):

```json
{
  "mode": "extend",
  "streamMode": "1280x720p30",
  "captureEncode": "dmabuf",
  "wfRecorderBin": "$HOME/src/wf-recorder/build/wf-recorder",
  "wfRecorderProto": "auto"
}
```

Or for one shell session:

```bash
export FLUXCAST_WFD_WF_RECORDER_BIN="$HOME/src/wf-recorder/build/wf-recorder"
export FLUXCAST_WFD_WF_RECORDER_PROTO=auto
```

Bad/missing ICC path → warning in `cast.log` and **fallback to PATH** (cast still
works, CPU win gone).

### 5. Connect and verify

1. Display panel → Miracast → Extend → connect.
2. Confirm binary and protocol:

```bash
pgrep -a wf-recorder
# Expect: .../src/wf-recorder/build/wf-recorder ... -r 30 ...

grep -E 'wf-recorder BIN|proto=|PATH \\(stock' \
  "${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-miracast/logs/cast.log" | tail -5
# Expect: BIN=.../build/wf-recorder PROTO=icc  and  proto=icc
```

3. Optional: watch Hyprland `%CPU` in `btop`/`pidstat` — should be far below the
   stock-wlr session.

To A/B stock vs ICC: clear `wfRecorderBin` (or point at `/usr/bin/wf-recorder`),
reconnect, compare Hyprland CPU.

### 6. Miracast audio (desktop → TV speakers)

No extra *build* step — this is runtime wiring once FluxCast patches above are
in use. Many cheap dongles advertise **LPCM only**; AAC-in-TS then gives picture
but silent speakers. The patched FluxCast path negotiates WFD LPCM
(`stream_type=0x83`) for those sinks.

| Piece | What to do |
|-------|------------|
| **Miracast sink** | `miracast-ctl` creates a PipeWire/Pulse null sink named `miracast` (plus a `pacat` silence feeder so the sink stays alive). The feeder uses `node.name=omarchy_speaker_tuning.miracast_feeder` so Omarchy’s sound-panel **SOURCES** list (per-app streams — not mics; those are **INPUT**) hides it. |
| **Route desktop audio** | Set the default output to **Miracast** in Sound settings (or `pactl set-default-sink miracast`). |
| **Capture** | FluxCast records **`miracast.monitor`** via `pw-cat --target` (not the mic / default source, and not `ffmpeg -f pulse` / Lavf — stream-restore was remapping Lavf onto Speakers.monitor). The route watcher still re-pins if anything drifts. |
| **Routing** | App streams **follow the default** sink (watcher moves them on/off Miracast). |
| **Volume** | Keep the Miracast sink near **100%** (`pactl set-sink-volume miracast 100%`). A low sink volume is captured as quiet PCM and sounds “faded” on the TV. |
| **Verify** | In `cast.log`: `Capturing audio : miracast.monitor (sink monitor via pw-cat — not mic)` and `negotiating WFD LPCM`. Expect `wf-recorder` **and** a `pw-cat … --target miracast.monitor` process while streaming. |

Escape hatch (picture-only debug): `FLUXCAST_WFD_FORCE_AAC=1` forces the stable
DMA+AAC path — often silent on LPCM-only TVs.

Design notes (AOSP-inspired wire layout, not a build recipe):
[docs/aosp-wfd-audio-notes.md](docs/aosp-wfd-audio-notes.md),
[docs/aosp-wfd-architecture.md](docs/aosp-wfd-architecture.md).

### 7. ICC stale presents (fixed via bundled plugin)

**Symptom:** keystrokes / cursor on the Extend head appear late on the TV until
the pointer moves. `grim` of the head already shows the glyphs — not an IRQ issue.

**Cause:** Hyprland ICC only `scheduleFrame`s on the *first* capture; wlr does it
every frame. Headless Miracast keeps a stale FB until something forces a present.

**Fix we ship:** `hyprland-plugins/icc-present-kick/` — tick listener that kicks
pending screenshare presents (no private hooks). `miracast-ctl` loads it when
`PROTO=icc` and unloads on stop.

```bash
make -C hyprland-plugins/icc-present-kick
```

| Mode | Settings | Notes |
|------|----------|--------|
| **ICC + kick plugin** | `wfRecorderBin` → ICC build, `wfRecorderProto: icc` | Preferred (GPU) |
| **Stock wlr** | `/usr/bin/wf-recorder`, `proto: wlr` | Fallback if plugin missing |


---

## Persist display across monitors

Display panel → MIRACAST → **Persist display across monitors** (default on).

| Setting | Hyprland Extend head | Switch TV A → B |
|---------|----------------------|-----------------|
| **On** | Shared name `persistent-miracast` | Keep same desktop/workspaces; only retarget P2P/FluxCast |
| **Off** | Peer-sanitized name (e.g. sink name) | Migrate windows to eDP on stop; next connect seeds fresh `ext-*` |

CLI: `miracast-ctl set-persist-display true|false` (alias: `set-preserve-display`).
Settings key remains `preserveDisplayAcrossMonitors` for compatibility.

Hyprland cannot rename outputs. On the first persist-on connect after a
peer-named session, miracast-ctl creates `persistent-miracast`, migrates
workspaces over, and **leaves the empty old head in place**. Those leftovers
appear on `miracast-ctl status` as `orphanExtendHeads`. They are **never**
`hyprctl output remove`’d (eDP freeze); they clear when Hyprland restarts.

On stop / link-drop, the cast head is **parked off-layout** (`-12000x0`) and
the seat is forced back to eDP (focus + cursor warp). That prevents a dead
Miracast head beside the laptop from trapping pointer/keyboard until reconnect
re-places the head via `ensure_extend_monitor`.

---

## Hard constraints (do not “simplify” these away)

- **Never** `hyprctl output remove` / destroy the Miracast headless on the hot
  path — that has frozen eDP for tens of seconds. Park and reuse. Empty
  peer-named leftovers after migrate-to-`persistent-miracast` are intentional.
- Prefer **DMA-BUF + CQP** (`captureEncode: dmabuf`) on Intel; pipe VAAPI/CPU are
  fallbacks.
- Stock DMA path must **not** pass `wf-recorder -r` (breaks VAAPI filter graph).
  ICC builds **should** get `-r` (FluxCast detects ICC and adds it).
- Software cursors stay on so the pointer is visible in capture.
- Capture **Miracast sink monitor** audio only — never the default mic source
  (that loops TV speakers → mic → TV).

---

## What “merged upstream” would mean

If these land in places normal users already install:

1. **`wf-recorder`** with ICC + Hyprland-safe shutdown + FFmpeg 9 guards  
2. **Hyprland** that implements ICC well (already largely true on recent builds)  
3. **FluxCast** (or equivalent) with DMA-BUF/CQP + BIN override  
4. **Omarchy** Display Miracast defaults that prefer ICC **when the binary on
   PATH supports it**

…then `wfRecorderBin` goes away for most people: install packages, connect, get
the ~15% Hyprland path instead of ~80%.

Until then, this BUILD guide is the PoC contract.

---

## Related links

| Piece | Link |
|-------|------|
| Plugin overview / settings | [README.md](README.md) |
| FluxCast patches | [patches/fluxcast/APPLY.md](patches/fluxcast/APPLY.md) |
| WFD LPCM / AOSP design notes | [docs/aosp-wfd-audio-notes.md](docs/aosp-wfd-audio-notes.md) |
| wf-recorder ICC PR | https://github.com/ammen99/wf-recorder/pull/347 |
| Hyprland SIGINT fix | https://github.com/soreau/wf-recorder/pull/1 |
| FFmpeg 7.1+/9 dual-path | https://github.com/ammen99/wf-recorder/pull/352 |
| FluxCast fork (encode work) | https://github.com/realchrisolin/fluxcast |

## License

MIT — see [LICENSE](LICENSE).
