# Miracast / Wi‑Fi Display — quick guide

Cast this Omarchy desktop to a TV or dongle over **Wi‑Fi Direct** (not your
normal Wi‑Fi SSID). Video is H.264; audio is typically LPCM on many TVs.

Press **q** (or Ctrl+C) to close this window.

---

## What you need

| Piece | Role |
|-------|------|
| **Wi‑Fi adapter with P2P** | Creates the Miracast link (Intel AX201 works; a USB P2P dongle can help) |
| **FluxCast** | Speaks the Miracast/WFD protocol (RTSP + RTP) |
| **wf-recorder + ffmpeg** | Capture & encode (DMA-BUF or VAAPI pipe) |
| **dnsmasq** | DHCP on the P2P link |
| **NetworkManager (`nmcli`)** | Brings up Wi‑Fi Direct |
| **PipeWire / Pulse** | Miracast null sink for desktop audio capture |

Optional: **UFW** — if enabled, Miracast ports must be allowed (see below).

---

## Panel CONTROLS

| Control | What it does |
|---------|----------------|
| **Scan** | Find nearby Miracast sinks |
| **Doctor** | Diagnose tools, engines, encode, audio, radio, workspaces. If UFW is blocking Miracast ports, offers to open them (sudo). |
| **Info** | Opens the themed help window (this doc via CLI) |
| **Stop / Reconnect** | End the cast, or reconnect to the last device |

**Persist display** — keep the Extend desktop when switching TVs.  
**Automatically switch audio** — after the cast is *streaming*, set default output to Miracast (speakers stay default during connect).

**ADVANCED SETTINGS** — stream mode, render engine (**GPU + DMA-BUF** / **GPU** / **CPU**),
preset quality, P2P radio.

---

## Firewall ports (if you use UFW or another firewall)

| Port | Purpose |
|------|---------|
| **7236/tcp** | WFD RTSP control |
| **67/udp**, **68/udp** | DHCP on P2P |
| **19000–19100/udp** | Local RTP |
| **42000–42100/udp** | Sink RTP/RTCP |

If **ufw is not installed**, Doctor cannot open ports for you — allow the
list above in nftables/firewalld/your router policy as needed.

---

## Tips for a good picture

- **20 MHz / 2.4 GHz:** can sustain **>20 Mbps** clear video on current DMA-BUF
  CQP (older ~22 Mbps “corruption” line was too pessimistic). Distortion / drop
  on hotyeah MCC showed up nearer **~40–50 Mbps** with very sharp QP (e.g. qp1).
- **GPU pipe** (id `vaapi`) presets use hard **CBR** when the encoder is VAAPI
  (High = **15 M / quality=3 / async=1 / VBV 1.0**; Medium 12 M; Low 8 M).
  **GPU + DMA-BUF High** uses **CQP qp=18 / quality=2 / i_qfactor=1.3**.  
  Do **not** use QVBR on VAAPI pipe (undershoots).  
  Avoid pipe **quality < 3** / **async > 1** — quality=1 hung the encode pipe.  
  FluxCast clamps those and auto-rebinds on `VIDEO_STALL` / `bufs_size` storms.  
  NVIDIA hosts use **NVENC** on the GPU pipe when available (DMA-BUF skipped).  
- **Best (Dynamic)** walks the full encode ladder from link health — DMA-BUF
  **every H.264 QP 1–51**, pipe/CPU dense CBR ~48→3 Mbps — not only the named
  High/Medium/Low pills. Starts near **High** (qp18). Demotes on **retries /
  delivery failures / true stalls** (not a fixed Mbps cap). After **3 demotes**,
  climbing stops; each demote raises a **climb floor** (won’t return to the QP
  that failed). If **signal improves ≥6 dB**, settle clears but the floor only
  relaxes **one step** (no free-run to qp1). Under DMA-BUF **CQP**, soft TX
  alone does **not** demote — quiet UI (e.g. Waydroid) at ~2 Mbps / 30 fps is
  normal. Each step rebinds capture (brief freeze). Pill shows e.g.
  **Best (qp18)**. **Lock Best** freezes the current QP. Manual **Very High**
  remains 5 GHz-gated.  

- After changing engine or preset quality while streaming, capture restarts
  automatically (`restart-capture` / SIGUSR1) — full reconnect is usually not required.  
- Soak continuous `-D` VAAPI pipe: `./scripts/soak_vaapi_pipe.py --duration 1800`.  
- Keep eDP and Miracast desktops separate (`ext-*` on the TV, numbers on the laptop).

## Wi‑Fi channel (“quiet” picking)

Default is **SCC** (same channel as home Wi‑Fi). Opt-in **quiet CSA** can move
P2P after PLAY. “Quiet” is only a **score threshold**; channels are ranked by
an interference **score** from nearby APs (lower is better). That ranking has
matched real Miracast TX retries on 2.4 GHz (e.g. ch 11 better than ch 1 when
the home AP and strong neighbors sit on/near ch 1). See README § P2P channel.
Inspect: `miracast-ctl pick-channel` or `./scripts/pick-p2p-channel.py --json --band 2.4`.

### 2.4 listen vs 5 GHz oper (common dongles)

Many Miracast sticks (e.g. Realtek **8192CU**) **advertise listen on 2.4 GHz**
but often accept a **5 GHz operating channel** (`oper_freq=5220` while
`listen_freq=2437`). Status / Doctor trust **oper** for air band
(`p2pFreqSource=peer_oper`). Check with:

```bash
./scripts/verify_p2p_air_band.py
miracast-ctl status   # p2pFreqMHz / p2pFreqSource / radioMcc
```

If **oper stays on 2.4** while laptop STA is **5 GHz** → true **MCC**. Industry
docs treat that as a known glitch tax. Software can pick a quieter 2.4 channel
and cap bitrate; it cannot invent 5 GHz oper on a 2.4-only sink. A dual‑band
sink or a **second idle Wi‑Fi NIC** (`p2pWifiInterface`) unlocks clean 5 GHz
SCC / avoids one-radio HT20 tax.

While streaming, a **link watcher** keeps cost low: it polls P2P TX bytes every
few seconds and only runs a **cached** `nmcli` score (no forced rescan) after
sustained high retries. Soft TX (~2 Mbps) with **healthy video fps (~30)** is
**not** treated as frozen (common for CQP + quiet UI). True freezes need collapsed
fps / `VIDEO_STALL` / UDP errors / TX≈0. A much quieter channel can trigger a CSA
(cooldown + hysteresis).

---

## Troubleshooting

1. Run **Doctor** and read STATUS (warnings name the area: `audio`, `firewall`, `link`, `radio_channel`, …).  
2. Confirm the TV is in Miracast / screen-mirroring receive mode.  
3. If video is fine but silent: set Sound default to **Miracast** (or enable auto-switch) and raise that sink’s volume.  
4. If Doctor says live encode ≠ settings: reconnect.  
5. Frozen TV picture with audio still going: FluxCast should log `VIDEO_STALL` and rebind within a few seconds; if not, run `miracast-ctl restart-capture`. Check `cast.log` for `bufs_size` storms / ffmpeg IO watchdog. Do **not** treat switching to DMA-BUF as the only fix — pipe VAAPI should recover.  
6. Occasional brief glitches with audio OK and TX still ~12 Mbps: usually 2.4 GHz RF / MCC — try a quieter channel, lower bitrate slightly, or soft‑block Bluetooth; a 5 GHz‑capable sink is the lasting fix.  
7. Logs: `~/.local/state/omarchy-miracast/logs/cast.log` / `link-watch.log`

CLI equivalents: `miracast-ctl doctor --fix`, `miracast-ctl info`, `miracast-ctl scan`.
