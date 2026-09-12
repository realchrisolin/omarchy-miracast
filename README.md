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

Plus a [FluxCast](https://github.com/IlyaP358/fluxcast) source tree:

```bash
export FLUXCAST_ROOT=/path/to/fluxcast
```

Apply the included performance / stream-mode patches (recommended):

```bash
# See patches/fluxcast/APPLY.md
```

## Settings

Override in `~/.config/omarchy-miracast/settings.json`:

| Key | Default | Notes |
|-----|---------|--------|
| `mode` | `extend` | `mirror` or `extend` |
| `extendPosition` | `right` | `left` / `right` / `above` / `below` |
| `streamMode` | `1280x720p30` | e.g. `1920x1080p30` — drives fps + resolution |
| `fps` | `30` | Miracast CEA HD is typically 30 or 60 |
| `outputRes` | `1280x720` | Encoded size |
| `extendResolution` | `1280x720` | Extend virtual output capture size |
| `extendRefresh` | `30` | Virtual output Hz (keep aligned with stream) |
| `bitrate` | `4M` | Trimmed further on battery / power-saver |
| `videoEncoder` | `auto` | `auto` → VAAPI, else QSV, else `libx264` |

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
