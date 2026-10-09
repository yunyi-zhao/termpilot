# termpilot

Supervise Codex, Claude Code and Antigravity CLI in your Windows terminals. Each CLI has its own saved model, reasoning effort and extra arguments; all three can run at the same time. A rule engine handles permission prompts, with an optional CLI approver. Use the WebUI over Tailscale, the desktop control panel or the interactive console. Windows only (ConPTY + UI Automation). The rest of this README is in Chinese.

---

本机多个 PowerShell 终端的 agent 审批监控 + 远程 WebUI（通过 Tailscale IP 访问）。

- **托管会话（pty）**：termpilot 自己用 ConPTY 启动 `pwsh`，输出流和按键都是确定的，WebUI 里是真正的 xterm.js 终端，可以远程敲命令。
- **挂接会话（wt）**：通过 UI Automation 读取已经开着的 Windows Terminal 窗口（当前活动 tab），用 PostMessage 发按键，不抢焦点。只读文本，不是完整终端。
- **审批引擎**：每秒看一次屏幕，识别 Claude Code / Codex / Gemini CLI 风格的权限提示；规则判定安全就自动 Enter，删除类命令、破坏性 git、进程/系统操作、"让你选方案"的提问一律 hold 住等人。可选接 Codex（默认）或 Claude CLI 做二次审核。
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

支持 `codex`、`claude`、`antigravity` 三种 provider，分别调用 `codex`、`claude`、`agy`。三个 CLI 的配置同时保存；选择 provider 只决定当前审批者使用哪一个。普通终端可以同时运行三个 CLI。

模型预设固定于 **2026-10-09**，启动和打开设置时不查询或更新模型列表。保留自定义模型 ID 输入。Codex 默认使用 `gpt-6.1-sol`，推理等级 `xhigh`（该模型另有 `max`、`ultra` 两档，`ultra` 会自动派生子任务，不适合每分钟一轮的巡检，需要时可手填）。

| CLI | 默认模型 | 其它固定预设 |
|---|---|---|
| Codex | `gpt-6.1-sol` | `gpt-6-astra`、`gpt-6-sol`、`gpt-6-luna`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna` |
| Claude Code | `claude-opus-5-5` | `claude-sonnet-5-5`、`claude-haiku-5-5`、`claude-fable-5-1` |
| Antigravity | `gemini-3.8-flash-high` | Gemini 3.8/3.7/3.6 Flash 的 high/medium/low、Gemini 3.1 Pro 的 high/low |

旧配置里钉住已下架的 `gpt-5.3-codex-spark`（以及 `gpt-5.3-codex`、`gpt-5.5`）时，load 时自动改写为 `gpt-6.1-sol` 并存盘；仍在服务的模型 ID 不动。

版本依据：本机 `codex debug models` 与 `agy models` 的一次性核对，以及 [Codex 模型文档](https://learn.chatgpt.com/docs/models)、[Claude Code 模型配置](https://code.claude.com/docs/en/model-config)、[Antigravity 模型文档](https://antigravity.google/docs/models/)。具体模型能否使用取决于各 CLI 的登录账号和权限。

默认模式是 **ChatGPT / Codex**，默认模型 `gpt-6.1-sol`，推理等级 `xhigh`。先在运行服务的同一台机器、同一个用户下完成 `codex login`，用 `codex login status` 确认登录。无需另填 OpenAI API key。接入使用官方 [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode) 和 [ChatGPT 登录](https://learn.chatgpt.com/docs/auth)。

Codex 审批者由 termpilot 定时调度：获取待审批提示和终端画面，通过 `codex exec` 请求 JSON 判断，再执行 approve / deny；hold 留给用户。每轮结束后等待 `every` 指定的间隔，没有待审批提示时不调用模型。调用以 read-only 运行，模型不需要直接访问 termpilot 服务。执行前会再次检查提示和画面；模型报错、输出不合法、提示已处理或画面变化时都不会批准。

审批者在 WebUI 中置顶，名为 `approver`，自身保持 manual 模式。可以在卡片里输入指导，或运行 `send approver "说明"`，供下一轮参考；需要输入文字或选择特殊按键的提示会保留，并说明应如何处理。Codex 每轮是独立调用，保留本次运行最近 20 条指导。Ctrl-C 停止巡检。

设置有三个入口：启动向导、WebUI 顶栏 ⚙、桌面面板。总控终端也可以用 `setup` 或 `setup restart`。切换 CLI 后只显示对应字段，未显示的配置仍然保留。`model` / `effort` / `extra_args` 属于 Codex；Claude 使用 `claude_model` / `claude_effort` / `claude_extra_args`；Antigravity 使用 `antigravity_model` / `antigravity_effort` / `antigravity_extra_args`。Antigravity 的推理强度留空时使用模型 ID 中的强度设置。

```json
"supervisor": {
  "provider": "codex",
  "model": "gpt-6.1-sol",
  "claude_model": "claude-opus-5-5",
  "antigravity_model": "gemini-3.8-flash-high",
  "effort": "xhigh",
  "claude_effort": "high",
  "antigravity_effort": "",
  "every": "1m",
  "autostart": true,
  "name": "approver",
  "permission_mode": "auto",
  "extra_args": "",
  "claude_extra_args": "",
  "antigravity_extra_args": ""
}
```

切换 Claude Code：设为 `claude`，保存并重启审批者。它使用 `claude --model claude-opus-5-5 --effort high --permission-mode auto` 和 `/loop 1m /termpilot`；`permission_mode` 只作用于 Claude。首次启动 Claude 审批者时安装 `/termpilot` 命令。

切换 Antigravity：先在本机交互式运行 `agy` 完成登录，然后设为 `antigravity`，保存并重启审批者。它与 Codex 一样由 Termpilot 定时调度，使用 `agy` 的 stdin/stdout JSON 流和 JSON Schema 返回决策，无需安装 Claude 命令。每次审核使用临时工作目录和 `plan` 模式，收到失败状态、无效 JSON、重复决策或变化的终端画面时不执行批准。接入依据：[Antigravity 非交互模式](https://antigravity.google/docs/cli/headless/)。`antigravity` 命令是 IDE 启动器，CLI 应使用 `agy`。

旧配置首次读取时迁移到 Codex，保留 token、地址、端口、巡检间隔、自动启动开关，并将原来的模型/附加参数移到 Claude 字段。自定义二审 `command` 仍是显式覆盖项；清空它才使用 `reviewer.provider`。模式选择一旦保存，之后不会反复迁移。

- 查看：WebUI approver 卡片，或总控终端 `watch approver`。
- 重启：总控终端 `supervisor`，或 `python termpilot.py supervisor [--every 5m]`。
- 单轮：`python termpilot.py supervisor --no-loop`，或将 `every` 留空。
- 二审：`reviewer.enabled=true`；同样支持三种 provider 及各自的模型字段，默认关闭。二审失败或回答含糊时 hold。

## 同时运行三种 CLI

WebUI 点击“新建终端”，选择 CLI 和模型，再填写会话名及工作目录。模型默认使用该 CLI 已保存的配置，也可以仅为这个终端指定其它模型。重复创建即可同时运行三种 CLI。选择“Shell / 自定义命令”仍可启动任意命令。

```powershell
python termpilot.py new codex1 --provider codex --cwd D:\proj
python termpilot.py new claude1 --provider claude --cwd D:\proj
python termpilot.py new agy1 --provider antigravity --model gemini-3.8-flash-medium --cwd D:\proj
```

总控终端中的 `new` 接受相同的 `--provider` 和 `--model` 参数。也可以直接输入启动命令：

三种等价方式：

- WebUI 总览页点"新建终端"，命令栏填 `codex`，工作目录填项目路径。
- WebUI 新建一个默认的 pwsh 终端，在里面敲 `codex`。
- 总控终端或 CLI：`new agent1 --cwd D:\proj --cmd codex`，或者 `new agent1 --cwd D:\proj` 再 `send agent1 codex`。

Claude Code、Antigravity CLI 同理，把命令换成 `claude` / `agy`。会话启动时会剥掉父进程的 `CLAUDECODE` / `CLAUDE_CODE_*` 环境变量，所以就算服务是从某个 Claude Code 里启动的，子会话也能正常起 Claude Code。

## 打包成 exe 到别的机器上用

```powershell
.\build.ps1        # 产出 dist\termpilot.exe（单文件，PyInstaller）
```

把 `termpilot.exe` 拷到目标 Windows 机器任意目录，安装并登录需要使用的 CLI：Codex 对应 `codex`，Claude Code 对应 `claude`，Antigravity 对应 `agy`，命令需在 PATH 中。**双击 exe 打开控制面板**：设置 WebUI 地址和端口，选择 CLI、模型、推理强度及巡检间隔，然后“保存并启动服务”。面板上可打开 WebUI、重启审批者、打开总控终端、添加防火墙规则和查看日志。关闭面板时可以选择让服务继续在后台运行。命令行用法：

```
termpilot.exe start          # 问答向导 → 后台起服务 → 自动拉起审批者；-q 跳过向导
termpilot.exe console        # 总控终端
termpilot.exe stop
termpilot.exe ls / new / send / approve / watch ...   # 和 python termpilot.py 一样
```

首次运行会在 exe 旁边生成 `config.json`（新 token）、`sessions.json`、日志，选择 Claude 模式时再把 `/termpilot` 斜杠命令写到那台机器的 `~\.claude\commands\termpilot.md`（内容来自 `templates\termpilot.md`，调用路径按 exe 位置填好；文件带 termpilot-managed 标记，去掉标记就不会再覆盖）。Tailscale 有则监听 Tailscale IP，没有则只有 127.0.0.1，可在 config.json 的 `hosts` 里写具体 IP 或 `"0.0.0.0"`。防火墙放行需要管理员：`termpilot.exe firewall`。

限制：只支持 Windows（ConPTY、UI Automation）；挂接模式只认 Windows Terminal 窗口；exe 内置的是 x64 的 pywinpty，ARM 机器需要在 ARM 上重新打包。

## 总控终端

双击 `console.bat`（服务没起会先起）。进去后会列出所有会话和等待中的提示，事件实时滚动；命令和下面的 CLI 一样但不用打前缀，另外 `a` / `d` 直接批准/拒绝最新的提示，`quiet` 关掉事件滚动。`quit` 退出时服务继续在后台跑。

## CLI

```
python termpilot.py new agent1 [--cmd "pwsh -NoLogo"] [--cwd D:\proj] [--mode auto|manual|off]
python termpilot.py ls
python termpilot.py send agent1 codex            # 输入一行 + Enter
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
| `reviewer` | `{"enabled": false, "provider": "codex", "model": "gpt-6.1-sol", "effort": "xhigh", "command": "", "timeout": 90}` | LLM 二审 |

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

不调用真实模型的回归测试：`python -m unittest discover -s tests -p 'test_*.py' -v`。覆盖三种 CLI 的配置保存、参数隔离、模型结果解析、接口路由、错误和过期审批结果。`node tests/test_settings.js` 检查设置页的字段切换和自定义模型保留。

打包后运行 `python tests/smoke_packaged.py` 验证 exe 与 ConPTY。测试使用临时配置和模拟服务，不连接正在运行的 termpilot，也不调用模型。

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
termpilot/reviewer.py   Codex / Claude / Antigravity 二审
termpilot/ai_cli.py     CLI 调用、模型参数、结果文件
termpilot/supervisor.py Codex / Antigravity 定时巡检与结果校验
termpilot/manager.py    会话表、提示生命周期、监控线程、事件总线
termpilot/server.py     aiohttp HTTP/WS + 静态页
static/                 WebUI（xterm.js 已本地 vendored）
```

依赖：`pip install pywinpty pyte aiohttp pywin32 comtypes`，装在 anaconda 的 Python 3.10 里（`C:\ProgramData\anaconda3\python.exe`）。所有 bat/ps1 启动脚本都固定用这个解释器，因为终端里的 `python` 可能指向 choco 装的 3.14，那里没有这些依赖。手动跑 CLI 时也请用 `C:\ProgramData\anaconda3\python.exe termpilot.py ...`。
