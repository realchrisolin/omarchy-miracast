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

echo "OK: encode CLI names + aliases"
