# Omarchy Miracast / Display plugin

Quickshell Display bar plugin (`manifest.json` id **`realchrisolin.monitor`**) plus `bin/miracast-ctl` and `scripts/`. Cast path depends on a patched **FluxCast** tree (see README / BUILD.md).

Keep this file short. Depth lives in the docs below — link, don’t paste.

## Documentation

| Doc | Audience / genre |
|-----|------------------|
| [README.md](README.md) | Install, settings keys, CLI, P2P channel, eDP lifecycle |
| [docs/MIRACAST-HELP.md](docs/MIRACAST-HELP.md) | End-user tips, Doctor, encode / Best (Dynamic), troubleshooting |
| [BUILD.md](BUILD.md) | FluxCast / wf-recorder / ICC performance PoC |
| [BENCHMARKS.md](BENCHMARKS.md) | Measured encode / CPU matrix |

## Hard constraints (do not regress)

- **Never freeze eDP.** Do not call `hyprctl output remove` / `monitor,disable` on the Miracast hot path (connect, disconnect, orphan cleanup). Leave virtual heads in place; reuse on next Extend. Details: README § Virtual output lifecycle.
- **LPCM-only sinks** are common. Prefer WFD LPCM (`stream_type=0x83`); don’t “fix” silence by switching away from LPCM without evidence.
- **Quiet P2P by default = SCC.** Opt-in quiet CSA only (`p2pQuietCsa`). Prefer peer `listen_freq` over Intel `iw` GO channel when they disagree.
- **20 MHz / 2.4 GHz budget (updated from hotyeah soaks):** PHY still ~72 Mbps HT20; usable Miracast is lower, but **>20 Mbps can stay clear**. Older “corruption above ~22 Mbps” was **too conservative** for current DMA-BUF CQP. Practical cliff on this MCC path was closer to **~40–50 Mbps** (sharp QP / q=1) with retries→UDP death — not 20. Named High/Medium/Low remain conservative; **Very High** stays **5 GHz+** gated as a pinned sharp preset.
- **RENDER ENGINE pills:** **GPU + DMA-BUF** / **GPU** / **CPU** (ids still `dmabuf`/`vaapi`/`cpu`). GPU pills show `(<card-reported lspci name>)`. GPU + DMA-BUF falls back to GPU pipe then CPU. DMA-BUF uses VAAPI when usable (Intel/AMD Mesa); NVIDIA skips DMA and uses NVENC on the GPU pipe when available.
- **DMA-BUF = CQP; VAAPI GPU pipe = hard CBR.** Do not put bitrate RC on DMA-BUF (starves). Do not use QVBR on VAAPI pipe (undershoots). Pipe: avoid **quality &lt; 3** / **async &gt; 1** (quality=1 hung → `bufs_size` / audio-only).
- **Best (Dynamic):** full fine ladder — DMA-BUF **QP 1–51**, pipe/CPU ~48→3 Mbps CBR (`BEST_STEPS`). Starts near High (qp18). **No hard Mbps ceiling** — climb while clean; demote only on **retries, stalls, TX failures, UDP errors** (and CBR under-delivery vs target). Under **CQP**, soft *low* TX and high TX alone must **not** demote. Each apply = `encode.env` + SIGUSR1 rebind (brief freeze). **`encodeBestLocked`** freezes the step. Logic: `dynamic_encode.py`; apply: `miracast_link_watch.py` + `set-quality-effective step:N`.
- **Settle after 3 demotes:** once Best has lowered quality **3 times**, stop climbing (no yo-yo). Each demote also raises a **climb floor** (never return to the QP that just failed). If **signal improves ≥6 dB**, clear the demote settle and allow climbs again, but only **relax the floor by one step** (probe) — never wipe it and free-run to qp1.
- **Dead sink → stop (not endless restart):** `miracast_link_watch.py` must `miracast-ctl stop` when the TV is gone but P2P/FluxCast linger — peer inactive / no stations, UDP send-error storm, or sustained TX≈0. Otherwise the UI stays on `streaming`.
- **Demote on link pain, not quiet UI:** rising retries / delivery failures / true stalls (soft TX **and** video fps collapsed). Under CQP, ~2 Mbps at 30 fps (Waydroid/static UI) is **not** a stall — do not audio-only/soft-TX demote then. Downs ignore ABR cooldown; severe pain multi-steps.
- **Plugin id** is `realchrisolin.monitor` (not `colin.monitor` / not stock `omarchy.monitor`).
- **Privacy in logs / commits / public benches:** scrub SSIDs and MACs; use the sanitize helpers when publishing benchmarks.

## Dual UI surfaces

Miracast UI is duplicated for bar + standalone display:

- `Panel.qml`
- `DisplayPanel.qml`
- Shared logic: `MiracastService.qml`, `Model.js`

Behavior/label/pill changes that touch Miracast controls must stay in sync across both QML entrypoints unless one path is intentionally different (say so in the commit). Example: Freq./Bandwidth were removed from the **Display** dropdown (`DisplayPanel.qml`) only; the bar `Panel.qml` still shows them.

## Where state lives

| Path | Role |
|------|------|
| `~/.config/omarchy-miracast/settings.json` | User settings |
| `~/.local/state/omarchy-miracast/status.json` | Live phase / radio / encode echo |
| `~/.local/state/omarchy-miracast/encode.env` | FluxCast encode knobs (SIGUSR1 reload) |
| `~/.local/state/omarchy-miracast/logs/cast.log` | FluxCast + ctl |
| `~/.local/state/omarchy-miracast/logs/link-watch.log` | Stall / ABR / CSA |

Live knob changes while streaming: `miracast-ctl set-quality` / `set-quality-effective` / `set-render-engine` → write settings + `restart-capture` (usually no full reconnect). After editing `miracast_link_watch.py`, **restart the watcher** (it does not hot-reload).

## Tests

Prefer targeted unit/CLI tests under `scripts/`:

```bash
python3 -m unittest scripts.test_dynamic_encode scripts.test_encode_quality_presets
bash scripts/test_miracast_ctl_encode_cli.sh
```

Other `scripts/test_*.py` / `test_*.sh` cover radios, channel pick, persist display, etc. Run what matches the change.

## Style

- Match surrounding code: QML/JS like neighboring Omarchy panels; Python scripts are stdlib-first with small CLIs.
- Comments: short and factual — non-obvious constraints only (e.g. why Very High is 5 GHz-gated), not narration of the edit.
- User-facing copy: prefer clear labels already in the UI (`Best (High)`, `Very High`, Doctor, PRESET QUALITY). Don’t invent new jargon.
- Do not expand scope into FluxCast or Hyprland trees unless the task requires it; this repo owns the plugin + ctl + scripts.
