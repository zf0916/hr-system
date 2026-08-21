"""Run both applications, on both ports, in one process.

**Two ports, one codebase, one container** (SPEC §14):

    the receiver      the device pushes punches at it, on the LAN only
    the HR interface  people use it, over the LAN or Tailscale

They are separate ASGI applications with separate routing tables, started side
by side here. **Nothing routes between them.** That separation is the whole
point: a tunnel pointed at the HR port cannot reach `/iclock/`, because the app
on that port does not have those routes to give.

    python -m app.serve
"""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from app.hr_app import hr_app
from app.main import app as device_app

log = logging.getLogger("hr.serve")

# Ports inside the container. What they are published as is compose's business
# (docker-compose.yml), and the published receiver port is half of the device's
# Cloud Server Setting (SPEC §10).
DEVICE_PORT = int(os.environ.get("DEVICE_PORT", "8000"))
HR_PORT = int(os.environ.get("HR_PORT_INTERNAL", "8100"))
HOST = os.environ.get("BIND_HOST", "0.0.0.0")


async def run() -> None:
    servers = [
        uvicorn.Server(uvicorn.Config(
            device_app, host=HOST, port=DEVICE_PORT, log_level="info",
            access_log=True,
        )),
        uvicorn.Server(uvicorn.Config(
            hr_app, host=HOST, port=HR_PORT, log_level="info",
            access_log=True,
        )),
    ]
    print(f"receiver      http://{HOST}:{DEVICE_PORT}/iclock/   (the device)")
    print(f"HR interface  http://{HOST}:{HR_PORT}/              (people)")
    print("the device routes are not served on the HR port (SPEC §14)")
    await asyncio.gather(*(server.serve() for server in servers))


def main() -> int:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
