---
title: Raspberry Pi Setup
description: Run Mycelos on a Raspberry Pi with network access from your phone or laptop
order: 11
---

# Running Mycelos on a Raspberry Pi

Mycelos runs beautifully on a Raspberry Pi — your own private AI assistant on a $50 device. Access it from any device on your home network: phone, tablet, laptop.

## Requirements

- Raspberry Pi 4 or 5 (4GB+ RAM recommended, 8GB on the Pi 5 if you run anything besides Mycelos)
- Raspberry Pi OS (64-bit)
- **Docker + Compose v2** — see the [official install guide](https://docs.docker.com/engine/install/debian/)
- Ollama on another machine (optional, for local LLM — see [Ollama Guide](/docs/ollama-setup))

Nothing else needs to be on the Pi. Python, Node, and every runtime dependency ship inside the container image.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/mycelos-ai/mycelos/main/scripts/install.sh | bash
```

That's the whole install. It creates `./data/`, generates the master key + proxy token, pulls the Mycelos image, and brings up the gateway + proxy containers. Expect 1-2 minutes on a Pi 5 (image pull dominates).

## Network access

By default the gateway binds to `127.0.0.1` inside the Pi, which means no other device on your network can reach it yet. To open it up to your home network:

```bash
cd ~/mycelos-new   # wherever install.sh placed docker-compose.yml
nano .env
```

Add or uncomment these two lines:

```env
MYCELOS_BIND=0.0.0.0
MYCELOS_PASSWORD=a-long-random-password-you-pick-now
```

Then apply:

```bash
mycelos restart
```

Open from any device on your network:

```
http://raspberrypi.local:9100
```

…or the IP directly: `http://192.168.1.42:9100`. Your browser will ask for a username (leave blank) and the password you set.

### Security notes

- **`MYCELOS_PASSWORD` is the only authentication in v0.3.** Make it long and random.
- **LAN-only is the safe scope.** Your home router already blocks external access.
- **Do not port-forward** the gateway to the public internet in v0.3 — put it behind a TLS-terminating reverse proxy (Caddy, Tailscale serve, Cloudflare tunnel) if you need off-LAN access.
- Passkey-based web auth ships in Phase 2.

## Autostart on boot

Docker handles this automatically. The `docker-compose.yml` sets `restart: unless-stopped`, so both containers come back on reboot. No systemd unit needed.

If Docker itself doesn't start at boot:

```bash
sudo systemctl enable docker
```

## Using with Ollama (local LLM)

> **Note:** Running LLMs directly on a Raspberry Pi is possible but very slow.
> For a usable local LLM experience, run Ollama on a **Mac Mini** (Apple Silicon)
> or another GPU-capable machine on your network, and point Mycelos to it via the
> web UI's provider setup.

For a basic offline setup on the Pi itself:

1. Install Ollama: `curl -fsSL https://ollama.com/install.sh | sh`
2. Pull a small model: `ollama pull gemma2:2b`
3. Configure Mycelos to use it — see the [Ollama Guide](/docs/ollama-setup).

### Recommended models

| Device | RAM | Model | Size | Speed |
|--------|-----|-------|------|-------|
| Raspberry Pi | 4GB | `gemma2:2b` | 1.6GB | Slow but works |
| Raspberry Pi | 8GB | `phi3:mini` | 2.3GB | Slow |
| **Mac Mini M1+** | **8GB+** | `gemma3:4b` | 3.3GB | **Fast** |
| **Mac Mini M1+** | **16GB+** | `llama3:latest` | 4.7GB | **Fast** |

**Recommended setup:** run Mycelos on the Raspberry Pi (low power, always on) and Ollama on a Mac Mini on the same network.

## Troubleshooting

If something isn't working, use the built-in diagnostic tool:

```bash
mycelos doctor              # Quick health check
mycelos doctor --why        # LLM-powered diagnosis (interactive)
mycelos logs -f             # Follow gateway + proxy logs
```

The doctor analyzes your system state, audit logs, and configuration to find root causes.

### `pi5.local` pings but curl / the browser hangs from the Mac

Happens on macOS when `ping pi5.local` succeeds but `curl http://pi5.local:9100` times out, while the IP works:

```bash
curl http://192.168.0.111:9100   # works
curl http://pi5.local:9100       # hangs
```

Usually the Mac's mDNS responder is sitting on a stale record (often an IPv6 address that is no longer valid). Clear it:

```bash
# on the Mac
sudo killall -HUP mDNSResponder
sudo killall mDNSResponderHelper
curl http://pi5.local:9100
```

If that doesn't help, force IPv4: `curl -4 http://pi5.local:9100`. For a permanent fix pin the hostname in `/etc/hosts` on the Mac:

```bash
sudo sh -c 'echo "192.168.0.111  pi5.local pi5" >> /etc/hosts'
```

### `pi5.local` resolves to a Docker address

Avahi on the Pi announces `.local` on every interface by default — including Docker's virtual `vethX` interfaces, which confuses clients on the LAN. Restrict Avahi to the real LAN interface:

```bash
sudo sed -i 's/^#allow-interfaces=.*/allow-interfaces=eth0/' /etc/avahi/avahi-daemon.conf
sudo systemctl restart avahi-daemon
```

(Change `eth0` to `wlan0` if the Pi is on Wi-Fi — `ip -br link | grep UP` shows which.)

### Port 9100 open on the Pi but times out from the Mac

After toggling `MYCELOS_BIND` in `.env`, Docker compose needs to recreate the container, not just restart it — the `ports:` mapping is set at container creation time:

```bash
cd ~/mycelos-new
docker compose up -d --force-recreate
```

`mycelos update` also triggers a recreate via `docker compose pull && up -d`.

## Tips

- Use **Telegram** to chat with Mycelos from your phone (no browser needed — see [connectors](/docs/connectors)).
- Register the Pi with Bonjour/mDNS so `raspberrypi.local` resolves on iOS and macOS.
- Put the data volume on a USB SSD instead of the SD card for better database performance: bind-mount your SSD path with `MYCELOS_DATA_DIR=/mnt/ssd/mycelos` in `.env`.
- The web UI works well on mobile browsers — Mycelos is PWA-friendly.
