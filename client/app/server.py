"""MCP Query — natural language interface to MCP servers via LLMs."""

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
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "10"))
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", "10000"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "120"))

STATIC_DIR = Path(__file__).resolve().parent / "static"

log = logging.getLogger("mcp-query")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)


# ---------------------------------------------------------------------------
# LLM Providers
# ---------------------------------------------------------------------------

class AnthropicProvider:
    name = "anthropic"
    api_url = "https://api.anthropic.com/v1/messages"
    default_model = "claude-sonnet-4-20250514"
    system_prompt = "You help users query and manage Kubernetes clusters via MCP tools. Be concise and direct."

    def __init__(self, api_key):
        self.api_key = api_key

    def headers(self):
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

    def format_tools(self, mcp_tools):
        return [
            {"name": t["name"], "description": t["description"][:1024],
             "input_schema": t["inputSchema"]}
            for t in mcp_tools
        ]

    def build_request(self, messages, tools, model):
        return json.dumps({
            "model": model, "max_tokens": 4096,
            "system": self.system_prompt,
            "tools": tools, "messages": messages,
        }).encode()

    def parse_response(self, reply):
        text_parts = []
        tool_calls = []
        for block in reply["content"]:
            if block["type"] == "text":
                text_parts.append(block["text"])
            elif block["type"] == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "name": block["name"],
                    "arguments": block["input"],
                })
        return text_parts, tool_calls

    def append_assistant(self, messages, reply):
        messages.append({"role": "assistant", "content": reply["content"]})

    def append_tool_results(self, messages, results):
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
            for r in results
        ]})


class OpenAIProvider:
    name = "openai"
    api_url = "https://api.openai.com/v1/chat/completions"
    default_model = "gpt-4o"
    system_prompt = "You help users query and manage Kubernetes clusters via tools. Be concise. Always use tools to answer questions rather than guessing."

    def __init__(self, api_key):
        self.api_key = api_key

    def headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def format_tools(self, mcp_tools):
        return [
            {"type": "function", "function": {
                "name": t["name"],
                "description": t["description"][:1024],
                "parameters": t["inputSchema"],
            }}
            for t in mcp_tools
        ]

    def build_request(self, messages, tools, model):
        return json.dumps({
            "model": model, "max_tokens": 4096,
            "messages": [{"role": "system", "content": self.system_prompt}] + messages,
            "tools": tools,
        }).encode()

    def parse_response(self, reply):
        text_parts = []
        tool_calls = []
        msg = reply["choices"][0]["message"]
        if msg.get("content"):
            text_parts.append(msg["content"])
        for tc in msg.get("tool_calls", []):
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append({
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": args,
            })
        return text_parts, tool_calls

    def append_assistant(self, messages, reply):
        messages.append(reply["choices"][0]["message"])

    def append_tool_results(self, messages, results):
        for r in results:
            messages.append({
                "role": "tool", "tool_call_id": r["id"], "content": r["content"],
            })


# Provider registry
PROVIDERS = {}


def _init_providers():
    if ANTHROPIC_API_KEY:
        PROVIDERS["anthropic"] = AnthropicProvider(ANTHROPIC_API_KEY)
        log.info("Anthropic provider: enabled")
    else:
        log.info("Anthropic provider: disabled (no ANTHROPIC_API_KEY)")
    if OPENAI_API_KEY:
        PROVIDERS["openai"] = OpenAIProvider(OPENAI_API_KEY)
        log.info("OpenAI provider: enabled")
    else:
        log.info("OpenAI provider: disabled (no OPENAI_API_KEY)")


# ---------------------------------------------------------------------------
# MCP client
# ---------------------------------------------------------------------------

class MCPClient:
    """Manages connection to an MCP server, with lazy initialization."""

    def __init__(self, url):
        self.url = url
        self._tools = None
        self._lock = threading.Lock()

    @property
    def connected(self):
        return self._tools is not None

    @property
    def tools(self):
        """Return raw MCP tools, fetching on first access."""
        if self._tools is not None:
            return self._tools
        with self._lock:
            if self._tools is not None:
                return self._tools
            self._fetch_tools()
            return self._tools

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
        log.info("Loaded %d MCP tools", len(self._tools))

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
# LLM agentic loop
# ---------------------------------------------------------------------------

def ask_llm(question, mcp, provider, model):
    """Send a question to an LLM, let it call MCP tools, return the final answer."""
    mcp_tools = mcp.tools
    tools = provider.format_tools(mcp_tools)
    messages = [{"role": "user", "content": question}]
    text_parts = []

    for _ in range(MAX_TOOL_ROUNDS):
        req_body = provider.build_request(messages, tools, model)
        req = urllib.request.Request(
            provider.api_url, data=req_body, headers=provider.headers(),
        )
        res = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
        reply = json.loads(res.read())

        text_parts, tool_calls = provider.parse_response(reply)

        if not tool_calls:
            return "\n".join(text_parts)

        provider.append_assistant(messages, reply)
        results = []
        for tc in tool_calls:
            log.info("Tool: %s(%s)", tc["name"], json.dumps(tc["arguments"])[:200])
            try:
                result = mcp.call_tool(tc["name"], tc["arguments"])
            except Exception as exc:
                log.error("MCP tool error: %s", exc)
                result = f"Error: {exc}"
            results.append({"id": tc["id"], "content": result})
        provider.append_tool_results(messages, results)

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
    mcp = None
    _index_html = b""

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/providers":
            models = []
            for p in PROVIDERS.values():
                models.append({"provider": p.name, "model": p.default_model})
            self._send_json(200, {"providers": models})
            return

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

        # Resolve provider
        provider_name = payload.get("provider", "")
        model = payload.get("model", "")
        provider = PROVIDERS.get(provider_name)
        if not provider:
            # Fall back to first available
            provider = next(iter(PROVIDERS.values()), None)
        if not provider:
            self._send_json(503, {"error": "No LLM provider configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."})
            return
        if not model:
            model = provider.default_model

        question = question.strip()[:2000]
        log.info("Question [%s/%s]: %s", provider.name, model, question[:100])

        try:
            answer = ask_llm(question, Handler.mcp, provider, model)
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


def _eager_mcp_connect(mcp, retries=5, delay=5):
    """Connect to MCP in the background with retries for startup ordering."""
    import time
    for attempt in range(retries):
        try:
            mcp.tools
            return
        except Exception as exc:
            if attempt < retries - 1:
                log.warning("MCP connect failed (%s), retrying in %ds...", exc, delay)
                time.sleep(delay)
            else:
                log.error("Could not connect to MCP server after %d attempts: %s", retries, exc)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    _init_providers()

    if not PROVIDERS:
        log.error("At least one LLM provider required. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")
        sys.exit(1)

    # Cache static HTML
    index_path = STATIC_DIR / "index.html"
    if index_path.is_file():
        Handler._index_html = index_path.read_bytes()
    else:
        log.warning("No index.html found at %s", index_path)

    # MCP is optional at startup
    if MCP_URL:
        Handler.mcp = MCPClient(MCP_URL)
        log.info("MCP server: %s", MCP_URL)
        threading.Thread(target=_eager_mcp_connect, args=(Handler.mcp,), daemon=True).start()
    else:
        log.warning("No MCP_URL set — queries will fail until one is configured")

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
