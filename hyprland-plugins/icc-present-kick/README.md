# icc-present-kick

Bundled Hyprland plugin for **omarchy-miracast**. Fixes ICC (ext-image-copy-capture)
stale presents on headless Extend outputs.

## Why

Hyprland’s ICC path only calls `scheduleFrame` / `damageMonitor` on the **first**
capture frame. Stock **wlr-screencopy** damages the monitor on **every** frame.
Result with ICC: keyboard/cursor updates sit in the compositor but the Miracast
stream keeps an old framebuffer until pointer motion forces a present.

## What this plugin does

On each compositor **tick**, if any monitor has an active ICC screenshare
(or pending copies), it:

1. Sets `m_forceFullFrames` so damage tracking cannot leave old workspace pixels  
2. `scheduleFrame` + `damageMonitor` (same kick wlr-screencopy does every frame)

**No private-function trampolines** (an earlier hook-based attempt crashed Hyprland).


Current: **v0.2.1** (pending-copy kick only; v0.3 full-session kicks caused artifacts).

## Packaging

`icc-present-kick.so` is **tracked in this repo** so Omarchy installs work without
a local Hyprland SDK rebuild. Rebuild with `make` after Hyprland upgrades if the
plugin fails to load (ABI mismatch).

## Build / load

```bash
make -C hyprland-plugins/icc-present-kick
# miracast-ctl loads it automatically when PROTO=icc
# Manual: hyprctl plugin load $PWD/icc-present-kick.so
```

Unload on Miracast stop (or `hyprctl plugin unload icc-present-kick`).

## Upstream

The proper fix is in Hyprland: always `scheduleFrame` in `CScreenshareFrame::share`
(not only `m_isFirst`). Keep this plugin until that lands.
