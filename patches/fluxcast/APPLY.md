# FluxCast patches (required for best results)

These files extend [FluxCast](https://github.com/IlyaP358/fluxcast) for the
Omarchy Display Miracast panel. Upstream FluxCast defaults stay non-breaking
(`libx264`, `wf-recorder -D`). Omarchy opts into GPU encode via environment
variables set by `miracast-ctl`; damage-aware capture remains opt-in.

| File | Purpose |
|------|---------|
| `src/wfd/power_plan.py` | Discover OS power profiles as ``power_plan_N`` (PPD/TLP-pd, platform_profile, system76-power, tuned) |
| `src/wfd/hw_encode.py` | Optional VAAPI/QSV encode; capture-encode mode (DMA-BUF vs pipe); power-plan throttling |
| `src/wfd/mode_state.py` | Persist sink-advertised stream modes for the UI (`FLUXCAST_WFD_MODE_STATE`) |
| `src/wfd/modes.py` | Full classic CEA table; CHP for M4; interlaced listed but not negotiated; default RTSP pick is best advertised progressive mode (`FLUXCAST_WFD_MODE_POLICY`) |
| `src/wfd/constants.py` | CEA bit constants (0–16) + VESA 1200p |
| `src/wfd/config.py` | `peer_address` for mode-state JSON; `WFDCEAMode.interlaced` |
| `src/wfd/session.py` | SIGUSR1 capture rebind loop; peer MAC on media config; NM-path `--wfd-p2p-channel` |
| `src/wfd/p2p/device.py` | P2P OperChannel for 2.4GHz + non-DFS 5GHz (quiet-channel picks) |
| `src/wfd/p2p/wpas.py` | Hard Connect `frequency=`; wait for `AP-STA-CONNECTED` before IP flush; remanage on failure |
| `src/wfd/p2p/wpas_ip.py` | `sudo -n` elevation; `mark_managed` helper |
| `src/wfd/p2p/dbus.py` | Privileged calls use `sudo -n` (no pkexec) |
| `src/wfd/media/wlroots.py` | DMA-BUF `h264_vaapi`+CQP; ICC `-r`; LPCM path captures **`*.monitor` via `pw-cat --target`** (not mic/`pipewiresrc`/`ffmpeg -f pulse`) |
| `src/wfd/wf_recorder.py` | Optional `FLUXCAST_WFD_WF_RECORDER_BIN` / `…_PROTO=icc` for PR #347 builds |
| `src/wfd/media/pipeline.py` | Desktop `restart_video()`; stop LPCM muxer + close video/audio fds on restart |
| `src/drivers/wfd_lpcm_mux.py` | WFD LPCM mux — AOSP PIDs (video `0x1011`, PCR `0x1000`), `0x83` + AU framing |
| `src/wfd/rtsp/handler.py` | Mode state; M16; LPCM-only → `0x83` mux (`FLUXCAST_WFD_FORCE_AAC=1` escapes) |
| `src/wfd/probe.py` | Same LPCM negotiation as the live RTSP handler |
| `src/wfd/rtsp/rtsp_server.py` | `restart_active_media()` for SIGUSR1 rebind |

Runtime audio how-to: plugin [BUILD.md §6](../../BUILD.md).  
Design notes (AOSP-inspired, not a port): [docs/aosp-wfd-audio-notes.md](../../docs/aosp-wfd-audio-notes.md),
[docs/aosp-wfd-architecture.md](../../docs/aosp-wfd-architecture.md).

## Apply

**Preferred:** use one FluxCast git checkout and point Miracast at it (no copies):

```bash
# Prefer env (portable — use $HOME / CODE_OTHER, not /home/<user>/...):
export FLUXCAST_ROOT="${FLUXCAST_ROOT:-$HOME/code/other/fluxcast}"
# export CODE_OTHER="$HOME/code/other"   # miracast-ctl uses $CODE_OTHER/fluxcast

# Optional: settings.json "fluxcastRoot" with ~/ or $HOME/...
# Panel UI settings (onlyExpandFocusedDisplay, extendPosition, …): see ../README.md

# If an AppImage extract still has vendor/.../usr/src/fluxcast, replace that
# directory with a symlink to the git checkout so paths cannot drift apart.
```

**Legacy (second tree):** copy these files into an extract only if you are not
using the git checkout directly:

```bash
export FLUXCAST_ROOT="${FLUXCAST_ROOT:-$HOME/code/other/fluxcast}"

cp patches/fluxcast/src/wfd/power_plan.py         "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/hw_encode.py          "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/mode_state.py         "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/modes.py              "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/constants.py          "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/config.py             "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/session.py            "$FLUXCAST_ROOT/src/wfd/"
mkdir -p "$FLUXCAST_ROOT/src/wfd/p2p"
cp patches/fluxcast/src/wfd/p2p/device.py         "$FLUXCAST_ROOT/src/wfd/p2p/"
cp patches/fluxcast/src/wfd/p2p/wpas.py           "$FLUXCAST_ROOT/src/wfd/p2p/"
cp patches/fluxcast/src/wfd/p2p/wpas_ip.py        "$FLUXCAST_ROOT/src/wfd/p2p/"
cp patches/fluxcast/src/wfd/p2p/dbus.py           "$FLUXCAST_ROOT/src/wfd/p2p/"
cp patches/fluxcast/src/wfd/media/wlroots.py      "$FLUXCAST_ROOT/src/wfd/media/"
cp patches/fluxcast/src/wfd/media/pipeline.py     "$FLUXCAST_ROOT/src/wfd/media/"
cp patches/fluxcast/src/wfd/wf_recorder.py        "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/probe.py              "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/rtsp/handler.py       "$FLUXCAST_ROOT/src/wfd/rtsp/"
cp patches/fluxcast/src/wfd/rtsp/rtsp_server.py   "$FLUXCAST_ROOT/src/wfd/rtsp/"
mkdir -p "$FLUXCAST_ROOT/src/drivers"
cp patches/fluxcast/src/drivers/wfd_lpcm_mux.py   "$FLUXCAST_ROOT/src/drivers/"
```

Then:

```bash
export FLUXCAST_ROOT="${FLUXCAST_ROOT:-$HOME/code/other/fluxcast}"
./bin/miracast-ctl doctor
```

`miracast-ctl` exports (when casting):

- `FLUXCAST_WFD_ENCODER` from settings `videoEncoder` (default `auto` → VAAPI/QSV when available)
- `FLUXCAST_WFD_CAPTURE_ENCODE=auto` when the encoder is `auto`/`vaapi`/`qsv` (DMA-BUF preferred)
- `FLUXCAST_WFD_MODE_STATE` for stream-mode pills in the Display panel

### Capture encode path (DMA-BUF + CQP)

Omarchy’s Display panel shows **RENDER ENGINE** and **QUALITY** pills on the Miracast
display row only while connected (`dmabuf` / `vaapi` / `cpu`). Preference
lives in `settings.captureEncode` and `$STATE_DIR/capture-encode` for live
rebind; GPU failures fall back to CPU and the active pill follows the
resolved path.

When `FLUXCAST_WFD_CAPTURE_ENCODE` is `auto`/`vaapi`, FluxCast prefers:

`wf-recorder -c h264_vaapi` (DMA-BUF) → `scale_vaapi=format=nv12:out_range=tv`
→ CQP (`qp=18` by default, override with `FLUXCAST_WFD_VAAPI_QP`) → ffmpeg `-c:v copy`

Scaled Hyprland outputs are included (logical region in logs is normal; the
DMA buffer is still physical mode size). Deny scaled DMA with
`FLUXCAST_WFD_DMABUF_ALLOW_SCALED=0`. Force the old pipe with
`FLUXCAST_WFD_CAPTURE_ENCODE=pipe`.

Do **not** pass `wf-recorder -r` on the **stock** DMA path (it appends `fps=`
after `scale_vaapi` and glitches). ICC (PR #347) builds need `-r` for capture
cadence; FluxCast detects ICC (`--toplevel` / version) and passes `-r` only then.
Keep `bf=0` + constrained baseline.

Optional ICC binary (not the default — stock `PATH` wf-recorder otherwise).
Set explicitly via `settings.wfRecorderBin` or:

```bash
export FLUXCAST_WFD_WF_RECORDER_BIN=/path/to/wf-recorder-icc
export FLUXCAST_WFD_WF_RECORDER_PROTO=auto   # or icc; bad icc config falls back to PATH
```

Omarchy does **not** probe `~/src/...` build trees.

Optional damage-aware (Omarchy `miracast-ctl` defaults this on):

- `FLUXCAST_WFD_WF_RECORDER_DAMAGE=1` (omit `wf-recorder -D` for damage-aware capture)

Without these patches the panel still works against stock FluxCast, but you
lose GPU encode opt-in wiring, DMA-BUF/CQP desktop quality, optional
damage-aware capture, live **STREAM MODE** capability discovery, and safe
eDP scale capture rebind (`ensure-capture` / SIGUSR1).
