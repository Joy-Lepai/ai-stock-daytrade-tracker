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
  readiness_json="$(mktemp)"
  cleanup_readiness_json() {
    rm -f "$readiness_json"
  }
  trap cleanup_readiness_json EXIT
  readiness_args=(
    --base-url "$BASE_URL"
    --timeout "$TIMEOUT"
    --json
  )
  if ! python3 scripts/check_release_readiness.py "${readiness_args[@]}" > "$readiness_json"; then
    echo "Release readiness blocked."
    python3 - "$readiness_json" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

print(f"status: {payload.get('status', '-')}")
print(f"local_head: {str(payload.get('local_head') or '-')[:12]}")
print(f"origin_main: {str(payload.get('origin_main') or '-')[:12]}")
print(f"public_runtime: {str(payload.get('public_runtime') or '-')[:12]}")
print(f"next_action: {payload.get('next_action') or '-'}")
print("operator_gate:")
print(f"- can_push: {payload.get('can_push')}")
print(f"- can_deploy_render: {payload.get('can_deploy_render')}")
print(f"- can_trust_public: {payload.get('can_trust_public')}")
steps = payload.get("release_steps") or []
if steps:
    print("release_steps:")
    for index, item in enumerate(steps, start=1):
        print(f"{index}. {item}")
PY
    exit 1
  fi
  echo "Release readiness OK."
  rm -f "$readiness_json"
  trap - EXIT
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
