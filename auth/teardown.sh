#!/bin/bash
set -euo pipefail

# =============================================================================
# Remove OAuth2 Proxy and Entra app registration
#
# Usage:
#   ./teardown.sh
#   ./teardown.sh --app-id <app-registration-id>   # also deletes the Entra app
# =============================================================================

APP_ID=""
NAMESPACE="default"

while [[ $# -gt 0 ]]; do
  case $1 in
    --app-id) APP_ID="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "=== Removing OAuth2 Proxy k8s resources ==="
kubectl delete -f auth/k8s/ --ignore-not-found
kubectl delete secret oauth2-proxy -n "$NAMESPACE" --ignore-not-found
kubectl delete configmap oauth2-proxy-config -n "$NAMESPACE" --ignore-not-found

echo "=== Restoring direct HTTPRoute ==="
kubectl apply -f client/k8s/httproute.yaml

if [ -n "$APP_ID" ]; then
  echo "=== Deleting Entra app registration: ${APP_ID} ==="
  az ad app delete --id "$APP_ID"
  echo "App registration deleted."
fi

echo ""
echo "=== Done! ==="
echo "Auth has been removed. The app is directly accessible again."
