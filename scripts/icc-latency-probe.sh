#!/usr/bin/env bash
# Correlate keypress time with icc-present-kick present kicks.
# Usage (while Miracast ICC is streaming, foot focused on Extend head):
#   ./scripts/icc-latency-probe.sh
# Then type without moving the mouse. Ctrl-C to stop.
set -euo pipefail

KICK_LOG=/tmp/icc-present-kick.log
KEY_LOG=/tmp/icc-keys-probe.log
OUT=/tmp/icc-latency-probe-summary.txt

: >"$KEY_LOG"
echo "Writing key timestamps to $KEY_LOG"
echo "Kick log: $KICK_LOG (plugin must be loaded)"
echo "Type on the Miracast foot; Ctrl-C when done."
echo

# Prefer evtest-style via libinput if available
if command -v libinput >/dev/null; then
  # Filter keyboard events; timestamps are relative in some versions — also log wall clock
  stdbuf -oL libinput debug-events 2>/dev/null | stdbuf -oL awk '
    /KEY_/ {
      cmd="date +%s%3N"; cmd | getline t; close(cmd);
      print t, $0; fflush();
    }' | tee -a "$KEY_LOG"
else
  echo "libinput not found; install libinput for key probes" >&2
  exit 1
fi
