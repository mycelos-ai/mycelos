---
title: Ollama Setup
description: Use Mycelos with local LLM models via Ollama — free, private, no cloud
order: 12
---

# Using Mycelos with Ollama

Run Mycelos completely locally with [Ollama](https://ollama.com) — no cloud API keys, no costs, full privacy. Your data never leaves your machine.

## Install Ollama

Ollama runs on the **host**, not inside the Mycelos container — the gateway talks to it over the network.

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# Download from https://ollama.com/download
```

## Pull a model

```bash
# Recommended for most machines (4GB RAM)
ollama pull gemma3:4b

# For more capable responses (8GB+ RAM)
ollama pull llama3.3

# For coding tasks
ollama pull devstral
```

## Make Ollama reachable from the Mycelos container

The Mycelos containers are on an isolated Docker network (`mycelos-internal`) and cannot reach `localhost` on the host. You must point Ollama at all interfaces:

```bash
# On the machine running Ollama:
OLLAMA_HOST=0.0.0.0 ollama serve
```

To make this permanent:

- **macOS app:** `launchctl setenv OLLAMA_HOST 0.0.0.0`, then restart the Ollama app.
- **Linux (systemd):** `sudo systemctl edit ollama`, add `Environment=OLLAMA_HOST=0.0.0.0`, `sudo systemctl restart ollama`.
- **Shell one-off:** `OLLAMA_HOST=0.0.0.0 ollama serve`.

From inside the Mycelos container, the host is reachable as `host.docker.internal` (Docker Desktop on macOS/Windows) or the host's LAN IP (Linux — typically `172.17.0.1` for the default bridge, or your machine's LAN IP if Ollama is on another box).

## Configure Mycelos

Open the web UI at **http://localhost:9100** and walk the provider setup wizard. When it asks which provider to use, choose **Ollama** and give the URL:

```
http://host.docker.internal:11434       # Docker Desktop (macOS / Windows)
http://192.168.1.42:11434               # Ollama on another LAN host
```

The URL is stored in memory (scope: `system`, key: `provider.ollama.url`) and flows to every LLM call through the SecurityProxy. No plaintext credentials are needed — Ollama is unauthenticated, and the URL itself isn't a secret.

If you prefer the CLI:

```bash
mycelos credential store ollama_url http://host.docker.internal:11434
# or, for a bare endpoint string in memory:
mycelos chat
# then in the chat: /memory set provider.ollama.url http://host.docker.internal:11434
```

## Recommended models

| Use case | Model | RAM needed | Quality |
|----------|-------|-----------|---------|
| Quick tasks | `gemma2:2b` | 2GB | Basic |
| General assistant | `gemma3:4b` | 4GB | Good |
| Conversations | `llama3.3` | 8GB | Very good |
| Coding | `devstral` | 8GB | Excellent |
| Reasoning | `deepseek-r1:8b` | 8GB | Very good |
| Documents | `qwen3.5:9b` | 12GB | Excellent |

## LM Studio (alternative)

[LM Studio](https://lmstudio.ai) provides a GUI for running local models and exposes an OpenAI-compatible API. Mycelos works with it out of the box:

1. Start the LM Studio server (defaults to `http://localhost:1234`).
2. Make sure it binds to all interfaces (LM Studio settings → "Serve on local network").
3. In the Mycelos web UI provider setup, choose **OpenAI-compatible** and point the base URL at LM Studio.
4. The API key field can be any non-empty string — LM Studio ignores it.

LM Studio advantages:

- Visual model browser and download manager
- GPU acceleration setup is easier (especially on Mac)
- Chat interface for testing models before using them with Mycelos

## Troubleshooting

### "Model too large" / process killed
Not enough RAM on the Ollama host. Try a smaller model:

```bash
ollama pull gemma2:2b  # only needs 2GB
```

### "Connection refused" from Mycelos
The gateway container can't reach Ollama. Check from inside the gateway:

```bash
mycelos shell
curl http://host.docker.internal:11434   # macOS / Windows
# or the LAN IP
```

If this fails, Ollama is on localhost only — set `OLLAMA_HOST=0.0.0.0` and restart it.

### "Slow responses"
Local models are slower than cloud APIs. Tips:

- Use smaller models (2B-4B parameters)
- Ensure GPU acceleration works (Ollama auto-detects; check `ollama ps` while generating)
- Store models on an SSD rather than HDD
- Close other applications to free RAM
