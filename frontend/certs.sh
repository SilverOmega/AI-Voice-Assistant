#!/bin/sh
# Runs before nginx starts (nginx's /docker-entrypoint.d). Makes the HTTPS certificate that
# phones and tablets use: a private certificate authority, kept in the certs volume so a phone
# only has to trust it once, and a server certificate for localhost plus TLS_HOSTS (the
# computer's LAN address or name). The server certificate is remade when TLS_HOSTS changes
# or it nears expiry.
#
# The authority may only vouch for private network addresses and .local/.lan/.home.arpa
# names (nameConstraints), so a phone that trusts it can't be fooled about any real website.
set -eu
dir=/etc/nginx/certs
mkdir -p "$dir"
cd "$dir"

if [ ! -s ca.key ] || [ ! -s ca.crt ]; then
    echo "certs: creating the Local Assistant certificate authority"
    openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
        -subj "/CN=Local Assistant CA/O=Local Assistant" \
        -keyout ca.key -out ca.crt \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "nameConstraints=critical,permitted;IP:127.0.0.0/255.0.0.0,permitted;IP:10.0.0.0/255.0.0.0,permitted;IP:172.16.0.0/255.240.0.0,permitted;IP:192.168.0.0/255.255.0.0,permitted;IP:100.64.0.0/255.192.0.0,permitted;DNS:localhost,permitted;DNS:local,permitted;DNS:lan,permitted;DNS:home.arpa" \
        2>/dev/null
    rm -f server.crt
fi

hosts="localhost,127.0.0.1"
for h in $(echo "${TLS_HOSTS:-}" | tr ',' ' '); do hosts="$hosts,$h"; done
san=""
for h in $(echo "$hosts" | tr ',' ' '); do
    case "$h" in
        *[!0-9.]*) entry="DNS:$h" ;;
        *) entry="IP:$h" ;;
    esac
    san="${san:+$san,}$entry"
done

if [ ! -s server.crt ] || [ "$(cat hosts 2>/dev/null)" != "$hosts" ] \
    || ! openssl x509 -checkend 2592000 -noout -in server.crt >/dev/null 2>&1; then
    echo "certs: certificate for $hosts"
    openssl req -newkey rsa:2048 -nodes -subj "/CN=Local Assistant" -keyout server.key -out server.csr 2>/dev/null
    printf 'subjectAltName=%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' "$san" > server.ext
    # 825 days: the longest iPhones and iPads accept.
    openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -sha256 -days 825 \
        -extfile server.ext -out server.crt 2>/dev/null
    rm -f server.csr server.ext
    echo "$hosts" > hosts
fi
chmod 600 ca.key server.key
