# Starts the Claude bridge on this computer, so the assistant can use Claude with your Claude
# subscription through Claude Code. Needs Claude Code installed and signed in (run `claude` once).
#
#   scripts\bridge.ps1              run it in this window (Ctrl+C stops it)
#   scripts\bridge.ps1 -Background  run it hidden in the background
#   scripts\bridge.ps1 -Install     start it hidden now and every time you sign in to Windows
#   scripts\bridge.ps1 -Uninstall   stop it and don't start it at sign-in any more
param([int]$Port = 8005, [switch]$Background, [switch]$Install, [switch]$Uninstall)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "Assistant Claude bridge.lnk"
$hiddenArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -Port $Port"

function Get-BridgeProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*uvicorn app.main:app*--port $Port*" }
}

if ($Uninstall) {
    if (Test-Path $shortcut) { Remove-Item $shortcut -Force -Confirm:$false }
    Get-BridgeProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
    Write-Host "The Claude bridge is stopped and won't start at sign-in."
    return
}
if (-not (Test-Path $py)) { throw "Virtualenv not found - run scripts\setup.ps1 first." }

# The chat service and the bridge share a token from .env; create one the first time.
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root ".env.example") $envFile }
$hasToken = Select-String -Path $envFile -Pattern '^\s*CLAUDE_BRIDGE_TOKEN\s*=\s*\S' -Quiet
if (-not $hasToken) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = -join ($bytes | ForEach-Object { $_.ToString("x2") })
    $lines = @(Get-Content $envFile | Where-Object { $_ -notmatch '^\s*CLAUDE_BRIDGE_TOKEN\s*=' })
    $lines += "CLAUDE_BRIDGE_TOKEN=$token"
    [System.IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Added CLAUDE_BRIDGE_TOKEN to .env." -ForegroundColor Yellow
    Write-Host "If the assistant runs in Docker, restart its chat service: docker compose up -d chat" -ForegroundColor Yellow
}

if ($Install) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = "powershell.exe"
    $link.Arguments = $hiddenArgs
    $link.WorkingDirectory = $root
    $link.WindowStyle = 7  # minimized; -WindowStyle Hidden then hides it
    $link.Description = "Lets the assistant use Claude with your Claude subscription"
    $link.Save()
    Write-Host "The Claude bridge will start every time you sign in to Windows."
    $Background = $true
}

if (Get-BridgeProcesses) {
    Write-Host "The Claude bridge is already running on http://127.0.0.1:$Port."
    return
}
if ($Background) {
    Start-Process powershell.exe -ArgumentList $hiddenArgs -WindowStyle Hidden
    Write-Host "The Claude bridge is running in the background on http://127.0.0.1:$Port."
    return
}

Write-Host "Claude bridge on http://127.0.0.1:$Port (Ctrl+C stops it)"
Push-Location (Join-Path $root "services\bridge")
try {
    & $py -m uvicorn app.main:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
