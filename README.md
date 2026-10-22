# proxytun

[![CI](https://github.com/alizn7/proxytun/actions/workflows/ci.yml/badge.svg)](https://github.com/alizn7/proxytun/actions/workflows/ci.yml)

Send **all** traffic on a Linux machine through a SOCKS5 or HTTP proxy — terminal, `apt`, `snap`, Docker, Electron apps, everything — with one command:

```bash
proxytun on 192.168.1.5
```

[فارسی](README.fa.md)

## Why

Desktop proxy settings (GNOME → Network → Proxy) only reach apps that choose to read them. CLI tools, package managers, Docker and many desktop apps ignore them. Environment variables help a bit more but still miss anything that doesn't honour `http_proxy`.

proxytun works at the network layer instead: it creates a TUN interface and routes the whole system into it, so nothing can bypass the proxy. It was built for the common setup where a phone shares its connection as a proxy (for example with *Every Proxy*) and a laptop needs to use it system-wide, but it works with any SOCKS5 or HTTP proxy.

## Features

- **System-wide** — a TUN device powered by [sing-box](https://github.com/SagerNet/sing-box) captures all IPv4 traffic.
- **No DNS leaks** — DNS is hijacked and resolved over TCP *through* the proxy, so answers are never poisoned locally.
- **UDP auto-detection** — UDP is relayed when the proxy supports SOCKS5 UDP ASSOCIATE; otherwise it is rejected instantly so apps fall back to TCP instead of hanging.
- **Health checks** — if the proxy disappears (phone disconnected, IP changed), traffic falls back to direct and recovers automatically when the proxy is back. With `--kill-switch` traffic is blocked instead.
- **Profiles** — `proxytun on phone`, `proxytun on office`.
- **GNOME Quick Settings toggle** — on/off and profile switching from the panel, no password prompt.
- **Survives reboots** — the last profile starts at boot until you run `proxytun off`.
- **Clean install/uninstall** — everything lives in a handful of well-known paths.

## How it works

```
 apps ─▶ kernel routing ─▶ proxytun0 (TUN) ─▶ sing-box ─▶ SOCKS5/HTTP proxy ─▶ internet
                                              │
                    LAN, localhost and the proxy itself go direct
```

A small daemon (`proxytun@<profile>.service`) supervises sing-box: it probes the proxy every few seconds, generates the sing-box config (including UDP support detected at start), and publishes its state in `/run/proxytun/status.json` for the CLI and the GNOME toggle.

## Install

Requirements: Linux with systemd, Python ≥ 3.9, curl. sing-box ≥ 1.12 is installed automatically (from the SagerNet apt repo on Debian/Ubuntu, otherwise from GitHub releases).

```bash
git clone https://github.com/alizn7/proxytun
cd proxytun
sudo ./install.sh
```

If downloads are blocked on your network, let the installer use your proxy:

```bash
sudo ./install.sh --proxy socks5h://192.168.1.5:1080
```

Then log out and back in once (for the `proxytun` group), and on GNOME enable the toggle:

```bash
gnome-extensions enable proxytun@alizn7.github.io
```

## Usage

| Task | Command |
|---|---|
| Turn on with a new address (SOCKS5, port 1080) | `proxytun on 192.168.1.5` |
| Different port | `proxytun on 192.168.1.5:1090` |
| HTTP proxy | `proxytun on 192.168.1.5:8080 -t http` |
| With login | `proxytun on 192.168.1.5 -u USER -p PASS` |
| Save under a name / use it later | `proxytun on 192.168.1.5 -n phone` → `proxytun on phone` |
| Turn on the last profile | `proxytun on` |
| Block traffic while the proxy is down | `proxytun on phone --kill-switch` |
| Turn off (and don't start at boot) | `proxytun off` |
| State and exit IP | `proxytun status` |
| Profiles | `proxytun list`, `proxytun add NAME HOST[:PORT]`, `proxytun rm NAME` |
| Check a proxy without changing anything | `proxytun test 192.168.1.5` |
| Logs | `proxytun logs -f` |

When the phone's IP changes, just run `proxytun on NEW_IP` — the port and other settings of the profile are kept.

`proxytun on` refuses to switch to a proxy it cannot reach, so a typo never leaves you offline (`--force` overrides).

### States

| State | Meaning |
|---|---|
| `active` | all traffic goes through the proxy |
| `fallback` | proxy unreachable — traffic goes direct until it is back |
| `blocked` | proxy unreachable — traffic blocked (kill switch) |
| `error` | sing-box keeps exiting — see `proxytun logs` |

### GNOME Quick Settings

The **Proxy** toggle turns the last profile on or off; the arrow lists all profiles. The panel shows an icon while proxytun is running. The toggle only affects the current session; whether proxytun starts at boot follows the last `proxytun on` / `proxytun off` from the CLI.

## Limitations

- The proxy address must be an IPv4 address.
- IPv6 traffic is blocked while proxytun is active (prevents leaks).
- `ping` (ICMP) is not proxied; use `proxytun status` or `curl` to test.
- UDP works only if the proxy relays it (many phone proxy apps don't).
- Passwords given with `-p` end up in your shell history; profiles are stored in `/etc/proxytun/profiles.json` (mode 0640, root:proxytun).

## Files

| Path | Purpose |
|---|---|
| `/usr/local/bin/proxytun` | CLI and daemon |
| `/etc/systemd/system/proxytun@.service` | one instance per profile |
| `/etc/systemd/system/proxytun-boot.service` | starts the last profile at boot |
| `/etc/polkit-1/rules.d/50-proxytun.rules` | lets the `proxytun` group start/stop instances without a password |
| `/etc/proxytun/profiles.json` | saved profiles |
| `/run/proxytun/` | live status and generated sing-box config |
| `/usr/share/gnome-shell/extensions/proxytun@alizn7.github.io/` | Quick Settings toggle |

## Uninstall

```bash
sudo ./uninstall.sh            # keep profiles
sudo ./uninstall.sh --purge    # remove profiles and the group too
```

sing-box itself is left installed.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests run a fake SOCKS5 server to exercise the probes (handshake, auth, UDP detection) and the daemon's state machine (active → fallback → recovery, kill switch). When `sing-box` is on `PATH`, generated configs are also validated with `sing-box check`. CI runs all of this plus ShellCheck on every push.

## License

MIT
