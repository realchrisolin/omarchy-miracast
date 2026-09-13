# Omarchy Display + Miracast

Extends the Omarchy **Display** bar panel (brightness, text size, scale,
per-output controls) with **Miracast / Wi‑Fi Display** mirror and extend.

This is still the Display dropdown — not a separate cast icon.

Cloned from first-party `omarchy.monitor`, then extended.

**Local performance PoC** (custom ICC `wf-recorder`, screencopy vs ICC,
why upstream merges matter): see **[BUILD.md](BUILD.md)**.  
**Measured CPU / encode matrix** (charts): see **[BENCHMARKS.md](BENCHMARKS.md)**.

## Features

- Scan / connect / disconnect Miracast sinks from the Display panel
- **Mirror** or **Extend** (Hyprland virtual output named after the sink)
- Stream mode pills (sink-advertised CEA modes such as 720p30 / 1080p30)
- Safe scale / position changes (pause capture → move → restart)
- Safer disconnect teardown (workspace migrate, cursor restore, eDP re-assert)
- Optional VAAPI/QSV encode with battery / power-saver bias (FluxCast patch)

## Install

### Plugin

```bash
# From a clone of this repo
omarchy plugin validate .
# Copy or clone into your Omarchy plugins dir, e.g.
#   ~/.config/omarchy/plugins/<you>.monitor
# Omarchy assigns the local plugin id when cloning; the bar label stays "Display".
```

Disable stock Display if the clone does not replace it automatically:

```bash
# Ensure shell.json points at your cloned Display plugin id
```

Validate / soft-reload:

```bash
omarchy plugin validate ~/.config/omarchy/plugins/<you>.monitor
omarchy-shell shell rescanPlugins
```

QML icon/layout changes sometimes need a full shell restart:

```bash
rm -rf ~/.cache/quickshell/qmlcache
omarchy restart shell
```

### Dependencies

```bash
omarchy pkg add dnsmasq wf-recorder xorg-xrandr ffmpeg
```

Plus a [FluxCast](https://github.com/IlyaP358/fluxcast) source tree (or a fork
such as `realchrisolin/fluxcast` with the WFD encode / SIGUSR1 work):

```bash
# Preferred: env (portable — use $HOME, not /home/<user>/…):
export FLUXCAST_ROOT="${FLUXCAST_ROOT:-$HOME/code/other/fluxcast}"
# Or parent dir: CODE_OTHER=$HOME/code/other → sibling …/fluxcast is discovered.
# Optional: settings.json "fluxcastRoot" with ~/ or $HOME/...
```

If you previously used an AppImage extract under
`vendor/squashfs-root/usr/src/fluxcast`, replace that directory with a
**symlink** to the git checkout so scripts and settings cannot drift.

Apply the included performance / stream-mode / capture-rebind patches only if
you are not already running that checkout (see `patches/fluxcast/APPLY.md`).

## Settings

Override in `~/.config/omarchy-miracast/settings.json` (merged with
`miracast-ctl` defaults on read):

| Key | Default | Notes |
|-----|---------|--------|
| `mode` | `mirror` | `mirror` or `extend` |
| `extendPosition` | `right` | `left` / `right` / `above` / `below` |
| `streamMode` | `1280x720p30` | e.g. `1920x1080p30` — drives fps + resolution |
| `fps` | `20` | Fallback when `streamMode` is unset |
| `outputRes` | `1280x720` | Encoded size (fallback) |
| `extendResolution` | `1280x720` | Extend virtual output size (fallback) |
| `extendRefresh` | `30` | Preferred virtual output Hz (keep aligned with stream) |
| `bitrate` | `8M` | Pipe-path / fallback bitrate; DMA path uses CQP (not this ceiling) |
| `videoEncoder` | `auto` | `auto` → VAAPI, else QSV, else `libx264` |
| *(env)* `FLUXCAST_WFD_CAPTURE_ENCODE` | `auto` (Omarchy) / `pipe` (upstream) | `auto`/`vaapi` = wf-recorder DMA-BUF encode (incl. scaled outputs); `pipe` = raw→ffmpeg hwupload |
| *(env)* `FLUXCAST_WFD_DMABUF_ALLOW_SCALED` | allow (default) | `0`/`false` = force pipe when Hyprland scale ≠ 1 |
| `captureEncode` | `dmabuf` | RENDER ENGINE: `dmabuf` (GPU·DMA-BUF) / `vaapi` (GPU·VAAPI) / `cpu` |
| `audioEnabled` | `true` | Creates a PipeWire/Pulse **Miracast** null sink (selectable in the sound panel) and muxes its `.monitor` into the WFD stream. Does **not** auto-change the default output — pick Miracast in the audio tooltip when you want the TV. `--no-audio` disables. |
| `extendRefresh` | `60` | Hyprland refresh for the Extend virtual output. Use **60** for local pointer feel; capture/stream fps still follow `streamMode` / `-r`. |
| `softwareCursors` | `false` | `true` = force SW cursors so the pointer appears on the TV (can feel laggy locally). `false` = HW cursors (snappier local pointer; TV may not show a cursor). On **Intel**, prefer Extend **right** of eDP — left placement hits an i915 HW-cursor edge bug. |
| `wfRecorderBin` | unset | Absolute path to a custom `wf-recorder` (e.g. ICC / PR #347). Empty = **PATH** stock binary (portable default). |
| `wfRecorderProto` | `auto` | `auto` / `icc` / `wlr`. `auto` upgrades to `icc` only when a **configured** binary advertises ICC. Bad `icc` config falls back to PATH. |
| *(env)* `FLUXCAST_WFD_VAAPI_QP` | `18` | DMA CQP quantizer (lower = sharper / more bitrate) |
| `sinkScales` | `{}` | Per-sink Extend scale, keyed by MAC (overrides default) |
| `defaultExtendScale` | `1` | Extend scale when a sink has no `sinkScales` entry — **1** is cheapest for Hyprland |
| `onlyExpandFocusedDisplay` | `false` | Display panel: `false` expands all outputs; `true` = accordion (focused only) |

### Display panel expansion

By default every enabled display row is expanded (Brightness / Scale / cast
controls). To restore single-row accordion behavior:

```json
"onlyExpandFocusedDisplay": true
```

While connected, the Miracast display row shows **CAST MODE** / **EXTEND
POSITION** first (← ↑ ↓ → when Extend), then **SCALE**, **STREAM MODE**, and
**RENDER ENGINE**. Scan / doctor / firewall / Stop stay under the **MIRACAST**
section.

With focus on the CAST MODE / EXTEND POSITION row and Extend active, vim
**hjkl** set position: **h** ← left, **j** ↓ below, **k** ↑ above, **l** → right.

### RENDER ENGINE

Shown only after the Miracast display exists (connected session). Default is
**GPU · DMA-BUF**. Pills:

| Pill | `captureEncode` | Path |
|------|-----------------|------|
| GPU · DMA-BUF | `dmabuf` | `wf-recorder` VAAPI DMA-BUF + CQP |
| GPU · VAAPI | `vaapi` | raw pipe → `hwupload` → `h264_vaapi` |
| CPU | `cpu` | raw pipe → `libx264` |

Preference is stored in `settings.json` and `$XDG_STATE_HOME/omarchy-miracast/capture-encode`
so a live session can SIGUSR1-rebind without restarting FluxCast. If a GPU path
fails, FluxCast falls back toward CPU; the **active** pill follows the resolved
path (`capturePath` / `encoder` in `miracast-ctl status`), not only the preference.

**Capture cadence:** Miracast Extend defaults to **damage-aware** capture
(omit `wf-recorder -D`) so Hyprland only produces frames when the output
changes — lower CPU, cursor still smooth. Force continuous grab with
`FLUXCAST_WFD_WF_RECORDER_DAMAGE=0`.

**Optional ICC wf-recorder (PR #347):** not required. Default is the distro
`wf-recorder` on `PATH` (wlr-screencopy). To opt into a local ICC build until
upstream ships it, set an absolute path explicitly:

```json
"wfRecorderBin": "/usr/local/bin/wf-recorder-icc",
"wfRecorderProto": "auto"
```

Or export `FLUXCAST_WFD_WF_RECORDER_BIN` / `FLUXCAST_WFD_WF_RECORDER_PROTO`
before connect. Env wins over settings. There is **no** automatic probing of
`~/src/...` build trees. If `wfRecorderProto` is `icc` but the binary is missing
or not ICC-capable, Miracast falls back to PATH stock and logs a warning.
See FluxCast `DOCUMENTATION.md`.

While connected, Hyprland **animations** are turned off and restored on stop.
Borders/gaps stay on so focus rings and the workspace indicator keep working.
Software cursors stay on (required for a visible pointer in screencopy).

**Extend scale:** default is `1` (`defaultExtendScale`) for lower Hyprland cost;
override per sink in the Display panel (`sinkScales`).

```bash
miracast-ctl set-capture-encode dmabuf|vaapi|cpu
```

## Virtual output lifecycle (eDP safety)

Miracast **must not** call `hyprctl output remove` / `monitor,disable` during
connect or disconnect — those calls have frozen the primary display for
30–90s. Stop leaves the virtual output in place; the next Extend session
**reuses** it. Orphan cleanup is never automatic on the hot path.

## Cast modes

| Mode | Behavior |
|------|----------|
| **Mirror** | Captures the current desktop monitor |
| **Extend** | Creates a Hyprland virtual output beside the laptop and casts that |

## Shortcuts (Display panel)

| Key | Action |
|-----|--------|
| S | Scan sinks |
| F | Open UFW ports |
| D | Doctor |
| C | Connect last device / reconnect |
| X | Stop cast |
| M | Mirror mode |
| E | Extend mode |

## License

MIT — see [LICENSE](LICENSE).
