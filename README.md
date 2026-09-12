# Omarchy Display + Miracast

Extends the Omarchy **Display** bar panel (brightness, text size, scale,
per-output controls) with **Miracast / Wi‑Fi Display** mirror and extend.

This is still the Display dropdown — not a separate cast icon.

Cloned from first-party `omarchy.monitor`, then extended.

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
| `bitrate` | `4M` | Trimmed further on battery / power-saver |
| `videoEncoder` | `auto` | `auto` → VAAPI, else QSV, else `libx264` |
| `sinkScales` | `{}` | Per-sink Extend scale, keyed by MAC |
| `onlyExpandFocusedDisplay` | `false` | Display panel: `false` expands all outputs; `true` = accordion (focused only) |

### Display panel expansion

By default every enabled display row is expanded (Brightness / Scale / cast
controls). To restore single-row accordion behavior:

```json
"onlyExpandFocusedDisplay": true
```

While connected, **CAST MODE**, **EXTEND POSITION**, and **STREAM MODE** live
under the Miracast display row (with **SCALE**). Scan / doctor / firewall /
Stop stay under the **MIRACAST** section.

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
