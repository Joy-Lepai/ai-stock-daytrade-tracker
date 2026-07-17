#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${STOCK_BOOTSTRAP_TRACKER_ON_STARTUP:-0}" == "1" ]] && ! compgen -G "reports/*-tracker.html" > /dev/null; then
  python3 -m stock_daytrade_system.cli tracker
fi

python3 -m stock_daytrade_system.cli web --host "${STOCK_WEB_HOST:-0.0.0.0}" --port "${PORT:-8000}"
