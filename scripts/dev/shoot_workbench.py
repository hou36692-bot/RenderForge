"""Capture full-resolution screenshots of the local workbench for the README.

The desktop app's preview screenshot is capped at 800 px wide, which is too small for a README figure on a
high-DPI display. This drives a real headless Chrome through the DevTools protocol instead, at a chosen device
scale factor, so the PNG has the pixel count the page actually renders at.

    python scripts/dev/shoot_workbench.py <out-dir> [--port 9222] [--width 1440] [--height 900] [--scale 2]

Requires Chrome or Edge on PATH (or set CHROME_PATH). Standard library only: it speaks the small subset of CDP
needed here (navigate, evaluate, screenshot) over a websocket implemented on top of socket + hashlib.
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------- minimal websocket client

class WS:
    def __init__(self, url):
        # ws://host:port/path
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError(f"websocket upgrade failed: {buf[:200]!r}")
        self.buf = buf.split(b"\r\n\r\n", 1)[1]

    def send(self, obj):
        data = json.dumps(obj).encode()
        header = bytearray([0x81])
        n = len(data)
        mask = os.urandom(4)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        b1, b2 = self._read(2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        return json.loads(self._read(n).decode())


# ---------------------------------------------------------------- driver

def chrome_path():
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).is_file():
        return env
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if Path(p).is_file():
            return p
    for name in ("chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("no Chrome/Edge found; set CHROME_PATH")


def start_browser(port, width, height, scale):
    """Launch headless Chrome and return (process, the page target's websocket URL).

    The browser opens the workbench itself, so the first page target already exists: /json/new needs a PUT on
    current Chrome and is not worth depending on."""
    exe = chrome_path()
    profile = ROOT / "workspace" / "_logs" / "shot-profile"
    profile.mkdir(parents=True, exist_ok=True)
    args = [exe, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
            f"--window-size={width},{height}", f"--force-device-scale-factor={scale}", "about:blank"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(160):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as r:
                targets = json.load(r)
            pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                return proc, pages[0]["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    proc.kill()
    raise SystemExit("headless browser did not expose a page target on its DevTools port")


class Tab:
    def __init__(self, ws_url, width, height, scale):
        self.ws = WS(ws_url)
        self.id = 0
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                  deviceScaleFactor=scale, mobile=False)

    def call(self, method, **params):
        self.id += 1
        self.ws.send({"id": self.id, "method": method, "params": params})
        while True:
            msg = self.ws.recv()
            if msg.get("id") == self.id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def goto(self, url, wait=2.0):
        self.call("Page.navigate", url=url)
        time.sleep(wait)

    def js(self, expr, await_promise=False):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=await_promise)
        return r.get("result", {}).get("value")

    def shot(self, path, clip=None):
        """PNG of the page (optionally a CSS-pixel rect), at the device scale factor."""
        params = {"format": "png", "captureBeyondViewport": False}
        if clip:
            params["clip"] = {**clip, "scale": 1}
        r = self.call("Page.captureScreenshot", **params)
        Path(path).write_bytes(base64.b64decode(r["data"]))
        return path


SHOTS = [
    # (file name, setup JS run before the shot, settle seconds)
    ("workbench-overview", "selectCase('wuwa_bridge'); setView('overview');", 2.5),
    ("workbench-assets", "setView('assets');", 2.0),
    ("workbench-locate", "document.querySelector('#btn-pick').click();", 1.2),
]

# The overview's input row prints the capture's absolute path, which is the author's own directory layout. A
# README figure must not publish it, so the row is shortened to the file name before the shot.
REDACT_JS = """
(() => {
  for (const dd of document.querySelectorAll('#case-main .kv dd')) {
    const t = dd.textContent || '';
    const m = t.match(/[\\\\/]([^\\\\/]+\\.rdc)$/i);
    if (m) { dd.textContent = '…\\\\' + m[1]; dd.title = 'capture path hidden in this screenshot'; }
  }
  return true;
})()
"""


def clip_of(tab, sel, pad=0):
    """CSS-pixel rect of an element, or None."""
    return tab.js(f"""(() => {{
      const el = document.querySelector({json.dumps(sel)});
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {{x: Math.max(0, r.left - {pad}), y: Math.max(0, r.top - {pad}),
               width: Math.min(innerWidth, r.right + {pad}) - Math.max(0, r.left - {pad}),
               height: Math.min(innerHeight, r.bottom + {pad}) - Math.max(0, r.top - {pad})}};
    }})()""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--url", default="http://127.0.0.1:8770/")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--scale", type=int, default=2)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    proc, ws_url = start_browser(a.port, a.width, a.height, a.scale)
    try:
        tab = Tab(ws_url, a.width, a.height, a.scale)
        tab.goto(a.url, wait=3.0)
        for name, setup, wait in SHOTS:
            if setup:
                tab.js(setup)
            time.sleep(wait)
            tab.js(REDACT_JS)
            # crop to the content that matters: the overview's report table and the locate view's empty grid
            # otherwise leave a large blank area that reads as "unfinished"
            clip = None
            if name == "workbench-locate":
                # the grid holds two cards after a pick; cropping to their bottom keeps the figure from being
                # three quarters empty panel
                clip = tab.js("""(() => {
                  const grid = document.querySelector('#asset-grid');
                  const cards = grid ? grid.querySelectorAll('.card') : [];
                  const rel = document.querySelector('#asset-related');
                  let bottom = 0;
                  for (const c of cards) bottom = Math.max(bottom, c.getBoundingClientRect().bottom);
                  if (rel && rel.classList.contains('show')) bottom = Math.max(bottom, rel.getBoundingClientRect().bottom);
                  if (!bottom) return null;
                  return {x: 0, y: 0, width: innerWidth, height: Math.min(innerHeight, bottom + 28)};
                })()""")
            if name == "workbench-overview":
                clip = tab.js("""(() => {
                  const pane = document.querySelector('#case-main');
                  const head = pane.querySelector('.case-head');
                  const kv = pane.querySelector('.kv');
                  const notes = pane.querySelector('.notes');
                  const report = pane.querySelector('.tbl');
                  if (!head || !kv) return null;
                  const top = pane.getBoundingClientRect().top;
                  const bottom = (report || notes || kv).getBoundingClientRect().bottom;
                  return {x: 0, y: 0, width: innerWidth, height: Math.min(innerHeight, bottom + 24)};
                })()""")
            p = tab.shot(out / f"{name}.png", clip=clip)
            print(f"{p}  {Path(p).stat().st_size // 1024} KB")
        # the locate shot needs a click on the frame, which a synthetic MouseEvent cannot fake (the handler reads
        # clientX/clientY from a real event): dispatch one with the coordinates the page expects instead
        box = tab.js("(() => { const i = document.querySelector('#picker img'); if (!i) return null;"
                     "const r = i.getBoundingClientRect();"
                     "return {x: r.left + r.width * 0.5, y: r.top + r.height * 0.38}; })()")
        if box:
            tab.js(f"(() => {{ const i = document.querySelector('#picker img');"
                   f"i.dispatchEvent(new MouseEvent('click', {{clientX: {box['x']}, clientY: {box['y']}, bubbles: true}})); }})()")
            time.sleep(2.0)
            tab.js("(() => { const h = [...document.querySelectorAll('#picker .hit')].find(e => e.innerText.includes('Bri'))"
                   " || document.querySelector('#picker .hit'); if (h) h.querySelector('button').click(); })()")
            time.sleep(2.0)
            clip = tab.js("""(() => {
              const grid = document.querySelector('#asset-grid');
              const cards = grid ? grid.querySelectorAll('.card') : [];
              const rel = document.querySelector('#asset-related');
              let bottom = 0;
              for (const c of cards) bottom = Math.max(bottom, c.getBoundingClientRect().bottom);
              if (rel && rel.classList.contains('show')) bottom = Math.max(bottom, rel.getBoundingClientRect().bottom);
              if (!bottom) return null;
              return {x: 0, y: 0, width: innerWidth, height: Math.min(innerHeight, bottom + 28)};
            })()""")
            p = tab.shot(out / "workbench-locate.png", clip=clip)
            print(f"{p}  {Path(p).stat().st_size // 1024} KB (after a pick)")
    finally:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
