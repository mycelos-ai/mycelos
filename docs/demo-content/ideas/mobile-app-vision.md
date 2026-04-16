# Mobile App — Vision

## Why Mobile?
Mycelos runs on your machine, but you're not always at your desk. A mobile companion app gives you access to your agent network from anywhere.

## Core Features (v1)

### Chat
- Talk to Mycelos from your phone
- Voice input with Whisper transcription
- Push notifications for completed tasks

### Knowledge
- Browse and search your knowledge base
- Quick capture — save ideas, links, photos
- Read notes offline (sync when connected)

### Tasks
- View and manage task lists
- Mark tasks complete
- Get reminded about due items

## Architecture
```
Phone App (React Native)
    ↕ HTTPS
Mycelos Gateway (home server / Pi)
    ↕
Local Mycelos Instance
```

## Key Decisions
- **Not a cloud app** — connects directly to your Mycelos instance
- **Tailscale/WireGuard** for secure remote access
- **Offline-first** — knowledge cached locally, syncs on connect
- **No LLM on phone** — all AI processing on the server

## Timeline
- May: Design + prototype
- June: Alpha (chat only)
- July: Beta (chat + knowledge)
- August: Public release
