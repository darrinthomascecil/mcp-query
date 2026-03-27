#!/bin/bash
set -euo pipefail

# =============================================================================
# Test OAuth2 Proxy auth protection
#
# Usage:
#   ./test-auth.sh --domain query.local.test
# =============================================================================

DOMAIN=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --domain) DOMAIN="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [ -z "$DOMAIN" ]; then
  echo "Usage: ./test-auth.sh --domain query.local.test"
  exit 1
fi

URL="https://${DOMAIN}"
PASS=0
FAIL=0

run_test() {
  local name="$1"
  local expected="$2"
  local actual="$3"

  if [ "$actual" = "$expected" ]; then
    echo "  PASS: $name (got $actual)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $name (expected $expected, got $actual)"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== Testing auth on ${URL} ==="
echo ""

# Test 1: Unauthenticated request should redirect to login
echo "[1] Unauthenticated request"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$URL/")
run_test "Returns 302 redirect" "302" "$STATUS"

# Test 2: Redirect should point to Microsoft login
echo "[2] Redirect target"
LOCATION=$(curl -s -I "$URL/" 2>/dev/null | grep -i "^location:" | head -1)
if echo "$LOCATION" | grep -q "login.microsoftonline.com"; then
  echo "  PASS: Redirects to Entra login"
  PASS=$((PASS + 1))
else
  echo "  FAIL: Not redirecting to Entra"
  echo "  Got: $LOCATION"
  FAIL=$((FAIL + 1))
fi

# Test 3: Healthz should also be protected
echo "[3] Healthz endpoint protected"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$URL/healthz")
run_test "Returns 302 redirect" "302" "$STATUS"

# Test 4: OAuth2 proxy ping endpoint should be accessible (proxy health)
echo "[4] OAuth2 Proxy health"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$URL/ping")
run_test "Returns 200" "200" "$STATUS"

# Test 5: Fake cookie should not grant access
echo "[5] Fake session cookie"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -b "_oauth2_proxy=fake" "$URL/")
run_test "Returns 302 or 403" "302" "$STATUS"

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
