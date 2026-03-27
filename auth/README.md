# Auth (Entra ID + OAuth2 Proxy)

Protects the mcp-query app with Microsoft Entra ID authentication. Users must log in with their organizational account to access the app.

## Architecture

```
Browser --> Gateway --> OAuth2 Proxy --> mcp-query
                            |
                      Entra ID (OIDC)
```

OAuth2 Proxy handles the login flow. Unauthenticated users are redirected to the Microsoft login page. Once authenticated, the proxy forwards requests to mcp-query with user identity headers (`X-Forwarded-User`, `X-Forwarded-Email`).

## Setup

```bash
./auth/setup.sh --domain query.yourdomain.com
```

This will:
1. Create an Entra ID app registration
2. Generate a client secret
3. Deploy OAuth2 Proxy into the cluster
4. Route traffic through the proxy

## Teardown

```bash
# Remove proxy, keep Entra app registration
./auth/teardown.sh

# Remove proxy AND delete the Entra app registration
./auth/teardown.sh --app-id <app-registration-id>
```

## Local development

For local dev without auth, just don't apply `auth/k8s/`. Traffic goes directly to the app via `client/k8s/httproute.yaml`.

To test auth locally with kind, run `setup.sh` with your `*.local.test` domain and register `https://query.local.test/oauth2/callback` as the redirect URI in the Entra app registration.

## Restricting access to specific groups

1. In the Entra app registration, go to **Token configuration** > **Add groups claim**
2. Add the `--allowed-group` flag to the OAuth2 Proxy deployment args:
   ```
   - --allowed-group=<entra-group-object-id>
   ```
