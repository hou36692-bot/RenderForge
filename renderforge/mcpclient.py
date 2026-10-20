"""Minimal synchronous MCP stdio client (used to drive renderdoc-mcp from scripts)."""
import json
import subprocess
import threading


class McpClient:
    def __init__(self, exe, cwd=None):
        self.p = subprocess.Popen(
            [exe], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
        self._id = 0
        self._lock = threading.Lock()
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "renderforge", "version": "0.1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def _rpc(self, method, params=None):
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            while True:
                line = self.p.stdout.readline()
                if not line:
                    raise RuntimeError("MCP server exited")
                line = line.strip()
                if not line.startswith("{"):
                    continue
                msg = json.loads(line)
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    return msg["result"]

    def tools(self):
        return self._rpc("tools/list")["tools"]

    def call(self, name, **args):
        """Call a tool; returns parsed JSON when the text payload is JSON, else the raw text."""
        res = self._rpc("tools/call", {"name": name, "arguments": args})
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        if res.get("isError"):
            raise RuntimeError(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()
