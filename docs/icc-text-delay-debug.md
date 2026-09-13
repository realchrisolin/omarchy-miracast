# ICC text delay — debug plan & root cause

## Symptom

On the Miracast Extend head (ICC capture), typed characters appear late on the
TV. Moving the mouse “flushes” them. Visual workspace smear is mostly gone with
`icc-present-kick` v0.2.1; **text delay remains**.

## What is *not* the cause

| Ruled out | Evidence |
|-----------|----------|
| Keyboard IRQ / lost keys | `grim` of the head shows full text (e.g. `HELLoDIAG`) while delay is perceived on TV |
| FluxCast LPCM mux alone | AAC+ICC path also delayed |
| wf-recorder buffer-pool experiments | Pre-pool ICC build still delayed |
| fcitx5 | Still delayed with fcitx stopped |
| Stock wlr path | No delay — different compositor capture code path |

## Root cause (Hyprland)

In `CScreenshareFrame::share()` (`src/managers/screenshare/ScreenshareFrame.cpp`):

```cpp
// schedule a frame so that when a screenshare starts it isn't black ...
if (m_isFirst) {
    PMONITOR->scheduleFrame(AQ_SCHEDULE_NEEDS_FRAME);
    g_pHyprRenderer->damageMonitor(PMONITOR);
}
```

**Only the first ICC frame** schedules a present. Later captures wait for an
unrelated present (pointer motion, etc.).

Contrast **wlr-screencopy** (`Screencopy.cpp`): `damageMonitor()` on (nearly)
every share — so each capture gets a fresh commit → `onOutputCommit` → `copy()`.

`icc-present-kick` approximates that from a plugin (tick + pendingFrames). It
helps GPU stay on ICC but cannot fully replace an in-compositor per-share kick.

## Instrumentation (correlate stages)

Log file for the kick plugin: `/tmp/icc-present-kick.log` (monotonic ms).

```bash
# Terminal A — key events
sudo libinput debug-events --device /dev/input/event* 2>&1 | tee /tmp/icc-keys.log

# Terminal B — present kicks (plugin must be loaded)
tail -f /tmp/icc-present-kick.log

# Type on Miracast foot without moving mouse; note:
# 1) time of KEY_PRESSED in icc-keys.log
# 2) time of first "kick" after that
# 3) when glyphs appear on TV (stopwatch / phone video)
```

Expected if root cause is correct:

- Keys appear in libinput immediately  
- Compositor has glyphs (grim) within ~1 frame  
- Kick may fire, but TV glyphs lag until mouse **or** until Hyprland patch lands  

After the Hyprland patch: TV glyphs should track typing without mouse flush.

## Well-engineered fix

**Patch Hyprland** so every `share()` schedules a frame (same as wlr), not only
`m_isFirst`. See `hyprland-patches/icc-schedule-every-share.patch`.

Build/install matching `efb50993780079460b0cbed1363e2166a2de1d9f` (v0.56.2),
restart the session, retest typing on the Extend head with ICC.

Then we can drop or slim `icc-present-kick`.


## Status (local)

- Patch prepared: `hyprland-patches/icc-schedule-every-share.patch`
- Patched tree: `$HOME/src/Hyprland` @ efb5099 + schedule-every-share
- **Built & installed:** `~/.local/hyprland-icc-fix` (session: “Hyprland (ICC present fix)”)
- Probe script: `scripts/icc-latency-probe.sh`
- Plugin log: `/tmp/icc-present-kick.log` (v0.2.2)

To run the patched compositor you must **restart the Hyprland session** after
`cmake --install build` (or point UWSM/session at the new binary).

## Upstream PR branch (local)

Clone/worktree: `$HOME/src/Hyprland-icc-pr`
Branch: `fix/icc-screenshare-schedule-every-share`
Commit: schedule ICC present on every share (matches wlr-screencopy).

**Do not** open the Hyprland PR via AI tooling — Hyprland requires the author to
be [vouched](https://wiki.hypr.land/Contributing-and-Debugging/), follow the
[AI policy](https://github.com/hyprwm/.github/blob/main/policies/AI_USAGE.md),
and open/interact with the PR themselves. Format with `clang-format` before
pushing from your fork.
