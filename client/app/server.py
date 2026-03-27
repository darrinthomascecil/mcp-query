"""MCP Query — natural language interface to MCP servers via Claude."""

import http.server
import json
import logging
import os
import signal
import socketserver
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MCP_URL = os.environ.get("MCP_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
PORT = int(os.environ.get("PORT", "8080"))
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You help users query and manage systems via MCP tools. Be concise.",
)
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "10"))
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", "10000"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "120"))

CLAUDE_API = "https://api.anthropic.com/v1/messages"
STATIC_DIR = Path(__file__).resolve().parent / "static"

log = logging.getLogger("mcp-query")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)


# ---------------------------------------------------------------------------
# MCP client
# ---------------------------------------------------------------------------

class MCPClient:
    """Manages connection to an MCP server, with lazy initialization."""

    def __init__(self, url):
        self.url = url
        self._tools = None
        self._claude_tools = None
        self._lock = threading.Lock()

    @property
    def connected(self):
        return self._tools is not None

    @property
    def claude_tools(self):
        """Return tools in Claude format, fetching from MCP on first access."""
        if self._claude_tools is not None:
            return self._claude_tools
        with self._lock:
            if self._claude_tools is not None:
                return self._claude_tools
            self._fetch_tools()
            return self._claude_tools

    def _request(self, data, headers=None):
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(
            self.url, data=json.dumps(data).encode(), headers=hdrs,
        )
        res = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
        return json.loads(res.read()), res.headers

    def _init_session(self):
        _, headers = self._request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "mcp-query", "version": "1.0"},
            },
        })
        sid = headers.get("Mcp-Session-Id")
        if not sid:
            raise RuntimeError("MCP server did not return a session ID")
        return sid

    def _fetch_tools(self):
        log.info("Connecting to MCP server: %s", self.url)
        sid = self._init_session()
        body, _ = self._request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={"Mcp-Session-Id": sid},
        )
        self._tools = body["result"]["tools"]
        self._claude_tools = [
            {"name": t["name"], "description": t["description"][:1024],
             "input_schema": t["inputSchema"]}
            for t in self._tools
        ]
        log.info("Loaded %d MCP tools", len(self._claude_tools))

    def call_tool(self, name, arguments):
        sid = self._init_session()
        body, _ = self._request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": name, "arguments": arguments}},
            headers={"Mcp-Session-Id": sid},
        )
        if "result" in body and "content" in body["result"]:
            return "\n".join(c.get("text", "") for c in body["result"]["content"])
        return json.dumps(body)


# ---------------------------------------------------------------------------
# Claude agentic loop
# ---------------------------------------------------------------------------

def ask_claude(question, mcp):
    """Send a question to Claude, let it call MCP tools, return the final answer."""
    claude_tools = mcp.claude_tools  # Triggers lazy MCP connection
    messages = [{"role": "user", "content": question}]
    text_parts = []

    for _ in range(MAX_TOOL_ROUNDS):
        req_body = json.dumps({
            "model": ANTHROPIC_MODEL, "max_tokens": 4096,
            "system": SYSTEM_PROMPT, "tools": claude_tools,
            "messages": messages,
        }).encode()
        req = urllib.request.Request(CLAUDE_API, data=req_body, headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        })
        res = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
        reply = json.loads(res.read())

        text_parts = []
        tool_calls = []
        for block in reply["content"]:
            if block["type"] == "text":
                text_parts.append(block["text"])
            elif block["type"] == "tool_use":
                tool_calls.append(block)

        if not tool_calls:
            return "\n".join(text_parts)

        messages.append({"role": "assistant", "content": reply["content"]})
        tool_results = []
        for tc in tool_calls:
            log.info("Tool: %s(%s)", tc["name"], json.dumps(tc["input"])[:200])
            try:
                result = mcp.call_tool(tc["name"], tc["input"])
            except Exception as exc:
                log.error("MCP tool error: %s", exc)
                result = f"Error: {exc}"
            tool_results.append({
                "type": "tool_result", "tool_use_id": tc["id"], "content": result,
            })
        messages.append({"role": "user", "content": tool_results})

    return "\n".join(text_parts) if text_parts else "Reached maximum tool call rounds."


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):
    mcp = None        # Set at startup
    _index_html = b""  # Cached at startup

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/query":
            self._send_json(404, {"error": "Not found"})
            return

        if not Handler.mcp:
            self._send_json(503, {"error": "No MCP server configured. Set MCP_URL."})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "Request too large"})
            return
        if content_length == 0:
            self._send_json(400, {"error": "Empty request"})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "Invalid JSON"})
            return

        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            self._send_json(400, {"error": "Missing or empty 'question' field"})
            return

        question = question.strip()[:2000]
        log.info("Question: %s", question[:100])

        try:
            answer = ask_claude(question, Handler.mcp)
            self._send_json(200, {"answer": answer})
        except urllib.error.HTTPError as exc:
            log.error("API error: %s %s", exc.code, exc.read().decode()[:500])
            self._send_json(502, {"error": "Upstream API error"})
        except urllib.error.URLError as exc:
            log.error("Connection error: %s", exc)
            self._send_json(503, {"error": "Cannot reach MCP server"})
        except Exception as exc:
            log.error("Internal error: %s", exc)
            self._send_json(500, {"error": "Internal server error"})

    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return

        if self.path == "/readyz":
            if Handler.mcp and Handler.mcp.connected:
                self._send_json(200, {"status": "ready"})
            else:
                self._send_json(503, {"status": "not ready"})
            return

        if self.path == "/" or self.path == "/index.html":
            if not Handler._index_html:
                self._send_json(404, {"error": "Not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(Handler._index_html)
            return

        self._send_json(404, {"error": "Not found"})

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is required")
        sys.exit(1)

    # Cache static HTML
    index_path = STATIC_DIR / "index.html"
    if index_path.is_file():
        Handler._index_html = index_path.read_bytes()
    else:
        log.warning("No index.html found at %s", index_path)

    # MCP is optional at startup — connects lazily on first query
    if MCP_URL:
        Handler.mcp = MCPClient(MCP_URL)
        log.info("MCP server configured: %s (will connect on first query)", MCP_URL)
    else:
        log.warning("No MCP_URL set — queries will fail until one is configured")

    log.info("Claude model: %s", ANTHROPIC_MODEL)

    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    log.info("Listening on :%d", PORT)

    stop.wait()
    log.info("Shutting down...")
    server.shutdown()
    thread.join()


if __name__ == "__main__":
    main()
