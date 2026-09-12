# FluxCast patches (required for best results)

These files extend [FluxCast](https://github.com/IlyaP358/fluxcast) for the
Omarchy Display Miracast panel:

| File | Purpose |
|------|---------|
| `src/wfd/hw_encode.py` | VAAPI/QSV encode with AC vs battery bias |
| `src/wfd/mode_state.py` | Persist sink-advertised stream modes for the UI |
| `src/wfd/media/wlroots.py` | Wire HW encode + damage-aware `wf-recorder` (no `-D`) |
| `src/wfd/rtsp/handler.py` | Write mode state after RTSP M3 negotiation |

## Apply

```bash
# Point at your FluxCast source tree (AppImage extract or git checkout)
export FLUXCAST_ROOT=/path/to/fluxcast

cp patches/fluxcast/src/wfd/hw_encode.py          "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/mode_state.py         "$FLUXCAST_ROOT/src/wfd/"
cp patches/fluxcast/src/wfd/media/wlroots.py      "$FLUXCAST_ROOT/src/wfd/media/"
cp patches/fluxcast/src/wfd/rtsp/handler.py       "$FLUXCAST_ROOT/src/wfd/rtsp/"
```

Then:

```bash
export FLUXCAST_ROOT=/path/to/fluxcast
./bin/miracast-ctl doctor
```

Without these patches the panel still works, but you lose GPU encode, quieter
capture, and live **STREAM MODE** capability discovery from the sink.
