#!/usr/bin/env bash
# Safe harness for MCC / wpas experiments.
# Rules (careful mode):
#   - Never down/up the home STA connection
#   - Never restart NetworkManager from experiments (leaves/repairs p2p-dev poorly)
#   - Do not unmanage p2p-dev-* (sticks unavailable until NM restart)
#   - May claim GO data iface unmanaged briefly
#   - Always remanage GO ifaces and restore known-good NM cast within 60s on exit
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CTL="${CTL:-$PLUGIN_ROOT/bin/miracast-ctl}"
# Defaults use RFC 7042 documentation MAC (00-00-5E-00-53-xx). Override for real runs:
#   PEER=aa:bb:cc:dd:ee:ff PEER_NAME=MySink STA_CONN=MyHomeSSID ./scripts/mcc-safe-experiment.sh -- …
PEER="${PEER:-00:00:5E:00:53:00}"
PEER_NAME="${PEER_NAME:-Example-Sink_P2P}"
STA_CONN="${STA_CONN:-HomeLab-5GHz}"
STA_DEV="${STA_DEV:-wlp0s20f3}"
P2P_DEV="${P2P_DEV:-p2p-dev-wlp0s20f3}"
STATUS_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-miracast"
LOG="${STATUS_DIR}/logs/mcc-safe-experiment.log"
mkdir -p "${STATUS_DIR}/logs"
: >"$LOG"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

assert_sta() {
  local state ssid
  state="$(nmcli -t -f DEVICE,STATE,CONNECTION device | awk -F: -v d="$STA_DEV" '$1==d{print $2":"$3}')"
  ssid="$(iw dev "$STA_DEV" info 2>/dev/null | awk '/ssid/{print $2; exit}')"
  if [[ "$state" != connected:* ]] || [[ -z "$ssid" ]]; then
    log "FATAL: STA not connected to ${STA_CONN} (state=$state ssid=$ssid)"
    return 1
  fi
  # Require the expected connection name when set (fixture/lab SSID).
  if [[ -n "$STA_CONN" && "$state" != *":$STA_CONN" ]]; then
    log "FATAL: STA connected as ${state##*:}, expected $STA_CONN"
    return 1
  fi
  log "STA ok: $state ssid=$ssid"
}

remanage_p2p() {
  sudo -n nmcli device set "$P2P_DEV" managed yes 2>/dev/null || true
  # Delete leftover GO ifaces without touching STA
  for i in $(iw dev 2>/dev/null | awk '/Interface p2p-/{print $2}'); do
    [[ "$i" == "$P2P_DEV" ]] && continue
    sudo -n wpa_cli -i "$STA_DEV" p2p_group_remove "$i" 2>/dev/null || true
    sudo -n iw dev "$i" del 2>/dev/null || true
  done
}

restore_nm_cast() {
  log "Restoring known-good NM cast..."
  # Ensure ctl is on NM backend (caller may have patched temporarily)
  if [[ -f /tmp/miracast-ctl.mccbak ]]; then
    cp /tmp/miracast-ctl.mccbak "$CTL"
    log "Restored miracast-ctl from backup"
  fi
  remanage_p2p
  "$CTL" stop >>"$LOG" 2>&1 || true
  remanage_p2p
  assert_sta || return 1
  "$CTL" start --peer "$PEER" --peer-name "$PEER_NAME" --mode extend >>"$LOG" 2>&1
  # Wait up to 60s for streaming without touching STA
  local deadline=$((SECONDS + 60))
  while ((SECONDS < deadline)); do
    local phase
    phase="$(python3 -c 'import json;print(json.load(open("'"$STATUS_DIR"'/status.json")).get("phase",""))' 2>/dev/null || echo missing)"
    if [[ "$phase" == "streaming" ]]; then
      log "Restore OK: streaming"
      assert_sta
      return 0
    fi
    if [[ "$phase" == "error" || "$phase" == "idle" ]]; then
      log "Restore phase=$phase — retrying start once"
      "$CTL" start --peer "$PEER" --peer-name "$PEER_NAME" --mode extend >>"$LOG" 2>&1 || true
    fi
    sleep 2
  done
  log "ERROR: restore did not reach streaming within 60s"
  assert_sta || true
  return 1
}

cleanup() {
  local ec=$?
  log "cleanup trap (exit=$ec)"
  remanage_p2p
  # Always attempt restore so the user is not left without cast/wifi path
  restore_nm_cast || log "cleanup: restore failed — STA should still be up"
  exit "$ec"
}
trap cleanup EXIT INT TERM

log "=== MCC safe experiment start ==="
assert_sta
# Snapshot routes so we can detect breakage
ip -4 route show default | tee -a "$LOG"

# Experiment body is passed as args: the command to run while STA is protected
if [[ $# -lt 1 ]]; then
  log "usage: $0 -- <experiment command...>"
  exit 2
fi
if [[ "$1" == "--" ]]; then shift; fi

log "Running experiment: $*"
set +e
"$@"
ec=$?
set -e
log "Experiment exit=$ec"
assert_sta || ec=1
exit "$ec"
