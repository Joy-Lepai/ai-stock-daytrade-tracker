#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-https://stock.letslepai.com}"
TIMEOUT="${TIMEOUT:-20}"
SKIP_RELEASE_READINESS="${SKIP_RELEASE_READINESS:-0}"
SKIP_OPERATIONAL_HEALTH="${SKIP_OPERATIONAL_HEALTH:-0}"
RUN_LEGACY_OPEN_REPORT="${RUN_LEGACY_OPEN_REPORT:-1}"

if [[ "$SKIP_RELEASE_READINESS" != "1" ]]; then
  echo "==> Opening check: release readiness"
  if ! python3 scripts/check_release_readiness.py --base-url "$BASE_URL" --timeout "$TIMEOUT"; then
    echo
    echo "Opening check stopped: 本機、GitHub 或公開站版本尚未對齊。"
    echo "請先依上方 next action 處理，避免用舊版 dashboard 看盤。"
    exit 1
  fi
fi

if [[ "$SKIP_OPERATIONAL_HEALTH" != "1" ]]; then
  echo
  echo "==> Opening check: operational health"
  if ! python3 scripts/check_operational_health.py --base-url "$BASE_URL" --timeout "$TIMEOUT"; then
    echo
    echo "Opening check stopped: 營運健康狀態 blocked。"
    echo "請先依上方 refresh_plan / next_action 修復資料層，再進行開盤判斷。"
    exit 1
  fi
fi

if [[ "$RUN_LEGACY_OPEN_REPORT" == "1" ]]; then
  echo
  echo "==> Opening check: legacy markdown report"
  python3 -m stock_daytrade_system.cli open-check
else
  echo
  echo "Opening check finished without legacy markdown report."
fi
