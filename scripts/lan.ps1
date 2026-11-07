# Sets the assistant up for phones, tablets and other computers on your network: finds this
# computer's LAN address, fills in TLS_HOSTS, APP_URL and (when empty) APP_PASSCODE in .env,
# and prints the address to open. Run it again if the computer's address changes.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root ".env.example") $envFile }

$net = Get-NetIPConfiguration |
    Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    Select-Object -First 1
if (-not $net) { throw "No network connection found. Connect to your Wi-Fi or LAN first." }
$ip = ($net.IPv4Address | Select-Object -First 1).IPAddress

$lines = [System.Collections.Generic.List[string]]::new([string[]](Get-Content $envFile -Encoding UTF8))
function Get-Value([string]$key) {
    $line = $lines | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
    if ($line) { return $line.Substring($key.Length + 1).Trim() } else { return "" }
}
function Set-Value([string]$key, [string]$value) {
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$key=") { $lines[$i] = "$key=$value"; return }
    }
    $lines.Add("$key=$value")
}

$port = Get-Value "APP_HTTPS_PORT"
if (-not $port) { $port = "8443" }
$url = "https://${ip}:$port"
Set-Value "TLS_HOSTS" $ip
$appUrl = Get-Value "APP_URL"
# Keep an address you chose yourself; replace one this script wrote before.
if (-not $appUrl -or $appUrl -match "^https://\d+\.\d+\.\d+\.\d+:\d+/?$") { Set-Value "APP_URL" $url }

$passcode = Get-Value "APP_PASSCODE"
if ($passcode.Length -lt 6) {
    # 12 characters that are easy to type on a phone (no 0/o, 1/l/i): about 59 bits.
    $alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $chars = @()
    $byte = [byte[]]::new(1)
    while ($chars.Count -lt 12) {
        $rng.GetBytes($byte)
        if ($byte[0] -lt 248) { $chars += $alphabet[$byte[0] % 31] }  # 248 = 8 * 31: no bias
    }
    $passcode = (-join $chars[0..3]) + "-" + (-join $chars[4..7]) + "-" + (-join $chars[8..11])
    Set-Value "APP_PASSCODE" $passcode
    $newPasscode = $true
}

# No byte-order mark: Docker Compose would read it as part of the first setting's name.
[System.IO.File]::WriteAllLines($envFile, $lines, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "Phones, tablets and other computers on the same network:" -ForegroundColor Cyan
Write-Host "  1. Restart the app:   docker compose up -d --build frontend chat"
Write-Host "  2. On the device, open $url"
if ($newPasscode) {
    Write-Host "  3. Enter the passcode $passcode (saved in .env as APP_PASSCODE)"
} else {
    Write-Host "  3. Enter the passcode from APP_PASSCODE in .env"
}
Write-Host "  The browser warns about the certificate the first time. To remove the warning, and make sure"
Write-Host "  the microphone is allowed, install $url/ca.crt on the device: see the README."

$netProfile = Get-NetConnectionProfile -InterfaceIndex $net.InterfaceIndex -ErrorAction SilentlyContinue
if ($netProfile -and $netProfile.NetworkCategory -eq "Public") {
    Write-Host ""
    Write-Host "Windows treats '$($netProfile.Name)' as a public network and blocks other devices." -ForegroundColor Yellow
    Write-Host "If your phone can't connect: Settings > Network & internet > (your network) > Private network."
}
