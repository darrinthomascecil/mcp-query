# MCP Query Client

Web UI and Python backend that takes natural language questions, sends them to Claude with MCP tool definitions, executes tool calls, and returns answers.

Zero dependencies — uses only the Python standard library.

## Run locally

```bash
cd app
ANTHROPIC_API_KEY=sk-ant-... MCP_URL=http://localhost:8000/mcp python3 server.py
```

Open http://localhost:8080.

## Docker

```bash
docker build -t mcp-query .
docker run -p 8080:8080 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e MCP_URL=http://your-mcp-server:8000/mcp \
  mcp-query
```

## Configuration

All via environment variables. See [.env.example](.env.example) for the full list.
