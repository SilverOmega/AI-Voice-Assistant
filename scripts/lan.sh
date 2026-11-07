#!/usr/bin/env bash
# Sets the assistant up for phones, tablets and other computers on your network: finds this
# computer's LAN address, fills in TLS_HOSTS, APP_URL and (when empty) APP_PASSCODE in .env,
# and prints the address to open. Run it again if the computer's address changes.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env

if command -v ip >/dev/null 2>&1; then
    ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')
else  # macOS
    ip=$(ipconfig getifaddr "$(route -n get default | awk '/interface:/ {print $2}')")
fi
[ -n "${ip:-}" ] || { echo "No network connection found. Connect to your Wi-Fi or LAN first." >&2; exit 1; }

get() { sed -n "s/^$1=//p" .env | head -n 1; }
set_value() {
    if grep -q "^$1=" .env; then
        awk -v key="$1" -v value="$2" 'index($0, key "=") == 1 { print key "=" value; next } { print }' .env > .env.tmp
        mv .env.tmp .env
    else
        printf '%s=%s\n' "$1" "$2" >> .env
    fi
}

port=$(get APP_HTTPS_PORT)
url="https://$ip:${port:-8443}"
set_value TLS_HOSTS "$ip"
app_url=$(get APP_URL)
# Keep an address you chose yourself; replace one this script wrote before.
if [ -z "$app_url" ] || echo "$app_url" | grep -Eq '^https://[0-9.]+:[0-9]+/?$'; then set_value APP_URL "$url"; fi

passcode=$(get APP_PASSCODE)
new_passcode=""
if [ "${#passcode}" -lt 6 ]; then
    # 12 characters that are easy to type on a phone (no 0/o, 1/l/i): about 59 bits.
    raw=$(LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' < /dev/urandom | head -c 12)
    passcode="${raw:0:4}-${raw:4:4}-${raw:8:4}"
    set_value APP_PASSCODE "$passcode"
    new_passcode=1
fi

echo
echo "Phones, tablets and other computers on the same network:"
echo "  1. Restart the app:   docker compose up -d --build frontend chat"
echo "  2. On the device, open $url"
if [ -n "$new_passcode" ]; then
    echo "  3. Enter the passcode $passcode (saved in .env as APP_PASSCODE)"
else
    echo "  3. Enter the passcode from APP_PASSCODE in .env"
fi
echo "  The browser warns about the certificate the first time. To remove the warning, and make sure"
echo "  the microphone is allowed, install $url/ca.crt on the device: see the README."
