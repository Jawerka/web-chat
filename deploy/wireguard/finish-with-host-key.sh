#!/usr/bin/env bash
# Подставить PublicKey Proxmox-хоста и поднять wg0.
set -euo pipefail

HOST_PUBKEY="${1:-}"
WG_CONF="/etc/wireguard/wg0.conf"

if [[ $EUID -ne 0 ]]; then
  echo "Запустите от root." >&2
  exit 1
fi

if [[ -z "$HOST_PUBKEY" ]]; then
  echo "Использование: $0 <HOST_WG0_PUBLIC_KEY>" >&2
  echo "На Proxmox: wg show wg0 public-key" >&2
  exit 1
fi

if ! echo "$HOST_PUBKEY" | grep -qE '^[A-Za-z0-9+/]{42,44}=$'; then
  echo "Похоже на неверный формат PublicKey." >&2
  exit 1
fi

sed -i "s|^PublicKey = .*|PublicKey = ${HOST_PUBKEY}|" "$WG_CONF"
sed -i "s|REPLACE_WITH_HOST_WG_PUBLIC_KEY|${HOST_PUBKEY}|" "$WG_CONF"

systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0
sleep 1
wg show
ip -4 addr show wg0

echo ""
echo "Добавьте на Proxmox блок из: /etc/wireguard/peer-for-proxmox-host.conf"
echo "Затем на хосте: wg syncconf wg0 <(wg-quick strip wg0)"
