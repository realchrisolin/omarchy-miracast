# Handoff: Hyprland ICC present fix (resume later)

Last updated: 2026-09-13  
Status: **local fix validated**; fork branch **pushed**; **PR not opened yet**

## Goal

Land the Hyprland fix so ICC Miracast Extend no longer stalls typed glyphs (and
similar small damage) until pointer motion. Keep ICC for GPU cost; stock wlr
remains the fallback.

## Root cause (confirmed)

In `CScreenshareFrame::share()` Hyprland only called
`scheduleFrame` / `damageMonitor` when `m_isFirst`.

wlr-screencopy damages the monitor on (nearly) every share. On headless Miracast
outputs, ICC therefore kept a stale framebuffer until an unrelated present
(e.g. mouse move). Evidence:

- `grim` of the Extend head already showed full typed text
- Stock `/usr/bin/wf-recorder` (wlr) had no typing flush bug
- Patched Hyprland (always schedule on share) felt “much better” in session

Related notes: [icc-text-delay-debug.md](icc-text-delay-debug.md)

## What’s already done

### Hyprland upstream prep

| Item | Location |
|------|----------|
| Upstream repo | https://github.com/hyprwm/Hyprland |
| Your fork | https://github.com/realchrisolin/Hyprland |
| Branch | `fix/icc-screenshare-schedule-every-share` |
| Commit | `af2108c04a41440ef3273a597a5b16dffaf29fa5` |
| Local clone | `$HOME/src/Hyprland-icc-pr` |
| Open PR UI | https://github.com/realchrisolin/Hyprland/pull/new/fix/icc-screenshare-schedule-every-share |

Patch copies in this repo:

- `hyprland-patches/icc-schedule-every-share.patch`
- `hyprland-patches/0001-fix-schedule-ICC-screenshare-present-on-every-share.patch`

Local install used for validation:

- Prefix: `~/.local/hyprland-icc-fix/`
- UWSM enable: `~/.config/uwsm/env.d/50-hyprland-icc-fix`
- Toggle: `~/.local/hyprland-icc-fix/toggle-icc-fix.sh {on,off,status}`

### omarchy-miracast packaging

- Plugin source + **tracked** `.so`: `hyprland-plugins/icc-present-kick/`
- `miracast-ctl` loads plugin when `PROTO=icc`, unloads on stop
- Docs: this file, `icc-text-delay-debug.md`, BUILD.md §7

Plugin is a **fallback** while on stock Arch Hyprland. After upstream merges,
plugin can be optional/removed.

### Local commits (not all necessarily pushed)

| Repo | Branch | Notes |
|------|--------|--------|
| `fluxcast` | `feat/wfd-hw-encode-modes` | LPCM mux harden commit |
| `omarchy-miracast` | `master` | LPCM docs/patches + Hyprland packaging commits |
| `wf-recorder` | `ext-copy-capture` | buffer-pool / NO_PAINT_CURSORS |
| Hyprland fork | `fix/icc-screenshare-schedule-every-share` | **pushed** to realchrisolin/Hyprland |

## Resume checklist — open the Hyprland PR

1. Read [PR Guidelines](https://wiki.hypr.land/Contributing-and-Debugging/PR-Guidelines/)
2. Confirm you are [vouched](https://wiki.hypr.land/Contributing-and-Debugging/) (required or PR auto-closes)
3. Follow [AI policy](https://github.com/hyprwm/.github/blob/main/policies/AI_USAGE.md) — **you** open and manage the PR (no AI-operated PR interactions)
4. Open PR: base `hyprwm/Hyprland:main` ← `realchrisolin:fix/icc-screenshare-schedule-every-share`
5. Suggested PR title: `fix: schedule ICC screenshare present on every share`
6. Suggested body points:
   - Problem: headless / Miracast Extend ICC stale FB until pointer motion
   - Evidence: grim has glyphs; wlr OK; patch validates
   - Change: always `scheduleFrame` + `damageMonitor` in `share()` (match wlr)
   - Also null-check `PMONITOR` before `damageMonitor`
   - Test plan: ICC capture on headless/Extend, type without moving mouse

## Resume checklist — re-validate after PR / new Hyprland

```bash
~/.local/hyprland-icc-fix/toggle-icc-fix.sh status   # or off once Arch has the fix
hyprctl version
# Miracast settings: wfRecorderBin → ICC build, wfRecorderProto → icc
# Type on Extend foot without mouse; workspace switch should not smear
```

## Optional cleanup later

- [ ] Unload / stop auto-loading `icc-present-kick` once Arch Hyprland includes the fix
- [ ] Push omarchy-miracast / fluxcast / wf-recorder commits if not yet pushed
- [ ] Upstream or drop `WF_RECORDER_NO_PAINT_CURSORS` if still useful
- [ ] Revisit `softwareCursors` default once ICC present is solid on stock Hyprland

## Do not

- Open/manage the Hyprland PR via AI tooling
- Re-enable the old **function-hook** plugin variant (crashed Hyprland)
- Force v0.3 “full session forceFullFrames every tick” (caused artifacts)
