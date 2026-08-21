#!/usr/bin/env python3
"""Drive the headless browser, for the things a dumped DOM cannot answer.

`screens_gate.rendered_dom` asks Chromium for the page's HTML after React has
drawn it, which settles what is *on* a page. It cannot settle two other kinds
of question:

  * **What the layout does.** Whether a page overflows sideways is a fact about
    boxes at a width, not about markup. `scrollWidth` exists only in a browser
    that has laid the page out.
  * **What happens when you press something.** A confirmation dialog is not in
    the document until a button is clicked.

So this drives a real browser over the DevTools protocol: start the Playwright
image with a debugging port, connect, navigate, and evaluate JavaScript in the
page. Standard library only — the WebSocket client below is about sixty lines
and is the whole reason no driver package is needed.

    from tools.browser import Browser
    with Browser(width=390, height=780) as browser:
        print(browser.evaluate(url, "document.documentElement.scrollWidth"))
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import struct
import subprocess
import time

IMAGE = "mcr.microsoft.com/playwright:v1.56.0-noble"
CHROME = ("/ms-playwright/chromium_headless_shell-1194/chrome-linux/"
          "headless_shell")
NETWORK = "hr-system_default"


class WebSocket:
    """The smallest client that can hold a DevTools conversation."""

    def __init__(self, host: str, port: int, path: str) -> None:
        self.sock = socket.create_connection((host, port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            .encode()
        )
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self.sock.recv(1)
            if not chunk:
                raise RuntimeError("the browser closed during the handshake")
            header += chunk
        if b"101" not in header.split(b"\r\n")[0]:
            raise RuntimeError(f"no websocket: {header[:120]!r}")
        self._buffer = b""

    def _read(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("the browser closed the connection")
            self._buffer += chunk
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    def send(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])  # FIN, text
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4]
                       for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        message = b""
        while True:
            first, second = self._read(2)
            final, opcode = first & 0x80, first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            if second & 0x80:  # a server frame is never masked
                self._read(4)
            message += self._read(length)
            if opcode == 0x8:
                raise RuntimeError("the browser closed the connection")
            if final:
                return message.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """A headless Chromium in a container, driven from here."""

    def __init__(self, width: int = 390, height: int = 780,
                 port: int = 9222, network: str = NETWORK) -> None:
        self.width, self.height, self.port = width, height, port
        self.network = network
        self.container: str | None = None
        self.socket: WebSocket | None = None
        self._id = 0

    def __enter__(self) -> "Browser":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # Chromium binds its debugging port to the container's own loopback and
    # ignores every flag that asks otherwise, so a published port reaches
    # nothing. Node is in this image for its own reasons; fifteen lines of it
    # forward the published port to Chromium's.
    FORWARDER = (
        "require('net').createServer(c=>{"
        "const u=require('net').connect(%d,'127.0.0.1');"
        "c.pipe(u);u.pipe(c);u.on('error',()=>c.end());c.on('error',()=>{})"
        "}).listen(%d,'0.0.0.0')"
    )

    def start(self) -> None:
        inner = self.port + 1
        command = (
            f"{CHROME} --no-sandbox --disable-gpu "
            f"--remote-debugging-port={inner} "
            f"--window-size={self.width},{self.height} about:blank & "
            f"exec node -e \"{self.FORWARDER % (inner, self.port)}\""
        )
        self.container = subprocess.run(
            ["docker", "run", "-d", "--rm", "--network", self.network,
             "-p", f"127.0.0.1:{self.port}:{self.port}", IMAGE,
             "sh", "-c", command],
            capture_output=True, check=True, text=True).stdout.strip()

        try:
            target = None
            for _ in range(120):
                try:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", self.port, timeout=2)
                    connection.request("GET", "/json/list")
                    pages = json.loads(connection.getresponse().read())
                    target = next((page["webSocketDebuggerUrl"] for page in pages
                                   if page.get("type") == "page"), None)
                    if target:
                        break
                except Exception:
                    pass
                time.sleep(0.5)
            if not target:
                raise RuntimeError("the browser never offered a page to drive")

            self.socket = WebSocket("127.0.0.1", self.port,
                                    "/devtools/page/" + target.rsplit("/", 1)[1])
            # The window size flag sets the window; the viewport is what layout
            # is measured against, so it is set explicitly and for the same
            # reason.
            self.command("Emulation.setDeviceMetricsOverride", {
                "width": self.width, "height": self.height,
                "deviceScaleFactor": 1, "mobile": True,
            })
        except Exception:
            # **Never leave the container running.** A browser holding the
            # published port makes the next run fail for a reason that has
            # nothing to do with the page.
            self.stop()
            raise

    def command(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self.socket.send(json.dumps(
            {"id": self._id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get("id") != self._id:
                continue  # an event; nothing here listens for events
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error']}")
            return message.get("result", {})

    def go(self, url: str, ready: str = "document.querySelector('#root')"
                                        " && document.querySelector('#root')"
                                        ".children.length > 0") -> None:
        """Navigate, and wait until the page has actually drawn something.

        Waiting on the load event is not enough: the interface fetches its data
        and renders afterwards, and a measurement taken before that is a
        measurement of an empty div.
        """
        self.command("Page.navigate", {"url": url})
        for _ in range(120):
            try:
                if self.evaluate_raw(ready) is True:
                    time.sleep(0.3)  # one more frame, for layout to settle
                    return
            except RuntimeError:
                pass
            time.sleep(0.25)
        raise RuntimeError(f"{url} never rendered anything")

    def evaluate_raw(self, expression: str):
        result = self.command("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        if result.get("exceptionDetails"):
            raise RuntimeError(str(result["exceptionDetails"])[:300])
        return result["result"].get("value")

    def evaluate(self, url: str, expression: str):
        self.go(url)
        return self.evaluate_raw(expression)

    def stop(self) -> None:
        if self.socket:
            self.socket.close()
        if self.container:
            subprocess.run(["docker", "kill", self.container],
                           capture_output=True)
        self.socket = self.container = None


if __name__ == "__main__":
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else "http://api:8100/guard"
    expression = sys.argv[2] if len(sys.argv) > 2 else (
        "JSON.stringify({scroll: document.documentElement.scrollWidth, "
        "view: window.innerWidth})")
    with Browser() as browser:
        print(browser.evaluate(url, expression))
