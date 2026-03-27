#!/bin/bash
set -euo pipefail

# =============================================================================
# Entra ID + OAuth2 Proxy setup for mcp-query
#
# Prerequisites:
#   - Azure CLI (az) logged in
#   - kubectl connected to your cluster
#   - A domain pointing at your gateway (e.g., query.yourdomain.com)
#
# Usage:
#   ./setup.sh --domain query.yourdomain.com
#
# What this does:
#   1. Creates an Entra ID app registration
#   2. Creates a client secret
#   3. Deploys OAuth2 Proxy into the cluster
#   4. Updates the HTTPRoute to go through the proxy
# =============================================================================

DOMAIN=""
NAMESPACE="default"
APP_NAME="mcp-query"
PROXY_NAMESPACE="default"

while [[ $# -gt 0 ]]; do
  case $1 in
    --domain) DOMAIN="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [ -z "$DOMAIN" ]; then
  echo "Usage: ./setup.sh --domain query.yourdomain.com"
  exit 1
fi

REDIRECT_URI="https://${DOMAIN}/oauth2/callback"
TENANT_ID=$(az account show --query tenantId -o tsv)

echo "=== Tenant: ${TENANT_ID} ==="
echo "=== Domain: ${DOMAIN} ==="
echo "=== Redirect URI: ${REDIRECT_URI} ==="
echo ""

# -------------------------------------------------------------------------
# Step 1: Create Entra app registration
# -------------------------------------------------------------------------
echo "=== Creating Entra app registration ==="
APP_ID=$(az ad app create \
  --display-name "${APP_NAME}" \
  --web-redirect-uris "${REDIRECT_URI}" \
  --sign-in-audience "AzureADMyOrg" \
  --query appId -o tsv)

echo "App ID: ${APP_ID}"

# Add openid, email, profile API permissions (Microsoft Graph)
GRAPH_API="00000003-0000-0000-c000-000000000000"
az ad app permission add --id "$APP_ID" \
  --api "$GRAPH_API" \
  --api-permissions \
    "openid=Scope" \
    "email=Scope" \
    "profile=Scope"

echo "=== Granting admin consent ==="
az ad app permission admin-consent --id "$APP_ID" || \
  echo "WARNING: Admin consent may require a Global Admin. Grant it manually in the portal."

# -------------------------------------------------------------------------
# Step 2: Create client secret
# -------------------------------------------------------------------------
echo ""
echo "=== Creating client secret ==="
CLIENT_SECRET=$(az ad app credential reset \
  --id "$APP_ID" \
  --display-name "oauth2-proxy" \
  --years 1 \
  --query password -o tsv)

echo "Client secret created (expires in 1 year)"

# -------------------------------------------------------------------------
# Step 3: Generate cookie secret
# -------------------------------------------------------------------------
COOKIE_SECRET=$(python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")

# -------------------------------------------------------------------------
# Step 4: Create k8s secret for OAuth2 Proxy
# -------------------------------------------------------------------------
echo ""
echo "=== Creating k8s secret ==="
kubectl create secret generic oauth2-proxy \
  --namespace "$PROXY_NAMESPACE" \
  --from-literal=client-id="$APP_ID" \
  --from-literal=client-secret="$CLIENT_SECRET" \
  --from-literal=cookie-secret="$COOKIE_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -

# -------------------------------------------------------------------------
# Step 5: Store config
# -------------------------------------------------------------------------
echo ""
echo "=== Creating OAuth2 Proxy configmap ==="
kubectl create configmap oauth2-proxy-config \
  --namespace "$PROXY_NAMESPACE" \
  --from-literal=oidc-issuer-url="https://login.microsoftonline.com/${TENANT_ID}/v2.0" \
  --from-literal=upstream="http://mcp-query.${NAMESPACE}.svc.cluster.local" \
  --from-literal=domain="$DOMAIN" \
  --dry-run=client -o yaml | kubectl apply -f -

# -------------------------------------------------------------------------
# Step 6: Deploy OAuth2 Proxy
# -------------------------------------------------------------------------
echo ""
echo "=== Deploying OAuth2 Proxy ==="
kubectl apply -f auth/k8s/

echo ""
echo "=== Waiting for OAuth2 Proxy ==="
kubectl wait --for=condition=ready pod -l app=oauth2-proxy \
  --namespace "$PROXY_NAMESPACE" --timeout=120s

echo ""
echo "=== Done! ==="
echo "App Registration: ${APP_ID}"
echo "Tenant: ${TENANT_ID}"
echo "URL: https://${DOMAIN}"
echo ""
echo "Users in your tenant can now log in via Entra to access the app."
echo "To restrict to specific groups, add group claims in the app registration."
