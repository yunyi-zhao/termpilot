"""Desktop control panel (tkinter). Opened when termpilot is launched without arguments.

Lets you set the WebUI address/port, the approver's model/effort/etc., start/stop the
background server, copy or open the WebUI URL, and watch the server log.
"""
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import messagebox, ttk

from . import config as config_mod
from .config import SUPERVISOR_PRESETS, supervisor_command


def _hide_console():
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetConsoleWindow()
        if h:
            ctypes.windll.user32.ShowWindow(h, 0)  # SW_HIDE
    except Exception:
        pass


def _self_argv():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.join(config_mod.ROOT, "termpilot.py")]


def _listening(port):
    try:
        import psutil
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port:
                return c.pid
    except Exception:
        pass
    return None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("termpilot 控制面板")
        self.geometry("720x640")
        self.minsize(640, 560)
        self.cfg = config_mod.load()
        self.proc = None
        self._build()
        self._load()
        self.after(500, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- layout ----------------
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # --- WebUI address
        f1 = ttk.LabelFrame(root, text="WebUI 地址", padding=8)
        f1.pack(fill="x", **pad)
        self.host_mode = tk.StringVar(value="auto")
        ttk.Radiobutton(f1, text="自动：本机 127.0.0.1 + Tailscale IP（推荐）", variable=self.host_mode, value="auto", command=self._refresh_urls).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(f1, text="所有网卡 0.0.0.0（局域网也能访问，注意安全）", variable=self.host_mode, value="all", command=self._refresh_urls).grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(f1, text="自定义 IP（逗号分隔）", variable=self.host_mode, value="custom", command=self._refresh_urls).grid(row=2, column=0, sticky="w")
        self.custom_hosts = tk.StringVar()
        ttk.Entry(f1, textvariable=self.custom_hosts, width=36).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(f1, text="端口").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.port = tk.StringVar()
        e = ttk.Entry(f1, textvariable=self.port, width=8); e.grid(row=3, column=1, sticky="w", padx=6, pady=(6, 0))
        e.bind("<KeyRelease>", lambda _e: self._refresh_urls())
        self.custom_hosts.trace_add("write", lambda *_: self._refresh_urls())
        ttk.Label(f1, text="访问地址（含 token）").grid(row=4, column=0, sticky="nw", pady=(6, 0))
        self.urls = tk.Listbox(f1, height=3, width=70, activestyle="none")
        self.urls.grid(row=4, column=1, columnspan=2, sticky="we", padx=6, pady=(6, 0))
        b = ttk.Frame(f1); b.grid(row=5, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Button(b, text="复制选中地址", command=self._copy_url).pack(side="left")
        ttk.Button(b, text="在浏览器打开", command=self._open_url).pack(side="left", padx=6)
        ttk.Button(b, text="重新生成 token", command=self._new_token).pack(side="left")

        # --- approver
        f2 = ttk.LabelFrame(root, text="审批者（一个由 termpilot 托管的 Claude Code 会话，定期跑 /termpilot 审查其它终端）", padding=8)
        f2.pack(fill="x", **pad)
        self.sv = {}
        rows = [("model", "模型 --model", [m[0] for m in SUPERVISOR_PRESETS["models"]]),
                ("effort", "推理强度 --effort", SUPERVISOR_PRESETS["efforts"]),
                ("every", "巡检间隔 /loop", SUPERVISOR_PRESETS["intervals"]),
                ("permission_mode", "权限模式", SUPERVISOR_PRESETS["permission_modes"]),
                ("extra_args", "附加参数", None),
                ("name", "会话名", None)]
        for i, (key, label, choices) in enumerate(rows):
            ttk.Label(f2, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky="w", pady=2)
            var = tk.StringVar(); self.sv[key] = var
            if choices:
                w = ttk.Combobox(f2, textvariable=var, values=choices, width=22)  # editable: any new model/effort can be typed
            else:
                w = ttk.Entry(f2, textvariable=var, width=25)
            w.grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(6, 18), pady=2)
            var.trace_add("write", lambda *_: self._refresh_cmd())
        self.autostart = tk.BooleanVar(value=True)
        ttk.Checkbutton(f2, text="启动服务时自动拉起审批者", variable=self.autostart).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.cmd_label = ttk.Label(f2, text="", foreground="#555", wraplength=640, justify="left")
        self.cmd_label.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # --- actions
        f3 = ttk.Frame(root); f3.pack(fill="x", **pad)
        self.btn_start = ttk.Button(f3, text="保存并启动服务", command=self._start); self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(f3, text="停止服务", command=self._stop); self.btn_stop.pack(side="left", padx=6)
        ttk.Button(f3, text="仅保存设置", command=self._save).pack(side="left")
        ttk.Button(f3, text="重启审批者", command=self._restart_approver).pack(side="left", padx=6)
        ttk.Button(f3, text="打开总控终端", command=self._open_console).pack(side="left")
        ttk.Button(f3, text="防火墙放行(管理员)", command=self._firewall).pack(side="left", padx=6)
        self.status = ttk.Label(root, text="", foreground="#0a6")
        self.status.pack(fill="x", padx=8)

        # --- log
        f4 = ttk.LabelFrame(root, text="服务日志", padding=4)
        f4.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(f4, height=10, font=("Consolas", 9), wrap="none", state="disabled", bg="#111", fg="#ddd")
        self.log.pack(fill="both", expand=True)

    # ---------------- config <-> widgets ----------------
    def _load(self):
        c = self.cfg
        hosts = c.get("hosts", "auto")
        if hosts == "auto":
            self.host_mode.set("auto")
        elif hosts in ("0.0.0.0", ["0.0.0.0"]):
            self.host_mode.set("all")
        else:
            self.host_mode.set("custom"); self.custom_hosts.set(", ".join(hosts if isinstance(hosts, list) else [hosts]))
        self.port.set(str(c.get("port", 8787)))
        sc = dict(config_mod.DEFAULTS["supervisor"]); sc.update(c.get("supervisor") or {})
        for k, var in self.sv.items():
            var.set(str(sc.get(k, "") or ""))
        self.autostart.set(bool(sc.get("autostart", True)))
        self._refresh_urls(); self._refresh_cmd()

    def _collect(self):
        c = config_mod.load()
        m = self.host_mode.get()
        if m == "auto":
            c["hosts"] = "auto"
        elif m == "all":
            c["hosts"] = "0.0.0.0"
        else:
            c["hosts"] = [h.strip() for h in self.custom_hosts.get().split(",") if h.strip()] or "auto"
        try:
            c["port"] = int(self.port.get())
        except ValueError:
            c["port"] = 8787
        sc = dict(config_mod.DEFAULTS["supervisor"]); sc.update(c.get("supervisor") or {})
        for k, var in self.sv.items():
            sc[k] = var.get().strip()
        sc["autostart"] = bool(self.autostart.get())
        c["supervisor"] = sc
        return c

    def _save(self, quiet=False):
        self.cfg = self._collect()
        config_mod.save(self.cfg)
        if not quiet:
            self._set_status("已保存 config.json")
        self._refresh_urls()

    def _hosts_preview(self):
        c = self._collect()
        hs = config_mod.resolve_hosts(c)
        if hs == ["0.0.0.0"]:
            hs = ["127.0.0.1"] + [ip for ip in _local_ips() if ip != "127.0.0.1"]
        return hs, c["port"], c["token"]

    def _refresh_urls(self):
        try:
            hs, port, tok = self._hosts_preview()
        except Exception:
            return
        self.urls.delete(0, "end")
        for h in hs:
            self.urls.insert("end", "http://%s:%s/?token=%s" % (h, port, tok))
        if self.urls.size():
            self.urls.selection_set(min(1, self.urls.size() - 1))

    def _refresh_cmd(self):
        sc = {k: v.get().strip() for k, v in self.sv.items()}
        self.cmd_label.config(text="启动命令：%s    →  /loop %s /termpilot" % (supervisor_command(sc), sc.get("every") or "1m"))

    def _sel_url(self):
        sel = self.urls.curselection()
        return self.urls.get(sel[0]) if sel else (self.urls.get(0) if self.urls.size() else "")

    def _copy_url(self):
        u = self._sel_url()
        if u:
            self.clipboard_clear(); self.clipboard_append(u); self._set_status("已复制：" + u)

    def _open_url(self):
        u = self._sel_url()
        if u:
            webbrowser.open(u)

    def _new_token(self):
        import secrets
        if not messagebox.askyesno("重新生成 token", "旧地址会失效，所有已登录的浏览器需要重新输入。继续？"):
            return
        c = config_mod.load(); c["token"] = secrets.token_urlsafe(24); config_mod.save(c)
        self.cfg = c; self._refresh_urls(); self._set_status("token 已更新，重启服务后生效")

    # ---------------- server control ----------------
    def _running_pid(self):
        try:
            return _listening(int(self.port.get()))
        except ValueError:
            return None

    def _start(self):
        self._save(quiet=True)
        pid = self._running_pid()
        if pid:
            self._stop(quiet=True); time.sleep(0.8)
        args = _self_argv() + ["serve"]
        if not self.autostart.get():
            args.append("--no-approver")
        out = open(os.path.join(config_mod.ROOT, "serve.out"), "ab")
        err = open(os.path.join(config_mod.ROOT, "serve.err"), "ab")
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        self.proc = subprocess.Popen(args, cwd=config_mod.ROOT, stdout=out, stderr=err, stdin=subprocess.DEVNULL, creationflags=flags)
        self._set_status("服务启动中… pid %d" % self.proc.pid)

    def _stop(self, quiet=False):
        pid = self._running_pid()
        if not pid:
            if not quiet:
                self._set_status("服务没有在运行")
            return
        try:
            import psutil
            p = psutil.Process(pid)
            for ch in p.children(recursive=True):
                try: ch.kill()
                except Exception: pass
            p.kill()
            if not quiet:
                self._set_status("已停止服务 (pid %d)" % pid)
        except Exception as e:
            self._set_status("停止失败：%s" % e)

    def _api(self, method, path, body=None):
        c = config_mod.load()
        hs = config_mod.resolve_hosts(c)
        host = next((h for h in hs if not h.startswith("127.") and h != "0.0.0.0"), "127.0.0.1")
        req = urllib.request.Request("http://%s:%d%s" % (host, int(c["port"]), path), method=method,
                                     headers={"X-Token": c["token"], "Content-Type": "application/json"})
        data = json.dumps(body).encode() if body is not None else None
        with urllib.request.urlopen(req, data, timeout=15) as r:
            return json.loads(r.read().decode())

    def _restart_approver(self):
        self._save(quiet=True)
        if not self._running_pid():
            self._set_status("服务没有在运行，先启动服务"); return
        def run():
            try:
                r = self._api("POST", "/api/config/supervisor", {"config": self.cfg["supervisor"], "restart": True})
                self.after(0, lambda: self._set_status("审批者已重启：" + r["command"]))
            except Exception as e:
                self.after(0, lambda: self._set_status("重启审批者失败：%s" % e))
        threading.Thread(target=run, daemon=True).start()

    def _open_console(self):
        exe = _self_argv()
        cmd = " ".join('"%s"' % a for a in exe) + " console"
        subprocess.Popen(["cmd", "/c", "start", "termpilot console", "cmd", "/k", cmd], cwd=config_mod.ROOT)

    def _firewall(self):
        port = int(self.port.get() or 8787)
        rule = 'netsh advfirewall firewall add rule name="termpilot %d" dir=in action=allow protocol=TCP localport=%d remoteip=100.64.0.0/10' % (port, port)
        try:
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", "/c " + rule, None, 0)
            self._set_status("已请求管理员权限添加防火墙规则（Tailscale 网段 → 端口 %d）" % port)
        except Exception as e:
            self._set_status("防火墙规则添加失败：%s" % e)

    # ---------------- periodic ----------------
    def _tick(self):
        pid = self._running_pid()
        if pid:
            self.btn_start.config(text="保存并重启服务")
            approver = ""
            try:
                st = self._api("GET", "/api/state")
                n = len(st["sessions"]); pend = len(st["pending"])
                ap = [s for s in st["sessions"] if s.get("role") == "approver"]
                approver = "，审批者 %s" % ("运行中" if ap and ap[0]["alive"] else "未运行")
                self.status.config(text="服务运行中 (pid %d)：%d 个会话，%d 个待审批%s" % (pid, n, pend, approver), foreground="#0a6")
            except Exception:
                self.status.config(text="服务运行中 (pid %d)，API 暂未响应…" % pid, foreground="#c80")
        else:
            self.btn_start.config(text="保存并启动服务")
            if not (self.status.cget("text") or "").startswith(("已", "token", "服务没有")):
                self.status.config(text="服务未运行", foreground="#c33")
        self._refresh_log()
        self.after(2000, self._tick)

    def _refresh_log(self):
        p = os.path.join(config_mod.ROOT, "serve.err")
        try:
            with open(p, "rb") as f:
                f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 6000))
                tail = f.read().decode("utf-8", "replace")
            lines = [l for l in tail.splitlines() if " INFO 1" not in l and "GET /api/" not in l and "GET /ws/" not in l][-40:]
            txt = "\n".join(lines)
        except Exception:
            txt = "(no log yet)"
        if getattr(self, "_last_log", None) != txt:
            self._last_log = txt
            self.log.config(state="normal"); self.log.delete("1.0", "end"); self.log.insert("end", txt); self.log.see("end"); self.log.config(state="disabled")

    def _set_status(self, msg):
        self.status.config(text=msg, foreground="#06c")

    def _on_close(self):
        if self._running_pid():
            ans = messagebox.askyesnocancel("退出", "服务仍在后台运行。\n\n是 = 停止服务并退出\n否 = 保持服务运行，只关闭面板\n取消 = 不退出")
            if ans is None:
                return
            if ans:
                self._stop(quiet=True)
        self.destroy()


def _local_ips():
    import socket
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def main(selftest=False):
    _hide_console()
    app = App()
    if selftest:
        app.after(1500, app.destroy)
    app.mainloop()
