#!/usr/bin/env bash
# Remove proxytun. Usage: sudo ./uninstall.sh [--purge]   (--purge also deletes saved profiles)
set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo -- "$0" "$@"

PREFIX=/usr/local
EXT_UUID="proxytun@alizn7.github.io"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

info() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

info "stopping proxytun"
systemctl stop 'proxytun@*.service' 2>/dev/null || true
systemctl disable proxytun-boot.service >/dev/null 2>&1 || true

info "removing files"
rm -f "$PREFIX/bin/proxytun" \
      /etc/systemd/system/proxytun@.service \
      /etc/systemd/system/proxytun-boot.service \
      /etc/polkit-1/rules.d/50-proxytun.rules
rm -rf "/usr/share/gnome-shell/extensions/$EXT_UUID"
systemctl daemon-reload

if [ "$PURGE" -eq 1 ]; then
  info "deleting profiles and the proxytun group"
  rm -rf /etc/proxytun
  getent group proxytun >/dev/null && groupdel proxytun
else
  echo "    profiles kept in /etc/proxytun (use --purge to delete them)"
fi

info "done. sing-box itself was left installed."
