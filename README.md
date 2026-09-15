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
- P2P Wi‑Fi radio picker (Auto prefers idle P2P-GO adapters; optional quiet-channel CSA)
- Safe scale / position changes (pause capture → move → restart)
- Safer disconnect teardown (workspace migrate, cursor restore, eDP re-assert)
- Optional VAAPI/QSV encode with OS power-plan throttling (FluxCast `power_plan_N`)
- **Persist display across monitors** (default on): switching Miracast sinks keeps the same Extend desktop on a shared Hyprland output named `persistent-miracast`; off uses a peer-named head and seeds a fresh desktop. Empty leftover heads from earlier peer names are **not** destroyed (Hyprland `output remove` can freeze eDP); `miracast-ctl status` lists them as `orphanExtendHeads`. They clear on Hyprland restart.

## Install

You need three pieces: this **Display** plugin, OS packages, and a **FluxCast**
tree with the WFD patches this panel expects. The bar label stays **Display**.

### 1. Packages

```bash
omarchy pkg add dnsmasq wf-recorder xorg-xrandr ffmpeg
```

Also needed (usually already present on Omarchy): NetworkManager, PipeWire /
WirePlumber or PulseAudio, `iw`, `nmcli`, Python 3. Optional: `ufw` so
**Check & fix** can open Miracast ports for you.

### 2. Install the plugin

**From git (recommended for users):**

```bash
omarchy plugin add https://github.com/realchrisolin/omarchy-miracast.git --enable
```

That clones into `~/.config/omarchy/plugins/<id>/` (id comes from
`manifest.json`, e.g. `realchrisolin.monitor`), places the bar widget, and enables it.
Disable the stock Display widget if it is still enabled:

```bash
omarchy plugin disable omarchy.monitor
omarchy plugin list   # expect your *.monitor enabled, omarchy.monitor disabled
```

**From a local checkout (developers):** keep one git tree and symlink it so edits
hot-reload without copying:

```bash
git clone https://github.com/realchrisolin/omarchy-miracast.git ~/code/other/omarchy-miracast
mkdir -p ~/.config/omarchy/plugins
ln -sfn ~/code/other/omarchy-miracast ~/.config/omarchy/plugins/realchrisolin.monitor

omarchy plugin validate ~/.config/omarchy/plugins/realchrisolin.monitor
omarchy plugin enable realchrisolin.monitor --section right   # or left / center
omarchy plugin disable omarchy.monitor
```

Confirm `~/.config/omarchy/shell.json` bar layout references your plugin id
(e.g. `"id": "realchrisolin.monitor"`), not `omarchy.monitor`. Soft-reload:

```bash
omarchy-shell shell rescanPlugins
```

QML icon/layout changes sometimes need a full shell restart:

```bash
rm -rf ~/.cache/quickshell/qmlcache
omarchy restart shell
```

### 3. FluxCast

Point Miracast at a FluxCast **source tree** (not an AppImage alone). Prefer a
fork that already includes the Omarchy WFD work (encode.env reload, LPCM,
pipe/DMA-BUF, SIGUSR1 restart), e.g.
[realchrisolin/fluxcast](https://github.com/realchrisolin/fluxcast)
`feat/wfd-hw-encode-modes`, or apply `patches/fluxcast/` onto upstream
[IlyaP358/fluxcast](https://github.com/IlyaP358/fluxcast) — see
[`patches/fluxcast/APPLY.md`](patches/fluxcast/APPLY.md).

```bash
git clone https://github.com/realchrisolin/fluxcast.git ~/code/other/fluxcast
cd ~/code/other/fluxcast && git checkout feat/wfd-hw-encode-modes   # if using that fork
```

`miracast-ctl` resolves FluxCast in this order:

1. `$FLUXCAST_ROOT` (environment)
2. `fluxcastRoot` in `~/.config/omarchy-miracast/settings.json` (`~/…` or `$HOME/…` OK)
3. Sibling `../fluxcast` next to this plugin checkout (e.g. `~/code/other/fluxcast` beside `~/code/other/omarchy-miracast`)

```bash
# Portable — prefer $HOME, not a hard-coded /home/<user>/…
export FLUXCAST_ROOT="${FLUXCAST_ROOT:-$HOME/code/other/fluxcast}"
# Or persist:
#   { "fluxcastRoot": "$HOME/code/other/fluxcast" }  in settings.json
```

If an old AppImage extract still lives under
`vendor/squashfs-root/usr/src/fluxcast`, replace that directory with a
**symlink** to the git checkout so paths cannot drift.

### 4. Optional: `miracast-ctl` on PATH

The panel always runs `pluginDir/bin/miracast-ctl`. For a terminal:

```bash
mkdir -p ~/.local/bin
ln -sfn ~/.config/omarchy/plugins/realchrisolin.monitor/bin/miracast-ctl ~/.local/bin/miracast-ctl
# ~/.local/bin is usually already on PATH on Omarchy
miracast-ctl status
miracast-ctl doctor
```

### 5. Smoke-check

```bash
miracast-ctl doctor          # or: Check & fix in the Display panel
miracast-ctl scan            # nearby Miracast sinks
```

Put the TV/dongle in Miracast / screen-mirroring receive mode, then **Scan** and
connect from the Display panel. Day-to-day tips (audio, channels, 2.4-only
dongles): panel **Info**, or [`docs/MIRACAST-HELP.md`](docs/MIRACAST-HELP.md).

**Hardware note:** many cheap Miracast sticks are **2.4 GHz-only** (e.g. Realtek
8192CU). With laptop Wi‑Fi on 5 GHz that means MCC — occasional brief glitches
are expected; a dual-band sink is the lasting RF fix. Doctor warns as `radio_mcc`
when this is active.

## Settings

`status` JSON includes live radio fields (no SSIDs): `staChannel`, `p2pChannel`,
`staWidthMHz`, `p2pRole`, `radioMcc` (true when STA and P2P channels differ).

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
| `audioEnabled` | `true` | Creates a PipeWire/Pulse **Miracast** null sink for capture. FluxCast captures **`miracast.monitor`**. LPCM-only TVs use WFD `stream_type=0x83`; keep the Miracast sink near **100%** or the TV sounds faded. See [BUILD.md §6](BUILD.md). |
| `autoSwitchAudioOutput` | `true` | Panel: **Automatically switch audio output**. On: default sink → Miracast at PLAY (speakers held during connect). Off: leave the currently selected output; still create the Miracast sink for manual routing / capture. |
| `extendRefresh` | `30` | Hyprland refresh for the Extend virtual output. Keep matched to stream fps (30) so capture does not outrun encode. |
| `softwareCursors` | `true` | Force SW cursors so the pointer appears on the TV (HW cursor plane is not captured). |
| `wfRecorderBin` | unset | Absolute path to a custom `wf-recorder` (e.g. ICC / PR #347). Empty = **PATH** stock. ICC preferred for perf; see [BUILD.md §7](BUILD.md) for Extend terminal typing lag. |
| `wfRecorderProto` | `auto` | `auto` / `icc` / `wlr`. `auto` upgrades to `icc` when a configured binary advertises ICC. Use `wlr` + stock binary if cast-head terminal keys feel buffered until the pointer moves. |
| `wfRecorderDamage` | `"1"` | `"1"` = damage-aware (omit `wf-recorder -D`); `"0"` = continuous `-D`. Set by `scripts/recommend-cast-profile.py --apply` or manually. |
| `captureEncode` | `dmabuf` | Render engine: `dmabuf` \| `vaapi` (pipe) \| `cpu`. |
| `encodeProfile` | `medium` | Quality tier **high\|medium\|low** — knobs are **per engine** (`scripts/encode_quality_presets.py`). |
| `castPreset` | `desktop` | Content hint: `desktop` = damage-aware; `movie` = continuous `-D`. |
| `vaapiQuality` | *(from profile)* | ffmpeg `h264_vaapi` `-quality` (1–8; higher = faster/worse). |
| `vbvMultiplier` | `0.5` | CBR VBV as a fraction of bitrate (~0.5 s). → `FLUXCAST_WFD_VBV_MULTIPLIER`. |
| `p2pWifiInterface` | `auto` | Managed Wi‑Fi iface for Miracast P2P, or `auto` (prefer idle P2P-GO). |
| `p2pQuietCsa` | `false` | `true` = post-PLAY CSA to a quiet channel (MCC). Default **SCC**. |
| `vaapiRcMode` | *(from profile)* | `CQP` / `QVBR` / `CBR` / `VBR` → `FLUXCAST_WFD_VAAPI_RC`. |
| `vaapiBitrate` | *(from profile)* | QVBR/CBR peak (`FLUXCAST_WFD_VAAPI_BITRATE`). |
| `vaapiQp` | *(from profile)* | Quantizer (lower = sharper). |
| `vaapiGop` | *(from profile)* | GOP frames. |
| `vaapiAsyncDepth` | `2` | Pipe `async_depth` (from profile). |
| `sinkScales` | `{}` | Per-sink Extend scale, keyed by MAC (overrides default) |
| `defaultExtendScale` | `1` | Extend scale when a sink has no `sinkScales` entry — **1** is cheapest for Hyprland |
| `onlyExpandFocusedDisplay` | `true` | Display panel accordion: only the **focused** output expands nested controls |

### Display panel expansion

By default every enabled display row is expanded (Brightness / Scale / cast
controls). To restore single-row accordion behavior:

```json
"onlyExpandFocusedDisplay": true
```

While connected, the Miracast display row shows **CAST MODE** / **EXTEND
POSITION** (← ↑ ↓ → when Extend) and **SCALE**. Under **MIRACAST** (below **CONTROLS**): collapsible **ADVANCED SETTINGS**
(**STREAM MODE**, **RENDER ENGINE**, **PRESET QUALITY**, **RADIO**).
Scan / Check & fix / Info / Stop stay under **CONTROLS**. Only the focused
display row expands nested brightness/scale/cast controls.

With focus on the CAST MODE / EXTEND POSITION row and Extend active, vim
**hjkl** set position: **h** ← left, **j** ↓ below, **k** ↑ above, **l** → right.

### RENDER ENGINE

Shown only after the Miracast display exists (connected session). Default is
**GPU · DMA-BUF**. Pills:

| Pill | `captureEncode` | Path |
|------|-----------------|------|
| GPU · DMA-BUF | `dmabuf` | `wf-recorder` VAAPI DMA-BUF |
| GPU · VAAPI | `vaapi` | raw pipe → `hwupload` → `h264_vaapi` |
| CPU | `cpu` | raw pipe → `libx264` |

```bash
miracast-ctl set-render-engine dmabuf|vaapi|cpu
# alias: set-capture-encode
```

Preference is stored in `settings.json` and `$XDG_STATE_HOME/omarchy-miracast/capture-encode`.
Changing engine **retargets** the current **QUALITY** tier (same label, different
knobs). Encode RC/QP/bitrate need a **reconnect** to apply (process env); a
SIGUSR1 capture restart alone is not enough for those. If a GPU path fails,
FluxCast falls back toward CPU; the **active** pill follows the resolved path
(`capturePath` / `encoder` in `miracast-ctl status`), not only the preference.

### PRESET QUALITY

Independent of render engine: **High** / **Medium** / **Low**. Knobs are looked
up per engine in `scripts/encode_quality_presets.py` (DMA-BUF high ≠ VAAPI-pipe
high). Under **MIRACAST → ADVANCED SETTINGS** (collapsed by default).

```bash
miracast-ctl set-quality high|medium|low
# alias: set-encode-profile
```

Reconnect if streaming (`needsReconnect: true`). `castPreset` (`desktop` /
`movie`) only toggles damage-aware vs continuous `-D`; it does not own RC/QP.

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

To score [BENCHMARKS.md](BENCHMARKS.md) results into settings (`captureEncode`,
`wfRecorderBin` / `Proto` / `Damage`, `videoEncoder`):

```bash
./scripts/recommend-cast-profile.py          # dry-run + docs/benchmarks/recommended.env
./scripts/recommend-cast-profile.py --apply  # update settings.json
miracast-ctl set-render-engine dmabuf|vaapi|cpu   # RENDER ENGINE (alias: set-capture-encode)
miracast-ctl set-quality high|medium|low          # QUALITY tier per engine (alias: set-encode-profile)
miracast-ctl set-cast-preset movie|desktop        # damage-aware vs continuous -D
miracast-ctl benchmark all                        # live matrix: every engine × every QUALITY tier
miracast-ctl benchmark --engines dmabuf,vaapi --tiers high,medium
# If already casting, matrix mode counts down 10s (Ctrl+C cancels; -y skips)
```

See [BENCHMARKS.md](BENCHMARKS.md) for presets and Intel CBR undershoot notes.
FluxCast auto-rebinds capture on buffer-pool / DTS spikes (RTSP stays up).

While connected, Hyprland **animations** are turned off and restored on stop.
Borders/gaps stay on so focus rings and the workspace indicator keep working.
Software cursors stay on (required for a visible pointer in screencopy).

**Extend scale:** default is `1` (`defaultExtendScale`) for lower Hyprland cost;
override per sink in the Display panel (`sinkScales`).

```bash
miracast-ctl benchmark                      # probes + live sample if streaming
miracast-ctl benchmark --offline-only       # capability-only (no live sample)
miracast-ctl benchmark --apply              # write settings.json + recommended-cast.env
miracast-ctl set-render-engine dmabuf|vaapi|cpu
miracast-ctl list-p2p-radios                # Auto / iface + P2P-GO / STA flags
miracast-ctl set-p2p-wifi-interface auto|IFACE
miracast-ctl pick-channel                   # scored quiet/quietest (default 5 GHz)
miracast-ctl pick-channel --band 2.4 --json
./scripts/bench_p2p_channel.sh              # SCC vs MCC TX A/B
```

`benchmark` probes VAAPI/`wf-recorder` support and P2P-GO radios, merges
`docs/benchmarks/results.tsv` when present, and — if a cast is already
streaming — runs a ~25s live TX/retry/CPU sample to refine the advice.
Use `--offline-only` to skip the live sample. `--apply` writes settings
(once per machine, or after adding a USB Wi‑Fi dongle).

### P2P channel (SCC vs quiet CSA)

By default Miracast uses **SCC**: force the P2P GO onto the **STA primary
channel** (`p2p_ignore_shared_freq=0`, `--wfd-p2p-channel=<STA>`), and after
PLAY align with CSA if negotiation landed elsewhere. Set `p2pQuietCsa: true`
(or `MIRACAST_P2P_QUIET_CSA=1`) to CSA onto a quieter channel instead (MCC).
If STA is 5 GHz but the sink/GO stays on 2.4 (common with 2.4-only dongles
like Realtek **8192CU** — WPS often reports `manufacturer=Realtek`,
`model_name=8192CU`), cross-band CSA is skipped and a quieter **2.4** channel
is used instead — true 5 GHz SCC is impossible with those sinks. That **MCC**
setup (STA on 5 GHz + P2P on 2.4) is a known Miracast quality tax in vendor
docs (Microsoft eCSA / multi-channel notes; ScreenBeam “DCM”); expect
**occasional brief glitches** from 2.4 interference even when TX looks healthy.
Mitigate with quiet-channel pick + bitrate headroom; fix properly with a
**dual-band** sink.

**How “quiet” channel picking works** (`scripts/pick-p2p-channel.py`): the
label *quiet* is only a score threshold (default ≤ 5). What actually ranks
channels is a numeric **interference score** (lower is better): visible APs
from `nmcli` add cost on their channel and bleed into neighbors; stronger APs
cost more. When nothing is under the threshold (typical on 2.4 GHz), the
picker chooses the **lowest score** (“quietest”). That ranking is what
matters — and it has matched real Miracast **TX retry** rates on-device
(2026-09-15, AX201 + 2.4-only dongle: among `{1,6,11}`, ch 11 best / fewest
retries, ch 1 worst because the home 2.4 AP and strong adjacent BSS sit
there). Inspect scores with:

```bash
./scripts/pick-p2p-channel.py --json --band 2.4
miracast-ctl pick-channel
```

Developer wiring (CSA commands, tests) lives in [BUILD.md](BUILD.md) § P2P
channel.

**While streaming**, `scripts/miracast_link_watch.py` (started after PLAY) watches
the link cheaply: sysfs TX bytes every ~8s, `iw station dump` every few ticks,
and a **cached** `nmcli wifi list` score at most every ~3 minutes and only after
sustained high retries / IDR bursts. Stall (TX &lt; ~3 Mbps for ~24s — often a
wedged VAAPI pipe) → `restart-capture`. A clearly better 2.4 GHz score can CSA
(5 min cooldown, max 2/hour, score margin). No forced Wi‑Fi rescans on the hot
path.

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
| D | Check & fix (diagnose; open UFW ports if needed) |
| I | Miracast help (themed card; CLI uses terminal) |
| C | Connect last device / reconnect |
| X | Stop cast |
| M | Mirror mode |
| E | Extend mode |

## License

MIT — see [LICENSE](LICENSE).
