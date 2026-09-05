"""Latency probe: wt screen push + pty round trip over the websocket."""
import asyncio
import json
import time
import urllib.request

import websockets

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from termpilot_cli_client import default_base  # noqa: E402

cfg = json.load(open("config.json"))
BASE = default_base(cfg)
WS = BASE.replace("http://", "ws://")
H = {"X-Token": cfg["token"], "Content-Type": "application/json"}


def call(m, p, b=None):
    req = urllib.request.Request(BASE + p, method=m, headers=H)
    return json.loads(urllib.request.urlopen(req, json.dumps(b).encode() if b else None, timeout=10).read())


async def main():
    wts = [s for s in call("GET", "/api/sessions") if s["kind"] == "wt"]
    if wts:
        async with websockets.connect(WS + "/ws/term/%s?token=%s" % (wts[0]["name"], cfg["token"]), max_size=None) as ws:
            first = await asyncio.wait_for(ws.recv(), 5)
            print("wt initial screen chars:", len(first))
    call("POST", "/api/sessions", {"name": "lat"})
    await asyncio.sleep(3)
    async with websockets.connect(WS + "/ws/term/lat?token=" + cfg["token"], max_size=None) as ws:
        await asyncio.wait_for(ws.recv(), 5)
        for i in range(4):
            t0 = time.time()
            await ws.send(json.dumps({"t": "input", "d": "echo LAT-MARK%d\r" % i}))
            buf = ""
            while ("LAT-MARK%d\r\n" % i) not in buf and time.time() - t0 < 5:
                buf += await asyncio.wait_for(ws.recv(), 5)
            print("pty round trip #%d (type -> echo over ws): %.0f ms" % (i, (time.time() - t0) * 1000))
            await asyncio.sleep(0.3)
    call("DELETE", "/api/sessions/lat")


asyncio.run(main())
