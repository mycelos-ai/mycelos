## OpenClaw High-Level Architecture Breakdown

### Purpose

This document explains OpenClaw at a high level and shows how the major layers fit together: CLI entry, Gateway runtime, channels, plugins, routing, session persistence, media understanding, and operator control surfaces.

---

## 1. System landscape

```mermaid
flowchart LR
    U[User / Operator] --> C[CLI + Control Clients]
    C --> G[OpenClaw Gateway Runtime]
    M[Messaging Channels] --> G
    G --> R[Routing + Session Resolution]
    R --> A[Agent Orchestration]
    A --> P[Providers / Tools / Hooks]
    A --> S[Session Store + Transcripts]
    G --> X[Plugin Registry + Runtime]
    X --> CH[Channel Plugins]
    X --> SV[Services / Routes / CLI Extensions]
    A --> MU[Media Understanding Pipeline]
    G --> N[Nodes + WebSocket Clients]
```

### Interpretation

- The CLI is a control surface, not the architectural center.
- The Gateway is the composition root and runtime hub.
- Routing connects inbound events to agent identity and session identity.
- Plugins provide most extensibility around channels, tools, services, and routes.
- Session persistence makes the system stateful across turns.

---

## 2. Startup flow

```mermaid
sequenceDiagram
    participant User
    participant Entry as entry.ts
    participant CLI as run-main.ts
    participant Registry as Command Registry
    participant Gateway as Gateway Runtime
    participant Plugins as Plugin Loader

    User->>Entry: Invoke openclaw
    Entry->>CLI: Normalize env and hand off
    CLI->>Registry: Build lazy command surface
    Registry-->>CLI: Resolve requested command
    CLI->>Gateway: Start gateway or command runtime
    Gateway->>Plugins: Create plugin runtime and load plugins
    Plugins-->>Gateway: Register channels / tools / services / routes
    Gateway-->>User: Runtime ready for clients and channels
```

### Interpretation

OpenClaw startup is layered. The executable entry point normalizes the process, the CLI resolves the requested capability, and the Gateway assembles the long-lived runtime when needed.

---

## 3. Runtime layering

```mermaid
flowchart TD
    L1[Surface Layer] --> L2[Gateway Coordination Layer]
    L2 --> L3[Core Runtime Services]
    L3 --> L4[External Integrations]

    L1 --> CLI[CLI Commands]
    L1 --> WS[WebSocket / HTTP Clients]
    L1 --> MSG[Inbound Channel Messages]

    L2 --> GW[Gateway Server]
    L2 --> CM[Channel Manager]
    L2 --> PR[Plugin Runtime]
    L2 --> NR[Node Registry]

    L3 --> RT[Routing]
    L3 --> AG[Agent Orchestration]
    L3 --> SS[Session Store]
    L3 --> SEC[Secrets + Config Runtime]
    L3 --> MM[Media Understanding]

    L4 --> CHS[Telegram / Discord / Slack / etc.]
    L4 --> MOD[Models / Providers]
    L4 --> EXT[Extensions / Plugin Packages]
```

### Interpretation

- The surface layer receives control input.
- The gateway coordination layer owns runtime lifecycle.
- The core runtime services make routing, execution, and persistence coherent.
- External integrations remain behind channel/provider/plugin boundaries.

---

## 4. Inbound message to reply flow

```mermaid
sequenceDiagram
    participant Channel
    participant Gateway
    participant Routing
    participant Session
    participant Agent
    participant Tools
    participant Delivery

    Channel->>Gateway: Inbound message event
    Gateway->>Routing: Resolve route and session key
    Routing-->>Gateway: Agent + session decision
    Gateway->>Session: Load session/transcript context
    Gateway->>Agent: Run centralized orchestration
    Agent->>Tools: Use tools / providers / hooks as needed
    Tools-->>Agent: Return intermediate results
    Agent-->>Gateway: Final payload / updates
    Gateway->>Session: Persist transcript and delivery state
    Gateway->>Delivery: Send reply back through channel path
```

### Interpretation

The key design choice is that channels do not independently own agent logic. They inject events into a shared orchestration path that is responsible for route selection, execution, persistence, and delivery.

---

## 5. Plugin-centric extension model

```mermaid
flowchart LR
    PKG[Plugin Package] --> LD[Plugin Loader]
    LD --> RG[Plugin Registry]
    RG --> CH[Channel Registrations]
    RG --> TL[Tools]
    RG --> HK[Hooks]
    RG --> SV[Services]
    RG --> RT[HTTP Routes]
    RG --> CLI[CLI Registrars]
    RG --> CE[Context Engines]
```

### Interpretation

OpenClaw is deliberately registry-driven. Most extensibility is expressed as registration into a shared runtime registry rather than custom ad-hoc code paths.

---

## 6. Session-centered state model

```mermaid
flowchart TD
    IN[Inbound Message] --> RR[Route Resolution]
    RR --> AK[Agent Identity]
    RR --> SK[Session Key]
    SK --> ST[Session Store]
    ST --> TR[Transcript Files]
    ST --> DS[Delivery State]
    AK --> AG[Agent Run]
    AG --> ST
```

### Interpretation

OpenClaw is not just replying to messages. It continuously maps inputs into a persistent identity model so that future turns can inherit the right context.

---

## 7. High-level architectural strengths

- **Gateway-centered composition** keeps the runtime coherent.
- **Registry-first extensibility** supports channels, tools, and services without fragmenting the core.
- **Centralized orchestration** keeps agent behavior consistent across messaging surfaces.
- **Explicit routing/session identity** supports long-lived conversational continuity.
- **Config and secret snapshots** improve safety and operational predictability.
- **Media subsystem separation** makes multimodal behavior easier to evolve.

---

## 8. High-level conclusion

At a high level, OpenClaw is best understood as a **local-first AI messaging platform runtime**:

- a CLI and control plane at the edge
- a Gateway orchestration core in the middle
- and channels, plugins, providers, and stateful sessions underneath

That architecture is what allows OpenClaw to behave like one product while spanning many channels, execution paths, and runtime concerns.