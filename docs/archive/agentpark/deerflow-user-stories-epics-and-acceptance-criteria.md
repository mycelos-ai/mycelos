## DeerFlow User Stories, Epics, and Acceptance Criteria

### Scope

These user stories are derived from the current architecture of the codebase, especially the thread model, sandbox execution layer, file lifecycle, streaming chat experience, artifacts, planning mode, and subagent support.

---

## Epic 1: Thread-based conversational workspace

### User Story 1
**As a user, I want to start a new thread instantly, so that I can begin working without setup friction.**

**Acceptance Criteria**
- A new workspace chat can be created without manual thread configuration.
- The user lands in a valid thread context before sending the first message.
- The chat UI supports a clean “new conversation” flow.

### User Story 2
**As a user, I want to revisit prior threads, so that I can continue previous work instead of starting over.**

**Acceptance Criteria**
- Recent threads are visible in the workspace navigation.
- Opening an existing thread restores its messages and state.
- Prior thread artifacts and outputs remain accessible.

---

## Epic 2: File-driven agent workflows

### User Story 3
**As a user, I want to upload files into a thread, so that the agent can analyze them as part of the conversation.**

**Acceptance Criteria**
- The UI supports attaching files to a thread.
- Uploaded files are stored in a thread-scoped location.
- The agent receives enough metadata to discover and read uploaded files.
- Previously uploaded files remain available in later turns of the same thread.

### User Story 4
**As a user, I want office and document files to become easier for the agent to use, so that analysis is more reliable.**

**Acceptance Criteria**
- Supported file formats can be converted into Markdown or a text-friendly representation.
- Converted versions are associated with the same thread.
- The agent can reference the converted outputs through known paths.

---

## Epic 3: Executable workspace and automation

### User Story 5
**As a user, I want the agent to inspect, edit, and create files in a workspace, so that it can do real implementation work.**

**Acceptance Criteria**
- The execution layer supports reading files, listing directories, and writing files.
- The workspace is scoped to the active thread.
- Intermediate files can persist across multiple turns.

### User Story 6
**As a user, I want the agent to run shell commands, so that it can execute scripts, automation, and development tasks.**

**Acceptance Criteria**
- The agent can invoke a shell execution tool.
- Shell execution runs within the configured sandbox model.
- Command output is returned to the agent for reasoning and follow-up.
- The environment can be configured for local or isolated container execution.

---

## Epic 4: Artifact-driven output delivery

### User Story 7
**As a user, I want final deliverables to appear as artifacts, so that I can view or download the results of the agent’s work.**

**Acceptance Criteria**
- The agent can mark output files for presentation.
- Presented files are associated with the current thread.
- The frontend can open artifact URLs and render common file types.
- Binary files can be downloaded when inline rendering is not appropriate.

### User Story 8
**As a user, I want the system to distinguish between scratch work and final outputs, so that my interface stays clean.**

**Acceptance Criteria**
- The agent can use a workspace area for internal work.
- Only files in the designated outputs area are eligible for presentation.
- User-visible artifacts do not automatically include every intermediate file.

---

## Epic 5: Guided and advanced agent execution

### User Story 9
**As a user, I want to choose between fast and deeper execution modes, so that the system matches the complexity of my task.**

**Acceptance Criteria**
- The UI exposes mode or model controls for different execution styles.
- The backend can receive planning and reasoning-related runtime settings.
- Simpler tasks can run with less overhead than planning-heavy tasks.

### User Story 10
**As a power user, I want the lead agent to delegate complex work to subagents, so that larger tasks can be parallelized and coordinated.**

**Acceptance Criteria**
- Subagent capability can be enabled at runtime.
- The lead agent can create delegated tasks when the mode allows it.
- The system enforces concurrency or lifecycle limits on subagent execution.
- The user can observe meaningful progress through thread updates, tasks, or artifacts.

---

## Product-level interpretation

Taken together, these stories describe DeerFlow as a product with four central promises:

1. **Persistent thread-based workspaces** instead of disposable chat turns
2. **Executable agent workflows** instead of text-only assistance
3. **File and artifact lifecycle management** instead of opaque tool activity
4. **Progressive power** from simple chat to planned, delegated, multi-step execution

### Recommended future epic framing

If these stories were used for product planning, the next natural grouping would be:

- Workspace and thread continuity
- File ingestion and document understanding
- Execution and automation
- Deliverables and artifacts
- Planning and multi-agent orchestration

That framing fits the current architecture very well.