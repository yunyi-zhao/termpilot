# Restart the termpilot server on 127.0.0.1 only (for tests). Logs to serve_test.out.
$root = Split-Path -Parent $PSScriptRoot
$py = if (Test-Path "C:\ProgramData\anaconda3\python.exe") { "C:\ProgramData\anaconda3\python.exe" } else { "python" }
Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
$p = Start-Process -FilePath $py -ArgumentList @("`"$root\termpilot.py`"", "serve", "--host", "127.0.0.1") `
  -WorkingDirectory $root -RedirectStandardOutput "$root\serve_test.out" -RedirectStandardError "$root\serve_test.err" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3
Write-Host "server pid $($p.Id)"
Get-Content "$root\serve_test.out" -Tail 3
