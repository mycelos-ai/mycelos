## DeerFlow High-Level Architecture Breakdown

### Purpose

This document gives a high-level, diagram-style explanation of how DeerFlow is structured and how the major components interact, with emphasis on thread flow, execution, artifacts, and user experience.

---

## 1. System landscape

```mermaid
flowchart LR
    U[User] --> F[Next.js Frontend]
    F --> N[Nginx Unified Entry]
    N --> G[FastAPI Gateway]
    N --> L[LangGraph Runtime]
    L --> T[Lead Agent + Middleware]
    T --> X[Tools / Skills / MCP / Subagents]
    T --> S[Sandbox Layer]
    G --> A[Uploads / Artifacts / Suggestions / Config APIs]
    S --> FS[Thread-scoped Filesystem]
```

### Interpretation

- The frontend is the user-facing workspace.
- Nginx makes the whole product look like a single application.
- LangGraph handles agent execution and streaming.
- The Gateway handles supporting APIs such as uploads and artifacts.
- The sandbox layer gives the agent a real execution environment.

---

## 2. Main user flow

```mermaid
sequenceDiagram
    participant User
    participant Frontend
    participant Gateway
    participant LangGraph
    participant Agent
    participant Sandbox

    User->>Frontend: Open workspace and send message
    Frontend->>Gateway: Upload files if needed
    Frontend->>LangGraph: Start or continue thread
    LangGraph->>Agent: Build lead agent with middleware
    Agent->>Sandbox: Read/write files or run commands
    Sandbox-->>Agent: Return outputs
    Agent-->>LangGraph: Stream response + artifacts + todos
    LangGraph-->>Frontend: Stream updates
    Frontend-->>User: Show messages and artifacts
```

### Interpretation

The user experiences one chat flow, but behind that flow the system coordinates file ingestion, stateful execution, artifact generation, and streamed UI updates.

---

## 3. Thread-centered file model

```mermaid
flowchart TD
    TH[Thread ID] --> TD[Thread Data]
    TD --> UP[/uploads/]
    TD --> WS[/workspace/]
    TD --> OUT[/outputs/]
    UP -->|user inputs| AG[Agent]
    WS -->|scratch work| AG
    AG -->|final deliverables| OUT
    OUT --> PF[present_files]
    PF --> ART[Artifacts in UI]
```

### Interpretation

- `uploads` contains user-provided files
- `workspace` contains internal working files
- `outputs` contains final deliverables
- only explicitly presented output files become user-facing artifacts

This is one of the cleanest concepts in the architecture.

---

## 4. Execution layer options

```mermaid
flowchart TD
    TOOLS[File + Bash Tools] --> SP[Sandbox Provider]
    SP --> LS[Local Sandbox]
    SP --> AIO[AIO Sandbox Provider]
    AIO --> LC[Local Container Backend]
    AIO --> RB[Remote Provisioner Backend]
```

### Interpretation

- In local mode, execution happens on the host with path rewriting.
- In container mode, execution happens inside a sandbox container.
- In provisioner mode, sandbox lifecycle can be managed remotely.

This gives DeerFlow a strong portability model.

---

## 5. Middleware pipeline

```mermaid
flowchart LR
    M1[ThreadDataMiddleware] --> M2[UploadsMiddleware]
    M2 --> M3[SandboxMiddleware]
    M3 --> M4[Summarization / Todo / Title / Memory]
    M4 --> M5[ViewImage / Clarification]
    M5 --> AG[Lead Agent]
```

### Interpretation

The middleware chain prepares context before the model runs. This keeps cross-cutting concerns out of the core agent loop and is one of the strongest backend design decisions.

---

## 6. Artifact delivery path

```mermaid
flowchart LR
    AG[Agent writes output file] --> OUT[/mnt/user-data/outputs]
    OUT --> PF[present_files]
    PF --> TS[Thread State: artifacts[]]
    TS --> FE[Frontend Artifact Panel]
    FE --> GW[Gateway Artifact Route]
    GW --> USER[View / Render / Download]
```

### Interpretation

The architecture deliberately separates file creation from file presentation. This keeps the user experience cleaner and makes artifact handling more explicit.

---

## 7. High-level architectural strengths

- **Thread-first design** unifies chat, files, and execution.
- **Stable virtual filesystem** makes tools portable across backends.
- **Middleware composition** keeps runtime behavior modular.
- **Artifact pipeline** turns execution results into user-visible deliverables.
- **Execution backend flexibility** supports local and more production-style deployment models.

---

## 8. High-level conclusion

At a high level, DeerFlow is best understood as a layered agent workspace:

- a chat product on the surface
- a stateful agent runtime underneath
- and a real execution/file system below that

That layered model is what gives the system both usability and power.