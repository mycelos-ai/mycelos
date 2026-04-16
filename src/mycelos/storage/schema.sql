-- Maicel Core Schema (V2)

PRAGMA foreign_keys = ON;

-- Users (must be defined first — referenced by most tables)
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    email       TEXT,
    language    TEXT DEFAULT 'en',
    timezone    TEXT,
    telegram_id INTEGER,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT
);

-- Seed default user (idempotent)
INSERT OR IGNORE INTO users (id, name, status) VALUES ('default', 'Default User', 'active');

-- Tasks
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    goal        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    inputs      TEXT,
    budget      REAL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT
);

-- Agents (V2: removed capabilities TEXT, added hash columns)
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    agent_type  TEXT NOT NULL DEFAULT 'deterministic',
    status      TEXT NOT NULL DEFAULT 'proposed',
    policy      TEXT,
    reputation  REAL NOT NULL DEFAULT 0.5,
    code_hash   TEXT,
    tests_hash  TEXT,
    prompt_hash TEXT,
    display_name TEXT,
    user_facing INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_by  TEXT NOT NULL DEFAULT 'system'
);

-- Agent Capabilities (V2: replaces JSON array in agents.capabilities)
CREATE TABLE IF NOT EXISTS agent_capabilities (
    agent_id    TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    capability  TEXT NOT NULL,
    PRIMARY KEY (agent_id, capability)
);
CREATE INDEX IF NOT EXISTS idx_agent_caps ON agent_capabilities(capability);

-- Plans
CREATE TABLE IF NOT EXISTS plans (
    id          TEXT PRIMARY KEY,
    task_id     TEXT REFERENCES tasks(id),
    steps       TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'new',
    success_rate REAL NOT NULL DEFAULT 0.0,
    use_count   INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Attempts
CREATE TABLE IF NOT EXISTS attempts (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    plan_id         TEXT REFERENCES plans(id),
    agent_id        TEXT REFERENCES agents(id),
    model_used      TEXT,
    cost            REAL,
    duration_ms     INTEGER,
    result          TEXT,
    success         INTEGER NOT NULL DEFAULT 0,
    escalation_count INTEGER NOT NULL DEFAULT 0,
    generation_id   INTEGER,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Config Generations (NixOS-style)
CREATE TABLE IF NOT EXISTS config_generations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    parent_id       INTEGER REFERENCES config_generations(id),
    config_hash     TEXT NOT NULL,
    config_snapshot TEXT NOT NULL,
    description     TEXT,
    trigger         TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS active_generation (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    generation_id   INTEGER NOT NULL REFERENCES config_generations(id),
    activated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Memory
CREATE TABLE IF NOT EXISTS memory_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    scope       TEXT NOT NULL,
    agent_id    TEXT,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT 'system',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT,
    expires_at  TEXT,
    UNIQUE(user_id, scope, agent_id, key)
);

CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_entries(user_id, scope, agent_id);

-- Audit Events
CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    agent_id    TEXT,
    task_id     TEXT,
    user_id     TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    details     TEXT,
    generation_id INTEGER,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type, created_at);

-- Workflow Definitions (V2: reusable, part of NixOS State)
CREATE TABLE IF NOT EXISTS workflows (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    goal          TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    steps         TEXT NOT NULL,
    plan          TEXT,                              -- LLM system prompt for WorkflowAgent
    model         TEXT DEFAULT 'haiku',              -- LLM model for WorkflowAgent
    allowed_tools TEXT DEFAULT '[]',                 -- JSON array: tools this workflow can use
    success_criteria TEXT,                           -- Natural language: when is this workflow successful?
    notification_mode TEXT DEFAULT 'result_only',    -- result_only | progress | none
    scope         TEXT,
    tags          TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_by    TEXT NOT NULL DEFAULT 'system',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT
);

-- Workflow Runs (V2: execution state, NOT part of NixOS State)
CREATE TABLE IF NOT EXISTS workflow_runs (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES workflows(id),
    task_id         TEXT REFERENCES tasks(id),
    user_id         TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    status          TEXT NOT NULL DEFAULT 'running',
    current_step    TEXT,
    completed_steps TEXT,
    artifacts       TEXT,
    error           TEXT,
    cost            REAL NOT NULL DEFAULT 0.0,
    budget_limit    REAL,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    conversation    TEXT,                            -- JSON: LLM conversation for pause/resume
    clarification   TEXT,                            -- Question text when needs_clarification
    notified_at     TEXT,
    session_id      TEXT,                            -- Chat session that started this run (null for cron/headless)
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_user ON workflow_runs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_session_id ON workflow_runs(session_id);

-- Workflow Events (Durable Execution, detail log)
CREATE TABLE IF NOT EXISTS workflow_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    run_id      TEXT REFERENCES workflow_runs(id),
    step_id     TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    checkpoint  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_workflow ON workflow_events(workflow_id, created_at);

-- Credentials (encrypted, Credential Proxy)
CREATE TABLE IF NOT EXISTS credentials (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    service           TEXT NOT NULL,
    label             TEXT NOT NULL DEFAULT 'default',
    description       TEXT,
    encrypted         BLOB NOT NULL,
    nonce             BLOB NOT NULL,
    security_rotated  INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT,
    UNIQUE(user_id, service, label)
);

-- Capability Tokens (Security Layer)
CREATE TABLE IF NOT EXISTS capability_tokens (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    agent_id    TEXT NOT NULL,
    task_id     TEXT,
    scope       TEXT NOT NULL,
    max_requests INTEGER,
    rate_limit  INTEGER,
    issued_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    used_requests INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cap_tokens_agent ON capability_tokens(agent_id, revoked);

-- Policies (Security Layer)
CREATE TABLE IF NOT EXISTS policies (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    agent_id    TEXT,
    resource    TEXT NOT NULL,
    decision    TEXT NOT NULL DEFAULT 'confirm',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(user_id, agent_id, resource)
);

CREATE INDEX IF NOT EXISTS idx_policies_agent ON policies(user_id, agent_id);

-- Filesystem Mounts (V2: scoped directory access, part of NixOS State)
CREATE TABLE IF NOT EXISTS mounts (
    id          TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    access      TEXT NOT NULL DEFAULT 'read',
    purpose     TEXT,
    agent_id    TEXT REFERENCES agents(id) ON DELETE CASCADE,
    workflow_id TEXT,
    user_id     TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- LLM Usage Tracking (cost per call)
CREATE TABLE IF NOT EXISTS llm_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model       TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    cost        REAL NOT NULL DEFAULT 0.0,
    purpose     TEXT,
    session_id  TEXT,
    agent_id    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_date ON llm_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(model);

-- Connectors (V2)
CREATE TABLE IF NOT EXISTS connectors (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    connector_type  TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    setup_type      TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT
);

-- Connector Capabilities (V2)
CREATE TABLE IF NOT EXISTS connector_capabilities (
    connector_id TEXT NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    capability   TEXT NOT NULL,
    PRIMARY KEY (connector_id, capability)
);
CREATE INDEX IF NOT EXISTS idx_connector_caps ON connector_capabilities(capability);

-- LLM Models (V2)
CREATE TABLE IF NOT EXISTS llm_models (
    id                  TEXT PRIMARY KEY,
    provider            TEXT NOT NULL,
    tier                TEXT NOT NULL DEFAULT 'sonnet',
    input_cost_per_1k   REAL,
    output_cost_per_1k  REAL,
    max_context         INTEGER,
    status              TEXT NOT NULL DEFAULT 'available',
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Agent LLM Models (V2: links agents to models with failover priority)
CREATE TABLE IF NOT EXISTS agent_llm_models (
    agent_id    TEXT REFERENCES agents(id) ON DELETE CASCADE,
    model_id    TEXT NOT NULL REFERENCES llm_models(id),
    priority    INTEGER NOT NULL DEFAULT 1,
    purpose     TEXT NOT NULL DEFAULT 'execution',
    PRIMARY KEY (agent_id, model_id, purpose)
);
CREATE INDEX IF NOT EXISTS idx_agent_llm ON agent_llm_models(agent_id, purpose, priority);

-- Channels (V2: messaging channels like Telegram, Slack — part of NixOS State)
CREATE TABLE IF NOT EXISTS channels (
    id              TEXT PRIMARY KEY,
    channel_type    TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'polling',
    status          TEXT NOT NULL DEFAULT 'active',
    config          TEXT NOT NULL DEFAULT '{}',
    allowed_users   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT
);

-- Scheduled Tasks (V2: part of NixOS State)
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES workflows(id),
    user_id         TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    schedule        TEXT NOT NULL,
    inputs          TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    last_run        TEXT,
    next_run        TEXT,
    run_count       INTEGER NOT NULL DEFAULT 0,
    budget_per_run  REAL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Background Tasks (transient execution state, NOT NixOS State)
CREATE TABLE IF NOT EXISTS background_tasks (
    id                  TEXT PRIMARY KEY,
    task_type           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    payload             TEXT NOT NULL,
    result              TEXT,
    error               TEXT,
    session_id          TEXT,
    agent_id            TEXT,
    user_id             TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
    capability_token_id TEXT,
    cost                REAL NOT NULL DEFAULT 0.0,
    cost_limit          REAL,
    current_step        TEXT,
    total_steps         INTEGER DEFAULT 0,
    notify_policy       TEXT NOT NULL DEFAULT 'on_completion',
    notify_channels     TEXT DEFAULT 'auto',
    notified_at         TEXT,
    waiting_reason      TEXT,
    timeout_seconds     INTEGER DEFAULT 600,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at          TEXT,
    completed_at        TEXT
);

CREATE TABLE IF NOT EXISTS background_task_steps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL REFERENCES background_tasks(id),
    step_number  INTEGER NOT NULL,
    step_name    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    message      TEXT,
    cost         REAL NOT NULL DEFAULT 0.0,
    started_at   TEXT,
    completed_at TEXT
);

-- Knowledge Base (NOT NixOS State — user content)
CREATE TABLE IF NOT EXISTS knowledge_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'note',
    status      TEXT NOT NULL DEFAULT 'active',
    tags        TEXT DEFAULT '[]',
    priority    INTEGER NOT NULL DEFAULT 0,
    due         TEXT,
    parent_path TEXT,                                -- topic path this note belongs to
    reminder    BOOLEAN DEFAULT FALSE,               -- trigger notification when due
    remind_at   TEXT,                                -- ISO datetime: exact moment to fire the reminder
    reminder_fired_at TEXT,                          -- ISO datetime: set when dispatched or user-dismissed
    remind_via  TEXT DEFAULT '["chat"]',             -- JSON: channels to notify ["chat","telegram","email"]
    sort_order  INTEGER DEFAULT 0,                   -- manual ordering within topic
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT,
    content_hash TEXT,
    organizer_state TEXT NOT NULL DEFAULT 'pending',
    organizer_seen_at TEXT,
    source_file TEXT                                  -- relative path to original document (e.g. documents/report.pdf)
);

CREATE INDEX IF NOT EXISTS idx_kn_type ON knowledge_notes(type, status);
CREATE INDEX IF NOT EXISTS idx_kn_due ON knowledge_notes(due) WHERE due IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kn_updated ON knowledge_notes(updated_at);
CREATE INDEX IF NOT EXISTS idx_kn_path ON knowledge_notes(path);
CREATE INDEX IF NOT EXISTS idx_kn_parent ON knowledge_notes(parent_path) WHERE parent_path IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge_links (
    from_path   TEXT NOT NULL,
    to_path     TEXT NOT NULL,
    PRIMARY KEY (from_path, to_path)
);

CREATE INDEX IF NOT EXISTS idx_kl_to ON knowledge_links(to_path);

CREATE TABLE IF NOT EXISTS knowledge_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

-- Session Agent Tracking (which agent is active per session)
CREATE TABLE IF NOT EXISTS session_agents (
    session_id      TEXT PRIMARY KEY,
    active_agent_id TEXT NOT NULL DEFAULT 'mycelos',
    handoff_reason  TEXT,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS organizer_suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    note_path  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    status     TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_organizer_pending
    ON organizer_suggestions(status) WHERE status = 'pending';

-- Tool Usage (Lazy Tool Discovery — tracks which tools each agent actually uses)
CREATE TABLE IF NOT EXISTS tool_usage (
    user_id    TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    tool_name  TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    last_used  TEXT,
    PRIMARY KEY (user_id, agent_id, tool_name)
);
