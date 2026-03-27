# mcp-query

A natural language interface to Kubernetes clusters via [MCP](https://modelcontextprotocol.io/). Ask questions in plain English, get answers powered by Claude and MCP tools.

```
client/   — Web UI + Python backend that sends questions to Claude
server/   — AKS MCP server that provides Kubernetes/Azure tools
```

## From scratch

Everything you need: a Kubernetes cluster, the MCP server, and the client.

### Prerequisites

- [kind](https://kind.sigs.k8s.io/) (or any Kubernetes cluster)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- Python 3.9+
- An [Anthropic API key](https://console.anthropic.com/settings/keys)

### Setup

```bash
git clone https://github.com/darrinthomascecil/mcp-query.git
cd mcp-query

# 1. Create a cluster (skip if you already have one)
kind create cluster

# 2. Deploy the MCP server
kubectl apply -f server/k8s/
kubectl wait --for=condition=ready pod -l app=aks-mcp -n aks-mcp --timeout=120s

# 3. Port-forward so the client can reach the MCP server
kubectl port-forward -n aks-mcp svc/aks-mcp 8000:8000 &

# 4. Run the client
cd client/app
ANTHROPIC_API_KEY=sk-ant-... MCP_URL=http://localhost:8000/mcp python3 server.py
```

Open http://localhost:8080 and ask a question.

## Quick start (Kubernetes)

```bash
# 1. Deploy the MCP server
kubectl apply -f server/k8s/

# 2. Create secrets and config for the client
kubectl create secret generic mcp-query-secret \
  --from-literal=anthropic-api-key=sk-ant-...

kubectl create configmap mcp-query-config \
  --from-literal=mcp-url=http://aks-mcp.aks-mcp.svc.cluster.local:8000/mcp

# 3. Deploy the client
kubectl apply -f client/k8s/
```

## Local development

```bash
# Terminal 1: port-forward the MCP server from your cluster
kubectl port-forward -n aks-mcp svc/aks-mcp 8000:8000

# Terminal 2: run the client
cd client/app
ANTHROPIC_API_KEY=sk-ant-... MCP_URL=http://localhost:8000/mcp python3 server.py
```

Then open http://localhost:8080.

## How it works

```
Browser  -->  POST /query  -->  client (Python)  -->  Claude API
                                     |                    |
                                     |   <-- tool_use  ---+
                                     |
                                     +--> server (MCP tools/call)
                                     |
                                     |   -- tool_result -->
                                     |                    |
                                     |   <-- text     ----+
                                     |
              <-- { answer } --------+
```

1. User types a natural language question
2. Client sends the question to Claude with all available MCP tool definitions
3. Claude decides which tools to call and with what arguments
4. Client executes those tool calls against the MCP server
5. Tool results go back to Claude for summarization
6. Final answer is returned to the browser

## Configuration

See [client/](client/) and [server/](server/) READMEs for details.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key |
| `MCP_URL` | No | — | MCP server endpoint |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-20250514` | Claude model |
| `PORT` | No | `8080` | Client listen port |
| `SYSTEM_PROMPT` | No | See `.env.example` | System prompt for Claude |
| `MAX_TOOL_ROUNDS` | No | `10` | Max tool-call rounds per question |
| `REQUEST_TIMEOUT` | No | `120` | HTTP timeout in seconds |
