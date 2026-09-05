# Start termpilot in the background (hidden window). Re-running restarts it.
$root = $PSScriptRoot
$py = if (Test-Path "C:\ProgramData\anaconda3\python.exe") { "C:\ProgramData\anaconda3\python.exe" } else { "python" }
Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
$p = Start-Process -FilePath $py -ArgumentList @("-u", "`"$root\termpilot.py`"", "serve") `
  -WorkingDirectory $root -RedirectStandardOutput "$root\serve.out" -RedirectStandardError "$root\serve.err" `
  -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3
if ($p.HasExited) { Write-Host "failed to start:"; Get-Content "$root\serve.err" -Tail 20; exit 1 }
Write-Host "termpilot running, pid $($p.Id)"
Get-Content "$root\serve.out"
Write-Host "stop:  Stop-Process -Id $($p.Id)"
