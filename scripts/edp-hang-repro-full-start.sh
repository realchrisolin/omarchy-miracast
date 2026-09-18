#!/usr/bin/env bash
# Logged full miracast-ctl start (the 21:41 hang path).
# Keep heartbeat + fsync of ctl logs running after start returns.
set -euo pipefail

STATE="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-miracast"
BASE="$STATE/logs/edp-repro"
RUN_ID="${RUN_ID:-full-$(date +%Y%m%d-%H%M%S)}"
RUN="$BASE/$RUN_ID"
CTL="${CTL:-$HOME/.local/bin/miracast-ctl}"
PEER="${PEER:-DE:84:03:8D:51:17}"
ROOT="${FLUXCAST_ROOT:-/home/colin/code/other/fluxcast}"
WAIT_S="${WAIT_S:-90}"

mkdir -p "$RUN"
ln -sfn "$RUN" "$BASE/LATEST"

fsync_line() {
  python3 - "$1" "$2" <<'PY'
import os, sys, time
path, msg = sys.argv[1], sys.argv[2]
line = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + msg + "\n"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.write(fd, line.encode()); os.fsync(fd); os.close(fd)
PY
}
slog() { fsync_line "$RUN/steps.log" "$*"; echo "$(date +%H:%M:%S) $*"; }
fsync_file() {
  python3 - "$1" <<'PY'
import os, sys
p=sys.argv[1]
if not os.path.exists(p):
    raise SystemExit(0)
fd=os.open(p, os.O_RDWR); os.fsync(fd); os.close(fd)
PY
}

sudo -n sysctl -w vm.dirty_expire_centisecs=50 >/dev/null 2>&1 || true
sudo -n sysctl -w vm.dirty_writeback_centisecs=50 >/dev/null 2>&1 || true
sudo -n sysctl -w kernel.sysrq=1 >/dev/null 2>&1 || true

HYPR_LOG="$(find /run/user/1000/hypr -name hyprland.log 2>/dev/null | head -1 || true)"

(
  while :; do
    python3 - "$RUN/heartbeat.log" <<'PY'
import os, time, json
from pathlib import Path
hb = __import__("sys").argv[1]
sp = Path.home() / ".local/state/omarchy-miracast/status.json"
try:
    d = json.loads(sp.read_text())
    st = f"{d.get('phase','')}|{str(d.get('message',''))[:60]}"
except Exception:
    st = "?"
line = f"{time.time():.3f} {st}\n"
fd = os.open(hb, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.write(fd, line.encode()); os.fsync(fd); os.close(fd)
for p in (
    str(sp),
    str(Path.home() / ".local/state/omarchy-miracast/logs/cast.log"),
    str(Path.home() / ".local/state/omarchy-miracast/logs/disconnect.log"),
):
    try:
        fd = os.open(p, os.O_RDWR)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass
PY
    sleep 0.25
  done
) &
HB_PID=$!
(
  while :; do
    [[ -n "$HYPR_LOG" && -f "$HYPR_LOG" ]] && cp -f "$HYPR_LOG" "$RUN/hyprland.log.copy" && fsync_file "$RUN/hyprland.log.copy"
    sleep 0.4
  done
) &
HY_PID=$!
(
  sudo -n dmesg --follow --ctime >>"$RUN/dmesg.log" 2>/dev/null || true
) &
DM_PID=$!
(
  while :; do [[ -f "$RUN/dmesg.log" ]] && fsync_file "$RUN/dmesg.log"; sleep 0.4; done
) &
DS_PID=$!

cleanup() {
  kill "$HB_PID" "$HY_PID" "$DM_PID" "$DS_PID" 2>/dev/null || true
  wait "$HB_PID" "$HY_PID" "$DM_PID" "$DS_PID" 2>/dev/null || true
}
trap cleanup EXIT

printf '%s\n' "$RUN" >"$BASE/LATEST.path"
fsync_file "$BASE/LATEST.path"
echo "full start $RUN_ID
If power-cycle: read $RUN/steps.log last line + heartbeat.log + hyprland.log.copy + ~/.local/state/omarchy-miracast/logs/cast.log" >"$RUN/README.txt"
fsync_file "$RUN/README.txt"

command -v notify-send >/dev/null && notify-send -u critical "eDP hang repro" "FULL ctl start $RUN_ID. After lockup read $RUN/steps.log" || true

slog "BEGIN full-start peer=$PEER root=$ROOT"
hyprctl monitors -j >"$RUN/monitors-before.json" 2>/dev/null || true
fsync_file "$RUN/monitors-before.json"
slog "ABOUT_TO $CTL start --peer $PEER --mode extend"
set +e
FLUXCAST_ROOT="$ROOT" "$CTL" start \
  --peer "$PEER" \
  --peer-name "hotyeah-8D5117_P2P" \
  --mode extend \
  --fluxcast-root "$ROOT" \
  --go-intent 0 \
  >"$RUN/ctl-start.out" 2>"$RUN/ctl-start.err"
rc=$?
set -e
fsync_file "$RUN/ctl-start.out"
fsync_file "$RUN/ctl-start.err"
slog "ctl start returned rc=$rc (0=wrapper spawned; hang would not reach here)"
slog "waiting ${WAIT_S}s for phase streaming/error (fsyncing cast.log/status)"

end=$((SECONDS + WAIT_S))
while (( SECONDS < end )); do
  ph="$(python3 -c 'import json; d=json.load(open("'"$STATE"'/status.json")); print(d.get("phase",""), d.get("message","")[:80])' 2>/dev/null || echo '?')"
  slog "status $ph"
  case "$ph" in
    streaming*|error*|idle*) slog "TERMINAL $ph"; break ;;
  esac
  sleep 3
done

hyprctl monitors -j >"$RUN/monitors-after.json" 2>/dev/null || true
fsync_file "$RUN/monitors-after.json"
cp -f "$STATE/logs/cast.log" "$RUN/cast.log.copy" 2>/dev/null || true
fsync_file "$RUN/cast.log.copy" 2>/dev/null || true
slog "END full-start still-alive run=$RUN_ID"
echo "$RUN"
exit 0
