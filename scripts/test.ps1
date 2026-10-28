# Runs every test suite: three Python services + the frontend.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$failed = @()
foreach ($svc in "chat", "voice", "wakeword") {
    Write-Host "`n=== $svc ===" -ForegroundColor Cyan
    Push-Location (Join-Path $root "services\$svc")
    & $py -m pytest -q; if ($LASTEXITCODE -ne 0) { $failed += $svc }
    Pop-Location
}
Write-Host "`n=== frontend ===" -ForegroundColor Cyan
Push-Location (Join-Path $root "frontend")
npm test; if ($LASTEXITCODE -ne 0) { $failed += "frontend" }
npx tsc -b; if ($LASTEXITCODE -ne 0) { $failed += "frontend-typecheck" }
Pop-Location
if ($failed.Count) { Write-Host "`nFAILED: $($failed -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "`nAll test suites passed." -ForegroundColor Green
