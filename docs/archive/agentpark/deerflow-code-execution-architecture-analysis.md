## DeerFlow Code Execution Architecture Analysis

### Scope

This document focuses on the execution and sandbox subsystem in DeerFlow, based on the repository structure, middleware chain, sandbox providers, file tools, upload handling, and artifact serving flow.

### 1. Core architectural idea

The central idea is simple but powerful: each thread gets its own execution context. That context is not only conversational state but also a dedicated file structure and sandbox lifecycle. The thread becomes the unit that ties together chat history, uploads, workspace files, outputs, artifacts, and execution state.

### 2. The execution contract exposed to the agent

The agent works against a stable virtual filesystem rooted at `/mnt/user-data`.

- `/mnt/user-data/uploads` stores user-provided inputs
- `/mnt/user-data/workspace` stores intermediate working files
- `/mnt/user-data/outputs` stores final outputs intended for presentation

This contract is important because the agent can reason about one consistent environment even when the actual backend changes.

### 3. Abstraction layers

#### 3.1 Sandbox interface

At the lowest abstraction level, DeerFlow defines a `Sandbox` contract with operations for:

- command execution
- file reads
- directory listing
- text writes
- binary updates

This is a strong design because tool behavior is decoupled from the execution backend.

#### 3.2 Sandbox provider interface

Above that, a `SandboxProvider` manages lifecycle:

- `acquire(thread_id)`
- `get(sandbox_id)`
- `release(sandbox_id)`

This separates execution mechanics from provisioning and reuse strategy.

### 4. Supported execution modes

#### 4.1 Local sandbox provider

The local provider returns a singleton `LocalSandbox` that runs commands directly on the host via subprocess execution. In this mode, virtual paths are rewritten to the actual thread directories on the host filesystem.

Strengths:

- simple local development
- fast startup
- low operational overhead

Trade-off:

- this is not strong isolation; it is structured host execution

#### 4.2 AIO sandbox provider with local container backend

The container-based provider starts a sandbox container and mounts thread-specific directories into it. The container exposes an HTTP sandbox API that the backend uses for command execution and file operations.

Strengths:

- stronger isolation
- reusable execution environment
- container-native operational story

#### 4.3 AIO sandbox provider with remote backend

The remote backend delegates lifecycle to a provisioner service, which can create and destroy sandbox pods or services. This is the most production-oriented mode.

Strengths:

- scalable provisioning
- stronger deployment flexibility
- better alignment with multi-service environments

### 5. Runtime flow

#### Step 1: Thread context is established

The lead agent is built with middleware including `ThreadDataMiddleware`, `UploadsMiddleware`, and `SandboxMiddleware`.

The middleware order is important:

1. thread paths are computed first
2. uploaded files are injected into context
3. sandbox lifecycle is managed for tool execution

#### Step 2: Thread directories are computed lazily

Thread data middleware computes host-side paths for workspace, uploads, and outputs. With lazy initialization, these directories are only created if and when execution or file access actually happens.

This is an efficient design because fully conversational turns do not pay sandbox setup costs.

#### Step 3: Sandbox acquisition happens on demand

Sandbox tools call `ensure_sandbox_initialized()`. If no sandbox exists yet for the turn, the provider acquires one using the current `thread_id`.

This creates a strong balance between capability and efficiency.

#### Step 4: Commands and file operations use a stable path model

The agent-facing tools assume `/mnt/user-data/...` paths. In local mode, DeerFlow rewrites those paths into actual host directories. In container mode, the directories are mounted into the sandbox so the paths already exist inside the environment.

This allows the same prompt and tool contract to work across different execution modes.

#### Step 5: Outputs become artifacts only when explicitly presented

Files written to `outputs/` do not automatically show up in the user interface. The agent must call `present_files` to register them as artifacts in thread state.

This is a product-quality decision because it separates:

- internal scratch work
- final user-visible deliverables

### 6. Why the file model is strong

The separation of uploads, workspace, and outputs is one of the best parts of the design.

#### Uploads

These are user-owned inputs. The backend stores them in the thread uploads directory and exposes virtual paths the agent can read.

#### Workspace

This is the agent’s scratch area. It is where intermediate files, scripts, notes, and temporary assets can live.

#### Outputs

This is the presentation layer of the filesystem. It contains the deliverables that can be surfaced back to the user.

This three-part structure supports clarity, better UX, and cleaner future security controls.

### 7. Warm reuse and lifecycle management

The AIO sandbox provider contains sophisticated lifecycle logic.

It supports:

- deterministic sandbox IDs for thread reuse
- in-process caching
- cross-process discovery
- warm pool reuse
- idle cleanup
- graceful shutdown

This is particularly strong because it avoids unnecessary cold starts while still making cleanup possible.

The warm-pool concept is important: after a turn, a sandbox can be released from active use without being destroyed immediately. That means the next turn for the same thread can often reclaim the environment quickly.

### 8. Concurrency design quality

The sandbox provider goes beyond basic lifecycle handling and addresses real race conditions.

It uses:

- per-thread in-process locks
- cross-process file locking
- deterministic IDs derived from `thread_id`
- discovery-before-create logic

This is strong systems engineering. It makes the execution layer more reliable in multi-process environments.

### 9. Upload flow and execution integration

Uploads are handled by the Gateway and written into the thread’s uploads directory. For non-local sandboxes, the file is also synced into the sandbox-visible virtual path.

Some file formats can be converted to Markdown at upload time, which improves downstream agent usability.

Then the uploads middleware prepends a structured `<uploaded_files>` block to the user message so the model knows exactly what is available and where.

This is a strong integration pattern because file awareness becomes part of the conversational context.

### 10. Artifact flow and user presentation

Artifacts are served through the Gateway by resolving virtual paths back to the correct thread-scoped host paths. Path resolution includes traversal protection and prefix validation.

This means the system has a controlled bridge between:

- sandbox-visible files
- host filesystem storage
- user-facing HTTP access

That bridge is one of the most sensitive parts of any execution system, and DeerFlow handles it with a good level of discipline.

### 11. Security observations

#### What is strong

- thread-scoped directories
- virtual path validation
- outputs-only presentation contract
- path traversal checks
- read-only skills mount in container mode
- stronger boundaries available through container and provisioned sandboxes

#### What to keep in mind

- local mode is operationally convenient but not strongly isolated
- the `bash` tool is intentionally powerful and therefore requires careful deployment choices

The architecture supports secure deployment patterns, but it does not force the safest mode by default.

### 12. Overall assessment

The code execution architecture is one of DeerFlow’s clearest differentiators. It is strong because it combines:

- clean abstractions
- a coherent thread-first model
- real file and command execution capability
- explicit artifact presentation
- scalable backend choices
- thoughtful lifecycle optimization

In practical terms, DeerFlow behaves much more like a persistent agent workspace than a stateless chat application. That is the key architectural insight.