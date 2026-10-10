/* termpilot WebUI */
(() => {
  const $ = (s) => document.querySelector(s);
  const qsToken = new URLSearchParams(location.search).get("token");
  if (qsToken) { try { localStorage.setItem("tp_token", qsToken); } catch (e) {} history.replaceState(null, "", location.pathname); }
  let token = null; try { token = localStorage.getItem("tp_token"); } catch (e) {}

  const LIVE = ["pending", "reviewing", "judging", "held"];
  const state = { sessions: [], pending: [], cur: null, terms: {}, wts: {}, evws: null, selHwnd: null, selTab: "" };
  const termHost = $("#termHost"), wtScreen = $("#wtScreen"), overview = $("#overview"), app = $("#app");
  const screens = {};  // name -> last screen text (overview cards)
  const isMobile = () => innerWidth <= 900;

  // ---------- api ----------
  async function api(method, path, body) {
    const r = await fetch(path, { method, headers: { "X-Token": token || "", "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined });
    if (r.status === 401) { showToken(); throw new Error("unauthorized"); }
    const j = await r.json();
    if (!r.ok) { alert(j.error || r.statusText); throw new Error(j.error); }
    return j;
  }
  const wsUrl = (path) => (location.protocol === "https:" ? "wss://" : "ws://") + location.host + path + "?token=" + encodeURIComponent(token || "");

  // ---------- token ----------
  function showToken() { $("#tokenModal").classList.remove("hidden"); }
  $("#tokenSave").onclick = () => { token = $("#tokenInput").value.trim(); try { localStorage.setItem("tp_token", token); } catch (e) {}
    $("#tokenModal").classList.add("hidden"); connectEvents(); };
  if (!token) showToken();

  // ---------- layout toggles ----------
  try { if (localStorage.getItem("tp_nav") === "0") app.classList.add("nav-collapsed"); if (localStorage.getItem("tp_panel") === "0") app.classList.add("panel-collapsed"); } catch (e) {}
  $("#menuBtn").onclick = () => {
    if (isMobile()) { $("#sidebar").classList.toggle("open"); $("#panel").classList.remove("open"); return; }
    app.classList.toggle("nav-collapsed"); try { localStorage.setItem("tp_nav", app.classList.contains("nav-collapsed") ? "0" : "1"); } catch (e) {}
    refit();
  };
  $("#promptsBtn").onclick = () => {
    if (isMobile()) { $("#panel").classList.toggle("open"); $("#sidebar").classList.remove("open"); return; }
    app.classList.toggle("panel-collapsed"); try { localStorage.setItem("tp_panel", app.classList.contains("panel-collapsed") ? "0" : "1"); } catch (e) {}
    refit();
  };
  function closeDrawers() { if (isMobile()) { $("#sidebar").classList.remove("open"); $("#panel").classList.remove("open"); } }
  function refit() { const t = state.terms[state.cur]; if (t) setTimeout(() => { try { t.fit.fit(); } catch (e) {} }, 160); }
  window.addEventListener("resize", refit);

  // ---------- events ws ----------
  function connectEvents() {
    if (state.evws) { try { state.evws.onclose = null; state.evws.close(); } catch (e) {} }
    const ws = new WebSocket(wsUrl("/ws/events"));
    state.evws = ws;
    ws.onopen = () => $("#connDot").classList.remove("off");
    ws.onclose = () => { $("#connDot").classList.add("off"); setTimeout(() => { if (state.evws === ws) connectEvents(); }, 2000); };
    ws.onmessage = (m) => { const msg = JSON.parse(m.data); if (msg.type === "state") applyState(msg.data); else if (msg.type === "event") logEvent(msg.data); };
  }
  function applyState(st) {
    state.sessions = st.sessions; state.pending = st.pending;
    $("#reviewerFlag").textContent = st.reviewer ? "规则 + LLM 二审" : "规则";
    renderSessions(); renderPrompts();
    if (state.cur && !state.sessions.find(s => s.name === state.cur)) selectSession(null);
    if (!state.cur) renderOverview();
    const s = state.sessions.find(x => x.name === state.cur); if (s) $("#modeSel").value = s.mode;
  }

  // ---------- sessions list ----------
  function statusClass(s, now) {
    const p = s.prompt && LIVE.includes(s.prompt.status);
    return !s.alive ? "dead" : p ? "prompt" : (now - s.last_output_at < 3 ? "busy" : "");
  }
  function renderSessions() {
    const ul = $("#sessionList"); ul.innerHTML = "";
    const now = Date.now() / 1000;
    for (const s of state.sessions) {
      const li = document.createElement("li");
      if (s.name === state.cur) li.classList.add("active");
      li.innerHTML = `<span class="st ${statusClass(s, now)}"></span><span class="nm" title="${esc(s.cmd)}">${s.pinned ? "★ " : ""}${esc(s.name)}</span><span class="kd">${s.kind}·${s.mode}</span>`;
      li.onclick = () => { selectSession(s.name); closeDrawers(); };
      ul.appendChild(li);
    }
    const n = state.pending.length; const b = $("#pendingBadge"); b.textContent = n; b.classList.toggle("hidden", n === 0);
    $("#sessCount").textContent = state.sessions.length ? `(${state.sessions.length})` : "";
    document.title = (n ? `(${n}) ` : "") + `termpilot · ${state.sessions.length} 终端`;
  }

  function selectSession(name) {
    state.cur = name; $("#curName").textContent = name || "";
    for (const k in state.terms) state.terms[k].el.style.display = "none";
    wtScreen.classList.add("hidden"); $("#wtHint").classList.add("hidden");
    const s = state.sessions.find(x => x.name === name);
    renderSessions();
    if (!s) { state.cur = null; $("#curName").textContent = ""; showOverview(); return; }
    hideOverview();
    $("#modeSel").value = s.mode;
    if (s.kind === "pty") openTerm(s); else openWt(s);
  }

  // ---------- pty: xterm over websocket ----------
  function openTerm(s) {
    let t = state.terms[s.name];
    if (!t) {
      const el = document.createElement("div"); termHost.appendChild(el);
      const term = new Terminal({ cursorBlink: true, fontSize: 13, lineHeight: 1.15, fontFamily: "'Cascadia Mono', 'JetBrains Mono', Consolas, Menlo, monospace",
        theme: { background: "#000000", foreground: "#d8dbe2", cursor: "#5b8def", selectionBackground: "rgba(91,141,239,.35)" }, scrollback: 5000, allowProposedApi: true });
      const fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open(el);
      t = state.terms[s.name] = { el, term, fit, ws: null };
      term.onData(d => { if (t.ws && t.ws.readyState === 1) t.ws.send(JSON.stringify({ t: "input", d })); });
      term.onResize(({ cols, rows }) => { if (t.ws && t.ws.readyState === 1) t.ws.send(JSON.stringify({ t: "resize", cols, rows })); });
      connectTerm(s.name, t);
    }
    t.el.style.display = "block";
    setTimeout(() => { try { t.fit.fit(); } catch (e) {} t.term.focus(); }, 30);
  }
  function connectTerm(name, t) {
    const ws = new WebSocket(wsUrl("/ws/term/" + encodeURIComponent(name)));
    t.ws = ws;
    ws.onmessage = (m) => t.term.write(m.data);
    ws.onopen = () => { t.term.clear(); try { t.fit.fit(); } catch (e) {} };
    ws.onclose = () => { setTimeout(() => { if (state.terms[name] === t && state.sessions.find(s => s.name === name)) connectTerm(name, t); }, 2000); };
  }

  // ---------- wt: full-screen text pushed over websocket ----------
  function openWt(s) {
    wtScreen.classList.remove("hidden"); $("#wtHint").classList.remove("hidden");
    let w = state.wts[s.name];
    if (!w) { w = state.wts[s.name] = { text: "", ws: null }; connectWt(s.name, w); }
    paintWt(s.name, true);
  }
  function connectWt(name, w) {
    const ws = new WebSocket(wsUrl("/ws/term/" + encodeURIComponent(name)));
    w.ws = ws;
    ws.onmessage = (m) => { w.text = m.data; screens[name] = m.data; if (state.cur === name) paintWt(name); };
    ws.onclose = () => { setTimeout(() => { if (state.wts[name] === w && state.sessions.find(s => s.name === name)) connectWt(name, w); }, 2000); };
  }
  function paintWt(name, force) {
    const w = state.wts[name]; if (!w) return;
    const atBottom = force || (wtScreen.scrollHeight - wtScreen.scrollTop - wtScreen.clientHeight < 40);
    if (wtScreen.textContent !== w.text) wtScreen.textContent = w.text;
    if (atBottom) wtScreen.scrollTop = wtScreen.scrollHeight;
  }

  // ---------- overview ----------
  let ovTimer = null;
  function renderOverview() {
    const grid = $("#ovGrid"); grid.innerHTML = "";
    $("#ovCount").textContent = state.sessions.length ? `(${state.sessions.length})` : "";
    $("#ovEmpty").classList.toggle("hidden", state.sessions.length > 0);
    const now = Date.now() / 1000;
    for (const s of state.sessions) {
      const p = s.prompt && LIVE.includes(s.prompt.status) ? s.prompt : null;
      const card = document.createElement("div");
      card.className = "card" + (p ? " has-prompt" : "") + (!s.alive ? " dead" : "") + (s.pinned ? " pinned" : "");
      const tail = (screens[s.name] || "").replace(/\s+$/, "").split("\n").slice(s.pinned ? -8 : -4).join("\n");
      const hidden = s.kind === "wt" && s.visible === false ? " · tab 未激活" : "";
      const role = s.role === "approver" ? `<span class="role">审批者</span>` : "";
      card.innerHTML = `<div class="top"><span class="st ${statusClass(s, now)}"></span><b>${s.pinned ? "★ " : ""}${esc(s.name)}</b>${role}<span class="kd">${s.kind} · ${s.mode}${s.alive ? "" : " · 已退出"}${hidden}</span></div>
        <div class="cmd">${esc(s.cmd)}${s.cwd ? " — " + esc(s.cwd) : ""}</div>
        <pre>${esc(tail || "…")}</pre>
        ${!p && s.explain ? `<div class="pr">？ 已拒绝一次，等待 agent 解释 · ${esc(s.explain.reason)}</div>` : ""}
        ${p ? `<div class="pr">⚑ ${p.escalated ? "需人工 · " : ""}${esc(p.status)} · ${esc(p.reason || "")}</div>
        <div class="acts"><button class="primary small ok">批准</button><button class="danger small no">拒绝</button></div>` : ""}`;
      card.onclick = (e) => { if (e.target.tagName !== "BUTTON") selectSession(s.name); };
      if (p) { card.querySelector(".ok").onclick = () => api("POST", `/api/prompts/${p.id}/approve`);
               card.querySelector(".no").onclick = () => api("POST", `/api/prompts/${p.id}/deny`); }
      grid.appendChild(card);
    }
  }
  async function refreshOverviewScreens() {
    if (state.cur) return;
    for (const s of state.sessions) {
      if (state.wts[s.name]) { screens[s.name] = state.wts[s.name].text; continue; }
      try { const r = await api("GET", "/api/sessions/" + enc(s.name) + "/screen"); screens[s.name] = r.text; } catch (e) {}
    }
    if (!state.cur) renderOverview();
  }
  function showOverview() { overview.classList.remove("hidden"); renderOverview(); refreshOverviewScreens(); if (!ovTimer) ovTimer = setInterval(refreshOverviewScreens, 2000); }
  function hideOverview() { overview.classList.add("hidden"); if (ovTimer) { clearInterval(ovTimer); ovTimer = null; } }
  $("#homeBtn").onclick = () => { selectSession(null); closeDrawers(); };
  $("#ovNew").onclick = () => { openSidebar(); $("#newForm").classList.remove("hidden"); $("#attachForm").classList.add("hidden"); $("#nName").focus(); };
  $("#ovAttach").onclick = () => { openSidebar(); $("#attachForm").classList.remove("hidden"); $("#newForm").classList.add("hidden"); loadWtList(); };
  function openSidebar() { if (isMobile()) $("#sidebar").classList.add("open"); else { app.classList.remove("nav-collapsed"); refit(); } }

  // ---------- toolbar ----------
  $("#modeSel").onchange = () => state.cur && api("POST", `/api/sessions/${enc(state.cur)}/mode`, { mode: $("#modeSel").value });
  document.querySelectorAll(".keys button").forEach(b => b.onclick = () => state.cur && api("POST", `/api/sessions/${enc(state.cur)}/keys`, { keys: [b.dataset.key] }));
  $("#pinBtn").onclick = async () => { if (!state.cur) return; const s = state.sessions.find(x => x.name === state.cur);
    await api("POST", `/api/sessions/${enc(state.cur)}/pin`, { pinned: !(s && s.pinned) }); };
  $("#renameBtn").onclick = async () => { if (!state.cur) return; const nn = prompt("新名字", state.cur); if (!nn || nn === state.cur) return;
    const old = state.cur; await api("POST", `/api/sessions/${enc(old)}/rename`, { new: nn });
    if (state.terms[old]) { state.terms[nn] = state.terms[old]; delete state.terms[old]; }
    if (state.wts[old]) { state.wts[nn] = state.wts[old]; delete state.wts[old]; }
    if (screens[old]) { screens[nn] = screens[old]; delete screens[old]; }
    state.cur = nn; $("#curName").textContent = nn; };
  $("#killBtn").onclick = async () => {
    if (!state.cur) return; const s = state.sessions.find(x => x.name === state.cur);
    if (!confirm((s && s.kind === "wt" ? "取消挂接 " : "关闭并移除 ") + state.cur + "？")) return;
    const name = state.cur; await api("DELETE", "/api/sessions/" + enc(name));
    const t = state.terms[name]; if (t) { try { t.ws.onclose = null; t.ws.close(); } catch (e) {} t.term.dispose(); t.el.remove(); delete state.terms[name]; }
    const w = state.wts[name]; if (w) { try { w.ws.onclose = null; w.ws.close(); } catch (e) {} delete state.wts[name]; }
    selectSession(null);
  };
  const sendLine = async () => { const v = $("#lineInput").value; if (!state.cur) return;
    await api("POST", `/api/sessions/${enc(state.cur)}/input`, { data: v + "\r" }); $("#lineInput").value = ""; };
  $("#sendBtn").onclick = sendLine;
  $("#lineInput").addEventListener("keydown", e => { if (e.key === "Enter") sendLine(); });

  // ---------- new / attach ----------
  $("#newBtn").onclick = () => { $("#newForm").classList.toggle("hidden"); $("#attachForm").classList.add("hidden"); $("#nName").focus(); };
  $("#nCancel").onclick = () => $("#newForm").classList.add("hidden");
  $("#nProvider").onchange = async () => {
    const provider = $("#nProvider").value;
    $("#nCommandField").classList.toggle("hidden", !!provider);
    $("#nModelField").classList.toggle("hidden", !provider);
    if (!provider) return;
    $("#nModel").value = "";
    $("#nModels").innerHTML = "";
    const r = await api("GET", "/api/config/supervisor");
    if ($("#nProvider").value !== provider) return;
    const info = r.providers[provider];
    $("#nModel").value = r.config[info.model_key] || "";
    $("#nModels").innerHTML = r.presets[info.models_key].map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("");
  };
  $("#nCreate").onclick = async () => {
    const name = $("#nName").value.trim(); if (!name) return;
    const provider = $("#nProvider").value;
    const s = await api("POST", "/api/sessions", { name, provider, model: provider ? $("#nModel").value.trim() : "",
      cmd: provider ? "" : $("#nCmd").value.trim(), cwd: $("#nCwd").value.trim(), mode: $("#nMode").value });
    $("#newForm").classList.add("hidden"); $("#nName").value = "";
    state.sessions.push(s); selectSession(name); closeDrawers();
  };
  $("#attachBtn").onclick = async () => {
    $("#attachForm").classList.toggle("hidden"); $("#newForm").classList.add("hidden");
    if (!$("#attachForm").classList.contains("hidden")) await loadWtList();
  };
  async function loadWtList() {
    const wins = await api("GET", "/api/wt/windows"); const ul = $("#wtList"); ul.innerHTML = "";
    if (!wins.length) ul.innerHTML = "<li class='muted'>没有打开的 Windows Terminal 窗口</li>";
    const pick = (li, hwnd, tab, nameHint) => {
      state.selHwnd = hwnd; state.selTab = tab; ul.querySelectorAll("li").forEach(x => x.classList.remove("sel")); li.classList.add("sel");
      $("#aTab").value = tab || ""; $("#aName").value = slug(nameHint);
    };
    for (const w of wins) {
      const head = document.createElement("li"); head.className = "wt-win";
      head.innerHTML = `<b>窗口 ${w.hwnd}</b> ${esc(w.title)} ${w.attached.length ? `<span class="tag">→ ${esc(w.attached.join(","))}</span>` : ""}`;
      head.onclick = () => pick(head, w.hwnd, "", w.title); ul.appendChild(head);
      for (const t of (w.tabs || [])) {
        const li = document.createElement("li"); li.className = "wt-tab";
        li.innerHTML = `${t.active ? "▶" : "&nbsp;&nbsp;"} ${esc(t.name)} ${t.attached.length ? `<span class="tag">→ ${esc(t.attached.join(","))}</span>` : ""}`;
        li.onclick = () => pick(li, w.hwnd, (w.tabs.length > 1 ? t.name : ""), t.name); ul.appendChild(li);
      }
    }
    const all = document.createElement("li"); all.className = "wt-all";
    all.innerHTML = `<button class="primary small">全部挂接（每个 tab 一个会话）</button>`;
    all.querySelector("button").onclick = async (e) => { e.stopPropagation(); const made = await api("POST", "/api/wt/attach_all", {});
      $("#attachForm").classList.add("hidden"); if (made.length) { made.forEach(s => state.sessions.push(s)); selectSession(null); } else alert("没有新的可挂接"); closeDrawers(); };
    ul.appendChild(all);
  }
  const slug = (t) => (t || "").replace(/^(Administrator|管理员)\s*:\s*/, "").replace(/[^\w一-鿿-]+/g, "-").replace(/^-|-$/g, "").slice(0, 24) || "wt";
  $("#aCancel").onclick = () => $("#attachForm").classList.add("hidden");
  $("#aAttach").onclick = async () => {
    const name = $("#aName").value.trim(); if (!name || !state.selHwnd) return alert("先选一个窗口或 tab，并填名字");
    const s = await api("POST", "/api/wt/attach", { name, hwnd: state.selHwnd, tab: $("#aTab").value.trim() });
    $("#attachForm").classList.add("hidden"); $("#aName").value = ""; state.sessions.push(s); selectSession(name); closeDrawers();
  };

  // ---------- prompts ----------
  function renderPrompts() {
    const ul = $("#promptList"); ul.innerHTML = "";
    if (!state.pending.length) { ul.innerHTML = "<li class='muted'>无</li>"; return; }
    for (const p of state.pending) {
      const li = document.createElement("li");
      li.innerHTML = `<div class="meta"><b>${esc(p.session)}</b> #${p.id} · ${p.escalated ? "需人工 · " : ""}${p.status} · ${ts(p.detected_at)}</div>
        <pre>${esc(p.snippet)}</pre><div class="reason">${esc(p.reason || "")}</div>
        <div class="acts"><button class="primary small ok">批准 (Enter)</button><button class="danger small no">拒绝 (Esc)</button><button class="small go">打开</button></div>`;
      li.querySelector(".ok").onclick = () => api("POST", `/api/prompts/${p.id}/approve`);
      li.querySelector(".no").onclick = () => api("POST", `/api/prompts/${p.id}/deny`);
      li.querySelector(".go").onclick = () => { selectSession(p.session); closeDrawers(); };
      ul.appendChild(li);
    }
  }

  // ---------- log ----------
  function logEvent(e) {
    const d = document.createElement("div"); d.className = "k-" + e.kind;
    d.textContent = `${ts(e.ts)} ${e.kind.padEnd(9)} ${(e.session || "-").padEnd(10)} ${e.msg}`;
    const log = $("#log"); log.appendChild(d); while (log.children.length > 400) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
    if (["detected", "held"].includes(e.kind) && "vibrate" in navigator) navigator.vibrate(60);
  }
  $("#clearLog").onclick = () => $("#log").innerHTML = "";
  api("GET", "/api/events").then(evs => evs.slice(-100).forEach(logEvent)).catch(() => {});

  // ---------- approver settings ----------
  const settings = { fields: [], presets: {}, config: {}, providers: {}, fieldProviders: {} };
  function settingsValues() {
    const out = {};
    for (const [key, , kind] of settings.fields) {
      const el = document.getElementById("sf_" + key);
      if (kind === "bool") out[key] = el.checked;
      else if (kind === "choice" || kind === "text") out[key] = el.value;
      else { const sel = el, txt = document.getElementById("sf_" + key + "_txt"); out[key] = sel.value === "__custom__" ? txt.value.trim() : sel.value; }
    }
    return out;
  }
  function settingsCommand(v) {
    const provider = v.provider || "codex", info = settings.providers[provider];
    if (!info) return;
    for (const [key] of settings.fields) {
      const el = document.getElementById("sf_" + key);
      el.closest("label").classList.toggle("hidden", !!settings.fieldProviders[key] && settings.fieldProviders[key] !== provider);
    }
    const codex = provider === "codex", claude = provider === "claude";
    const parts = codex ? ["codex", "exec", "--sandbox", "read-only"] : claude ? ["claude"]
      : ["agy", "--input-format", "stream-json", "--output-format", "stream-json", "--mode", "plan"];
    if (claude && v.permission_mode) parts.push("--permission-mode", v.permission_mode);
    const model = v[info.model_key];
    if (model) parts.push("--model", model);
    const effort = v[info.effort_key];
    if (effort) parts.push(codex ? "-c" : "--effort", codex ? `model_reasoning_effort="${effort}"` : effort);
    const extra = v[info.extra_key];
    if (extra) parts.push(extra);
    const schedule = !claude ? (v.every ? `   → 每轮结束后 ${v.every} 再巡检` : "   → 单次巡检")
      : (v.every ? `   → /loop ${v.every} /termpilot` : "   → /termpilot");
    $("#settingsCmd").textContent = parts.join(" ") + schedule;
  }
  async function openSettings() {
    const r = await api("GET", "/api/config/supervisor");
    settings.fields = r.fields; settings.presets = r.presets; settings.config = r.config;
    settings.providers = r.providers; settings.fieldProviders = r.field_providers;
    $("#catalogDate").textContent = `模型预设固定于 ${r.catalog_date}，可输入自定义模型 ID。`;
    const box = $("#settingsFields"); box.innerHTML = "";
    for (const [key, label, kind, ck] of r.fields) {
      const cur = r.config[key];
      const lab = document.createElement("label");
      if (kind === "bool") {
        lab.className = "check"; lab.innerHTML = `<input type="checkbox" id="sf_${key}" ${cur ? "checked" : ""}> ${esc(label)}`;
      } else if (kind === "text") {
        lab.innerHTML = `${esc(label)}<input id="sf_${key}" value="${esc(cur ?? "")}">`;
      } else {
        const choices = (r.presets[ck] || []).map(c => Array.isArray(c) ? c : [c, ""]);
        const known = choices.some(c => c[0] === cur);
        const opts = choices.map(c => `<option value="${esc(c[0])}" ${c[0] === cur ? "selected" : ""}>${esc(c[0])}${c[1] ? " — " + esc(c[1]) : ""}</option>`).join("");
        const custom = kind === "choice+text" ? `<option value="__custom__" ${!known ? "selected" : ""}>自定义…</option>` : "";
        lab.innerHTML = `${esc(label)}<div class="pair"><select id="sf_${key}">${opts}${custom}</select>${kind === "choice+text" ? `<input id="sf_${key}_txt" placeholder="自定义值" value="${!known ? esc(cur ?? "") : ""}" ${known ? "hidden" : ""}>` : ""}</div>`;
      }
      box.appendChild(lab);
    }
    box.querySelectorAll("select, input").forEach(el => el.addEventListener("input", () => {
      for (const [key, , kind] of settings.fields) if (kind === "choice+text") {
        const sel = document.getElementById("sf_" + key), txt = document.getElementById("sf_" + key + "_txt");
        txt.hidden = sel.value !== "__custom__";
      }
      settingsCommand(settingsValues());
    }));
    settingsCommand(r.config);
    $("#settingsStatus").textContent = r.running ? `审批者 ${r.config.name} 正在运行` : `审批者 ${r.config.name} 未运行`;
    $("#settingsModal").classList.remove("hidden");
  }
  async function saveSettings(restart) {
    const v = settingsValues();
    $("#settingsStatus").textContent = restart ? "保存并重启中…" : "保存中…";
    const r = await api("POST", "/api/config/supervisor", { config: v, restart });
    $("#settingsStatus").textContent = restart ? `已重启：${r.command}` : `已保存：${r.command}（下次重启审批者时生效）`;
    if (restart) setTimeout(() => { $("#settingsModal").classList.add("hidden"); selectSession(null); }, 800);
  }
  $("#settingsBtn").onclick = openSettings;
  $("#settingsClose").onclick = () => $("#settingsModal").classList.add("hidden");
  $("#settingsSave").onclick = () => saveSettings(false);
  $("#settingsSaveRestart").onclick = () => saveSettings(true);

  // ---------- utils ----------
  function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  const enc = (s) => encodeURIComponent(s);
  const ts = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });

  if (token) connectEvents();
})();
