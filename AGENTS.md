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
- **Quiet P2P by default = SCC.** Opt-in quiet CSA only (`p2pQuietCsa`). For air/UI freq: prefer peer **`oper_freq` when 5 GHz**; only fall back to `listen_freq` when iw GO merely mirrors STA 5 GHz and the peer has no 5 GHz oper (Intel MCC “iw lie”). Dual idle NIC preferred over sharing STA (`p2pWifiInterface=auto`).
- **20 MHz / 2.4 GHz budget (updated from hotyeah soaks):** PHY still ~72 Mbps HT20; usable Miracast is lower, but **>20 Mbps can stay clear**. Older “corruption above ~22 Mbps” was **too conservative** for current DMA-BUF CQP. Practical cliff on this MCC path was closer to **~40–50 Mbps** (sharp QP / q=1) with retries→UDP death — not 20. Named High/Medium/Low remain conservative; **Very High** stays **5 GHz+** gated as a pinned sharp preset.
- **RENDER ENGINE pills:** **GPU + DMA-BUF** / **GPU** / **CPU** (ids still `dmabuf`/`vaapi`/`cpu`). GPU pills show `(<card-reported lspci name>)`. GPU + DMA-BUF falls back to GPU pipe then CPU. DMA-BUF uses VAAPI when usable (Intel/AMD Mesa); NVIDIA skips DMA and uses NVENC on the GPU pipe when available.
- **Encoding strategy** (`encodeStrategy`): **`smartview`** (default) = QVBR + BitrateController; try **DMA-BUF first**, auto-fallback to **VAAPI pipe** if DMA BRC starves (Intel historically ~0.5–3 Mbps). **`performance`** = DMA-BUF **CQP** only (Best uses QP ladder — no `set-bitrate-kbps`). Pipe: avoid **quality &lt; 3** / **async &gt; 1** (quality=1 hung → `bufs_size` / audio-only).
- **Best (Dynamic):** under **smartview** → SV ABR (`bitrate_controller.py`). Under **performance** → DMA-BUF **QP 1–51** ladder (`dynamic_encode.py`). Each apply = `encode.env` + SIGUSR1 rebind (brief freeze). **`encodeBestLocked`** freezes the step.
- **Settle after 3 demotes:** climb hold only at **≥3 demotes**, until demotes **decay** (long clean streak) or **signal improves ≥10 dB**. Demotes 1–2 still allow climbing but a **soft climb ceiling** (encodeBestClimbFloorStep) blocks re-climbing sharper than the last demote target until demotes fully clear — stops the qp12→qp9→UDP race. Cliff climbs need a longer clean streak per step. **No hard permanent climb floor**.
- **No leftover helpers:** audio-route runs as `scripts/miracast_audio_route_watch.sh` (own process group), link-watch / health-watch have pidfiles, and stop reaps orphans by name so `ps` does not fill with stuck `miracast-ctl start` shells.
- **Zombie streaming:** `cmd_status` must reconcile when phase=streaming but media is frozen (Sender health video_frames flat ≥25s, or no health ≥45s) — stop and write idle so the plugin does not keep showing an active cast after the TV goes dark. UDP EBADF during rebind must not suppress stop forever (≤20s window).
- **Dead sink → stop (not endless restart):** `miracast_link_watch.py` must `miracast-ctl stop` when the TV is gone but P2P/FluxCast linger — peer inactive / no stations, UDP send-error storm, or sustained TX≈0. Otherwise the UI stays on `streaming`.
- **Demote on failures / fps sag / severe retries, not Mbps:** prefer **tx failed** / UDP / true stalls. **Video fps &lt; ~28** demotes on the **first** bad tick; **fps &lt; ~24** is severe. In the **cliff zone (≈qp≤10)**, fps&lt;29 and any tx-failed are severe (multi-step). Mild MCC retry% alone must **not** demote (only ≥~5%). Do **not** use a TX Mbps ceiling under CQP.
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
