# FluxCast patches (required for best results)

These files extend [FluxCast](https://github.com/IlyaP358/fluxcast) for the
Omarchy Display Miracast panel. Upstream FluxCast defaults stay non-breaking
(`libx264`, `wf-recorder -D`). Omarchy opts into GPU encode via environment
variables set by `miracast-ctl`; damage-aware capture remains opt-in.

| File | Purpose |
|------|---------|
| `src/wfd/hw_encode.py` | Optional VAAPI/QSV encode; battery / power-saver bias when GPU is opted in |
| `src/wfd/mode_state.py` | Persist sink-advertised stream modes for the UI (`FLUXCAST_WFD_MODE_STATE`) |
| `src/wfd/config.py` | `peer_address` for mode-state JSON |
| `src/wfd/session.py` | SIGUSR1 capture rebind loop; peer MAC on media config |
| `src/wfd/media/wlroots.py` | Wire HW encode plan; damage-aware `wf-recorder` when `FLUXCAST_WFD_WF_RECORDER_DAMAGE=1` |
| `src/wfd/media/pipeline.py` | Desktop `restart_video()` + `restarting` flag |
| `src/wfd/rtsp/handler.py` | Mode state after negotiation; bare-Session M16; probe grace |
| `src/wfd/rtsp/rtsp_server.py` | `restart_active_media()` for SIGUSR1 rebind |

## Apply

**Preferred:** use one FluxCast git checkout and point Miracast at it (no copies):

```bash
# settings.json fluxcastRoot, or:
export FLUXCAST_ROOT=/path/to/fluxcast

# If an AppImage extract still has vendor/.../usr/src/fluxcast, replace that
# directory with a symlink to the git checkout so paths cannot drift apart.
```

**Legacy (second tree):** copy these files into an extract only if you are not
using the git checkout directly:

```bash
export FLUXCAST_ROOT=/path/to/fluxcast

cp patches/fluxcast/src/wfd/hw_encode.py          "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/mode_state.py         "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/config.py             "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/session.py            "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/media/wlroots.py      "$FLUXCAST_ROOT/src/wfd/media/"
cp patches/fluxcast/src/wfd/media/pipeline.py     "$FLUXCAST_ROOT/src/wfd/media/"
cp patches/fluxcast/src/wfd/rtsp/handler.py       "$FLUXCAST_ROOT/src/wfd/rtsp/"
cp patches/fluxcast/src/wfd/rtsp/rtsp_server.py   "$FLUXCAST_ROOT/src/wfd/rtsp/"
```

Then:

```bash
export FLUXCAST_ROOT=/path/to/fluxcast
./bin/miracast-ctl doctor
```

`miracast-ctl` exports (when casting):

- `FLUXCAST_WFD_ENCODER` from settings `videoEncoder` (default `auto` → VAAPI/QSV when available)
- `FLUXCAST_WFD_MODE_STATE` for stream-mode pills in the Display panel

Optional (not set by default — continuous `wf-recorder -D` is preferred on
virtual Extend outputs for fewer wakeups / lower battery draw):

- `FLUXCAST_WFD_WF_RECORDER_DAMAGE=1` (omit `wf-recorder -D` for damage-aware capture)

Without these patches the panel still works against stock FluxCast, but you
lose GPU encode opt-in wiring, optional damage-aware capture, live **STREAM MODE**
capability discovery, and safe eDP scale capture rebind (`ensure-capture` /
SIGUSR1).
