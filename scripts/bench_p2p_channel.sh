#!/usr/bin/env bash
# A/B: SCC (stay on STA channel) vs MCC (CSA to quiet off-block channel).
#
# Measures P2P GO TX rate, iw station bitrate/signal, and sender_health
# continuity while home Wi-Fi stays up. Does not unmanage p2p-dev or restart NM.
#
# Usage:
#   PEER=... PEER_NAME=... ./scripts/bench_p2p_channel.sh
#   SAMPLE_S=20 SETTLE_S=8 ./scripts/bench_p2p_channel.sh
#
# Writes:
#   docs/benchmarks/p2p_channel_ab.tsv
#   docs/benchmarks/p2p_channel_ab.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CTL="${CTL:-$PLUGIN_ROOT/bin/miracast-ctl}"
SETTINGS="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy-miracast/settings.json"
STATUS_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-miracast"
OUT_DIR="${OUT_DIR:-$PLUGIN_ROOT/docs/benchmarks}"
STA_DEV="${STA_DEV:-wlp0s20f3}"
MODE="${MODE:-extend}"
SAMPLE_S="${SAMPLE_S:-20}"
SETTLE_S="${SETTLE_S:-8}"
CONNECT_TIMEOUT_S="${CONNECT_TIMEOUT_S:-90}"

mkdir -p "$OUT_DIR"

if [[ -z "${PEER:-}" && -f "$SETTINGS" ]]; then
  PEER="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("lastPeerMac",""))' "$SETTINGS")"
  PEER_NAME="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("lastPeerName","") or "Sink")' "$SETTINGS")"
fi
PEER="${PEER:?set PEER= or use a prior connected sink in settings.json}"
PEER_NAME="${PEER_NAME:-Sink}"

# Public-safe equipment (for console summary). Full private dump written at end.
HOST_INFO_JSON="$(python3 "$SCRIPT_DIR/bench_host_info.py" --settings "$SETTINGS" 2>/dev/null || echo '{}')"
HOST_INFO_FULL_JSON="$(python3 "$SCRIPT_DIR/bench_host_info.py" --full --settings "$SETTINGS" 2>/dev/null || echo '{}')"
export HOST_INFO_JSON HOST_INFO_FULL_JSON

log() { echo "[bench-p2p] $*" >&2; }

log_host_summary() {
  python3 - <<'PY' >&2
import json, os
info = json.loads(os.environ.get("HOST_INFO_JSON") or "{}")
print("[bench-p2p] equipment:")
if info.get("cpu"):
    print("  cpu:", info["cpu"])
for g in info.get("gpu") or []:
    print("  gpu:", g)
wifi = info.get("wifi") or {}
if wifi.get("pci"):
    for line in wifi["pci"]:
        print("  wifi:", line)
if wifi.get("driver") or wifi.get("firmware_version"):
    print("  wifi driver/fw:", wifi.get("driver"), wifi.get("firmware_version") or "")
if wifi.get("sta_channel"):
    print("  sta link:", wifi["sta_channel"])
if info.get("sink_display_name"):
    print("  sink:", info["sink_display_name"])
for m in info.get("monitors") or []:
    print(
        "  monitor:",
        m.get("name"),
        m.get("description") or "",
        f"{m.get('width')}x{m.get('height')}",
    )
if info.get("kernel"):
    print("  kernel:", info["kernel"])
PY
}

assert_sta() {
  nmcli -t -f DEVICE,STATE device | rg -q "^${STA_DEV}:connected" \
    && ip -4 route show default | rg -q "dev ${STA_DEV}"
}

p2p_go_iface() {
  iw dev 2>/dev/null | awk '/Interface p2p-/{iface=$2} /type P2P-GO/{print iface; exit}'
}

channel_of() {
  iw dev "$1" info 2>/dev/null | awk '/channel/{print $2; exit}'
}

wait_streaming() {
  local deadline=$((SECONDS + CONNECT_TIMEOUT_S)) phase=""
  while ((SECONDS < deadline)); do
    assert_sta || return 2
    phase="$(python3 -c 'import json;print(json.load(open("'"$STATUS_DIR"'/status.json")).get("phase",""))' 2>/dev/null || true)"
    if [[ "$phase" == "streaming" ]]; then
      sleep 2
      return 0
    fi
    if [[ "$phase" == "error" || "$phase" == "idle" ]]; then
      log "connect failed phase=$phase"
      return 1
    fi
    sleep 1
  done
  log "timeout waiting for streaming"
  return 1
}

sample_window() {
  local label="$1"
  local go tx0 tx1 rx0 rx1 dt
  local sta_ch go_ch signal tx_br inactive auth
  local health_n=0

  sleep "$SETTLE_S"
  go="$(p2p_go_iface)"
  [[ -n "$go" ]] || { log "no GO iface"; return 1; }
  assert_sta || return 2

  sta_ch="$(channel_of "$STA_DEV")"
  go_ch="$(channel_of "$go")"
  signal="$(iw dev "$go" station dump 2>/dev/null | awk '/signal:/{print $2; exit}')"
  tx_br="$(iw dev "$go" station dump 2>/dev/null | awk '/tx bitrate:/{print $3; exit}')"
  inactive="$(iw dev "$go" station dump 2>/dev/null | awk '/inactive time:/{print $3; exit}')"
  auth="$(iw dev "$go" station dump 2>/dev/null | awk '/authorized:/{print $2; exit}')"

  tx0="$(cat "/sys/class/net/$go/statistics/tx_bytes")"
  rx0="$(cat "/sys/class/net/$go/statistics/rx_bytes")"
  local t0
  t0="$(date +%s.%N)"
  sleep "$SAMPLE_S"
  tx1="$(cat "/sys/class/net/$go/statistics/tx_bytes")"
  rx1="$(cat "/sys/class/net/$go/statistics/rx_bytes")"
  local t1
  t1="$(date +%s.%N)"
  dt="$(python3 -c "print(max(0.001, float('$t1')-float('$t0')))")"

  local tx_kbps rx_kbps
  tx_kbps="$(python3 -c "print(round(($tx1-$tx0)*8/1000/$dt, 1))")"
  rx_kbps="$(python3 -c "print(round(($rx1-$rx0)*8/1000/$dt, 1))")"

  # Count sender_health events during window (from latency log mtime/content)
  health_n="$(python3 - <<PY
import json, time
from pathlib import Path
p = Path("$STATUS_DIR") / "logs" / "latency.jsonl"
if not p.exists():
    print(0); raise SystemExit
cutoff = time.time() - float("$SAMPLE_S") - 1
n = 0
for line in p.read_text().splitlines()[-500:]:
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get("event") != "sender_health":
        continue
    ts = d.get("ts") or ""
    # fall back: count all recent health lines
    n += 1
print(n)
PY
)"

  local phase
  phase="$(python3 -c 'import json;print(json.load(open("'"$STATUS_DIR"'/status.json")).get("phase",""))' 2>/dev/null || true)"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$label" "$sta_ch" "$go_ch" "$tx_kbps" "$rx_kbps" \
    "${signal:--}" "${tx_br:--}" "${inactive:--}" "${auth:--}" \
    "$phase" "$SAMPLE_S" "$health_n"
}

run_case() {
  local label="$1"
  local skip_csa="$2"
  log "=== case $label (SKIP_CSA=$skip_csa) ==="
  assert_sta || return 2
  "$CTL" stop >/dev/null 2>&1 || true
  assert_sta || return 2
  if [[ "$skip_csa" == "1" ]]; then
    MIRACAST_SKIP_P2P_CSA=1 "$CTL" start --peer "$PEER" --peer-name "$PEER_NAME" --mode "$MODE" >/dev/null
  else
    env -u MIRACAST_SKIP_P2P_CSA "$CTL" start --peer "$PEER" --peer-name "$PEER_NAME" --mode "$MODE" >/dev/null
  fi
  wait_streaming || return 1
  # Extra beat so CSA (if any) finishes
  sleep 3
  sample_window "$label"
}

TSV="$OUT_DIR/public/p2p_channel_ab.tsv"
JSON="$OUT_DIR/public/p2p_channel_ab.json"
PRIVATE_JSON="$OUT_DIR/private/p2p_channel_ab_$(date +%Y%m%d_%H%M%S).json"
mkdir -p "$OUT_DIR/public" "$OUT_DIR/private"
# Legacy paths kept as symlinks/copies for older docs links when absent.
LEGACY_TSV="$OUT_DIR/p2p_channel_ab.tsv"
LEGACY_JSON="$OUT_DIR/p2p_channel_ab.json"
printf 'case\tsta_ch\tgo_ch\ttx_kbps\trx_kbps\tsignal_dbm\ttx_bitrate_mbps\tinactive_ms\tauthorized\tphase\tsample_s\thealth_events\n' >"$TSV"

assert_sta || { log "STA not connected"; exit 2; }

# SCC first, then MCC — restores MCC as the ending state
row_scc="$(run_case scc 1)" || { log "SCC case failed"; exit 1; }
echo "$row_scc" | tee -a "$TSV"
assert_sta || { log "STA lost after SCC"; exit 2; }

row_mcc="$(run_case mcc 0)" || { log "MCC case failed"; exit 1; }
echo "$row_mcc" | tee -a "$TSV"
assert_sta || { log "STA lost after MCC"; exit 2; }

export HOST_INFO_JSON
log_host_summary

python3 - <<PY
import json, csv, os, sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, "$SCRIPT_DIR")
from benchmark_privacy import guess_manufacturer, guess_model_hint, sanitize_display_name
from fingerprint_miracast_sink import fingerprint_sink, public_identity

tsv = Path("$TSV")
rows = [r for r in csv.DictReader(tsv.open(), delimiter="\t") if r.get("case") in ("scc", "mcc")]
equipment = json.loads(os.environ.get("HOST_INFO_JSON") or "{}")
equipment_full = json.loads(os.environ.get("HOST_INFO_FULL_JSON") or "{}")
sink_priv = (equipment_full.get("sink") or {})
sink_mac = sink_priv.get("mac") or "$PEER"
fp = fingerprint_sink(sink_mac, use_wpa=True)
ident = fp.get("identity") or {}
fp_pub = public_identity(fp)
sink_name = ident.get("device_name") or sink_priv.get("display_name") or equipment.get("sink_display_name") or "$PEER_NAME"
mfr = ident.get("manufacturer_normalized") or ident.get("manufacturer") or guess_manufacturer(sink_name)
model = ident.get("model_hint") or ident.get("model_code") or guess_model_hint(sink_name, mfr)
ts = datetime.now(timezone.utc).isoformat()

public_payload = {
    "ok": True,
    "ts": ts,
    "kind": "p2p_channel_ab",
    "mode": "$MODE",
    "sample_s": int("$SAMPLE_S"),
    "settle_s": int("$SETTLE_S"),
    "sink_display_name": sanitize_display_name(sink_name),
    "sink": {
        "display_name": sanitize_display_name(sink_name),
        "manufacturer": mfr,
        "model_hint": model,
        "model_code": ident.get("model_code"),
        "model_name": ident.get("model_name"),
        "device_category": (ident.get("primary_device_type") or {}).get("category"),
        "cea_mask": (fp.get("capabilities") or {}).get("cea_mask"),
        "vesa_mask": (fp.get("capabilities") or {}).get("vesa_mask"),
        "fingerprint_sources": fp.get("sources"),
    },
    "equipment": equipment,
    "notes": (
        "SCC = MIRACAST_SKIP_P2P_CSA=1; MCC = quiet-channel CSA after PLAY. "
        "TX from /sys/class/net/<go>/statistics. "
        "This public file omits Wi-Fi SSIDs/BSSIDs and MAC addresses."
    ),
    "rows": rows,
}
Path("$JSON").write_text(json.dumps(public_payload, indent=2) + "\n")
Path("$LEGACY_JSON").write_text(json.dumps(public_payload, indent=2) + "\n")
Path("$LEGACY_TSV").write_text(tsv.read_text())

private_payload = {
    "stem": Path("$PRIVATE_JSON").stem,
    "privacy": "private",
    "kind": "p2p_channel_ab",
    "ts": ts,
    "sink": {
        "display_name": sink_name,
        "mac": sink_mac,
        "manufacturer": mfr,
        "model_hint": model,
        "model_name": ident.get("model_name"),
        "model_code": ident.get("model_code"),
        "product_line": ident.get("product_line"),
        "p2p_role": sink_priv.get("p2p_role"),
        "fingerprint": fp,
        "fingerprint_public": fp_pub,
    },
    "host": {
        "cpu": equipment_full.get("cpu") or equipment.get("cpu"),
        "gpu": equipment_full.get("gpu") or equipment.get("gpu"),
        "wifi": equipment_full.get("wifi") or equipment.get("wifi"),
        "kernel": equipment_full.get("kernel") or equipment.get("kernel"),
        "compositor": equipment_full.get("compositor") or equipment.get("compositor"),
        "monitors": equipment_full.get("monitors") or equipment.get("monitors"),
    },
    "session": {
        "mode": "$MODE",
        "stream_mode": sink_priv.get("stream_mode"),
        "capture_path": sink_priv.get("capture_path"),
        "encoder": sink_priv.get("encoder"),
    },
    "metrics": {
        "sample_s": int("$SAMPLE_S"),
        "rows": rows,
        "sta_channel": rows[0].get("sta_ch") if rows else None,
        "p2p_channel": rows[-1].get("go_ch") if rows else None,
        "p2p_tx_kbps": float(rows[-1]["tx_kbps"]) if rows and rows[-1].get("tx_kbps") else None,
        "radio_mcc": True,
    },
    "public_notes": [
        "P2P SCC vs MCC A/B; sanitized via scripts/sanitize_benchmark_results.py.",
    ],
    "notes": ["PRIVATE — do not commit. Contains sink MAC and possibly Wi-Fi SSIDs."],
}
Path("$PRIVATE_JSON").write_text(json.dumps(private_payload, indent=2) + "\n")
print(json.dumps(public_payload, indent=2))
print(f"[bench-p2p] private dump: $PRIVATE_JSON", file=sys.stderr)
print("[bench-p2p] next: ./scripts/sanitize_benchmark_results.py", file=sys.stderr)
PY

log "wrote $TSV"
log "wrote $JSON"
log "wrote $PRIVATE_JSON"
log "DONE (left on MCC streaming)"
