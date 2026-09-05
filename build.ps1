# Build a single-file termpilot.exe with PyInstaller (run from the project dir).
#   .\build.ps1            -> dist\termpilot.exe
# Requirements on the build machine: the anaconda python with pywinpty, pyte, aiohttp,
# pywin32, comtypes, psutil, websockets, pyinstaller installed.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = if (Test-Path "C:\ProgramData\anaconda3\python.exe") { "C:\ProgramData\anaconda3\python.exe" } else { "python" }
Set-Location $root

# Pre-generate the comtypes UIAutomation wrapper so it can be bundled.
& $py -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll'); print('comtypes gen ok')"

# Anaconda drags in half of the distribution through pkg_resources / entry points; keep the exe lean.
$excludes = @("jedi","IPython","ipykernel","jupyter_client","nbformat","matplotlib","numpy","scipy","pandas",
  "PyQt5","PyQt6","PySide2","PySide6","PIL","pygments","prompt_toolkit","pydantic",
  "rich","cryptography","paramiko","invoke","nacl","pyasn1","zmq","tornado","jinja2","tzdata","setuptools","pkg_resources",
  "distutils","wheel","chardet","urllib3","markdown_it","jsonschema","jsonschema_specifications","parso","sqlite3",
  "unittest","pydoc","doctest","test","tests","lib2to3","xmlrpc","email.mime","curses","pytest","docutils","babel")
$ex = $excludes | ForEach-Object { "--exclude-module"; $_ }

& $py -m PyInstaller --noconfirm --clean --onefile --console --name termpilot @ex `
  --python-option "W ignore::UserWarning" `
  --add-data "static;static" `
  --add-data "templates;templates" `
  --collect-all winpty `
  --collect-submodules comtypes.gen `
  --hidden-import comtypes.gen.UIAutomationClient `
  --hidden-import win32timezone `
  --hidden-import termpilot_cli_client --hidden-import termpilot_cli `
  --hidden-import termpilot.console --hidden-import termpilot.server --hidden-import termpilot.install `
  --hidden-import termpilot.gui --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import tkinter.messagebox `
  termpilot.py

if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
$exe = Join-Path $root "dist\termpilot.exe"
Write-Host "`nbuilt: $exe  ($([math]::Round((Get-Item $exe).Length/1MB,1)) MB)"
Write-Host "distribute: dist\termpilot.exe alone. On the target machine run:  termpilot.exe start"
