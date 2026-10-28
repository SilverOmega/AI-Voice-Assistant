# Runs the three backend services and the Vite dev server natively. Ctrl+C stops everything.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Virtualenv not found - run scripts\setup.ps1 first." }

$env:WAKEWORD_CUSTOM_DIR = Join-Path $root "models\wakeword"
$services = @(
    @{ name = "chat"; port = 8001 },
    @{ name = "voice"; port = 8002 },
    @{ name = "wakeword"; port = 8003 }
)
$procs = @()
try {
    foreach ($s in $services) {
        Write-Host "Starting $($s.name) service on :$($s.port)"
        $procs += Start-Process -FilePath $py -NoNewWindow -PassThru `
            -WorkingDirectory (Join-Path $root "services\$($s.name)") `
            -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $s.port
    }
    Write-Host "Open http://localhost:5173"
    Push-Location (Join-Path $root "frontend")
    npm run dev
} finally {
    Pop-Location -ErrorAction SilentlyContinue
    foreach ($p in $procs) { taskkill /T /F /PID $p.Id 2>$null | Out-Null }
}
