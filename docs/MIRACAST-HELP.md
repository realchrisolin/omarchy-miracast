# Miracast help

Cast to a TV/dongle over **Wi‑Fi Direct** (not your home Wi‑Fi).
Press **q** to close.

## What you need

- Wi‑Fi adapter with **P2P** (Intel AX201 OK; USB P2P dongle helps)
- **FluxCast** — Miracast/WFD protocol
- **wf-recorder** + **ffmpeg** — capture & encode
- **dnsmasq** — DHCP on the P2P link
- **NetworkManager** (`nmcli`) — Wi‑Fi Direct
- **PipeWire** — Miracast audio sink

Optional: **UFW** (if active, ports below must be allowed).

## CONTROLS

| Button | Action |
|--------|--------|
| **Scan** | Find nearby sinks |
| **Check & fix** | Diagnose; open UFW ports if blocked |
| **Info** | This help |
| **Stop / Reconnect** | End cast, or reconnect last device |

Also under Miracast: **Persist display**, **Automatically switch audio**
(switches default output only after streaming starts), and **ADVANCED
SETTINGS** (stream / engine / quality / radio).

## Firewall ports

| Port | Use |
|------|-----|
| **7236/tcp** | RTSP control |
| **67–68/udp** | DHCP |
| **19000–19100/udp** | Local RTP |
| **42000–42100/udp** | Sink RTP/RTCP |

No **ufw**? Open these in nftables/firewalld yourself — Check & fix
cannot do it.

## Picture tips

- Prefer **DMA-BUF** + preset **High** when stable
- On a **20 MHz** P2P link, pipe path uses capped QVBR (avoid huge spikes)
- After engine/quality changes: **Reconnect**
- Keep laptop (`eDP`) and TV (`ext-*`) workspaces separate

## Stuck?

1. **Check & fix** — read STATUS warnings (`audio`, `firewall`, `link`, …)
2. Put the TV in Miracast / screen-mirror receive mode
3. Silent but video OK → Sound default **Miracast** (or enable auto-switch)
4. Live encode ≠ settings → reconnect
5. Logs: `~/.local/state/omarchy-miracast/logs/cast.log`

CLI: `miracast-ctl doctor --fix` · `info` · `scan`
