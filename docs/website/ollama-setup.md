---
title: Ollama Setup
description: Use Mycelos with local LLM models via Ollama — free, private, no cloud
order: 12
---

# Using Mycelos with Ollama

Run Mycelos completely locally with [Ollama](https://ollama.com) — no cloud API keys, no costs, full privacy. Your data never leaves your machine.

## Install Ollama

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# Download from https://ollama.com/download
```

## Pull a Model

```bash
# Recommended for most machines (4GB RAM)
ollama pull gemma3:4b

# For more capable responses (8GB+ RAM)
ollama pull llama3.3

# For coding tasks
ollama pull devstral
```

## Configure Mycelos

During `mycelos init`, choose Ollama as your provider:

```bash
export OLLAMA_API_BASE=http://localhost:11434
mycelos init
# When asked for provider, enter the Ollama URL
```

Or add Ollama to an existing setup:

```bash
mycelos credential store ollama
# Enter: http://localhost:11434
```

## Network Setup (Access from Other Devices)

By default, Ollama only listens on localhost. To use it from another machine (e.g., Mycelos on a Raspberry Pi, Ollama on a Mac):

```bash
# On the machine running Ollama:
OLLAMA_HOST=0.0.0.0 ollama serve
```

To make this permanent:

```bash
# macOS (if running as app)
launchctl setenv OLLAMA_HOST 0.0.0.0
# Then restart Ollama app

# macOS / Linux (command line)
echo 'export OLLAMA_HOST=0.0.0.0' >> ~/.zshrc
# Then: source ~/.zshrc && ollama serve

# Linux (systemd service)
sudo systemctl edit ollama
# Add: Environment=OLLAMA_HOST=0.0.0.0
sudo systemctl restart ollama
```

Then configure Mycelos to point to the Ollama server:

```bash
export OLLAMA_API_BASE=http://192.168.1.42:11434  # Ollama machine IP
mycelos serve
```

## Recommended Models

| Use Case | Model | RAM Needed | Quality |
|----------|-------|-----------|---------|
| Quick tasks | `gemma2:2b` | 2GB | Basic |
| General assistant | `gemma3:4b` | 4GB | Good |
| Conversations | `llama3.3` | 8GB | Very Good |
| Coding | `devstral` | 8GB | Excellent |
| Reasoning | `deepseek-r1:8b` | 8GB | Very Good |
| Documents | `qwen3.5:9b` | 12GB | Excellent |

## LM Studio (Alternative)

[LM Studio](https://lmstudio.ai) provides a GUI for running local models and exposes an OpenAI-compatible API. Mycelos works with LM Studio out of the box:

```bash
# LM Studio starts a server at localhost:1234 by default
# Configure as OpenAI provider with local URL:
export OPENAI_API_BASE=http://localhost:1234/v1
mycelos credential store openai
# Enter any string as API key (LM Studio ignores it)
```

LM Studio advantages:
- Visual model browser and download manager
- GPU acceleration setup is easier (especially on Mac)
- Chat interface for testing models before using with Mycelos

## Troubleshooting

### "Model too large" / Process killed
Your machine doesn't have enough RAM. Try a smaller model:
```bash
ollama pull gemma2:2b  # Only needs 2GB
```

### "Connection refused"
Ollama isn't running or is on a different port:
```bash
# Check if Ollama is running
curl http://localhost:11434
# Should respond: "Ollama is running"

# If not, start it:
ollama serve
```

### "Slow responses"
Local models are slower than cloud APIs. Tips:
- Use smaller models (2B-4B parameters)
- Enable GPU acceleration (Ollama auto-detects)
- Use an SSD instead of HDD for model storage
- Close other applications to free RAM

### Network: "Can't connect from other device"
```bash
# Verify Ollama listens on all interfaces
curl http://YOUR_IP:11434
# If this fails, Ollama is still on localhost only
# Set OLLAMA_HOST=0.0.0.0 and restart
```
