# WireGuard: Proxmox LXC + web-chat

Контейнер **web-chat** (LAN `192.168.88.44`) получает VPN-адрес **10.88.0.44** в подсети `10.88.0.0/24`, общей с WireGuard на Proxmox-хосте.

## Топология

```text
[Клиент WG] ──UDP──► [Proxmox wg0 10.88.0.1] ──► [LXC wg0 10.88.0.44 :8090]
                              │
                              └── маршрут 192.168.88.44 (LAN)
```

Удалённый браузер после VPN: `http://10.88.0.44:8090` или `http://192.168.88.44:8090` (если в AllowedIPs клиента есть LAN).

## 1. Параметры LXC на Proxmox (хост)

Файл `/etc/pve/lxc/<CTID>.conf` (или через UI → Options → Features):

```ini
# Уже обычно достаточно для Ubuntu LXC:
features: nesting=1,keyctl=1

# Если wg-quick не поднимает интерфейс — добавьте:
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
```

Перезапуск контейнера после изменения конфига:

```bash
pct stop <CTID> && pct start <CTID>
```

## 2. WireGuard на хосте (уже установлен)

Убедитесь, что на **хосте** в `wg0` есть адрес подсети, например:

```ini
[Interface]
Address = 10.88.0.1/24
ListenPort = 51820
# ...
```

Добавьте peer контейнера (ключ из `/etc/wireguard/peer-for-proxmox-host.conf` в LXC):

```bash
# На хосте Proxmox
wg show wg0 public-key          # → вставить в LXC wg0.conf [Peer] PublicKey
cat /path/from/container/peer-for-proxmox-host.conf >> /etc/wireguard/wg0.conf
wg syncconf wg0 <(wg-quick strip wg0)
# или: systemctl restart wg-quick@wg0
```

На **хосте** включите форвардинг и при необходимости NAT для клиентов VPN в LAN:

```bash
sysctl -w net.ipv4.ip_forward=1
# Пример (интерфейс LAN — vmbr0 или eth0):
# iptables -A FORWARD -i wg0 -o vmbr0 -j ACCEPT
# iptables -A FORWARD -i vmbr0 -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT
```

## 3. В контейнере web-chat

```bash
# Уже выполнено при подготовке; для повторной установки:
WG_HOST_PUBLIC_KEY="$(ssh root@192.168.88.1 'wg show wg0 public-key')" \
  bash /root/web-chat/deploy/wireguard/setup-container.sh

# Подставить PublicKey хоста в /etc/wireguard/wg0.conf, затем:
systemctl enable --now wg-quick@wg0
wg show
ip -4 addr show wg0
```

Проверка:

```bash
curl -s http://10.88.0.44:8090/api/health
```

## 4. web-chat `.env`

Для доступа **только через VPN**:

```env
PUBLIC_BASE_URL=http://10.88.0.44:8090
```

Для гибрида LAN + VPN оставьте LAN URL; картинки по VPN могут требовать URL с `10.88.0.44`.

## 5. Файрвол

| Где | Порт | Назначение |
|-----|------|------------|
| Proxmox (WAN) | UDP 51820 | WireGuard клиенты → хост |
| LXC | UDP 51820 | Опционально, если endpoint контейнера |
| LXC | TCP 8090 | web-chat HTTP |

## 6. Устранение неполадок

| Симптом | Действие |
|---------|----------|
| `RTNETLINK answers: Operation not permitted` | Добавить `lxc.mount.entry` для `/dev/net/tun`, `cap_net_admin` |
| Handshake 0 | Проверить PublicKey на обеих сторонах, UDP 51820, Endpoint |
| Сайт открывается по LAN, не по VPN | AllowedIPs клиента, маршрут на хосте, `PUBLIC_BASE_URL` |
