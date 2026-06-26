#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-https://stock.letslepai.com}"
TIMEOUT="${TIMEOUT:-20}"
SKIP_RELEASE_READINESS="${SKIP_RELEASE_READINESS:-0}"
SKIP_OPERATIONAL_HEALTH="${SKIP_OPERATIONAL_HEALTH:-0}"

if [[ "$SKIP_RELEASE_READINESS" != "1" ]]; then
  echo "==> Tracker rebuild: release readiness"
  if ! python3 scripts/check_release_readiness.py --base-url "$BASE_URL" --timeout "$TIMEOUT"; then
    echo
    echo "Tracker rebuild stopped: 本機、GitHub 或公開站版本尚未對齊。"
    echo "若只是本機除錯，可明確設定 SKIP_RELEASE_READINESS=1；正式看盤前請先完成 push / deploy。"
    exit 1
  fi
fi

if [[ "$SKIP_OPERATIONAL_HEALTH" != "1" ]]; then
  echo
  echo "==> Tracker rebuild: operational health"
  if ! python3 scripts/check_operational_health.py --base-url "$BASE_URL" --timeout "$TIMEOUT"; then
    echo
    echo "Tracker rebuild stopped: 營運健康狀態 blocked。"
    echo "若要修復資料，請優先依 refresh_plan 執行分層刷新；不要用壞資料重建 tracker。"
    exit 1
  fi
fi

echo
echo "==> Tracker rebuild"
python3 -m stock_daytrade_system.cli tracker
