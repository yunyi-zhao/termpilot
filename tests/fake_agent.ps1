# Simulates an AI agent asking for permission, Claude Code style.
# Usage: pwsh -File fake_agent.ps1 [safe|unsafe|choice]
param([string]$Kind = "safe")

$cmd = switch ($Kind) {
  "unsafe" { "Remove-Item -Recurse -Force .\build" }
  "choice" { "" }
  default  { "Get-ChildItem -Recurse src" }
}

Write-Host ""
Write-Host "* Thinking about the next step..."
Start-Sleep -Seconds 1
if ($Kind -eq "choice") {
  Write-Host "I see two possible approaches here:"
  Write-Host "  Option A: rewrite the parser"
  Write-Host "  Option B: patch the tokenizer"
  Write-Host "Which approach would you like me to take?"
} else {
  Write-Host "PowerShell($cmd)"
}
Write-Host ""
Write-Host "Do you want to proceed?"
Write-Host "❯ 1. Yes"
Write-Host "  2. Yes, and don't ask again"
Write-Host "  3. No, and tell Claude what to do differently (esc)"
Write-Host ""
Write-Host "Esc to cancel"

$k = [Console]::ReadKey($true)
Write-Host ""
if ($k.Key -eq "Enter") { Write-Host "[fake-agent] APPROVED -> running: $cmd" }
elseif ($k.Key -eq "Escape") { Write-Host "[fake-agent] CANCELLED by Esc" }
else { Write-Host "[fake-agent] got key: $($k.Key)" }
