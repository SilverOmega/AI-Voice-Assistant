# One-time native setup on Windows: Python venv (3.11 recommended) + frontend packages.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not (Test-Path ".venv")) { py -3.11 -m venv .venv }
    & .venv\Scripts\python.exe -m pip install --upgrade pip
    & .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    if (-not (Test-Path ".env")) { Copy-Item .env.example .env; Write-Host "Created .env from .env.example" }
    Push-Location frontend; npm install; Pop-Location
    Write-Host "`nSetup complete. Start everything with: scripts\dev.ps1"
} finally { Pop-Location }
