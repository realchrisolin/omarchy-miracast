#!/usr/bin/env bash
# Integration checks for P2P channel picking / CSA.
#
# Always runs a smoke check that pick-p2p-channel.py emits scored JSON
# (--band 2.4 and --band 5). Optionally starts a cast and asserts the
# quiet-CSA / MCC path (GO channel ≠ STA) unless SKIP_CAST=1.
#
# Defaults read the last peer from settings.json. Override with env:
#   PEER=aa:bb:... PEER_NAME=Sink STA_DEV=wlp0s20f3 ./scripts/test_p2p_channel_integration.sh
#   SKIP_CAST=1   # smoke only (no connect)
#
# Exit 0 on pass. Does not unmanage p2p-dev or restart NetworkManager.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CTL="${CTL:-$PLUGIN_ROOT/bin/miracast-ctl}"
PICK="${PICK:-$SCRIPT_DIR/pick-p2p-channel.py}"
SETTINGS="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy-miracast/settings.json"
STATUS_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-miracast"
STA_DEV="${STA_DEV:-wlp0s20f3}"
MODE="${MODE:-extend}"
TIMEOUT_S="${TIMEOUT_S:-90}"
SKIP_CAST="${SKIP_CAST:-0}"

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

# --- Smoke: picker JSON includes channel + score (no cast required) ----------
smoke_pick_scores() {
  local band="$1"
  echo "[integration] smoke: pick-p2p-channel --json --band $band"
  [[ -f "$PICK" ]] || { echo "[integration] FAIL: missing $PICK"; return 1; }
  python3 "$PICK" --json --band "$band" | python3 -c '
import json,sys
d=json.load(sys.stdin)
ok=d.get("ok")
ch=d.get("channel")
score=d.get("score")
cands=d.get("candidates") or []
if ok is not True:
  print("[integration] FAIL: pick ok!=True", d, file=sys.stderr); sys.exit(1)
if not isinstance(ch, int):
  print("[integration] FAIL: pick channel not int", ch, file=sys.stderr); sys.exit(1)
if not isinstance(score, (int, float)):
  print("[integration] FAIL: pick score missing", score, file=sys.stderr); sys.exit(1)
if not isinstance(cands, list) or not cands:
  print("[integration] FAIL: pick candidates empty", file=sys.stderr); sys.exit(1)
# Each candidate should expose score for ranking (“quietest”)
for c in cands:
  if not isinstance(c, dict) or "score" not in c or "channel" not in c:
    print("[integration] FAIL: candidate lacks score/channel", c, file=sys.stderr); sys.exit(1)
print(f"[integration] smoke ok band={sys.argv[1]} ch={ch} score={score} n_cands={len(cands)}")
' "$band"
}

smoke_pick_scores 2.4
smoke_pick_scores 5

if [[ "$SKIP_CAST" == "1" || "$SKIP_CAST" == "true" ]]; then
  echo "[integration] SKIP_CAST=1 — smoke only PASS"
  exit 0
fi

if [[ -z "${PEER:-}" && -f "$SETTINGS" ]]; then
  PEER="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("lastPeerMac",""))' "$SETTINGS")"
  PEER_NAME="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("lastPeerName","") or "Sink")' "$SETTINGS")"
fi
PEER="${PEER:?set PEER= or connect once so settings.json has lastPeerMac (or SKIP_CAST=1)}"
PEER_NAME="${PEER_NAME:-Sink}"

# Log sink display name only (never echo PEER MAC in pass/fail banners).
echo "[integration] sink=${PEER_NAME} mode=$MODE"
python3 "$SCRIPT_DIR/bench_host_info.py" --settings "$SETTINGS" 2>/dev/null \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);w=d.get("wifi") or {};
print("[integration] wifi:", (w.get("pci") or ["?"])[0]);
print("[integration] sta:", w.get("sta_channel") or "?");
print("[integration] sink_display:", d.get("sink_display_name") or "?")' \
  || true
assert_sta || { echo "[integration] FAIL: STA $STA_DEV not connected"; exit 2; }

"$CTL" stop >/dev/null 2>&1 || true
assert_sta || { echo "[integration] FAIL: STA dropped after stop"; exit 2; }

"$CTL" start --peer "$PEER" --peer-name "$PEER_NAME" --mode "$MODE" >/dev/null

deadline=$((SECONDS + TIMEOUT_S))
phase=""
while ((SECONDS < deadline)); do
  assert_sta || { echo "[integration] FAIL: STA lost during connect"; exit 2; }
  phase="$(python3 -c 'import json;print(json.load(open("'"$STATUS_DIR"'/status.json")).get("phase",""))' 2>/dev/null || true)"
  if [[ "$phase" == "streaming" ]]; then
    # Give CSA a moment after PLAY.
    sleep 2
    break
  fi
  if [[ "$phase" == "error" || "$phase" == "idle" ]]; then
    echo "[integration] FAIL: phase=$phase"
    exit 1
  fi
  sleep 1
done

[[ "$phase" == "streaming" ]] || { echo "[integration] FAIL: timeout waiting for streaming"; exit 1; }

sta_ch="$(channel_of "$STA_DEV")"
go="$(p2p_go_iface)"
[[ -n "$go" ]] || { echo "[integration] FAIL: no P2P-GO iface"; exit 1; }
go_ch="$(channel_of "$go")"
auth="$(iw dev "$go" station dump 2>/dev/null | awk '/authorized:/{print $2; exit}')"

echo "[integration] STA ch=$sta_ch  GO $go ch=$go_ch  authorized=$auth"
rg -n 'quiet target|CSA:' "$STATUS_DIR/logs/cast.log" 2>/dev/null | tail -5 || true

fail=0
[[ "$sta_ch" =~ ^[0-9]+$ ]] || fail=1
[[ "$go_ch" =~ ^[0-9]+$ ]] || fail=1
[[ "$go_ch" != "$sta_ch" ]] || { echo "[integration] FAIL: still SCC (GO==STA ch $sta_ch)"; fail=1; }
[[ "$auth" == "yes" ]] || { echo "[integration] FAIL: sink not authorized"; fail=1; }
assert_sta || fail=1

if [[ "$fail" -eq 0 ]]; then
  echo "[integration] PASS"
  exit 0
fi
echo "[integration] FAIL"
exit 1
