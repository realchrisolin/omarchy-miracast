#!/usr/bin/env bash
# Logged reproduction of the Extend/ICC eDP hang.
# Survives a power cycle: every step is appended with fsync before the next
# dangerous hyprctl. After a lockup, read:
#   ~/.local/state/omarchy-miracast/logs/edp-repro/LATEST
#   <that-dir>/steps.log          # last line = last completed/about-to
#   <that-dir>/heartbeat.log      # 250ms clock; stops when kernel stops
#   <that-dir>/hyprland.log.copy  # Hyprland log is otherwise tmpfs-only
#   <that-dir>/dmesg.log
#
# Does NOT call hyprctl output remove / monitor,disable.
# Default: Hyprland headless + scale-2 geometry + ICC plugin + wf-recorder.
# USB/P2P is not in this pass (last hang already had ICC loaded).
set -euo pipefail

STATE="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-miracast"
BASE="$STATE/logs/edp-repro"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN="$BASE/$RUN_ID"
HEADLESS="${HEADLESS_NAME:-persistent-miracast}"
WF_BIN="${WF_RECORDER_BIN:-/home/colin/src/wf-recorder/build/wf-recorder}"
ICC_SO="${ICC_SO:-/home/colin/code/other/omarchy-miracast/hyprland-plugins/icc-present-kick/icc-present-kick.so}"
SOAK_S="${SOAK_S:-4}"
CAPTURE_S="${CAPTURE_S:-8}"
# scale 2 + left + 1920x1080@60 matches hotyeah settings at the 21:41 hang.
MODE="${MODE:-1920x1080@60}"
SCALE="${SCALE:-2}"
POS="${POS:--960x0}"   # 1920/scale2 logical width to the left of eDP

mkdir -p "$RUN"
ln -sfn "$RUN" "$BASE/LATEST"

fsync_line() {
  local file="$1"
  shift
  python3 - "$file" "$*" <<'PY'
import os, sys, time
path, msg = sys.argv[1], sys.argv[2]
line = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + msg + "\n"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
try:
    os.write(fd, line.encode())
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

slog() { fsync_line "$RUN/steps.log" "$*"; echo "$(date +%H:%M:%S) $*"; }

fsync_file() {
  python3 - "$1" <<'PY'
import os, sys
p = sys.argv[1]
fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

snapshot_monitors() {
  local tag="$1"
  hyprctl monitors -j >"$RUN/monitors-$tag.json" 2>"$RUN/monitors-$tag.err" || true
  fsync_file "$RUN/monitors-$tag.json"
  python3 - "$RUN/monitors-$tag.json" <<'PY' 2>/dev/null || true
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    ms = json.loads(p.read_text() or "[]")
except Exception:
    print("monitors: unreadable")
    raise SystemExit
for m in ms:
    print(f"  {m.get('name')} {m.get('width')}x{m.get('height')} scale={m.get('scale')} pos={m.get('x')},{m.get('y')}")
PY
}

# Faster dirty writeback so a hang a second later still lands on disk.
sudo -n sysctl -w vm.dirty_expire_centisecs=50 >/dev/null 2>&1 || true
sudo -n sysctl -w vm.dirty_writeback_centisecs=50 >/dev/null 2>&1 || true
sudo -n sysctl -w kernel.hung_task_timeout_secs=20 >/dev/null 2>&1 || true
# Allow SysRq sync/umount/reboot if the keyboard still works during a hang.
sudo -n sysctl -w kernel.sysrq=1 >/dev/null 2>&1 || true

HYPR_LOG="/run/user/1000/hypr/${HYPRLAND_INSTANCE_SIGNATURE:-}/hyprland.log"
if [[ ! -f "$HYPR_LOG" ]]; then
  HYPR_LOG="$(find /run/user/1000/hypr -name hyprland.log 2>/dev/null | head -1 || true)"
fi

# Heartbeat: no hyprctl (must keep ticking if compositor is stuck).
(
  while :; do
    python3 - "$RUN/heartbeat.log" <<'PY'
import os, time
path = __import__("sys").argv[1]
line = "%.3f up=%.2f load=%s\n" % (
    time.time(),
    time.clock_gettime(time.CLOCK_BOOTTIME),
    " ".join(open("/proc/loadavg").read().split()[:3]),
)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.write(fd, line.encode())
os.fsync(fd)
os.close(fd)
PY
    sleep 0.25
  done
) &
HB_PID=$!

# Copy tmpfs Hyprland log to disk.
(
  while :; do
    if [[ -n "$HYPR_LOG" && -f "$HYPR_LOG" ]]; then
      cp -f "$HYPR_LOG" "$RUN/hyprland.log.copy" 2>/dev/null || true
      fsync_file "$RUN/hyprland.log.copy"
    fi
    sleep 0.4
  done
) &
HY_PID=$!

# Kernel ring to disk.
(
  sudo -n dmesg --follow --ctime >>"$RUN/dmesg.log" 2>/dev/null ||
    dmesg --follow --ctime >>"$RUN/dmesg.log" 2>/dev/null || true
) &
DM_PID=$!
(
  while :; do
    [[ -f "$RUN/dmesg.log" ]] && fsync_file "$RUN/dmesg.log"
    sleep 0.4
  done
) &
DM_SYNC_PID=$!

cleanup_helpers() {
  kill "$HB_PID" "$HY_PID" "$DM_PID" "$DM_SYNC_PID" 2>/dev/null || true
  wait "$HB_PID" "$HY_PID" "$DM_PID" "$DM_SYNC_PID" 2>/dev/null || true
  fsync_file "$RUN/steps.log"
  [[ -f "$RUN/dmesg.log" ]] && fsync_file "$RUN/dmesg.log"
  [[ -f "$RUN/hyprland.log.copy" ]] && fsync_file "$RUN/hyprland.log.copy"
}

trap cleanup_helpers EXIT

cat >"$RUN/README.txt" <<EOF
eDP hang repro $RUN_ID
If the machine power-cycled, the last fsynced event is the last line of steps.log.
heartbeat.log stopping = kernel/userspace stopped ticking.
hyprland.log.copy is a disk mirror of the tmpfs compositor log.
NEVER hyprctl output remove this head — park at -12000x0 only.
EOF
fsync_file "$RUN/README.txt"
printf '%s\n' "$RUN" >"$BASE/LATEST.path"
fsync_file "$BASE/LATEST.path"

command -v notify-send >/dev/null && notify-send -u critical "eDP hang repro" "Starting $RUN_ID. If we lock up, read $RUN/steps.log" || true

slog "BEGIN run=$RUN_ID headless=$HEADLESS mode=$MODE scale=$SCALE pos=$POS"
slog "hyprland=$(hyprctl version 2>/dev/null | head -1 | tr -d '\n')"
slog "wf_bin=$WF_BIN icc_so=$(test -f "$ICC_SO" && echo present || echo MISSING)"
snapshot_monitors before
slog "monitors_before done"
sudo -n dmesg -T | tail -20 >"$RUN/dmesg.before" 2>/dev/null || true
fsync_file "$RUN/dmesg.before"

# --- 1. create headless (same as ensure_extend_monitor) ---
if hyprctl monitors -j 2>/dev/null | python3 -c 'import json,sys
name=sys.argv[1]
ms=json.load(sys.stdin)
raise SystemExit(0 if any(m.get("name")==name for m in ms) else 1)
' "$HEADLESS" 2>/dev/null; then
  slog "SKIP create: $HEADLESS already exists"
else
  slog "ABOUT_TO hyprctl output create headless $HEADLESS"
  if timeout 5 hyprctl output create headless "$HEADLESS" >"$RUN/create.out" 2>"$RUN/create.err"; then
    fsync_file "$RUN/create.out"
    slog "OK create headless $HEADLESS"
  else
    rc=$?
    fsync_file "$RUN/create.err"
    slog "FAIL create rc=$rc (timeout-or-error)"
    slog "ABORT after create failure"
    exit 2
  fi
fi
sleep "$SOAK_S"
snapshot_monitors after-create
slog "soak_after_create ${SOAK_S}s ok"

# --- 2. geometry: left of eDP, scale 2, 1080p60 (hotyeah settings) ---
slog "ABOUT_TO hl.monitor output=$HEADLESS mode=$MODE position=$POS scale=$SCALE"
if timeout 5 hyprctl eval "hl.monitor({ output = \"${HEADLESS}\", mode = \"${MODE}\", position = \"${POS}\", scale = ${SCALE} })" >"$RUN/geom.out" 2>"$RUN/geom.err"; then
  slog "OK geometry"
else
  rc=$?
  slog "FAIL geometry rc=$rc"
  slog "ABORT after geometry failure"
  exit 3
fi
sleep "$SOAK_S"
snapshot_monitors after-geometry
slog "soak_after_geometry ${SOAK_S}s ok"

# --- 3. software cursors + animations off (cmd_start does both) ---
slog "ABOUT_TO software cursors + animations off"
timeout 3 hyprctl eval 'hl.config({ cursor = { no_hardware_cursors = true } })' >/dev/null 2>&1 || true
timeout 3 hyprctl eval 'hl.config({
  animations = { enabled = false },
  decoration = { rounding = 6 },
  general = { border_size = 2, gaps_in = 5, gaps_out = 10 },
  render = { direct_scanout = 0 },
})' >/dev/null 2>&1 || true
slog "OK cursors_animations"
sleep "$SOAK_S"
slog "soak_after_cursors ${SOAK_S}s ok"

# --- 4. ICC present-kick plugin ---
if timeout 2 hyprctl plugin list 2>/dev/null | grep -q 'icc-present-kick'; then
  slog "SKIP plugin already loaded"
else
  slog "ABOUT_TO hyprctl plugin load $ICC_SO"
  if timeout 5 hyprctl plugin load "$ICC_SO" >"$RUN/plugin.out" 2>"$RUN/plugin.err"; then
    slog "OK plugin load"
  else
    rc=$?
    slog "FAIL plugin load rc=$rc"
    # Continue: hang at 21:41 still loaded the plugin; a load failure is data.
  fi
fi
sleep "$SOAK_S"
hyprctl plugin list >"$RUN/plugins.after" 2>/dev/null || true
fsync_file "$RUN/plugins.after"
slog "soak_after_plugin ${SOAK_S}s ok"

# --- 5. wf-recorder ICC/VAAPI on the new head (closest to DMA-BUF start) ---
if [[ -x "$WF_BIN" ]]; then
  slog "ABOUT_TO wf-recorder -o $HEADLESS -c h264_vaapi ${CAPTURE_S}s"
  timeout "$CAPTURE_S" "$WF_BIN" -o "$HEADLESS" -c h264_vaapi -y \
    -f "$RUN/probe.mp4" -r 30 -l \
    >"$RUN/wf-recorder.out" 2>"$RUN/wf-recorder.err" || true
  fsync_file "$RUN/wf-recorder.out"
  fsync_file "$RUN/wf-recorder.err"
  slog "OK wf-recorder returned (or timeout ${CAPTURE_S}s)"
else
  slog "SKIP wf-recorder missing $WF_BIN"
fi
sleep "$SOAK_S"
snapshot_monitors after-capture
slog "soak_after_capture ${SOAK_S}s ok"

# --- park, do not destroy ---
slog "ABOUT_TO park $HEADLESS at -12000x0"
timeout 5 hyprctl eval "hl.monitor({ output = \"${HEADLESS}\", mode = \"1920x1080@30\", position = \"-12000x0\", scale = 1 })" >/dev/null 2>&1 || true
timeout 3 hyprctl eval 'hl.config({ animations = { enabled = true } })' >/dev/null 2>&1 || true
timeout 3 hyprctl eval 'hl.config({ cursor = { no_hardware_cursors = 2 } })' >/dev/null 2>&1 || true
snapshot_monitors after-park
slog "OK parked (no output remove)"
slog "SURVIVED all steps run=$RUN_ID"
echo "$RUN"
exit 0
