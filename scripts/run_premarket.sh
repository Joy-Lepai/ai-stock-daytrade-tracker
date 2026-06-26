#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-https://stock.letslepai.com}"
TIMEOUT="${TIMEOUT:-20}"
SKIP_RELEASE_READINESS="${SKIP_RELEASE_READINESS:-0}"
SKIP_OPERATIONAL_HEALTH="${SKIP_OPERATIONAL_HEALTH:-0}"

if [[ "$SKIP_RELEASE_READINESS" != "1" ]]; then
  echo "==> Premarket check: release readiness"
  if ! python3 scripts/check_release_readiness.py --base-url "$BASE_URL" --timeout "$TIMEOUT"; then
    echo
    echo "Premarket report stopped: 本機、GitHub 或公開站版本尚未對齊。"
    echo "請先依上方 next action 處理，避免用舊版或未部署資料做盤前判斷。"
    exit 1
  fi
fi

if [[ "$SKIP_OPERATIONAL_HEALTH" != "1" ]]; then
  echo
  echo "==> Premarket check: operational health"
  if ! python3 scripts/check_operational_health.py --base-url "$BASE_URL" --timeout "$TIMEOUT"; then
    echo
    echo "Premarket report stopped: 營運健康狀態 blocked。"
    echo "請先依上方 refresh_plan / next_action 修復資料層，再產生盤前報告。"
    exit 1
  fi
fi

echo
echo "==> Premarket report"
python3 -m stock_daytrade_system.cli report
