# AKS MCP Server

The MCP server that provides Kubernetes and Azure management tools. Uses the official [`ghcr.io/azure/aks-mcp`](https://github.com/Azure/aks-mcp) image.

## Deploy to Kubernetes

```bash
kubectl apply -f k8s/
```

This creates:
- `aks-mcp` namespace
- ServiceAccount with ClusterRole for Kubernetes API access
- Deployment running the MCP server on port 8000
- ClusterIP Service

## Verify

```bash
kubectl get pods -n aks-mcp
```

## Access

From within the cluster:
```
http://aks-mcp.aks-mcp.svc.cluster.local:8000/mcp
```

From your machine (for local development):
```bash
kubectl port-forward -n aks-mcp svc/aks-mcp 8000:8000
# Then: http://localhost:8000/mcp
```

## Configuration

The server runs with these defaults:

| Setting | Value | Description |
|---|---|---|
| Transport | `streamable-http` | MCP transport protocol |
| Port | `8000` | Listen port |
| Access level | `readwrite` | Allows both read and write kubectl operations |
| Timeout | `600` | Request timeout in seconds |

Edit `k8s/deployment.yaml` args to change these.
