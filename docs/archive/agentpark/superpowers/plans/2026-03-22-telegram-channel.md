# Telegram Channel — Erster externer Channel

**Goal:** User kann mit Mycelos ueber Telegram chatten. Gleiche Funktionalitaet wie Terminal — Tool Use, Memory, Slash Commands.

---

## Architektur

```
Telegram User
    ↓ (Webhook)
FastAPI Gateway (/telegram/webhook)
    ↓
aiogram Dispatcher
    ↓
ChatService.handle_message()
    ↓ (gleicher Code wie Terminal)
ChatEvents → Telegram Message
```

**aiogram** (v3.26+) — Python-native, fully async, mountet direkt in FastAPI als Webhook.

Kein separater Prozess, kein Node.js. Der Gateway hat einfach einen zusaetzlichen Endpoint.

---

## Setup-Flow

```
/connector setup telegram

  1. Erstelle einen Bot bei @BotFather in Telegram
  2. Kopiere den Bot-Token (z.B. 123456:ABC-DEF...)
  3. Bot-Token eingeben: ********

  Bot verbunden! Setze den Webhook mit:
    mycelos serve (Gateway muss laufen)
    oder: manuell auf https://your-domain/telegram/webhook

  Teste mit: Schreibe deinem Bot in Telegram!
```

---

## Tasks

### Task 1: aiogram Dependency + Telegram Channel Adapter

- Add `aiogram>=3.26` to pyproject.toml
- Create `src/mycelos/channels/__init__.py`
- Create `src/mycelos/channels/telegram.py`

### Task 2: Gateway Webhook Endpoint

- Add `/telegram/webhook` POST route to Gateway
- Webhook receives Telegram Updates, dispatches to ChatService

### Task 3: `/connector setup telegram`

- Slash Command fragt nach Bot-Token
- Token wird verschluesselt im Credential Proxy gespeichert

### Task 4: Event Rendering fuer Telegram

- ChatEvents → Telegram-Nachrichten (Markdown, Buttons)
- Streaming: Post + Edit Pattern

### Task 5: Tests

---

## Reihenfolge

```
Task 1 (aiogram + Adapter) → Task 2 (Webhook) → Task 3 (Setup) → Task 4 (Rendering) → Task 5 (Tests)
```
