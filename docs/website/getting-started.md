---
title: Getting Started
description: Install Mycelos, initialize your database, and start your first chat session.
order: 1
icon: rocket_launch
---

## Prerequisites

- **Python 3.12+** with pip
- **Node.js** (for MCP connector servers)
- **API key** from any supported provider (Anthropic, OpenAI, Google, or a local Ollama instance)

## Installation

Clone the repository and install in development mode:

```bash
git clone https://github.com/your-org/mycelos.git
cd mycelos
pip install -e ".[dev]"
```

## First Run

Initialize the database, start the Gateway, and open the Web UI:

```bash
# Initialize Mycelos (creates database, config, master key)
mycelos init

# Start the Gateway server
mycelos serve

# Open in your browser
open http://localhost:9100
```

## Quick Chat (CLI Mode)

If you prefer the terminal, start an interactive session directly:

```bash
mycelos chat
```

CLI mode talks directly to the LLM without the Gateway. The Web UI requires `mycelos serve` to be running.
