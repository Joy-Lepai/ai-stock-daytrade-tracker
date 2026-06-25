#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${1:-${BASE_URL:-https://stock.letslepai.com}}"
ADVISOR_SYMBOL="${2:-${ADVISOR_SYMBOL:-6919,2886,8150}}"
EXPECTED_COMMIT="${3:-${EXPECTED_COMMIT:-}}"
TIMEOUT="${TIMEOUT:-20}"
SKIP_RELEASE_READINESS="${SKIP_RELEASE_READINESS:-0}"

if [[ "$SKIP_RELEASE_READINESS" != "1" ]]; then
  echo "==> Checking release readiness before public validation..."
  readiness_args=(
    --base-url "$BASE_URL"
    --timeout "$TIMEOUT"
  )
  python3 scripts/check_release_readiness.py "${readiness_args[@]}"
fi

args=(
  --base-url "$BASE_URL"
  --timeout "$TIMEOUT"
)

if [[ -n "$EXPECTED_COMMIT" ]]; then
  args+=(--expected-commit "$EXPECTED_COMMIT")
fi

if [[ -n "$ADVISOR_SYMBOL" ]]; then
  args+=(--advisor-symbol "$ADVISOR_SYMBOL")
fi

python3 scripts/verify_public_deployment.py "${args[@]}"
