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
`-D`). With ICC, Hyprland may still wait for content change after the first
frame (protocol-allowed); stock wlr is better at “force frames” on idle
outputs. For a live desktop cast this rarely matters.

---

## Measured performance (same laptop, live Miracast)

Hardware: Intel i7-1165G7 + iGPU VAAPI. Session: Extend ~1280×720@30, DMA-BUF
`h264_vaapi` CQP. Samples: ~20s after warmup. CPU = % of **one** core.

| Capture binary | Hyprland | wf-recorder | Notes |
|----------------|----------|-------------|--------|
| ICC (PR #347 + fix), damage-aware | **~15%** | ~0.8% | Target PoC path |
| ICC, continuous `-D` | **~15%** | ~0.8% | Similar under ICC |
| Stock 0.6.0, damage-aware | **~83%** | ~2.4% | PATH default |
| Stock 0.6.0, continuous `-D` | **~81%** | ~2.3% | |

So: **shipping ICC in distro `wf-recorder` is the large Hyprland win.** FluxCast
DMA/CQP and Omarchy UI polish matter for quality and UX, but they do not replace
that protocol change.

---

## What must be true before it “just works” for everyone

| Layer | Upstream / packaging status | Local PoC |
|-------|----------------------------|-----------|
| Hyprland advertises ICC | Recent releases (verify with `hyprctl` / Wayland globals) | Use a new enough Omarchy/Hyprland |
| `wf-recorder` ICC client | [PR #347](https://github.com/ammen99/wf-recorder/pull/347) open | Build fork branch + fixes |
| SIGINT teardown race on Hyprland | [soreau#1](https://github.com/soreau/wf-recorder/pull/1) | Include in local build |
| FFmpeg 7.1+/9 `avcodec_get_supported_config` | [ammen99#352](https://github.com/ammen99/wf-recorder/pull/352) | Dual-path patch or Arch `ffmpeg-9` patch |
| FluxCast DMA-BUF + CQP + BIN/PROTO | Fork branch / patches in this repo | Point `FLUXCAST_ROOT` at patched tree |
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
  "wfRecorderBin": "/home/YOU/src/wf-recorder/build/wf-recorder",
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

---

## Hard constraints (do not “simplify” these away)

- **Never** `hyprctl output remove` / destroy the Miracast headless on the hot
  path — that has frozen eDP for tens of seconds. Park and reuse.
- Prefer **DMA-BUF + CQP** (`captureEncode: dmabuf`) on Intel; pipe VAAPI/CPU are
  fallbacks.
- Stock DMA path must **not** pass `wf-recorder -r` (breaks VAAPI filter graph).
  ICC builds **should** get `-r` (FluxCast detects ICC and adds it).
- Software cursors stay on so the pointer is visible in capture.

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
| wf-recorder ICC PR | https://github.com/ammen99/wf-recorder/pull/347 |
| Hyprland SIGINT fix | https://github.com/soreau/wf-recorder/pull/1 |
| FFmpeg 7.1+/9 dual-path | https://github.com/ammen99/wf-recorder/pull/352 |
| FluxCast fork (encode work) | https://github.com/realchrisolin/fluxcast |

## License

MIT — see [LICENSE](LICENSE).
