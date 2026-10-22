#!/usr/bin/env bash
# Install proxytun. Usage: sudo ./install.sh [--proxy URL]
set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo -- "$0" "$@"

PREFIX=/usr/local
EXT_UUID="proxytun@alizn7.github.io"
MIN_SINGBOX="1.12.0"
SRC="$(cd "$(dirname "$0")" && pwd)"
DL_PROXY=""

info() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --proxy) [ $# -ge 2 ] || die "--proxy needs a URL"; DL_PROXY="$2"; shift 2 ;;
    -h|--help) echo "usage: sudo ./install.sh [--proxy URL]   e.g. --proxy socks5h://192.168.1.5:1080"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

CURL=(curl -fsSL)
APT_OPTS=()
if [ -n "$DL_PROXY" ]; then
  CURL+=(-x "$DL_PROXY")
  APT_OPTS=(-o "Acquire::http::Proxy=$DL_PROXY" -o "Acquire::https::Proxy=$DL_PROXY")
fi

version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

singbox_ok() {
  command -v sing-box >/dev/null 2>&1 || return 1
  local v
  v="$(sing-box version 2>/dev/null | awk 'NR==1 {print $3}')"
  [ -n "$v" ] && version_ge "$v" "$MIN_SINGBOX"
}

install_singbox_apt() {
  info "installing sing-box from the SagerNet apt repository"
  mkdir -p /etc/apt/keyrings
  "${CURL[@]}" https://sing-box.app/gpg.key -o /etc/apt/keyrings/sagernet.asc
  chmod a+r /etc/apt/keyrings/sagernet.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/sagernet.asc] https://deb.sagernet.org/ * *" \
    > /etc/apt/sources.list.d/sagernet.list
  # Refresh only the sagernet list so a broken unrelated repo cannot fail the install.
  apt-get "${APT_OPTS[@]}" update \
    -o Dir::Etc::sourcelist=sources.list.d/sagernet.list \
    -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
  DEBIAN_FRONTEND=noninteractive apt-get "${APT_OPTS[@]}" install -y \
    -o Dpkg::Options::=--force-confold sing-box
}

install_singbox_tarball() {
  local arch tag ver tmp
  case "$(uname -m)" in
    x86_64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    armv7l) arch=armv7 ;;
    *) die "unsupported CPU architecture: $(uname -m)" ;;
  esac
  info "installing sing-box from GitHub releases"
  tag="$("${CURL[@]}" https://api.github.com/repos/SagerNet/sing-box/releases/latest \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])')"
  ver="${tag#v}"
  tmp="$(mktemp -d)"
  "${CURL[@]}" "https://github.com/SagerNet/sing-box/releases/download/$tag/sing-box-$ver-linux-$arch.tar.gz" \
    | tar -xz -C "$tmp"
  install -m 0755 "$tmp/sing-box-$ver-linux-$arch/sing-box" "$PREFIX/bin/sing-box"
  rm -rf "$tmp"
}

command -v systemctl >/dev/null || die "systemd is required"
command -v python3 >/dev/null || die "python3 is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "python 3.9 or newer is required"
command -v curl >/dev/null || die "curl is required"

if singbox_ok; then
  info "sing-box $(sing-box version | awk 'NR==1 {print $3}') found"
else
  if command -v apt-get >/dev/null && command -v dpkg >/dev/null; then
    install_singbox_apt
  else
    install_singbox_tarball
  fi
  singbox_ok || die "sing-box >= $MIN_SINGBOX could not be installed"
fi

info "installing proxytun"
getent group proxytun >/dev/null || groupadd --system proxytun
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
  usermod -aG proxytun "$SUDO_USER"
fi

install -Dm0755 "$SRC/proxytun" "$PREFIX/bin/proxytun"
install -Dm0644 "$SRC/systemd/proxytun@.service" /etc/systemd/system/proxytun@.service
install -Dm0644 "$SRC/systemd/proxytun-boot.service" /etc/systemd/system/proxytun-boot.service
install -Dm0644 "$SRC/polkit/50-proxytun.rules" /etc/polkit-1/rules.d/50-proxytun.rules
install -d -m0750 -g proxytun /etc/proxytun
if [ -f /etc/proxytun/profiles.json ]; then
  chgrp proxytun /etc/proxytun/profiles.json
  chmod 0640 /etc/proxytun/profiles.json
fi

systemctl daemon-reload
systemctl enable proxytun-boot.service >/dev/null 2>&1

EXT_INSTALLED=0
if command -v gnome-shell >/dev/null; then
  info "installing the GNOME Quick Settings toggle"
  install -d "/usr/share/gnome-shell/extensions/$EXT_UUID"
  install -m0644 "$SRC/gnome-extension/$EXT_UUID/"* "/usr/share/gnome-shell/extensions/$EXT_UUID/"
  EXT_INSTALLED=1
fi

if systemctl is-active --quiet sing-box 2>/dev/null || systemctl is-enabled --quiet sing-box 2>/dev/null; then
  warn "sing-box.service is enabled or running; two TUNs will conflict with proxytun."
  if [ -t 0 ]; then
    read -r -p "    disable sing-box.service now? [Y/n] " ans
    case "${ans:-y}" in
      [Yy]*) systemctl disable --now sing-box >/dev/null 2>&1 && info "sing-box.service disabled" ;;
      *) warn "left as is; disable it with: sudo systemctl disable --now sing-box" ;;
    esac
  else
    warn "disable it with: sudo systemctl disable --now sing-box"
  fi
fi

[ -e "$PREFIX/bin/sysproxy" ] && warn "an old $PREFIX/bin/sysproxy exists; you can remove it: sudo rm $PREFIX/bin/sysproxy"

echo
info "done. next steps:"
echo "    1. log out and back in (needed once, for the proxytun group)"
if [ "$EXT_INSTALLED" -eq 1 ]; then
  echo "    2. gnome-extensions enable $EXT_UUID   (Quick Settings toggle)"
fi
echo "    -> proxytun on 192.168.1.5        (your proxy's IP[:PORT])"
echo "    -> proxytun status"
