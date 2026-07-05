#!/usr/bin/env bash
# Mount SD ReForge extensions share to /root/reforge-ext (192.168.88.52).
set -euo pipefail

MOUNT_AI="/root/reforge-mount"
MOUNT_EXT="/root/reforge-ext"
REMOTE="//192.168.88.52/AI"
CIFS_OPTS="username=Guest,password=,vers=3.0,uid=0,gid=0,file_mode=0755,dir_mode=0755"

mkdir -p "$MOUNT_AI" "$MOUNT_EXT"

if ! mountpoint -q "$MOUNT_AI"; then
  echo "Mounting $REMOTE -> $MOUNT_AI"
  mount -t cifs "$REMOTE" "$MOUNT_AI" -o "$CIFS_OPTS"
fi

if ! mountpoint -q "$MOUNT_EXT"; then
  echo "Bind $MOUNT_AI/reforge/webui/extensions -> $MOUNT_EXT"
  mount --bind "$MOUNT_AI/reforge/webui/extensions" "$MOUNT_EXT"
fi

echo "OK: $(ls -1 "$MOUNT_EXT" | wc -l) entries in $MOUNT_EXT"
