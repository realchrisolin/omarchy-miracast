#!/usr/bin/env bash
# Smoke: encode CLI primary names + legacy aliases dispatch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CTL="$ROOT/bin/miracast-ctl"

help_out="$("$CTL" --help 2>&1 || true)"
echo "$help_out" | grep -q 'set-render-engine' || {
  echo "FAIL: help missing set-render-engine" >&2
  exit 1
}
echo "$help_out" | grep -q 'set-quality' || {
  echo "FAIL: help missing set-quality" >&2
  exit 1
}

# Aliases still dispatch (bogus arg → usage, not "unknown command").
err_alias="$("$CTL" set-capture-encode bogus 2>&1 || true)"
echo "$err_alias" | grep -qiE 'set-render-engine|dmabuf|vaapi|cpu' || {
  echo "FAIL: set-capture-encode alias not dispatched: $err_alias" >&2
  exit 1
}
err_alias2="$("$CTL" set-encode-profile bogus 2>&1 || true)"
echo "$err_alias2" | grep -qiE 'set-quality|high|medium|low' || {
  echo "FAIL: set-encode-profile alias not dispatched: $err_alias2" >&2
  exit 1
}

# benchmark help: all vs --engines/--tiers on plain benchmark
bench_help="$("$CTL" benchmark --help 2>&1 || true)"
echo "$bench_help" | grep -q 'benchmark all' || {
  echo "FAIL: benchmark --help missing 'benchmark all'" >&2
  exit 1
}
echo "$bench_help" | grep -q -- '--engines' || {
  echo "FAIL: benchmark --help missing --engines" >&2
  exit 1
}
# all rejects --engines
err_all="$("$CTL" benchmark all --engines dmabuf 2>&1 || true)"
echo "$err_all" | grep -qi 'omit --engines\|tests everything' || {
  echo "FAIL: benchmark all should reject --engines: $err_all" >&2
  exit 1
}
# -y / --yes / --force documented for matrix countdown skip
echo "$bench_help" | grep -q -- '-y' || {
  echo "FAIL: benchmark --help missing -y" >&2
  exit 1
}

echo "OK: encode CLI names + aliases"
