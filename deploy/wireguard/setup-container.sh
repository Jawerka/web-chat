#!/usr/bin/env bash
# Установка WireGuard в LXC web-chat и подготовка wg0.
set -euo pipefail

WG_DIR="/etc/wireguard"
LAN_IP="${WEB_CHAT_LAN_IP:-192.168.88.44}"
WG_ADDRESS="${WG_CONTAINER_ADDRESS:-10.88.0.44/24}"
WG_PORT="${WG_LISTEN_PORT:-51820}"
HOST_ENDPOINT="${WG_HOST_ENDPOINT:-192.168.88.1:51820}"
HOST_PUBKEY="${WG_HOST_PUBLIC_KEY:-}"

if [[ $EUID -ne 0 ]]; then
  echo "Запустите от root." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wireguard wireguard-tools iproute2 iptables

mkdir -p "$WG_DIR"
chmod 700 "$WG_DIR"

if [[ ! -f "$WG_DIR/private.key" ]]; then
  wg genkey | tee "$WG_DIR/private.key" | wg pubkey > "$WG_DIR/public.key"
  chmod 600 "$WG_DIR/private.key"
  chmod 644 "$WG_DIR/public.key"
fi

PRIV="$(tr -d '\n' < "$WG_DIR/private.key")"
PUB="$(tr -d '\n' < "$WG_DIR/public.key")"

if [[ -z "$HOST_PUBKEY" ]]; then
  echo "WG_HOST_PUBLIC_KEY не задан."
  echo "На Proxmox: wg show wg0 public-key"
  HOST_PUBKEY="REPLACE_WITH_HOST_WG_PUBLIC_KEY"
fi

cat > "$WG_DIR/wg0.conf" <<EOF
[Interface]
Address = ${WG_ADDRESS}
ListenPort = ${WG_PORT}
PrivateKey = ${PRIV}
PostUp = ip route add 192.168.88.0/24 dev eth0 metric 50 2>/dev/null || true
PostDown = ip route del 192.168.88.0/24 dev eth0 metric 50 2>/dev/null || true

[Peer]
PublicKey = ${HOST_PUBKEY}
Endpoint = ${HOST_ENDPOINT}
AllowedIPs = 10.88.0.0/24
PersistentKeepalive = 25
EOF
chmod 600 "$WG_DIR/wg0.conf"

cat > "$WG_DIR/peer-for-proxmox-host.conf" <<EOF
[Peer]
PublicKey = ${PUB}
AllowedIPs = 10.88.0.44/32, ${LAN_IP}/32
Endpoint = ${LAN_IP}:${WG_PORT}
PersistentKeepalive = 25
EOF

cat > /etc/sysctl.d/99-wireguard-web-chat.conf <<'EOF'
net.ipv4.conf.all.src_valid_mark = 1
EOF
sysctl -p /etc/sysctl.d/99-wireguard-web-chat.conf

echo ""
echo "Контейнер public key: ${PUB}"
echo "Фрагмент для хоста: ${WG_DIR}/peer-for-proxmox-host.conf"
echo ""
if [[ "$HOST_PUBKEY" == REPLACE_WITH_HOST_WG_PUBLIC_KEY ]]; then
  echo "Добавьте PublicKey хоста в ${WG_DIR}/wg0.conf, затем:"
else
  echo "После добавления peer на хосте:"
fi
echo "  systemctl enable --now wg-quick@wg0"
echo "  wg show"
