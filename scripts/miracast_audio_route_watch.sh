#!/usr/bin/env bash
# Named audio-route watcher for Miracast.
# Runs as its own argv (not "miracast-ctl start …") so leftovers are obvious
# and stop can reap by name without killing the cast supervisor.
set -euo pipefail

CTL_BIN="${1:?miracast-ctl path required}"
SINK_NAME="${MIRACAST_AUDIO_SINK_NAME:-miracast}"

# Clearer in `ps -o comm`.
printf 'miracast-audio\n' >/proc/self/comm 2>/dev/null || true

pactl subscribe 2>/dev/null | while read -r _ev; do
  case "$_ev" in
    *\'change\'*on*sink*|*\'new\'*on*sink*|*server*)
      "$CTL_BIN" audio-route-sync >/dev/null 2>&1 || true
      ;;
    *source-output*)
      "$CTL_BIN" audio-pin-capture >/dev/null 2>&1 || true
      ;;
    *sink-input*)
      def="$(pactl get-default-sink 2>/dev/null || true)"
      if [[ "$def" == "$SINK_NAME" ]]; then
        "$CTL_BIN" audio-route-sync >/dev/null 2>&1 || true
      else
        "$CTL_BIN" audio-pin-silence >/dev/null 2>&1 || true
        "$CTL_BIN" audio-pin-capture >/dev/null 2>&1 || true
      fi
      ;;
  esac
done
