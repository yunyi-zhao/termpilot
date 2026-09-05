"""Attach to an already-open Windows Terminal window via UI Automation.

Reading needs COM/UIA and must happen on the monitor thread (call refresh()).
Sending keys uses PostMessage and works from any thread without stealing focus.
Only the *active* tab of a window can be read; run one agent per window, or
give a tab name and we will select it before reading.
"""
import threading
import time

import win32api
import win32con
import win32gui

_tls = threading.local()

VK = {
    "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}
CTRL_CHARS = {"ctrl-c": 0x03, "ctrl-d": 0x04, "ctrl-z": 0x1A, "ctrl-l": 0x0C}


def _uia():
    """Per-thread UIAutomation instance."""
    if getattr(_tls, "uia", None) is None:
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        _frozen_gen_dir(comtypes)
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        _tls.U = U
        _tls.uia = comtypes.CoCreateInstance(
            U.CUIAutomation._reg_clsid_, interface=U.IUIAutomation,
            clsctx=comtypes.CLSCTX_INPROC_SERVER)
    return _tls.uia, _tls.U


def _frozen_gen_dir(comtypes):
    """In a PyInstaller bundle comtypes cannot write its generated wrappers into
    the (read-only, temporary) bundle; point comtypes.gen at a dir next to the exe."""
    import sys
    if not getattr(sys, "frozen", False):
        return
    import os
    import comtypes.client
    import comtypes.gen
    from . import config as config_mod
    gen = os.path.join(config_mod.ROOT, "comtypes_gen")
    os.makedirs(gen, exist_ok=True)
    if gen not in comtypes.gen.__path__:
        comtypes.gen.__path__.append(gen)
    comtypes.client.gen_dir = gen


def list_windows(with_tabs=True):
    """All visible Windows Terminal windows, each with its tabs (active one flagged)."""
    out = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "CASCADIA_HOSTING_WINDOW_CLASS":
            out.append({"hwnd": hwnd, "title": win32gui.GetWindowText(hwnd), "tabs": []})
    win32gui.EnumWindows(cb, None)
    if with_tabs:
        try:
            uia, U = _uia()
            for w in out:
                try:
                    root = uia.ElementFromHandle(w["hwnd"])
                    cond = uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_TabItemControlTypeId)
                    arr = root.FindAll(U.TreeScope_Descendants, cond)
                    for i in range(arr.Length):
                        e = arr.GetElement(i)
                        active = False
                        try:
                            pat = e.GetCurrentPattern(U.UIA_SelectionItemPatternId)
                            if pat:
                                active = bool(pat.QueryInterface(U.IUIAutomationSelectionItemPattern).CurrentIsSelected)
                        except Exception:
                            pass
                        w["tabs"].append({"name": e.CurrentName, "active": active})
                except Exception as ex:
                    w["error"] = str(ex)
        except Exception:
            pass
    return out


def foreground_hwnd():
    try:
        return win32gui.GetForegroundWindow()
    except Exception:
        return 0


def _input_site(parent):
    res = [None]

    def cb(hwnd, _):
        if "InputSite" in win32gui.GetClassName(hwnd):
            res[0] = hwnd
    try:
        win32gui.EnumChildWindows(parent, cb, None)
    except Exception:
        pass
    return res[0]


class WtSession:
    kind = "wt"

    def __init__(self, name, hwnd, tab=None):
        self.name = name
        self.hwnd = int(hwnd)
        self.tab = tab
        self.mode = "auto"
        self.created_at = time.time()
        self.last_output_at = self.created_at
        self.current_prompt = None
        self.cmd = "attached: " + win32gui.GetWindowText(self.hwnd)
        self.cwd = None
        self._text = ""
        self._input_hwnd = _input_site(self.hwnd)
        self.error = None
        self.visible = True      # False while our tab is not the active one and we chose not to switch
        self.switch_when_background = True

    # -- reading (monitor thread only) ----------------------------------
    def refresh(self):
        try:
            uia, U = _uia()
            root = uia.ElementFromHandle(self.hwnd)
            if self.tab:
                tab = None
                cond = uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_TabItemControlTypeId)
                arr = root.FindAll(U.TreeScope_Descendants, cond)
                for i in range(arr.Length):
                    e = arr.GetElement(i)
                    if e.CurrentName == self.tab or e.CurrentName.startswith(self.tab):
                        tab = e
                        break
                if tab is None:
                    self.error = "tab %r not found" % self.tab
                    self.visible = False
                    return
                pat = tab.GetCurrentPattern(U.UIA_SelectionItemPatternId)
                si = pat.QueryInterface(U.IUIAutomationSelectionItemPattern) if pat else None
                if si and not si.CurrentIsSelected:
                    # Only the active tab's text is exposed. Switch tabs only when the
                    # user is not working in this window (it is not the foreground window).
                    if self.switch_when_background and foreground_hwnd() != self.hwnd:
                        si.Select()
                        time.sleep(0.25)
                    else:
                        self.visible = False
                        self.error = None
                        return
                self.visible = True
            cond = uia.CreatePropertyCondition(U.UIA_ClassNamePropertyId, "TermControl")
            term = root.FindFirst(U.TreeScope_Descendants, cond)
            if not term:
                self.error = "TermControl not found"
                return
            pat = term.GetCurrentPattern(U.UIA_TextPatternId)
            tp = pat.QueryInterface(U.IUIAutomationTextPattern)
            text = tp.DocumentRange.GetText(-1) or ""
            text = "\n".join(l.rstrip() for l in text.splitlines())
            text = text[-12000:]  # UIA returns the whole scrollback; keep the tail only
            changed = text != self._text
            if changed:
                self._text = text
                self.last_output_at = time.time()
            self.error = None
            return changed
        except Exception as e:
            self.error = str(e)
            return False

    def screen_text(self):
        return self._text

    # -- writing ----------------------------------------------------------
    def _post_vk(self, vk):
        h = self._input_hwnd or self.hwnd
        win32api.PostMessage(h, win32con.WM_KEYDOWN, vk, 1)
        time.sleep(0.03)
        win32api.PostMessage(h, win32con.WM_KEYUP, vk, 1 | (1 << 30) | (1 << 31))

    def _post_char(self, ch):
        """Type one character. Windows Terminal ignores posted WM_CHAR, so we post
        WM_KEYDOWN/UP with the virtual key from VkKeyScan. Keys that need Shift
        (upper case, symbols like ! or |) may arrive unshifted: the terminal reads
        the real keyboard state, which PostMessage cannot change."""
        if ord(ch) < 0x20:
            self._post_vk(ord(ch) + 0x40)  # control chars: ctrl-c -> C with ctrl not held; best effort
            return
        r = win32api.VkKeyScan(ch)
        if r == -1:
            return
        vk, shift = r & 0xFF, bool((r >> 8) & 1)
        h = self._input_hwnd or self.hwnd
        if shift:
            win32api.PostMessage(h, win32con.WM_KEYDOWN, win32con.VK_SHIFT, 1)
        self._post_vk(vk)
        if shift:
            win32api.PostMessage(h, win32con.WM_KEYUP, win32con.VK_SHIFT, 1 | (1 << 30) | (1 << 31))

    def send_keys(self, keys):
        for k in keys:
            kl = k.lower()
            if kl in VK:
                self._post_vk(VK[kl])
            elif kl in CTRL_CHARS:
                # ctrl-x: post VK_CONTROL down + letter + up (best effort, see _post_char)
                h = self._input_hwnd or self.hwnd
                win32api.PostMessage(h, win32con.WM_KEYDOWN, win32con.VK_CONTROL, 1)
                self._post_vk(CTRL_CHARS[kl] + 0x40)
                win32api.PostMessage(h, win32con.WM_KEYUP, win32con.VK_CONTROL, 1 | (1 << 30) | (1 << 31))
            elif len(k) == 1:
                self._post_char(k)
            else:
                raise ValueError("unknown key: %s" % k)
            time.sleep(0.05)

    def write(self, data):
        for ch in data:
            if ch == "\r" or ch == "\n":
                self._post_vk(VK["enter"])
            else:
                self._post_char(ch)
            time.sleep(0.01)

    def resize(self, cols, rows):
        pass

    def alive(self):
        return bool(win32gui.IsWindow(self.hwnd))

    def kill(self):
        pass  # never close a window we did not create

    def info(self):
        return {
            "name": self.name, "kind": self.kind, "cmd": self.cmd, "cwd": None,
            "hwnd": self.hwnd, "tab": self.tab, "alive": self.alive(), "mode": self.mode,
            "error": self.error, "visible": self.visible,
            "created_at": self.created_at, "last_output_at": self.last_output_at,
            "prompt": self.current_prompt.info() if self.current_prompt else None,
        }
