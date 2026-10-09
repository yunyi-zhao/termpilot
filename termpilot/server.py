"""aiohttp HTTP + WebSocket API and static WebUI."""
import asyncio
import json
import logging
import os
import sys

from aiohttp import web

from . import config as config_mod
from .manager import Manager

log = logging.getLogger("termpilot")
STATIC = config_mod.STATIC_DIR


def _json(data, status=200):
    return web.json_response(data, status=status)


@web.middleware
async def auth_mw(request, handler):
    if request.path.startswith("/api/") or request.path.startswith("/ws/"):
        tok = request.app["token"]
        got = request.headers.get("X-Token") or request.query.get("token") or request.cookies.get("tp_token")
        if got != tok:
            return _json({"error": "unauthorized"}, 401)
    return await handler(request)


@web.middleware
async def error_mw(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except KeyError as e:
        return _json({"error": str(e)}, 404)
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    except Exception as e:
        log.exception("handler error")
        return _json({"error": "%s: %s" % (type(e).__name__, e)}, 500)


def build_app(cfg, mgr):
    app = web.Application(middlewares=[error_mw, auth_mw])
    app["cfg"] = cfg
    app["mgr"] = mgr
    app["token"] = cfg["token"]

    async def index(request):
        return web.FileResponse(os.path.join(STATIC, "index.html"))

    async def state(request):
        return _json(mgr.snapshot())

    async def sessions_list(request):
        return _json(mgr.snapshot()["sessions"])

    async def sessions_create(request):
        b = await request.json()
        name = (b.get("name") or "").strip()
        if not name or "/" in name:
            raise ValueError("name required")
        cmd = b.get("cmd") or None
        if b.get("provider"):
            if cmd:
                raise ValueError("choose a CLI provider or a custom command, not both")
            cmd = config_mod.session_command(mgr.supervisor_cfg(), b["provider"], b.get("model") or None)
        s = mgr.create(name, cmd, b.get("cwd") or None,
                       int(b.get("cols") or 0) or None, int(b.get("rows") or 0) or None,
                       b.get("mode") or "auto")
        return _json(s.info())

    async def session_get(request):
        return _json(mgr.get(request.match_info["name"]).info())

    async def session_delete(request):
        mgr.remove(request.match_info["name"])
        return _json({"ok": True})

    async def session_input(request):
        b = await request.json()
        s = mgr.get(request.match_info["name"])
        s.write(b.get("data", ""))
        return _json({"ok": True})

    async def session_keys(request):
        b = await request.json()
        s = mgr.get(request.match_info["name"])
        keys = b.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        mgr.send_keys(s.name, keys)
        mgr.emit("keys", s.name, "[human] sent %s" % " ".join(keys))
        return _json({"ok": True})

    async def session_rename(request):
        b = await request.json()
        s = mgr.rename(request.match_info["name"], b.get("new"))
        return _json(s.info())

    async def session_pin(request):
        b = await request.json()
        mgr.set_pinned(request.match_info["name"], b.get("pinned", True))
        return _json({"ok": True})

    async def session_mode(request):
        b = await request.json()
        mgr.set_mode(request.match_info["name"], b.get("mode"))
        return _json({"ok": True})

    async def session_resize(request):
        b = await request.json()
        s = mgr.get(request.match_info["name"])
        s.resize(int(b["cols"]), int(b["rows"]))
        return _json({"ok": True})

    async def session_screen(request):
        s = mgr.get(request.match_info["name"])
        return _json({"text": s.screen_text(), "error": getattr(s, "error", None)})

    async def prompts_list(request):
        st = request.query.get("status")
        ps = [p.info() for p in mgr.prompts.values() if not st or p.status == st]
        return _json(ps[-200:])

    async def prompt_approve(request):
        p = mgr.approve(int(request.match_info["id"]), by="human", reason="approved from UI/CLI")
        return _json(p.info())

    async def prompt_deny(request):
        p = mgr.deny(int(request.match_info["id"]), by="human", reason="denied from UI/CLI")
        return _json(p.info())

    async def events(request):
        since = int(request.query.get("since", 0))
        return _json([e for e in mgr.events if e["id"] > since][-300:])

    async def supervisor_start(request):
        b = await request.json() if request.can_read_body else {}
        overrides = {key: b[key] for key in config_mod.DEFAULTS["supervisor"] if key in b}
        s = mgr.start_supervisor(restart=bool(b.get("restart", True)), **overrides)
        return _json(s.info())

    async def supervisor_config_get(request):
        sc = mgr.supervisor_cfg()
        return _json({"config": sc, "command": config_mod.supervisor_command(sc),
                      "presets": config_mod.SUPERVISOR_PRESETS, "fields": config_mod.SUPERVISOR_FIELDS,
                      "providers": config_mod.PROVIDERS, "field_providers": config_mod.FIELD_PROVIDERS,
                      "catalog_date": config_mod.MODEL_CATALOG_DATE,
                      "running": bool(mgr.sessions.get(sc["name"]) and mgr.sessions[sc["name"]].alive())})

    async def supervisor_config_set(request):
        b = await request.json()
        sc = mgr.save_supervisor_cfg(b.get("config") or b)
        out = {"config": sc, "command": config_mod.supervisor_command(sc)}
        if b.get("restart"):
            out["session"] = mgr.start_supervisor(restart=True).info()
        return _json(out)

    async def wt_windows(request):
        return _json(mgr.list_wt_windows())

    async def wt_attach(request):
        b = await request.json()
        s = mgr.attach(b["name"].strip(), int(b["hwnd"]), b.get("tab") or None, b.get("mode") or "auto")
        return _json(s.info())

    async def wt_attach_all(request):
        b = await request.json() if request.can_read_body else {}
        return _json(mgr.attach_all(b.get("mode") or "auto"))

    # -- websockets -------------------------------------------------------
    async def ws_term(request):
        name = request.match_info["name"]
        s = mgr.get(name)
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        loop = asyncio.get_event_loop()
        q = asyncio.Queue()
        # pty: raw byte stream replay for xterm.js; wt: full screen text, resent on every change
        await ws.send_str(s.replay() if s.kind == "pty" else s.screen_text())
        mgr.subscribe_term(name, loop, q)

        async def pump():
            while True:
                data = await q.get()
                if data is None:
                    await ws.send_str("\r\n\x1b[31m[process exited]\x1b[0m\r\n")
                    continue
                await ws.send_str(data)

        task = asyncio.ensure_future(pump())
        try:
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except Exception:
                    continue
                t = m.get("t")
                if t == "input":
                    s.write(m.get("d", ""))
                elif t == "resize":
                    s.resize(int(m["cols"]), int(m["rows"]))
        finally:
            task.cancel()
            mgr.unsubscribe_term(name, q)
        return ws

    async def ws_events(request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        loop = asyncio.get_event_loop()
        q = asyncio.Queue()
        mgr.subscribe_events(loop, q)
        await ws.send_str(json.dumps({"type": "state", "data": mgr.snapshot()}))

        async def pump():
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=2.0)
                    await ws.send_str(json.dumps({"type": "event", "data": ev}))
                except asyncio.TimeoutError:
                    pass
                await ws.send_str(json.dumps({"type": "state", "data": mgr.snapshot()}))

        task = asyncio.ensure_future(pump())
        try:
            async for _ in ws:
                pass
        finally:
            task.cancel()
            mgr.unsubscribe_events(q)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/sessions", sessions_list)
    app.router.add_post("/api/sessions", sessions_create)
    app.router.add_get("/api/sessions/{name}", session_get)
    app.router.add_delete("/api/sessions/{name}", session_delete)
    app.router.add_post("/api/sessions/{name}/input", session_input)
    app.router.add_post("/api/sessions/{name}/keys", session_keys)
    app.router.add_post("/api/sessions/{name}/mode", session_mode)
    app.router.add_post("/api/sessions/{name}/rename", session_rename)
    app.router.add_post("/api/sessions/{name}/pin", session_pin)
    app.router.add_post("/api/sessions/{name}/resize", session_resize)
    app.router.add_get("/api/sessions/{name}/screen", session_screen)
    app.router.add_get("/api/prompts", prompts_list)
    app.router.add_post("/api/prompts/{id}/approve", prompt_approve)
    app.router.add_post("/api/prompts/{id}/deny", prompt_deny)
    app.router.add_get("/api/events", events)
    app.router.add_post("/api/supervisor", supervisor_start)
    app.router.add_get("/api/config/supervisor", supervisor_config_get)
    app.router.add_post("/api/config/supervisor", supervisor_config_set)
    app.router.add_get("/api/wt/windows", wt_windows)
    app.router.add_post("/api/wt/attach", wt_attach)
    app.router.add_post("/api/wt/attach_all", wt_attach_all)
    app.router.add_get("/ws/term/{name}", ws_term)
    app.router.add_get("/ws/events", ws_events)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app


def serve(cfg, hosts=None, port=None, approver=True):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(os.path.join(config_mod.ROOT, cfg.get("log_file", "termpilot.log")),
                                      encoding="utf-8")],
    )
    hosts = hosts or config_mod.resolve_hosts(cfg)
    port = port or int(cfg["port"])
    if sys.platform == "win32":
        # The default Proactor loop sometimes resets connections on close
        # (OSError 10022 in _call_connection_lost), which the CLI sees as
        # "connection reset". The selector loop does not have that problem.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    mgr = Manager(cfg)
    mgr.restore_state()
    mgr.start()
    if approver and mgr.supervisor_cfg().get("autostart"):
        try:
            mgr.start_supervisor()
        except Exception as e:
            log.warning("approver autostart failed: %s", e)
    app = build_app(cfg, mgr)
    print("termpilot listening on:", flush=True)
    for h in hosts:
        print("  http://%s:%d/?token=%s" % (h, port, cfg["token"]), flush=True)
    web.run_app(app, host=hosts, port=port, print=None)
