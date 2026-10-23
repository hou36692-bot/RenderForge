"""Minimal Streamable-HTTP MCP client (used to talk to the official UE 5.8 Unreal MCP server and other HTTP MCPs)."""
import json
import urllib.request


class HttpMcp:
    def __init__(self, url="http://localhost:8000/mcp", timeout=60):
        self.url, self.timeout, self.sid, self._id = url, timeout, None, 0
        r = self._post("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "renderforge", "version": "0.1"}})
        self._post("notifications/initialized", None, notify=True)
        self.server = r

    def _post(self, method, params, notify=False):
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self._id += 1
            body["id"] = self._id
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(self.url, json.dumps(body).encode(), h)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.sid = sid
            raw = resp.read().decode("utf-8", "replace")
        if notify or not raw.strip():
            return None
        if raw.lstrip().startswith("event:") or raw.lstrip().startswith("data:"):
            raw = next(l[5:].strip() for l in raw.splitlines() if l.startswith("data:"))
        msg = json.loads(raw)
        if "error" in msg:
            raise RuntimeError(msg["error"])
        return msg["result"]

    def tools(self):
        return self._post("tools/list", {})["tools"]

    def call(self, name, **args):
        res = self._post("tools/call", {"name": name, "arguments": args})
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        if res.get("isError"):
            raise RuntimeError(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
