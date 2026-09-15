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
| **Check & fix** | Runs Doctor (tools, engines, encode, audio, radio, workspaces). If UFW is blocking Miracast ports, offers to open them (sudo). |
| **Info** | Opens the themed help window (this doc via CLI) |
| **Stop / Reconnect** | End the cast, or reconnect to the last device |

**Persist display** — keep the Extend desktop when switching TVs.  
**Automatically switch audio** — after the cast is *streaming*, set default output to Miracast (speakers stay default during connect).

**ADVANCED SETTINGS** — stream mode, render engine (DMA-BUF / VAAPI / CPU), preset quality, P2P radio.

---

## Firewall ports (if you use UFW or another firewall)

| Port | Purpose |
|------|---------|
| **7236/tcp** | WFD RTSP control |
| **67/udp**, **68/udp** | DHCP on P2P |
| **19000–19100/udp** | Local RTP |
| **42000–42100/udp** | Sink RTP/RTCP |

If **ufw is not installed**, Check & fix cannot open ports for you — allow the
list above in nftables/firewalld/your router policy as needed.

---

## Tips for a good picture

- **20 MHz** P2P budget ≈ **12–14 Mbps** video (corruption seen above ~22).  
- **VAAPI pipe** presets use CBR (High ≈ 12 M, quality=4); DMA-BUF High uses CQP.  
  Avoid pipe **quality=1** — it can hang the encode pipe (frozen picture, audio-only).  
- After changing engine or preset quality, **reconnect** (or `restart-capture`) so encode settings reload.  
- Keep eDP and Miracast desktops separate (`ext-*` on the TV, numbers on the laptop).

## Wi‑Fi channel (“quiet” picking)

Default is **SCC** (same channel as home Wi‑Fi). Opt-in **quiet CSA** can move
P2P after PLAY. “Quiet” is only a **score threshold**; channels are ranked by
an interference **score** from nearby APs (lower is better). That ranking has
matched real Miracast TX retries on 2.4 GHz (e.g. ch 11 better than ch 1 when
the home AP and strong neighbors sit on/near ch 1). See README § P2P channel.
Inspect: `miracast-ctl pick-channel` or `./scripts/pick-p2p-channel.py --json --band 2.4`.

### 2.4‑only sinks (common cheap dongles)

Many Miracast sticks (e.g. Realtek **8192CU**, advertised via WPS as
`manufacturer=Realtek` / `model_name=8192CU`) are **2.4 GHz only**. If your
laptop STA is on **5 GHz**, P2P cannot follow → **MCC** (two channels at once).
Industry Miracast docs (Microsoft, ScreenBeam, etc.) treat that as a known
cause of **occasional pixelation / glitches** even when the link looks fine.
Software can pick a quieter 2.4 channel and cap bitrate; it cannot invent 5 GHz
on the dongle. A dual‑band sink unlocks real 5 GHz SCC.

While streaming, a **link watcher** keeps cost low: it polls P2P TX bytes every
few seconds and only runs a **cached** `nmcli` score (no forced rescan) after
sustained high retries. Frozen video (TX stuck near audio-only ~2 Mbps) triggers
`restart-capture`; a much quieter channel can trigger a CSA (cooldown + hysteresis).

---

## Troubleshooting

1. Run **Check & fix** and read STATUS (warnings name the area: `audio`, `firewall`, `link`, `radio_channel`, …).  
2. Confirm the TV is in Miracast / screen-mirroring receive mode.  
3. If video is fine but silent: set Sound default to **Miracast** (or enable auto-switch) and raise that sink’s volume.  
4. If Doctor says live encode ≠ settings: reconnect.  
5. Frozen TV picture with audio still going: wait for auto `restart-capture`, or run `miracast-ctl restart-capture`.  
6. Occasional brief glitches with audio OK and TX still ~12 Mbps: usually 2.4 GHz RF / MCC — try a quieter channel, lower bitrate slightly, or soft‑block Bluetooth; a 5 GHz‑capable sink is the lasting fix.  
7. Logs: `~/.local/state/omarchy-miracast/logs/cast.log` / `link-watch.log`

CLI equivalents: `miracast-ctl doctor --fix`, `miracast-ctl info`, `miracast-ctl scan`.
