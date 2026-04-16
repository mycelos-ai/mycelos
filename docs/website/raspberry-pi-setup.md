---
title: Raspberry Pi Setup
description: Run Mycelos on a Raspberry Pi with network access from your phone or laptop
order: 11
---

# Running Mycelos on a Raspberry Pi

Mycelos runs beautifully on a Raspberry Pi — your own private AI assistant on a $50 device. Access it from any device on your home network: phone, tablet, laptop.

## Requirements

- Raspberry Pi 4 or 5 (4GB+ RAM recommended)
- Python 3.12+
- Node.js 18+ (for MCP connectors)
- Ollama (optional, for local LLM — see [Ollama Guide](/docs/ollama-setup))

## Installation

```bash
# Install Python 3.12+ (if not already available)
sudo apt update
sudo apt install python3.12 python3.12-pip nodejs npm

# Install Mycelos
pip install mycelos

# Initialize
export MYCELOS_MASTER_KEY=$(openssl rand -hex 32)
mycelos init
```

## Network Access

By default, Mycelos only listens on localhost. To access it from other devices:

```bash
# Listen on all interfaces (accessible from your home network)
mycelos serve --host 0.0.0.0 --port 9100 --password your-secret-password
```

Then open from any device on your network:
```
http://raspberrypi.local:9100
```

Or use the IP address directly:
```
http://192.168.1.42:9100
```

### Security Notes

- `--password` enables Basic Auth — recommended for network access
- Your home router blocks external access (no internet exposure)
- The password protects against other devices on your network (e.g., guests)
- For extra security, bind to a specific IP: `--host 192.168.1.42`

## Autostart on Boot

Create a systemd service so Mycelos starts automatically:

```bash
sudo nano /etc/systemd/system/mycelos.service
```

```ini
[Unit]
Description=Mycelos AI Assistant
After=network.target

[Service]
Type=simple
User=pi
Environment=MYCELOS_MASTER_KEY=your-master-key-here
ExecStart=/usr/local/bin/mycelos serve --host 0.0.0.0 --port 9100 --password your-password
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable mycelos
sudo systemctl start mycelos
```

## Using with Ollama (Local LLM)

> **Note:** Running LLMs directly on a Raspberry Pi is possible but very slow.
> For a better local LLM experience, run Ollama on a **Mac Mini** (Apple Silicon)
> or another machine on your network, and point Mycelos to it:
> `mycelos model add ollama --api-base http://mac-mini.local:11434`

For a basic offline setup on the Pi:

1. Install Ollama: `curl -fsSL https://ollama.com/install.sh | sh`
2. Pull a small model: `ollama pull gemma2:2b`
3. Configure Mycelos to use Ollama (see [Ollama Guide](/docs/ollama-setup))

### Recommended Models

| Device | RAM | Model | Size | Speed |
|--------|-----|-------|------|-------|
| Raspberry Pi | 4GB | `gemma2:2b` | 1.6GB | Slow but works |
| Raspberry Pi | 8GB | `phi3:mini` | 2.3GB | Slow |
| **Mac Mini M1+** | **8GB+** | `gemma3:4b` | 3.3GB | **Fast** |
| **Mac Mini M1+** | **16GB+** | `llama3:latest` | 4.7GB | **Fast** |

**Recommended setup:** Run Mycelos on the Raspberry Pi (low power, always on) and Ollama on a Mac Mini on the same network. Best of both worlds.

## Troubleshooting

If something isn't working, use the built-in diagnostic tool:

```bash
mycelos doctor              # Quick health check
mycelos doctor --why        # LLM-powered diagnosis (interactive)
```

The doctor analyzes your system state, audit logs, and configuration to find root causes.

## Tips

- Use Telegram to chat with Mycelos from your phone (no browser needed)
- Set up Mycelos as a `.local` mDNS service for easy discovery
- Use a USB SSD instead of the SD card for better database performance
- The Web UI works great on mobile browsers
