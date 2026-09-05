# termpilot

Supervise the AI coding agents (Claude Code, Codex, Gemini CLI, …) running in your Windows terminals: a rule engine auto-approves the boring permission prompts, holds the risky ones, and a Claude Code "approver" session reviews what is held. Everything is visible and controllable from a WebUI over your Tailscale network, from a desktop control panel, or from an interactive console. Windows only (ConPTY + UI Automation). Download `termpilot.exe` from the Releases page, double-click it, set the address and the approver's model, start. The rest of this README is in Chinese.

---

本机多个 PowerShell 终端的 agent 审批监控 + 远程 WebUI（通过 Tailscale IP 访问）。

- **托管会话（pty）**：termpilot 自己用 ConPTY 启动 `pwsh`，输出流和按键都是确定的，WebUI 里是真正的 xterm.js 终端，可以远程敲命令。
- **挂接会话（wt）**：通过 UI Automation 读取已经开着的 Windows Terminal 窗口（当前活动 tab），用 PostMessage 发按键，不抢焦点。只读文本，不是完整终端。
- **审批引擎**：每秒看一次屏幕，识别 Claude Code / Codex / Gemini CLI 风格的权限提示；规则判定安全就自动 Enter，删除类命令、破坏性 git、进程/系统操作、"让你选方案"的提问一律 hold 住等人。可选接 `claude -p` 做二次审核。
- **三种模式**：`auto`（规则自动批准）、`manual`（全部等人）、`off`（不检测）。

## 启动

双击 `start.bat`（或在终端里运行）。它会检查防火墙规则（不存在则添加，需要管理员）并在后台重启服务，打印带 token 的 URL。`stop.bat` 停止服务。

也可以前台运行看日志：

```powershell
cd <项目目录>
python termpilot.py serve          # 监听 127.0.0.1 + Tailscale IP
```

首次运行会生成 `config.json`（含随机 token）。

手机/别的机器打开 `python termpilot.py url` 打印的地址即可（`http://100.x.x.x:8787/?token=...`），token 会存在浏览器 localStorage。

## 审批者（approver）

真正做判断的不是正则，而是一个跑 `/loop 1m /termpilot` 的 Claude Code 会话。服务启动时会自动把它作为托管会话拉起来（名字 `approver`，置顶，WebUI 第一张卡片，带"审批者"标签），命令是 `claude --permission-mode auto --model opus`，工作目录是本项目。它自己在 termpilot 里是 manual 模式：规则引擎不会替它按键，它的提示只由你处理。

- 看它：WebUI 点 approver 卡片，或总控终端 `watch approver`。
- 跟它说话：卡片里直接打字，或 `send approver "agent1 选方案 B"`。
- 重启它：总控终端 `supervisor`，或 `python termpilot.py supervisor [--every 5m]`。
- 设置（模型 / 推理强度 / 巡检间隔 / 权限模式 / 附加参数 / 自动拉起）有三个入口，改的都是 config.json 的 `supervisor` 段：
  - `start.bat` 启动时的问答向导（回车保留当前值，输数字选预设，或直接输入自定义值）。`start.bat /q` 跳过问答。
  - WebUI 顶栏 ⚙，"保存并重启审批者"立即生效，"仅保存"下次重启审批者时生效。手机上也能改。
  - 总控终端 `setup` / `setup restart`，或 `python termpilot.py setup [--show|--quick]`。
  - 预设列表在 `termpilot/config.py` 的 `SUPERVISOR_PRESETS`；所有字段都接受自定义值，所以新模型名（如 `--model` 支持的别名 fable/opus/sonnet 或完整模型 ID）和新的 effort 等级出来后直接填就行，`extra_args` 可以原样追加以后新增的 claude 参数。

规则引擎仍然在前面挡一层：明显安全的直接放行，删除、破坏性 git、杀进程、改系统、方案选择这些 hold 住等 approver 来看。

## 开一个 Claude Code 会话

三种等价方式：

- WebUI 总览页点"新建终端"，命令栏填 `claude`，工作目录填项目路径。
- WebUI 新建一个默认的 pwsh 终端，在里面敲 `claude`。
- 总控终端或 CLI：`new agent1 --cwd D:\proj --cmd claude`，或者 `new agent1 --cwd D:\proj` 再 `send agent1 claude`。

Codex、Gemini CLI 同理，把命令换成 `codex` / `gemini`。会话启动时会剥掉父进程的 `CLAUDECODE` / `CLAUDE_CODE_*` 环境变量，所以就算服务是从某个 Claude Code 里启动的，子会话也能正常起 Claude Code。

## 打包成 exe 到别的机器上用

```powershell
.\build.ps1        # 产出 dist\termpilot.exe（单文件，PyInstaller）
```

把 `termpilot.exe` 拷到目标 Windows 机器任意目录，那台机器只需要装了 Claude Code（`claude` 在 PATH 里）。**双击 exe 打开控制面板**（图形界面）：设 WebUI 地址（自动 / 所有网卡 / 自定义 IP）和端口，设审批者的模型、推理强度、巡检间隔、权限模式，然后"保存并启动服务"。面板上能复制或直接打开带 token 的地址、重启审批者、打开总控终端、一键加防火墙规则、看服务日志。关闭面板时可以选择让服务继续在后台跑。命令行用法：

```
termpilot.exe start          # 问答向导 → 后台起服务 → 自动拉起审批者；-q 跳过向导
termpilot.exe console        # 总控终端
termpilot.exe stop
termpilot.exe ls / new / send / approve / watch ...   # 和 python termpilot.py 一样
```

首次运行会在 exe 旁边生成 `config.json`（新 token）、`sessions.json`、日志，并把 `/termpilot` 斜杠命令写到那台机器的 `~\.claude\commands\termpilot.md`（内容来自 `templates\termpilot.md`，调用路径按 exe 位置填好；文件带 termpilot-managed 标记，去掉标记就不会再覆盖）。Tailscale 有则监听 Tailscale IP，没有则只有 127.0.0.1，可在 config.json 的 `hosts` 里写具体 IP 或 `"0.0.0.0"`。防火墙放行需要管理员：`termpilot.exe firewall`。

限制：只支持 Windows（ConPTY、UI Automation）；挂接模式只认 Windows Terminal 窗口；exe 内置的是 x64 的 pywinpty，ARM 机器需要在 ARM 上重新打包。

## 总控终端

双击 `console.bat`（服务没起会先起）。进去后会列出所有会话和等待中的提示，事件实时滚动；命令和下面的 CLI 一样但不用打前缀，另外 `a` / `d` 直接批准/拒绝最新的提示，`quiet` 关掉事件滚动。`quit` 退出时服务继续在后台跑。

## CLI

```
python termpilot.py new agent1 [--cmd "pwsh -NoLogo"] [--cwd D:\proj] [--mode auto|manual|off]
python termpilot.py ls
python termpilot.py send agent1 claude            # 输入一行 + Enter
python termpilot.py keys agent1 enter | esc | up | down | 1 | y | ctrl-c
python termpilot.py screen agent1 -n 40
python termpilot.py prompts                       # 等待处理的提示
python termpilot.py approve 12 | deny 12
python termpilot.py mode agent1 manual
python termpilot.py windows                       # 可挂接的 Windows Terminal 窗口
python termpilot.py attach wt1 <HWND> [--tab "标题"]
python termpilot.py log -n 50
python termpilot.py kill agent1
```

## WebUI

打开是总览页：每个终端一张卡片（黄点＝有提示等待，绿点＝刚有输出，红点＝进程退出），点卡片进入。中间是终端（pty，xterm.js 真终端）或屏幕文本（wt）加快捷键条（Enter/Esc/↑/↓/1/2/3/y/n/^C），右栏是待审批提示（批准 / 拒绝 / 打开）和事件日志。顶栏 ☰ 收起/展开左侧会话栏，"⚑ 待审批"收起/展开右栏，状态记在浏览器里。窄屏（手机）下左右栏变成抽屉。

## 延迟

- 托管会话（pty）：直接读 ConPTY 管道，敲键到浏览器回显 20–60 ms（`tests\ws_probe.py` 可测）。pwsh 启动后的第一条命令要 2–3 秒，那是 PowerShell 自己在加载 PSReadLine 和 profile，与 termpilot 无关。
- 挂接会话（wt）：服务端每 0.3 秒用 UIA 读一次屏（4 个窗口一轮约 25 ms），有变化立刻通过 WebSocket 推给浏览器，延迟 0.3 秒左右。`poll_interval` 可调。
- 曾经的坑：pywinpty 的 `PtyProcess` 把输出经由一个 127.0.0.1 socket 转发，在装了本地流量过滤驱动的机器上每次读要 500 ms（回环被本地过滤驱动拖慢），所以 `pty_session.py` 改为直接用 `winpty.PTY`。

## 为什么 CLI 走 Tailscale IP 而不是 127.0.0.1

有些机器上 127.0.0.1 的 HTTP 连接会被本地流量过滤驱动（AdGuard、火绒之类的安全软件）随机重置，连标准库的 http.server 都中招，而 Tailscale 地址上零重置（`tests\reset_probe.py` 可以复现）。所以 CLI 和总控终端默认连第一个非回环地址；要改用 `config.json` 里的 `api_base`，例如 `"api_base": "http://127.0.0.1:8787"`。

## 配置 `config.json`

| 键 | 默认 | 说明 |
|---|---|---|
| `port` | 8787 | 端口 |
| `hosts` | `"auto"` | `auto` = 127.0.0.1 + Tailscale IPv4；也可写列表 |
| `token` | 自动生成 | API/WS 鉴权 |
| `poll_interval` | 1.0 | 轮询秒数 |
| `confirm_cycles` | 2 | 同一提示连续看到 N 轮才行动（避免半渲染误判） |
| `stale_lines` | 1 | 提示下方超过 N 行新输出就视为已回答 |
| `action_cooldown` | 2.5 | 按键后多少秒内不把变化的屏幕当成新提示 |
| `retry_cycles` / `max_retries` | 5 / 3 | 批准后提示仍在就重发 Enter |
| `default_shell` | `pwsh -NoLogo` | 新会话命令 |
| `rules` | `{}` | 见下 |
| `reviewer` | `{"enabled": false, "command": "claude -p", "timeout": 90}` | LLM 二审 |

### 规则

`rules` 里可以整体覆盖 `approval_patterns` / `unsafe_patterns` / `direction_patterns` / `safe_override_patterns`，或者用 `*_extra` 追加。默认规则在 `termpilot/rules.py`。例子：

```json
"rules": {
  "safe_override_patterns_extra": ["openFPGALoader", "sudo dd .*mmcblk"],
  "unsafe_patterns_extra": ["Invoke-WebRequest .*\\| iex"]
}
```

判定顺序：safe_override → direction（hold）→ unsafe（hold）→ approve。`reviewer.enabled` 为 true 时，规则判定为 approve 的提示还要过一遍 LLM，LLM 回 HOLD 则 hold。

## 挂接模式的限制

- 只能读窗口当前活动 tab 的文本；建议一个 agent 一个窗口。给了 `--tab` 会先切到那个 tab 再读（会切走你的视图）。
- PostMessage 发 Enter/Esc/方向键/数字/小写字母可靠（已在真实窗口验证）。需要 Shift 的字符（大写、`!` `|` 等）可能变成未 Shift 的版本，Ctrl 组合键也只是尽力而为，因为终端读的是真实键盘状态。要远程敲完整命令请用 pty 会话。
- 如果 Windows Terminal 大版本更新改了控件类名（`TermControl` / `InputSite`）需要调整 `wt_attach.py`。

## 测试

```powershell
.\tests\restart_server.ps1      # 只绑 127.0.0.1 的测试实例
python tests\e2e.py             # 用 tests\fake_agent.ps1 模拟 safe / unsafe / choice 三种提示
```

## 文件

```
termpilot.py            CLI 入口
termpilot/config.py     配置
termpilot/rules.py      提示识别 + 策略
termpilot/screen.py     pyte 终端模拟
termpilot/pty_session.py  ConPTY 会话
termpilot/wt_attach.py  Windows Terminal 挂接（UIA + PostMessage）
termpilot/reviewer.py   claude -p 二审
termpilot/manager.py    会话表、提示生命周期、监控线程、事件总线
termpilot/server.py     aiohttp HTTP/WS + 静态页
static/                 WebUI（xterm.js 已本地 vendored）
```

依赖：`pip install pywinpty pyte aiohttp pywin32 comtypes`，装在 anaconda 的 Python 3.10 里（`C:\ProgramData\anaconda3\python.exe`）。所有 bat/ps1 启动脚本都固定用这个解释器，因为终端里的 `python` 可能指向 choco 装的 3.14，那里没有这些依赖。手动跑 CLI 时也请用 `C:\ProgramData\anaconda3\python.exe termpilot.py ...`。
