---
summary: "Research notes: code-oriented map of OpenClaw's runtime architecture, extension model, and message flow"
read_when:
  - You need a code-level mental model of how OpenClaw is assembled
  - You are changing CLI startup, gateway runtime, routing, channels, plugins, or sessions
  - You want one document that ties together the main subsystems and data flow
title: "OpenClaw Architecture Research"
---

# OpenClaw architecture research

Last updated: 2026-03-11

## Purpose of this document

This is a **code-oriented research document** for understanding how OpenClaw is actually put together today.

It is intentionally broader than `docs/concepts/architecture.md`: that document explains the Gateway architecture at a product/system level, while this one is a **repo-level analysis** of the main building blocks, the startup path, the extension boundaries, and the end-to-end message/agent/session flow.

The goal is to answer four questions:

1. What are the main architectural layers?
2. Where does control enter the system, and how does it move?
3. Which abstractions are stable extension points versus internal implementation details?
4. Where are the real complexity hotspots?

## Executive summary

OpenClaw is best understood as a **long-lived local AI gateway** with three major personalities:

- a **CLI application** that bootstraps configuration, maintenance, messaging, and agent turns
- a **Gateway daemon** that owns HTTP/WebSocket APIs, connected operator clients, connected nodes, and running messaging channels
- an **extension host** that loads channels, providers, tools, hooks, services, HTTP routes, and context engines through a plugin registry

The architectural center of gravity is **not** the CLI. The CLI is mostly a control surface. The real hub is the **Gateway runtime**, which coordinates:

- channel lifecycle
- agent execution
- routing and session identity
- plugin registration
- secrets/config snapshots
- WebSocket clients and device pairing
- node capability dispatch

The design is modular, but not “microservice” modular. It is a **single-process orchestration architecture** with strong subsystem boundaries expressed through registries, typed adapters, and runtime snapshots.

## Repo shape and module topology

At the workspace level, `pnpm-workspace.yaml` shows a monorepo with:

- the root package `openclaw`
- `ui`
- `packages/*`
- `extensions/*`

The root `package.json` describes OpenClaw as a **multi-channel AI gateway with extensible messaging integrations** and exports a large `plugin-sdk` surface, including channel-specific SDK entry points such as Telegram, Discord, Slack, Signal, iMessage, WhatsApp, LINE, Matrix, and more.

In practice, the codebase is organized around these directories:

- `src/cli`: CLI bootstrapping, lazy command registration, parsing, profiles, routing
- `src/commands`: execution logic for major commands such as `agent`
- `src/gateway`: long-lived gateway runtime, server state, node registry, channel manager, startup sidecars, auth
- `src/channels`: channel metadata plus the generic channel plugin interface
- `src/routing`: route resolution and session key construction
- `src/config`: config loading, defaults, includes, validation, runtime refresh, session store plumbing
- `src/plugins`: plugin discovery, loading, registry, runtime API, services, hook integration
- `src/plugin-sdk`: exported SDK surface for external channel/provider/plugin authors
- `src/auto-reply`: inbound dispatch and reply orchestration
- `src/media-understanding`: attachment normalization and media-to-text/image/video understanding pipeline
- `src/secrets`: secret reference resolution and runtime secret snapshots
- `extensions/*`: concrete channel/plugin packages, usually built on top of `openclaw/plugin-sdk/*`
- `docs/*`: product docs, operational docs, and research notes

## Architectural thesis

There are a few important architectural ideas repeated across the codebase.

### 1. “Registry first” composition

OpenClaw frequently avoids hard-coding implementations directly into hot paths. Instead it builds registries for:

- CLI commands (`src/cli/program/command-registry.ts`)
- plugin capabilities (`src/plugins/registry.ts`)
- active plugin state (`src/plugins/runtime.ts`)
- context engines (`src/context-engine/registry.ts`)
- connected nodes (`src/gateway/node-registry.ts`)

This keeps the core process centralized while still allowing late registration and optional features.

### 2. “Runtime snapshot” instead of ad-hoc globals

Configuration and secrets are not just read once and scattered. The config stack in `src/config/io.ts` and the secrets runtime in `src/secrets/runtime.ts` build a **resolved runtime snapshot** that can be activated, refreshed, and queried safely.

This is an important design choice: it reduces the amount of code that needs to understand secret references, auth store overrides, env substitution, and config refresh semantics.

### 3. Messaging surfaces are plugins, not special cases

Even built-in channels are moving toward the same abstraction as external channels:

- `src/channels/plugins/types.ts`
- `src/channels/plugins/types.plugin.ts`
- `src/gateway/server-channels.ts`

The gateway channel manager asks channel plugins how to list accounts, determine whether they are configured/enabled, and how to start/stop accounts. That means the runtime sees “Telegram”, “Discord”, “Slack”, etc. as implementations of the same lifecycle contract.

### 4. Sessions and routing are first-class, not accidental

OpenClaw does not treat chat identity as a single `chatId`. It has explicit models for:

- agent identity
- channel identity
- account identity
- peer identity
- main session vs peer session
- last-route policy
- thread inheritance and route bindings

This is concentrated in `src/routing/resolve-route.ts` and the session store modules under `src/config/sessions/*`.

## Startup path: from executable to command execution

### Stage 1: entry wrapper

The real executable entry point is `src/entry.ts`.

Its job is not “run business logic”; it is a **startup guard and environment normalizer**. It:

- sets process identity (`process.title = "openclaw"`)
- normalizes environment variables
- optionally enables Node compile cache
- forces a read-only auth store for `secrets audit`
- normalizes Windows argv
- fast-paths root `--help` and `--version`
- may respawn Node with experimental warning suppression
- finally hands off to `src/cli/run-main.ts`

This separation matters: `entry.ts` is operational glue, while `run-main.ts` is the real CLI bootstrap.

### Stage 2: CLI bootstrap

`src/cli/run-main.ts` performs the main CLI bring-up:

- loads `.env`
- normalizes env
- ensures the `openclaw` CLI is on `PATH` when needed
- asserts the supported runtime
- attempts CLI routing shortcuts via `tryRouteCli`
- enables structured console capture
- builds the Commander program lazily
- installs unhandled rejection / uncaught exception handling

Two details are easy to miss and architecturally important:

1. **Command registration is lazy.** `buildProgram()` in `src/cli/program/build-program.ts` delegates to `registerProgramCommands`, but the real command implementations are only loaded on demand.
2. **Plugin CLI commands are also late-bound.** `run-main.ts` only registers plugin commands after it determines whether the requested primary command is built-in.

The result is a CLI that scales across a large command surface without paying the import cost up front.

### Stage 3: command registry as a dispatch map

`src/cli/program/command-registry.ts` is effectively the **public CLI capability index**.

It defines the top-level command groups and lazily imports the relevant registrar for each one, including:

- `setup`
- `onboard`
- `configure`
- `config`
- `backup`
- maintenance commands (`doctor`, `dashboard`, `reset`, `uninstall`)
- `message`
- `memory`
- `agent` / `agents`
- `status` / `health` / `sessions`
- `browser`

This file is a good “map of product surface area”, even though it is not the business logic itself.

## The Gateway as the real runtime core

The most important file in the codebase, architecturally, is `src/gateway/server.impl.ts`.

It is where multiple subsystems get assembled into a single long-lived runtime:

- config loading and migration
- control UI asset resolution
- plugin runtime creation
- plugin loading
- secrets runtime preparation and activation
- channel manager creation
- node registry setup
- gateway auth/rate limiting setup
- HTTP/WebSocket runtime state creation
- startup sidecars and background services
- shutdown and restart behavior

This is not a “thin server file”. It is the **composition root**.

### Gateway runtime state

`src/gateway/server-runtime-state.ts` builds the core server state. It creates and wires:

- HTTP server(s)
- WebSocket server
- connected client set
- event broadcaster
- chat run state and buffers
- dedupe maps
- tool-event recipient registry
- optional canvas host
- plugin HTTP route handling
- readiness and auth-aware HTTP serving

An important design detail: the gateway can bind to **multiple loopback aliases**, but still treats them as one runtime. This is why the function returns both `httpServer` and `httpServers`.

### Gateway startup sidecars

`src/gateway/server-startup.ts` handles the “everything else that should come alive with the gateway” path:

- stale session lock cleanup
- browser control server startup
- Gmail watcher startup
- hook loading
- channel startup
- plugin service startup
- ACP session identity reconciliation
- memory backend startup
- restart sentinel wake scheduling

This file is valuable because it shows that the gateway is more than a socket server; it is an **orchestrator for adjacent background runtimes**.

## Channel architecture: a lifecycle-driven plugin model

### Static metadata vs runtime plugin resolution

`src/channels/registry.ts` contains **lightweight static metadata** for built-in channel identities: labels, docs paths, aliases, ordering, and UI-facing descriptions.

It intentionally does **not** import channel implementations. Instead, runtime resolution goes through the active plugin registry using `normalizeAnyChannelId()`.

That split is important:

- `src/channels/registry.ts` = lightweight metadata and normalization
- plugin registry = actual runtime implementations

### Generic channel contract

The channel abstraction is defined through the plugin types re-exported from `src/channels/plugins/types.ts` and concretely shaped in `src/channels/plugins/types.plugin.ts`.

A channel plugin can provide capabilities such as:

- config/account management
- pairing
- security / DM policy
- messaging / outbound resolution
- directory listing
- group behavior
- threading
- onboarding/setup
- gateway lifecycle hooks
- streaming behavior

The architecture is adapter-heavy by design: each concern is its own small interface rather than one giant class.

### Channel manager

The actual lifecycle controller is `src/gateway/server-channels.ts`.

It keeps per-channel/per-account runtime state and is responsible for:

- listing configured accounts through the plugin
- checking enabled/configured state
- starting account runtimes
- tracking status snapshots
- stopping runtimes
- restart backoff and retry policy
- respecting manual stops

This means a “channel” is not just a send function; it is a **managed runtime with restart semantics**.

### Example: Telegram as an extension package

`extensions/telegram/index.ts` shows the packaging model clearly:

- the extension exports a plugin definition (`id`, `name`, `description`, `configSchema`)
- `register(api)` stores runtime access and calls `api.registerChannel(...)`

`extensions/telegram/src/channel.ts` then defines the real Telegram channel plugin:

- channel metadata and capabilities
- pairing behavior
- config accessors and validation
- security and allowlist warnings
- directory helpers
- threading and messaging helpers
- setup and onboarding hooks

This is the canonical pattern for a surface integration: **small package entrypoint, richer internal channel adapter**.

## Plugin system: the main extensibility backbone

### Loader

`src/plugins/loader.ts` is the plugin ingestion pipeline. It handles:

- plugin discovery
- module loading through `jiti`
- plugin SDK alias resolution
- config validation against plugin schemas
- record creation and diagnostics
- runtime creation and activation
- registry caching

The interesting architectural point is that the loader does real compatibility work for plugin authors, especially around SDK path resolution and src/dist aliasing.

### Registry

`src/plugins/registry.ts` is where plugin registrations become structured runtime state. The registry supports:

- tools
- hooks
- typed hooks
- channels
- providers
- gateway request handlers
- plugin HTTP routes
- CLI registrars
- services
- commands
- diagnostics

That list is effectively the “OpenClaw extension model”. If a feature needs to be extensible, it usually enters the system by being added here.

### Active registry is process-global

`src/plugins/runtime.ts` stores the active plugin registry in a process-global singleton (`Symbol.for(...)`). That is a practical solution to chunk duplication / multi-entry loading: no matter how the process is bundled, there is one active registry state.

### Plugin runtime API

`src/plugins/runtime/index.ts` builds the runtime object handed to plugins. It includes access to:

- config
- system helpers
- media helpers
- tools
- channel helpers
- events
- logging
- state directory lookup
- text-to-speech / speech-to-text
- model auth helpers (with guardrails)
- subagent APIs when in a gateway-request context

This is an important security boundary: plugins get **capability-scoped helpers**, not unrestricted access to internal credential selection logic.

### SDK export strategy

The root `package.json` and `src/plugin-sdk/index.ts` show how OpenClaw exposes a large, typed SDK for external packages. This is not a tiny plugin API. It is a fairly comprehensive surface that includes channel abstractions, runtime helpers, webhook utilities, status helpers, ACP types, tool runtime helpers, and more.

## Routing and session identity

### Route resolution

`src/routing/resolve-route.ts` is where OpenClaw decides **which agent and which session** an inbound message belongs to.

A resolved route includes:

- `agentId`
- `channel`
- `accountId`
- `sessionKey`
- `mainSessionKey`
- `lastRoutePolicy`
- `matchedBy`

The matching model is richer than “channel + user”. It can resolve based on:

- exact peer binding
- parent peer binding
- guild + role bindings
- guild binding
- team binding
- account binding
- channel binding
- default fallback

This is why the routing module is central: it determines both **which agent should act** and **where state should be stored**.

### Session key model

The same file exposes `buildAgentSessionKey(...)`, which shows that session identity can encode:

- agent id
- channel
- account id
- peer kind / peer id
- DM scoping policy
- optional identity links

So the system can collapse or separate conversations depending on configured session semantics.

## Session persistence and transcripts

### Session store

The session store lives under `src/config/sessions/*`, with `src/config/sessions/store.ts` being the core persistence layer.

The store is not a naive JSON blob writer. It includes:

- normalized session keys
- cache with TTL
- lock queues and write serialization
- delivery-context normalization
- migrations
- file rotation / maintenance
- disk-budget enforcement
- archived transcript cleanup integration

The session layer is therefore both **state persistence** and **operational hygiene**.

### Transcript updates

The `agent` command path in `src/commands/agent.ts` updates session transcripts, session entries, and delivery state as agent runs complete. It also emits transcript update events and persists prompt/reply turns into transcript files when ACP-backed flows are used.

Architecturally, this means session history is not just “for humans later”; it is part of the runtime contract between agent execution, delivery, and future routing.

## Inbound message -> reply flow

The most representative high-level message path is in `src/auto-reply/dispatch.ts` and `src/auto-reply/reply/dispatch-from-config.ts`.

### Dispatch stages

The flow is roughly:

1. finalize inbound context
2. acquire a reply dispatcher
3. dedupe inbound messages
4. resolve session store entry
5. emit plugin hooks / internal hooks for message receipt
6. decide whether replies must route back to an originating channel
7. enforce send policy
8. try ACP dispatch when applicable
9. otherwise call `getReplyFromConfig(...)`
10. stream tool results / block replies / final replies through dispatcher or route-reply
11. optionally apply TTS to tool, block, or final payloads

Important behaviors visible in the code:

- duplicate inbound suppression is explicit
- route-to-originating-channel is a first-class path
- reasoning payloads can be suppressed for generic channel delivery
- tool-result delivery is not identical to final-reply delivery
- TTS is layered in as a payload transformation step, not a separate top-level system

This is a good example of OpenClaw’s style: the “reply pipeline” is built out of many small policy decisions rather than a single monolithic executor.

## Agent execution flow

`src/commands/agent.ts` is the central command/orchestration path for agent turns.

Even a quick read shows how many concerns converge here:

- session resolution
- workspace resolution
- model/provider selection
- model fallback execution
- CLI-vs-embedded execution paths
- ACP dispatch policies
- auth profile overrides
- transcript persistence
- delivery of final payloads
- session store updates after runs

One key architectural insight: the agent system is **pluggable at the model/runtime layer, but centralized at the orchestration layer**. OpenClaw does not let every channel invent its own agent execution semantics; the central command path owns that logic.

## Media understanding pipeline

`src/media-understanding/runner.ts` shows that media processing is treated as a dedicated subsystem, not an afterthought.

It is responsible for:

- normalizing attachments from message context
- resolving trusted local roots for inbound media
- caching attachment lookups
- selecting providers/models per capability
- probing local binaries (for example Gemini CLI, whisper, whisper-cpp, sherpa-onnx)
- running either local CLI entries or provider-backed entries
- building per-capability decisions for image/audio/video understanding

The architecture here is intentionally hybrid:

- local CLI tooling when possible
- provider-backed media understanding when needed
- capability-specific model selection

This is a useful microcosm of OpenClaw in general: **policy + registry + fallback + operational checks**.

## Config and secrets architecture

### Config I/O

`src/config/io.ts` is a major subsystem, not a helper file. It handles:

- JSON5 config parsing
- dotenv loading
- shell env fallback
- include resolution
- env var substitution and preservation
- default application
- path normalization
- merge patching
- validation
- runtime overrides
- write auditing and backup rotation

That is why configuration changes in OpenClaw are comparatively safe: the config layer is doing normalization, validation, and guardrail work before the rest of the runtime sees the result.

### Secret resolution

`src/secrets/resolve.ts` resolves secret references against configured providers and enforces path and permission checks for file-backed secrets.

The code explicitly handles:

- env/file/exec provider resolution
- per-provider concurrency and batch limits
- absolute/trusted path checks
- symlink and permission restrictions
- bounded output sizes and timeouts

### Runtime secret snapshots

`src/secrets/runtime.ts` builds a resolved secret snapshot, applies it to config/auth stores, and installs refresh hooks so the runtime can stay coherent after config reloads.

This is one of the cleaner architectural boundaries in the repo:

- raw config may contain secret refs
- runtime config should contain resolved values
- refresh should rebuild the same picture consistently

## Gateway security, pairing, and node model

The WebSocket message handler in `src/gateway/server/ws-connection/message-handler.ts` is where many of the trust boundaries become concrete.

It handles:

- connect-frame validation
- auth decision handling
- origin and proxy-aware checks
- device identity verification
- pairing approval / token verification
- client role and scope decisions
- browser-origin specific rate limiting
- gateway request dispatch after handshake

This matches the product model documented in the docs: the gateway is local-first, but not “trust everything on localhost blindly”. There is an explicit device-pairing and role/scope model.

### Nodes

`src/gateway/node-registry.ts` models remote/connected nodes as capability-bearing sessions with:

- identity
- display metadata
- caps
- commands
- permissions
- path env
- invoke request/response tracking

So nodes are not generic sockets. They are **registered capability providers** connected to the main gateway.

### Canvas host and control surfaces

`src/gateway/server-runtime-state.ts` also shows that the HTTP server can mount the canvas host and plugin HTTP routes on the same runtime. This makes the gateway a unified control plane for:

- WebSocket control traffic
- HTTP control/UI traffic
- plugin HTTP endpoints
- canvas-hosted UI surfaces

## Context engine model

The context-engine system is intentionally minimal in core:

- `src/context-engine/init.ts` ensures the legacy engine is always registered
- `src/context-engine/registry.ts` provides the registry and resolution logic

The key architectural decision is that additional context engines are expected to arrive through plugins via `api.registerContextEngine()`. In other words, context retrieval is treated as a **slot-based extension point**, not a permanently hard-coded subsystem.

## Why the architecture looks like this

OpenClaw is solving a hard combination of problems at once:

- many transport/channel integrations
- one shared AI orchestration core
- per-channel policy differences
- local-first gateway semantics
- device/node pairing
- optional extension packages
- long-lived sessions and delivery state

The chosen architecture reflects that reality. Instead of splitting into many services, OpenClaw uses:

- one long-lived process
- many registries
- typed plugin boundaries
- config/secrets runtime snapshots
- explicit routing/session identity

That keeps deployment simple while still allowing high internal modularity.

## Main complexity hotspots

If you are trying to reason about risk, these are the areas with the highest architectural density:

1. `src/gateway/server.impl.ts`
   - composition root; many subsystems meet here
2. `src/plugins/loader.ts`
   - discovery, compatibility, runtime activation, diagnostics
3. `src/routing/resolve-route.ts`
   - binds product routing policy to persistent session identity
4. `src/commands/agent.ts`
   - orchestration hub for model execution, transcripts, delivery, fallbacks
5. `src/auto-reply/reply/dispatch-from-config.ts`
   - real-world reply policy pipeline
6. `src/config/io.ts`
   - config normalization/validation/write safety
7. `src/gateway/server/ws-connection/message-handler.ts`
   - trust boundaries and protocol enforcement

These are the files most likely to require careful reading before any non-trivial refactor.

## Recommended reading order for new contributors

If you want to build a real mental model quickly, read in this order:

1. `package.json`
2. `src/entry.ts`
3. `src/cli/run-main.ts`
4. `src/cli/program/command-registry.ts`
5. `src/gateway/server.impl.ts`
6. `src/gateway/server-runtime-state.ts`
7. `src/gateway/server-startup.ts`
8. `src/plugins/registry.ts`
9. `src/plugins/loader.ts`
10. `src/channels/plugins/types.plugin.ts`
11. `src/gateway/server-channels.ts`
12. `src/routing/resolve-route.ts`
13. `src/config/sessions/store.ts`
14. `src/auto-reply/reply/dispatch-from-config.ts`
15. `src/commands/agent.ts`
16. one real extension, e.g. `extensions/telegram/src/channel.ts`

That sequence mirrors the actual layering of the runtime.

## Bottom line

OpenClaw is not just “a chatbot CLI” and not just “a WebSocket server”. It is a **stateful local AI gateway platform** built around:

- a lazy CLI front end
- a long-lived gateway composition root
- plugin-registered channels/providers/tools/hooks/services
- explicit routing and session identity
- durable session/transcript state
- secure config and secret resolution
- optional node/canvas/control-plane capabilities

If you keep that model in mind, most of the repo starts to make sense: the codebase is organized around **owning the boundary between messaging surfaces, AI execution, and persistent local state**.