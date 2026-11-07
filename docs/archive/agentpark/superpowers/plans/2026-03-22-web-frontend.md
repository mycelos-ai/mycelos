# Web Frontend — Next.js Chat UI mit Mycelos Gateway

**Status:** PLAN (nicht implementieren)

---

## Vision

Ein schoenes, modernes Web-UI das gegen den Mycelos Gateway spricht. Funktioniert lokal und remote. Authentication via Telegram OTP wenn nicht-localhost.

---

## Architektur

```
Browser (Next.js App)
    ↓ SSE (Vercel AI SDK useChat)
Mycelos Gateway (FastAPI, localhost:9100)
    ↓
ChatService → LLM → Tools → Response
```

### Lokal (localhost)
- Kein Login noetig — Gateway laeuft auf 127.0.0.1
- `mycelos serve --web` startet Gateway + oeffnet Browser

### Remote (Server, Cloud)
- Login erforderlich — OTP via Telegram
- User oeffnet Web UI → gibt Telegram-Username ein
- Mycelos sendet 6-stelligen Code an Telegram Bot
- User gibt Code im Browser ein → Session-Token
- Token als HTTP-Only Cookie, 24h gueltig

---

## Authentication: Telegram OTP

```
Browser                    Mycelos Gateway                Telegram Bot
   |                            |                            |
   |-- POST /auth/request ----->|                            |
   |   {telegram_user: "@stefan"}                            |
   |                            |-- sendMessage ------------->|
   |                            |   "Dein Login-Code: 482916"|
   |                            |                            |
   |-- POST /auth/verify ------>|                            |
   |   {code: "482916"}         |                            |
   |                            |                            |
   |<-- Set-Cookie: session ----|                            |
   |    (HTTP-Only, Secure)     |                            |
```

### Warum Telegram OTP?

- User hat Telegram bereits eingerichtet (Onboarding)
- Kein Passwort noetig (Zero-Knowledge)
- Kein Email-Setup noetig
- 2FA-aehnlich (Besitz des Telegram-Accounts)
- Code 5 Min gueltig, einmalig verwendbar

### Fallback wenn kein Telegram

- Einfaches Passwort-Login (`mycelos web set-password`)
- Oder: nur localhost (kein Remote-Zugriff)

---

## Tech Stack

```
Frontend:         Next.js 14+ (App Router)
UI Components:    shadcn/ui (Tailwind, accessible)
Chat:             Vercel AI SDK (useChat hook + SSE)
State:            React Context (kein Redux)
Styling:          Tailwind CSS
Icons:            Lucide React
Deployment:       Static export + Mycelos serves it, oder separat
```

### Vercel AI SDK Integration

```typescript
// app/page.tsx
import { useChat } from 'ai/react'

export default function Chat() {
  const { messages, input, handleInputChange, handleSubmit, isLoading } = useChat({
    api: '/api/chat',  // Proxy zu Mycelos Gateway
  })

  return (
    <div className="flex flex-col h-screen">
      <ChatMessages messages={messages} />
      <ChatInput
        value={input}
        onChange={handleInputChange}
        onSubmit={handleSubmit}
        isLoading={isLoading}
      />
    </div>
  )
}
```

### SSE Proxy (Next.js API Route → Mycelos Gateway)

```typescript
// app/api/chat/route.ts
export async function POST(req: Request) {
  const { messages } = await req.json()
  const lastMessage = messages[messages.length - 1].content

  const response = await fetch('http://localhost:9100/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: lastMessage }),
  })

  // Forward SSE stream to client
  return new Response(response.body, {
    headers: { 'Content-Type': 'text/event-stream' },
  })
}
```

---

## UI Design

### Layout

```
┌─────────────────────────────────────────────────┐
│ ┌─ Sidebar ──┐ ┌─ Main ─────────────────────┐  │
│ │            │ │                              │  │
│ │ Sessions   │ │  Chat Messages               │  │
│ │ ─────────  │ │                              │  │
│ │ Session 1  │ │  Mycelos> Hallo Stefan!       │  │
│ │ Session 2  │ │                              │  │
│ │ Session 3  │ │  You> Such nach AI News      │  │
│ │            │ │                              │  │
│ │ ─────────  │ │  Mycelos> [searching...]      │  │
│ │ Quick      │ │  Hier sind die Ergebnisse:   │  │
│ │ Actions    │ │  - Article 1                 │  │
│ │ ─────────  │ │  - Article 2                 │  │
│ │ /memory    │ │                              │  │
│ │ /config    │ │                              │  │
│ │ /cost      │ │ ┌───────────────────────┐    │  │
│ │            │ │ │ Type a message...  [⏎] │    │  │
│ └────────────┘ │ └───────────────────────┘    │  │
│                └──────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### Features

1. **Chat** — Markdown rendering, Code highlighting, streaming
2. **Sidebar** — Session-Liste, Quick Actions (Slash Commands als Buttons)
3. **System Panel** — Config, Agents, Connectors, Mounts (readonly)
4. **Cost Display** — Live Token-Counter + Kosten oben rechts
5. **Slash Command Palette** — Cmd+K fuer Quick-Command-Suche
6. **Dark/Light Mode** — System-Preference oder Toggle
7. **Mobile Responsive** — Sidebar collapsed auf Mobile

### Besondere UI-Elemente

**Tool-Call Anzeige:**
```
┌── search_web ──────────────────────┐
│ 🔍 Searching: "AI News 2026"      │
│ ████████████░░░░ Loading...        │
└────────────────────────────────────┘
```

**Confirmable Command:**
```
┌── Vorgeschlagener Befehl ──────────┐
│ /mount add ~/Documents --rw        │
│                                    │
│ [Ausfuehren]  [Abbrechen]         │
└────────────────────────────────────┘
```

**Cost Badge:**
```
[🪙 $0.0045 | 150 tokens | claude-sonnet-4-6]
```

---

## Sicherheit

### Localhost (Default)

- Gateway bindet auf 127.0.0.1 (nicht 0.0.0.0)
- Kein Login, kein Token
- Browser und Gateway auf gleicher Maschine

### Remote (--host 0.0.0.0)

- **HTTPS erforderlich** (Let's Encrypt oder Reverse Proxy)
- **Telegram OTP** fuer Login
- **Session-Token** als HTTP-Only Cookie (nicht im JavaScript zugaenglich)
- **CSRF Protection** via Same-Site Cookie + Origin-Check
- **Rate Limiting** auf /auth/* Endpoints (max 5 Versuche / 5 Min)
- **IP Allowlist** optional (`mycelos serve --allow-ip 192.168.1.0/24`)

### API Endpoints (erweitert)

```
POST /auth/request        ← Telegram OTP anfordern
POST /auth/verify         ← Code verifizieren, Session-Cookie setzen
GET  /auth/status         ← Login-Status pruefen
POST /auth/logout         ← Session beenden

POST /api/chat            ← Chat (auth required wenn remote)
GET  /api/health          ← Kein Auth
GET  /api/config          ← Auth required
GET  /api/sessions        ← Auth required
```

---

## Deployment-Optionen

### Option A: Mycelos served alles

```bash
mycelos serve --web          # Gateway + Web UI aus einem Prozess
```

- Next.js Static Export in `src/mycelos/web/dist/`
- FastAPI served die statischen Files + API
- Ein Port, ein Prozess

### Option B: Separates Frontend

```bash
mycelos serve                # Gateway auf :9100
cd web && npm run dev       # Next.js auf :3000
```

- Next.js Proxy → Gateway
- Unabhaengig deploybar
- Besser fuer Entwicklung

### Option C: Docker Compose

```yaml
services:
  mycelos:
    image: mycelos:latest
    ports: ["9100:9100"]
  web:
    image: mycelos-web:latest
    ports: ["3000:3000"]
    environment:
      MYCELOS_GATEWAY: http://mycelos:9100
```

---

## Implementation Tasks (wenn wir es bauen)

### Phase 1: Grundgeruest (2-3 Tage)
1. Next.js Projekt mit shadcn/ui + Tailwind
2. Chat-Seite mit useChat + SSE Proxy
3. Markdown Rendering (react-markdown)
4. Session-Sidebar (GET /api/sessions)
5. Dark/Light Mode

### Phase 2: Features (2-3 Tage)
6. Slash Command Palette (Cmd+K)
7. Confirmable Commands als UI-Buttons
8. Tool-Call Progress Anzeige
9. Cost Badge
10. System Panel (Config, Agents, Connectors)

### Phase 3: Auth (1-2 Tage)
11. Telegram OTP Flow
12. Session-Cookie Management
13. Protected Routes
14. Rate Limiting

### Phase 4: Polish (1-2 Tage)
15. Mobile Layout
16. Animationen (Framer Motion)
17. Error States
18. Loading Skeletons
19. PWA Support (Offline, Add to Homescreen)

---

## Offene Fragen

1. **Mycelos served oder separates Frontend?** Option A ist einfacher, Option B flexibler
2. **SSE direkt oder Vercel AI SDK Data Stream Protocol?** SDK hat mehr Features (tool rendering)
3. **Statische Files im Python-Package?** Oder als separates npm-Package?
4. **PWA?** Wuerde offline Nachrichten-Draft ermoeglichen
5. **Multi-User?** Aktuell user_id="default" — Web-UI braucht echte User
