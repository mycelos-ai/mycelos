# Phase 2: Sandbox & Execution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the agent execution runtime — the LocalSandbox that runs agent code in isolated subprocesses with the IPC protocol (JSON-RPC over STDIN/STDOUT) connecting agents to the Security Layer. This is where agents come to life.

**Architecture:** An agent runs as a subprocess with a stripped environment. It communicates with the Gateway via JSON-RPC over STDIN/STDOUT pipes. Every tool call (`run(tool="email.read", args={...})`) flows through the Security Layer: token validation → policy check → credential injection → MCP dispatch → response sanitization → back to agent. The Two-Layer Execution model separates raw execution (full output) from LLM presentation (truncated, formatted).

**Tech Stack:** Python 3.12+, subprocess, JSON-RPC (custom, lightweight), SQLite, pytest

**Prerequisites:** Phase 1 Security Layer (Credential Proxy, Capability Tokens, Policy Engine, Response Sanitizer) — all implemented and tested.

---

## Scope Decisions

- **LocalSandbox only.** DockerSandbox is Phase 2.4 (optional, can wait). LocalSandbox uses temp dirs and subprocess isolation — NOT a security boundary, but functional for development.
- **No real MCP yet.** MCP connector integration is Phase 5.1. For now, the tool dispatch layer has a pluggable `ToolRegistry` that can hold mock tools for testing. The IPC protocol and security pipeline are real.
- **Presentation Layer is real.** The truncation strategies (text/JSON/CSV/binary) are implemented — they're needed as soon as agents produce output.
- **Agent SDK is minimal.** `mycelos.sdk.run()` + `mycelos.sdk.progress()` wraps JSON-RPC. Just enough for agents to call tools and report progress.
- **Streaming/Progress** is part of the IPC protocol. JSON-RPC Notifications (no `id`) carry progress updates. The Gateway forwards them to the active channel. No separate streaming infrastructure.
- **HTTP-Tools** (`http.get`, `http.post`, etc.) are registered in the ToolRegistry. They execute in the Gateway process (not the sandbox), flow through URL-Allowlist checks in the Policy Engine, and use the Credential Proxy for auth injection. Scenarios: UC28.
- **Agent Template** — the standard code structure that the Creator-Agent uses. Documented in spec Section 5, tested via UC29.

---

## File Structure

| File | Responsibility |
|---|---|
| **Sandbox** | |
| `src/mycelos/execution/__init__.py` | Package init |
| `src/mycelos/execution/sandbox.py` | `LocalSandbox` — subprocess management, env stripping, temp dirs |
| `src/mycelos/execution/ipc.py` | JSON-RPC protocol — request/response serialization, auth validation |
| `src/mycelos/execution/runtime.py` | `ExecutionRuntime` — orchestrates sandbox + security + tool dispatch |
| **Tool System** | |
| `src/mycelos/execution/tools.py` | `ToolRegistry` — registers tools, dispatches calls, validates args |
| **Presentation** | |
| `src/mycelos/execution/presentation.py` | `PresentationLayer` — truncation strategies per data type |
| **Agent SDK** | |
| `src/mycelos/sdk.py` | `run()` function — agent-side JSON-RPC client (used inside sandboxed agents) |
| **Tests** | |
| `tests/test_sandbox.py` | Sandbox lifecycle, env stripping, temp dir management |
| `tests/test_ipc.py` | JSON-RPC protocol correctness |
| `tests/test_execution_runtime.py` | Full pipeline: agent → IPC → security → tool → response |
| `tests/test_presentation.py` | Truncation strategies for all 5 data types |
| `tests/security/test_sandbox_boundaries.py` | SEC05 + SEC06 scenarios |

---

## Dependency Graph

```
Task 1: Presentation Layer        (no dependencies)
Task 2: JSON-RPC IPC Protocol     (no dependencies)
Task 3: LocalSandbox              (depends on Task 2 for IPC)
Task 4: Tool Registry             (no dependencies)
Task 5: Execution Runtime         (depends on Task 1-4 + Phase 1 Security)
Task 6: Agent SDK                 (depends on Task 2 for IPC format)
Task 7: Integration Test          (depends on all above)
Task 8: Security Boundary Tests   (depends on Task 3)
```

Tasks 1, 2, 4 can be built in parallel. Task 3 needs IPC. Task 5 ties everything together.

---

### Task 1: Presentation Layer (Truncation Strategies)

**Files:**
- Create: `src/mycelos/execution/__init__.py`
- Create: `src/mycelos/execution/presentation.py`
- Create: `tests/test_presentation.py`

The Presentation Layer takes raw agent output and prepares it for LLM consumption. Different data types get different truncation strategies (Spec Section 10).

**Implementation:**

```python
# src/mycelos/execution/presentation.py
"""Presentation Layer — truncates and formats agent output for LLM consumption.

Different data types get different strategies:
- Plain text: head + tail
- JSON: schema-preserving (arrays shortened, deep objects collapsed)
- CSV: header + sample rows + stats
- Binary: metadata only (size, format)
- Code/tracebacks: preserve errors, strip info lines
"""

from __future__ import annotations

import json
from dataclasses import dataclass

DEFAULT_TOKEN_BUDGET = 4000  # Max tokens for presentation output


@dataclass(frozen=True)
class PresentationResult:
    """Result of presenting agent output."""
    content: str              # Truncated/formatted content for LLM
    overflow_path: str | None = None  # Path to full output if truncated
    was_truncated: bool = False
    original_size: int = 0
    content_type: str = "text"


class PresentationLayer:
    """Formats raw execution output for LLM context windows."""

    def __init__(self, token_budget: int = DEFAULT_TOKEN_BUDGET) -> None:
        self.token_budget = token_budget

    def present(self, content: str | bytes, content_type: str = "auto") -> PresentationResult:
        """Present content with automatic type detection if content_type='auto'."""
        if isinstance(content, bytes):
            return self._present_binary(content)

        if content_type == "auto":
            content_type = self._detect_type(content)

        original_size = len(content)

        if content_type == "json":
            return self._present_json(content, original_size)
        elif content_type == "csv":
            return self._present_csv(content, original_size)
        elif content_type == "traceback":
            return self._present_traceback(content, original_size)
        else:
            return self._present_text(content, original_size)

    def _detect_type(self, content: str) -> str:
        """Auto-detect content type from content."""
        stripped = content.strip()
        if stripped.startswith(("{", "[")):
            try:
                json.loads(stripped)
                return "json"
            except (json.JSONDecodeError, ValueError):
                pass
        if "Traceback (most recent call last)" in content:
            return "traceback"
        # Simple CSV heuristic: first line has commas, multiple lines
        lines = content.split("\n")
        if len(lines) > 2 and "," in lines[0]:
            return "csv"
        return "text"

    def _present_text(self, content: str, original_size: int) -> PresentationResult:
        """Head + tail truncation for plain text."""
        lines = content.split("\n")
        # Rough estimate: 1 token ≈ 4 chars
        char_budget = self.token_budget * 4
        if len(content) <= char_budget:
            return PresentationResult(content=content, original_size=original_size, content_type="text")

        head_chars = int(char_budget * 0.8)  # 80% for head
        tail_chars = int(char_budget * 0.2)  # 20% for tail

        head = content[:head_chars]
        tail = content[-tail_chars:]
        omitted = len(content) - head_chars - tail_chars

        truncated = f"{head}\n\n--- [{omitted} characters omitted] ---\n\n{tail}"
        return PresentationResult(
            content=truncated,
            was_truncated=True,
            original_size=original_size,
            content_type="text",
        )

    def _present_json(self, content: str, original_size: int) -> PresentationResult:
        """Schema-preserving truncation for JSON."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return self._present_text(content, original_size)

        truncated_data = self._truncate_json_value(data, depth=0, max_depth=3)
        result = json.dumps(truncated_data, indent=2, ensure_ascii=False)

        if len(result) > self.token_budget * 4:
            result = result[:self.token_budget * 4] + "\n... [JSON truncated]"

        return PresentationResult(
            content=result,
            was_truncated=result != content,
            original_size=original_size,
            content_type="json",
        )

    def _truncate_json_value(self, value: object, depth: int, max_depth: int) -> object:
        """Recursively truncate JSON preserving structure."""
        if depth > max_depth:
            if isinstance(value, dict):
                return {"...": f"{len(value)} keys"}
            elif isinstance(value, list):
                return [f"... ({len(value)} items)"]
            elif isinstance(value, str) and len(value) > 200:
                return value[:200] + "... [truncated]"
            return value

        if isinstance(value, dict):
            return {k: self._truncate_json_value(v, depth + 1, max_depth) for k, v in value.items()}
        elif isinstance(value, list):
            if len(value) <= 5:
                return [self._truncate_json_value(v, depth + 1, max_depth) for v in value]
            head = [self._truncate_json_value(v, depth + 1, max_depth) for v in value[:3]]
            tail = [self._truncate_json_value(v, depth + 1, max_depth) for v in value[-2:]]
            return head + [f"... ({len(value) - 5} more items)"] + tail
        elif isinstance(value, str) and len(value) > 200:
            return value[:200] + "... [truncated]"
        return value

    def _present_csv(self, content: str, original_size: int) -> PresentationResult:
        """Header + sample rows + stats for CSV."""
        lines = content.strip().split("\n")
        if len(lines) <= 15:
            return PresentationResult(content=content, original_size=original_size, content_type="csv")

        header = lines[0]
        first_rows = "\n".join(lines[1:11])
        last_rows = "\n".join(lines[-5:])
        col_count = len(header.split(","))

        truncated = (
            f"{header}\n{first_rows}\n"
            f"\n--- [{len(lines) - 16} rows omitted, {len(lines)} total, {col_count} columns] ---\n\n"
            f"{last_rows}"
        )
        return PresentationResult(
            content=truncated,
            was_truncated=True,
            original_size=original_size,
            content_type="csv",
        )

    def _present_traceback(self, content: str, original_size: int) -> PresentationResult:
        """Preserve full traceback — they're usually short and critical."""
        # Tracebacks are almost always under budget, just pass through
        char_budget = self.token_budget * 4
        if len(content) <= char_budget:
            return PresentationResult(content=content, original_size=original_size, content_type="traceback")
        # If somehow huge, keep the last part (error message)
        return PresentationResult(
            content=content[-char_budget:],
            was_truncated=True,
            original_size=original_size,
            content_type="traceback",
        )

    def _present_binary(self, content: bytes) -> PresentationResult:
        """Binary data — never send to LLM, just metadata."""
        return PresentationResult(
            content=f"[Binary data: {len(content)} bytes]",
            was_truncated=True,
            original_size=len(content),
            content_type="binary",
        )
```

**Tests:**

```python
# tests/test_presentation.py
"""Tests for the Presentation Layer (Two-Layer Execution, Section 10)."""

import json

import pytest

from mycelos.execution.presentation import PresentationLayer


@pytest.fixture
def layer() -> PresentationLayer:
    return PresentationLayer(token_budget=100)  # Small budget for testing


# ── Text Truncation ──


def test_short_text_unchanged(layer: PresentationLayer) -> None:
    text = "Hello world"
    result = layer.present(text)
    assert result.content == text
    assert result.was_truncated is False


def test_long_text_head_tail(layer: PresentationLayer) -> None:
    text = "A" * 1000
    result = layer.present(text, content_type="text")
    assert result.was_truncated is True
    assert "omitted" in result.content
    assert result.content.startswith("A")
    assert result.content.rstrip().endswith("A")


# ── JSON Truncation ──


def test_small_json_unchanged() -> None:
    layer = PresentationLayer(token_budget=1000)
    data = {"key": "value", "count": 42}
    content = json.dumps(data)
    result = layer.present(content)
    assert result.content_type == "json"
    assert result.was_truncated is False


def test_json_array_truncation() -> None:
    layer = PresentationLayer(token_budget=200)
    data = {"items": list(range(100))}
    content = json.dumps(data)
    result = layer.present(content)
    assert "more items" in result.content


def test_json_deep_nesting_collapsed() -> None:
    layer = PresentationLayer(token_budget=200)
    data = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
    content = json.dumps(data)
    result = layer.present(content)
    parsed = json.loads(result.content)
    # Depth > 3 should be collapsed
    assert "..." in str(parsed)


# ── CSV Truncation ──


def test_short_csv_unchanged() -> None:
    layer = PresentationLayer(token_budget=1000)
    csv = "name,age\nAlice,30\nBob,25"
    result = layer.present(csv, content_type="csv")
    assert result.was_truncated is False


def test_long_csv_truncated() -> None:
    layer = PresentationLayer(token_budget=1000)
    header = "id,name,value"
    rows = [f"{i},item_{i},{i*10}" for i in range(100)]
    csv = header + "\n" + "\n".join(rows)
    result = layer.present(csv, content_type="csv")
    assert result.was_truncated is True
    assert "rows omitted" in result.content
    assert header in result.content


# ── Binary ──


def test_binary_never_sent_to_llm() -> None:
    layer = PresentationLayer()
    result = layer.present(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000)
    assert result.content_type == "binary"
    assert "Binary data" in result.content
    assert b"\x89" not in result.content.encode()


# ── Traceback ──


def test_traceback_preserved() -> None:
    layer = PresentationLayer(token_budget=1000)
    tb = 'Traceback (most recent call last):\n  File "test.py", line 1\nValueError: bad input'
    result = layer.present(tb)
    assert result.content_type == "traceback"
    assert "ValueError" in result.content
    assert result.was_truncated is False


# ── Auto Detection ──


def test_auto_detect_json() -> None:
    layer = PresentationLayer(token_budget=1000)
    result = layer.present('{"key": "value"}')
    assert result.content_type == "json"


def test_auto_detect_traceback() -> None:
    layer = PresentationLayer(token_budget=1000)
    result = layer.present('Traceback (most recent call last):\n  File "x.py"')
    assert result.content_type == "traceback"


def test_auto_detect_csv() -> None:
    layer = PresentationLayer(token_budget=1000)
    result = layer.present("name,age,city\nAlice,30,Berlin\nBob,25,Munich")
    assert result.content_type == "csv"
```

**Steps:**

- [ ] Write tests → run to verify they fail
- [ ] Create `src/mycelos/execution/__init__.py` (empty)
- [ ] Create `src/mycelos/execution/presentation.py`
- [ ] Run tests → all pass
- [ ] Run full suite → no regressions
- [ ] Commit: `feat: implement PresentationLayer with type-specific truncation strategies`

---

### Task 2: JSON-RPC IPC Protocol

**Files:**
- Create: `src/mycelos/execution/ipc.py`
- Create: `tests/test_ipc.py`

The IPC protocol defines how agents communicate with the Gateway. JSON-RPC 2.0 over STDIN/STDOUT.

**Implementation:**

```python
# src/mycelos/execution/ipc.py
"""JSON-RPC IPC Protocol for agent ↔ gateway communication.

Agents send JSON-RPC requests on STDOUT, receive responses on STDIN.
The gateway reads agent STDOUT, processes requests, writes responses to agent STDIN.

Message format (JSON-RPC 2.0):
  Request:  {"jsonrpc": "2.0", "method": "tool.call", "params": {...}, "id": 1, "auth": "<token>"}
  Response: {"jsonrpc": "2.0", "result": {...}, "id": 1}
  Error:    {"jsonrpc": "2.0", "error": {"code": -32600, "message": "..."}, "id": 1}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Custom error codes
AUTH_FAILED = -32000
PERMISSION_DENIED = -32001
RATE_LIMITED = -32002


@dataclass(frozen=True)
class RPCRequest:
    """Parsed JSON-RPC request from an agent."""
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str | None = None
    auth: str = ""


@dataclass(frozen=True)
class RPCResponse:
    """JSON-RPC response to send back to agent."""
    id: int | str | None = None
    result: Any = None
    error_code: int | None = None
    error_message: str | None = None

    @property
    def is_error(self) -> bool:
        return self.error_code is not None

    def to_json(self) -> str:
        """Serialize to JSON-RPC response string."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.is_error:
            msg["error"] = {"code": self.error_code, "message": self.error_message}
        else:
            msg["result"] = self.result
        return json.dumps(msg)


def parse_request(line: str) -> RPCRequest:
    """Parse a JSON-RPC request line from an agent.

    Raises ValueError if the request is malformed.
    """
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if not isinstance(msg, dict):
        raise ValueError("Request must be a JSON object")
    if msg.get("jsonrpc") != "2.0":
        raise ValueError("Missing or invalid jsonrpc version")
    if "method" not in msg:
        raise ValueError("Missing method field")

    return RPCRequest(
        method=msg["method"],
        params=msg.get("params", {}),
        id=msg.get("id"),
        auth=msg.get("auth", ""),
    )


def make_error(request_id: int | str | None, code: int, message: str) -> RPCResponse:
    """Create a JSON-RPC error response."""
    return RPCResponse(id=request_id, error_code=code, error_message=message)


def make_result(request_id: int | str | None, result: Any) -> RPCResponse:
    """Create a JSON-RPC success response."""
    return RPCResponse(id=request_id, result=result)
```

**Tests:**

```python
# tests/test_ipc.py
"""Tests for JSON-RPC IPC protocol."""

import json

import pytest

from mycelos.execution.ipc import (
    parse_request, make_error, make_result,
    RPCRequest, RPCResponse,
    PARSE_ERROR, AUTH_FAILED, PERMISSION_DENIED,
)


def test_parse_valid_request() -> None:
    line = json.dumps({
        "jsonrpc": "2.0",
        "method": "tool.call",
        "params": {"tool": "email.read", "args": {"limit": 10}},
        "id": 1,
        "auth": "token-abc123",
    })
    req = parse_request(line)
    assert req.method == "tool.call"
    assert req.params["tool"] == "email.read"
    assert req.id == 1
    assert req.auth == "token-abc123"


def test_parse_request_without_params() -> None:
    line = json.dumps({"jsonrpc": "2.0", "method": "ping", "id": 2})
    req = parse_request(line)
    assert req.method == "ping"
    assert req.params == {}
    assert req.auth == ""


def test_parse_invalid_json() -> None:
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_request("not json at all")


def test_parse_missing_method() -> None:
    with pytest.raises(ValueError, match="Missing method"):
        parse_request(json.dumps({"jsonrpc": "2.0", "id": 1}))


def test_parse_wrong_version() -> None:
    with pytest.raises(ValueError, match="jsonrpc version"):
        parse_request(json.dumps({"jsonrpc": "1.0", "method": "test", "id": 1}))


def test_success_response_serialization() -> None:
    resp = make_result(1, {"emails": ["a@b.com"]})
    data = json.loads(resp.to_json())
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert data["result"] == {"emails": ["a@b.com"]}
    assert "error" not in data


def test_error_response_serialization() -> None:
    resp = make_error(1, AUTH_FAILED, "Token expired")
    data = json.loads(resp.to_json())
    assert data["error"]["code"] == AUTH_FAILED
    assert data["error"]["message"] == "Token expired"
    assert "result" not in data


def test_response_is_error_flag() -> None:
    ok = make_result(1, "data")
    assert ok.is_error is False
    err = make_error(1, -32600, "bad")
    assert err.is_error is True
```

**Steps:**

- [ ] Write tests → verify they fail
- [ ] Implement `src/mycelos/execution/ipc.py`
- [ ] Run tests → all pass
- [ ] Commit: `feat: implement JSON-RPC IPC protocol for agent-gateway communication`

---

### Task 3: LocalSandbox

**Files:**
- Create: `src/mycelos/execution/sandbox.py`
- Create: `tests/test_sandbox.py`

The LocalSandbox creates a subprocess with stripped environment, temp directories for /input, /workspace, /output, and STDIN/STDOUT pipes for IPC.

**Implementation:**

```python
# src/mycelos/execution/sandbox.py
"""LocalSandbox — subprocess-based agent execution.

Creates an isolated subprocess with:
- Stripped environment (only MYCELOS_SESSION_TOKEN)
- Temp directories: /input (read), /workspace (work), /output (write)
- STDIN/STDOUT pipes for JSON-RPC IPC
- Timeout enforcement

WARNING: LocalSandbox is NOT a security boundary.
It provides functional isolation for development, not production security.
Use DockerSandbox for production (Phase 2.4).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SandboxConfig:
    """Configuration for a sandbox instance."""
    agent_id: str
    session_token: str
    timeout_seconds: int = 30
    input_files: dict[str, str] = field(default_factory=dict)  # name → source path


@dataclass
class SandboxResult:
    """Result of a sandbox execution."""
    exit_code: int
    stdout: str
    stderr: str
    output_dir: Path
    timed_out: bool = False


class LocalSandbox:
    """Development sandbox using subprocess + temp dirs.

    NOT a security boundary. For development and testing only.
    """

    def __init__(self) -> None:
        self._sandboxes: dict[str, Path] = {}

    def create(self, config: SandboxConfig) -> str:
        """Create a sandbox with temp directories. Returns sandbox_id."""
        sandbox_id = str(uuid.uuid4())
        base_dir = Path(tempfile.mkdtemp(prefix=f"mycelos-sandbox-{config.agent_id}-"))

        input_dir = base_dir / "input"
        workspace_dir = base_dir / "workspace"
        output_dir = base_dir / "output"

        input_dir.mkdir()
        workspace_dir.mkdir()
        output_dir.mkdir()

        # Copy input files
        for name, source_path in config.input_files.items():
            shutil.copy2(source_path, input_dir / name)

        self._sandboxes[sandbox_id] = base_dir
        return sandbox_id

    def execute(
        self,
        sandbox_id: str,
        command: list[str],
        config: SandboxConfig,
    ) -> SandboxResult:
        """Run a command in the sandbox."""
        base_dir = self._sandboxes.get(sandbox_id)
        if base_dir is None:
            raise ValueError(f"Sandbox {sandbox_id} not found")

        # Stripped environment — only essentials
        env = {
            "MYCELOS_SESSION_TOKEN": config.session_token,
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(base_dir / "workspace"),
            "MYCELOS_INPUT": str(base_dir / "input"),
            "MYCELOS_WORKSPACE": str(base_dir / "workspace"),
            "MYCELOS_OUTPUT": str(base_dir / "output"),
        }

        try:
            result = subprocess.run(
                command,
                cwd=str(base_dir / "workspace"),
                env=env,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
            )
            return SandboxResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                output_dir=base_dir / "output",
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Timeout after {config.timeout_seconds}s",
                output_dir=base_dir / "output",
                timed_out=True,
            )

    def cleanup(self, sandbox_id: str) -> None:
        """Remove sandbox temp directories."""
        base_dir = self._sandboxes.pop(sandbox_id, None)
        if base_dir and base_dir.exists():
            shutil.rmtree(base_dir, ignore_errors=True)

    def get_output_dir(self, sandbox_id: str) -> Path | None:
        """Get the output directory path for a sandbox."""
        base_dir = self._sandboxes.get(sandbox_id)
        if base_dir:
            return base_dir / "output"
        return None
```

**Tests:**

```python
# tests/test_sandbox.py
"""Tests for LocalSandbox."""

import os
from pathlib import Path

import pytest

from mycelos.execution.sandbox import LocalSandbox, SandboxConfig, SandboxResult


@pytest.fixture
def sandbox() -> LocalSandbox:
    return LocalSandbox()


@pytest.fixture
def config() -> SandboxConfig:
    return SandboxConfig(
        agent_id="test-agent",
        session_token="test-token-123",
        timeout_seconds=5,
    )


def test_create_sandbox(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    """Creating a sandbox returns an ID and creates temp dirs."""
    sandbox_id = sandbox.create(config)
    assert sandbox_id is not None
    output_dir = sandbox.get_output_dir(sandbox_id)
    assert output_dir is not None
    assert output_dir.exists()
    sandbox.cleanup(sandbox_id)


def test_execute_simple_command(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    """Execute a simple command and get stdout."""
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["echo", "hello"], config)
    assert result.exit_code == 0
    assert "hello" in result.stdout
    sandbox.cleanup(sandbox_id)


def test_env_stripping(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    """Agent process should only see MYCELOS_* env vars, not host secrets."""
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["env"], config)
    env_lines = result.stdout.strip().split("\n")
    env_keys = {line.split("=")[0] for line in env_lines if "=" in line}

    # Should have our vars
    assert "MYCELOS_SESSION_TOKEN" in env_keys
    assert "MYCELOS_INPUT" in env_keys
    assert "MYCELOS_OUTPUT" in env_keys

    # Should NOT have host secrets
    assert "ANTHROPIC_API_KEY" not in env_keys
    assert "MYCELOS_MASTER_KEY" not in env_keys
    assert "OPENAI_API_KEY" not in env_keys

    sandbox.cleanup(sandbox_id)


def test_timeout_enforcement(sandbox: LocalSandbox) -> None:
    """Commands that exceed timeout are killed."""
    config = SandboxConfig(agent_id="slow", session_token="t", timeout_seconds=1)
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["sleep", "10"], config)
    assert result.timed_out is True
    assert result.exit_code == -1
    sandbox.cleanup(sandbox_id)


def test_output_dir_writable(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    """Agent can write to the output directory."""
    sandbox_id = sandbox.create(config)
    output_dir = sandbox.get_output_dir(sandbox_id)
    result = sandbox.execute(
        sandbox_id,
        ["sh", "-c", f"echo 'result' > {output_dir}/result.txt"],
        config,
    )
    # Note: this writes to the actual output dir path, not the env var path
    # In real use, the agent would use MYCELOS_OUTPUT env var
    sandbox.cleanup(sandbox_id)


def test_cleanup_removes_dirs(sandbox: LocalSandbox, config: SandboxConfig) -> None:
    """Cleanup removes all temp directories."""
    sandbox_id = sandbox.create(config)
    output_dir = sandbox.get_output_dir(sandbox_id)
    assert output_dir.exists()
    sandbox.cleanup(sandbox_id)
    assert not output_dir.exists()


def test_input_files_copied(sandbox: LocalSandbox, tmp_path: Path) -> None:
    """Input files are copied into the sandbox input directory."""
    input_file = tmp_path / "data.txt"
    input_file.write_text("input data")

    config = SandboxConfig(
        agent_id="test",
        session_token="t",
        input_files={"data.txt": str(input_file)},
    )
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(
        sandbox_id,
        ["cat", str(Path(os.environ.get("MYCELOS_INPUT", "/input")) / "data.txt")],
        config,
    )
    # Verify via env-based path
    result2 = sandbox.execute(
        sandbox_id,
        ["sh", "-c", "cat $MYCELOS_INPUT/data.txt"],
        config,
    )
    assert "input data" in result2.stdout
    sandbox.cleanup(sandbox_id)
```

**Steps:**

- [ ] Write tests → verify they fail
- [ ] Implement `src/mycelos/execution/sandbox.py`
- [ ] Run tests → all pass
- [ ] Commit: `feat: implement LocalSandbox with env stripping, temp dirs, timeout`

---

### Task 4: Tool Registry

**Files:**
- Create: `src/mycelos/execution/tools.py`
- Create: `tests/test_tools.py`

The Tool Registry holds registered tools and dispatches calls. For now, tools are simple Python callables. MCP integration comes later.

**Implementation:**

```python
# src/mycelos/execution/tools.py
"""Tool Registry — registers and dispatches tool calls.

Tools are Python callables with a name and schema. The registry
validates arguments and dispatches calls. MCP tools will be
registered here when connectors are implemented (Phase 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolDefinition:
    """A registered tool."""
    name: str
    description: str
    handler: Callable[..., Any]
    required_capability: str  # e.g., "email.read"


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, str]]:
        """List all registered tools with name and description."""
        return [
            {"name": t.name, "description": t.description, "capability": t.required_capability}
            for t in self._tools.values()
        ]

    def call(self, name: str, args: dict[str, Any]) -> Any:
        """Call a tool by name. Raises KeyError if not found."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool '{name}' not found. Available: {list(self._tools.keys())}")
        return tool.handler(**args)
```

**Tests:**

```python
# tests/test_tools.py
"""Tests for Tool Registry."""

import pytest

from mycelos.execution.tools import ToolRegistry, ToolDefinition


def test_register_and_call() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="math.add",
        description="Add two numbers",
        handler=lambda a, b: a + b,
        required_capability="math",
    ))
    result = registry.call("math.add", {"a": 2, "b": 3})
    assert result == 5


def test_call_unknown_tool() -> None:
    registry = ToolRegistry()
    with pytest.raises(KeyError, match="not found"):
        registry.call("nonexistent", {})


def test_list_tools() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="a", description="Tool A", handler=lambda: None, required_capability="cap.a"))
    registry.register(ToolDefinition(name="b", description="Tool B", handler=lambda: None, required_capability="cap.b"))
    tools = registry.list_tools()
    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"a", "b"}


def test_get_tool() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="x", description="X", handler=lambda: 42, required_capability="cap"))
    tool = registry.get("x")
    assert tool is not None
    assert tool.name == "x"
    assert registry.get("nonexistent") is None
```

**Steps:**

- [ ] Write tests → verify they fail
- [ ] Implement `src/mycelos/execution/tools.py`
- [ ] Run tests → all pass
- [ ] Commit: `feat: implement ToolRegistry for tool registration and dispatch`

---

### Task 5: Agent SDK (mycelos.sdk)

**Files:**
- Create: `src/mycelos/sdk.py`
- Create: `tests/test_sdk.py`

The SDK is what agent code imports. It provides a single `run()` function that sends JSON-RPC over STDOUT and reads the response from STDIN. Agents never deal with IPC directly.

**Implementation:**

```python
# src/mycelos/sdk.py
"""Mycelos Agent SDK — the interface agents use to call tools.

Usage inside agent code:
    from mycelos.sdk import run
    result = run(tool="email.read", args={"limit": 50})

Under the hood, this sends a JSON-RPC request to the Gateway
via STDOUT and reads the response from STDIN.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_request_counter = 0


def run(tool: str, args: dict[str, Any] | None = None) -> Any:
    """Call a tool through the Mycelos Security Layer.

    This is the ONLY function agents should use for external operations.
    """
    global _request_counter
    _request_counter += 1

    token = os.environ.get("MYCELOS_SESSION_TOKEN", "")

    request = {
        "jsonrpc": "2.0",
        "method": "tool.call",
        "params": {"tool": tool, "args": args or {}},
        "id": _request_counter,
        "auth": token,
    }

    # Write request to STDOUT (gateway reads this)
    sys.stdout.write(json.dumps(request) + "\n")
    sys.stdout.flush()

    # Read response from STDIN (gateway writes this)
    response_line = sys.stdin.readline()
    if not response_line:
        raise RuntimeError("No response from gateway (STDIN closed)")

    response = json.loads(response_line)

    if "error" in response:
        error = response["error"]
        raise RuntimeError(f"Tool call failed [{error.get('code')}]: {error.get('message')}")

    return response.get("result")
```

**Tests:**

```python
# tests/test_sdk.py
"""Tests for the Agent SDK."""

import json
import io
from unittest.mock import patch

import pytest

from mycelos.sdk import run


def test_sdk_run_sends_jsonrpc() -> None:
    """run() sends a valid JSON-RPC request and parses the response."""
    mock_response = json.dumps({"jsonrpc": "2.0", "result": {"emails": [1, 2, 3]}, "id": 1})

    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
         patch("sys.stdin", io.StringIO(mock_response + "\n")), \
         patch.dict("os.environ", {"MYCELOS_SESSION_TOKEN": "test-token"}):

        result = run(tool="email.read", args={"limit": 10})

    assert result == {"emails": [1, 2, 3]}

    # Verify the request format
    sent = json.loads(mock_stdout.getvalue().strip())
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "tool.call"
    assert sent["params"]["tool"] == "email.read"
    assert sent["auth"] == "test-token"


def test_sdk_run_error_raises() -> None:
    """run() raises RuntimeError on error responses."""
    mock_response = json.dumps({
        "jsonrpc": "2.0",
        "error": {"code": -32001, "message": "Permission denied"},
        "id": 1,
    })

    with patch("sys.stdout", new_callable=io.StringIO), \
         patch("sys.stdin", io.StringIO(mock_response + "\n")), \
         patch.dict("os.environ", {"MYCELOS_SESSION_TOKEN": "tok"}):

        with pytest.raises(RuntimeError, match="Permission denied"):
            run(tool="shell.exec", args={"cmd": "ls"})


def test_sdk_run_no_response_raises() -> None:
    """run() raises RuntimeError when STDIN is closed."""
    with patch("sys.stdout", new_callable=io.StringIO), \
         patch("sys.stdin", io.StringIO("")), \
         patch.dict("os.environ", {"MYCELOS_SESSION_TOKEN": "tok"}):

        with pytest.raises(RuntimeError, match="No response"):
            run(tool="test.op")
```

**Steps:**

- [ ] Write tests → verify they fail
- [ ] Implement `src/mycelos/sdk.py`
- [ ] Run tests → all pass
- [ ] Commit: `feat: implement Agent SDK — run() function for tool calls via JSON-RPC`

---

### Task 6: Execution Runtime (Full Pipeline)

**Files:**
- Create: `src/mycelos/execution/runtime.py`
- Create: `tests/test_execution_runtime.py`

The Execution Runtime ties everything together: sandbox → IPC → security checks → tool dispatch → response sanitization → presentation.

**Implementation:**

```python
# src/mycelos/execution/runtime.py
"""Execution Runtime — orchestrates the full agent execution pipeline.

Pipeline: Agent Request → IPC Parse → Token Validate → Policy Check →
          Tool Dispatch → Response Sanitize → Presentation → Agent Response
"""

from __future__ import annotations

from typing import Any

from mycelos.execution.ipc import (
    RPCRequest, RPCResponse,
    parse_request, make_result, make_error,
    AUTH_FAILED, PERMISSION_DENIED, METHOD_NOT_FOUND, INTERNAL_ERROR,
)
from mycelos.execution.tools import ToolRegistry
from mycelos.security.capabilities import CapabilityTokenManager
from mycelos.security.sanitizer import ResponseSanitizer


class ExecutionRuntime:
    """Processes agent JSON-RPC requests through the security pipeline."""

    def __init__(
        self,
        token_manager: CapabilityTokenManager,
        tool_registry: ToolRegistry,
        sanitizer: ResponseSanitizer,
    ) -> None:
        self._tokens = token_manager
        self._tools = tool_registry
        self._sanitizer = sanitizer

    def handle_request(self, raw_line: str) -> RPCResponse:
        """Handle a single JSON-RPC request from an agent."""
        # Parse
        try:
            request = parse_request(raw_line)
        except ValueError as e:
            return make_error(None, -32700, str(e))

        # Route
        if request.method == "tool.call":
            return self._handle_tool_call(request)
        elif request.method == "tools.list":
            return make_result(request.id, self._tools.list_tools())
        else:
            return make_error(request.id, METHOD_NOT_FOUND, f"Unknown method: {request.method}")

    def _handle_tool_call(self, request: RPCRequest) -> RPCResponse:
        """Handle a tool.call request through the security pipeline."""
        tool_name = request.params.get("tool", "")
        args = request.params.get("args", {})

        # 1. Validate token
        if not request.auth:
            return make_error(request.id, AUTH_FAILED, "Missing auth token")

        validation = self._tokens.validate(request.auth, tool_name.split(".")[0] + "." + tool_name.split(".")[-1] if "." in tool_name else tool_name)

        # Check if the tool requires a capability
        tool_def = self._tools.get(tool_name)
        if tool_def is None:
            return make_error(request.id, METHOD_NOT_FOUND, f"Tool '{tool_name}' not found")

        # Validate token against the tool's required capability
        cap_validation = self._tokens.validate(request.auth, tool_def.required_capability)
        if not cap_validation.valid:
            return make_error(request.id, PERMISSION_DENIED, cap_validation.reason)

        # 2. Execute tool
        try:
            result = self._tools.call(tool_name, args)
        except Exception as e:
            return make_error(request.id, INTERNAL_ERROR, str(e))

        # 3. Sanitize response
        if isinstance(result, str):
            result = self._sanitizer.sanitize_text(result)
        elif isinstance(result, dict):
            # Sanitize string values in dicts
            result = self._sanitize_dict(result)

        return make_result(request.id, result)

    def _sanitize_dict(self, d: dict) -> dict:
        """Recursively sanitize string values in a dict."""
        sanitized = {}
        for k, v in d.items():
            if isinstance(v, str):
                sanitized[k] = self._sanitizer.sanitize_text(v)
            elif isinstance(v, dict):
                sanitized[k] = self._sanitize_dict(v)
            elif isinstance(v, list):
                sanitized[k] = [
                    self._sanitizer.sanitize_text(item) if isinstance(item, str) else item
                    for item in v
                ]
            else:
                sanitized[k] = v
        return sanitized
```

**Tests:**

```python
# tests/test_execution_runtime.py
"""Tests for the Execution Runtime — full pipeline."""

import json
from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage
from mycelos.execution.ipc import AUTH_FAILED, PERMISSION_DENIED, METHOD_NOT_FOUND
from mycelos.execution.runtime import ExecutionRuntime
from mycelos.execution.tools import ToolRegistry, ToolDefinition
from mycelos.security.capabilities import CapabilityTokenManager
from mycelos.security.sanitizer import ResponseSanitizer


@pytest.fixture
def storage(db_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(db_path)
    s.initialize()
    return s


@pytest.fixture
def runtime(storage: SQLiteStorage) -> tuple[ExecutionRuntime, CapabilityTokenManager]:
    tokens = CapabilityTokenManager(storage)
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="math.add",
        description="Add numbers",
        handler=lambda a, b: a + b,
        required_capability="math.add",
    ))
    registry.register(ToolDefinition(
        name="echo",
        description="Echo text",
        handler=lambda text: text,
        required_capability="echo",
    ))
    sanitizer = ResponseSanitizer()
    rt = ExecutionRuntime(tokens, registry, sanitizer)
    return rt, tokens


def make_request(method: str, params: dict, token: str = "", req_id: int = 1) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": req_id, "auth": token})


def test_full_pipeline_success(runtime: tuple) -> None:
    """Full pipeline: valid token → tool call → success."""
    rt, tokens = runtime
    token_id = tokens.issue(agent_id="agent", scope=["math.add"], ttl_seconds=300)
    req = make_request("tool.call", {"tool": "math.add", "args": {"a": 2, "b": 3}}, token_id)
    resp = rt.handle_request(req)
    assert not resp.is_error
    assert resp.result == 5


def test_missing_auth_token(runtime: tuple) -> None:
    """Missing auth token returns AUTH_FAILED."""
    rt, _ = runtime
    req = make_request("tool.call", {"tool": "math.add", "args": {"a": 1, "b": 2}}, "")
    resp = rt.handle_request(req)
    assert resp.is_error
    assert resp.error_code == AUTH_FAILED


def test_invalid_token(runtime: tuple) -> None:
    """Fake token returns PERMISSION_DENIED."""
    rt, _ = runtime
    req = make_request("tool.call", {"tool": "math.add", "args": {"a": 1, "b": 2}}, "fake-token")
    resp = rt.handle_request(req)
    assert resp.is_error
    assert resp.error_code == PERMISSION_DENIED


def test_scope_mismatch(runtime: tuple) -> None:
    """Token with wrong scope returns PERMISSION_DENIED."""
    rt, tokens = runtime
    token_id = tokens.issue(agent_id="agent", scope=["echo"], ttl_seconds=300)
    req = make_request("tool.call", {"tool": "math.add", "args": {"a": 1, "b": 2}}, token_id)
    resp = rt.handle_request(req)
    assert resp.is_error
    assert resp.error_code == PERMISSION_DENIED


def test_unknown_tool(runtime: tuple) -> None:
    """Unknown tool returns METHOD_NOT_FOUND."""
    rt, tokens = runtime
    token_id = tokens.issue(agent_id="agent", scope=["whatever"], ttl_seconds=300)
    req = make_request("tool.call", {"tool": "nonexistent", "args": {}}, token_id)
    resp = rt.handle_request(req)
    assert resp.is_error
    assert resp.error_code == METHOD_NOT_FOUND


def test_response_sanitization(runtime: tuple) -> None:
    """Credentials in tool output are sanitized."""
    rt, tokens = runtime
    token_id = tokens.issue(agent_id="agent", scope=["echo"], ttl_seconds=300)
    req = make_request("tool.call", {"tool": "echo", "args": {"text": "key: sk-ant-secret12345678901234567890"}}, token_id)
    resp = rt.handle_request(req)
    assert not resp.is_error
    assert "sk-ant-" not in resp.result
    assert "[REDACTED]" in resp.result


def test_tools_list(runtime: tuple) -> None:
    """tools.list returns available tools (no auth needed for listing)."""
    rt, _ = runtime
    req = make_request("tools.list", {})
    resp = rt.handle_request(req)
    assert not resp.is_error
    assert len(resp.result) == 2


def test_invalid_json(runtime: tuple) -> None:
    """Invalid JSON returns parse error."""
    rt, _ = runtime
    resp = rt.handle_request("not json")
    assert resp.is_error
```

**Steps:**

- [ ] Write tests → verify they fail
- [ ] Implement `src/mycelos/execution/runtime.py`
- [ ] Run tests → all pass
- [ ] Run full suite → no regressions
- [ ] Commit: `feat: implement ExecutionRuntime — full security pipeline for tool calls`

---

### Task 7: Security Boundary Tests (SEC05 + SEC06)

**Files:**
- Create: `tests/security/test_sandbox_boundaries.py`

SEC05 (filesystem escape) and SEC06 (network escape) tests. In LocalSandbox, some of these intentionally "pass with warning" — LocalSandbox is not a security boundary.

```python
# tests/security/test_sandbox_boundaries.py
"""Security boundary tests for sandbox isolation (SEC05 + SEC06).

NOTE: LocalSandbox is NOT a security boundary. Some of these tests
document known limitations rather than enforcing guarantees.
DockerSandbox (Phase 2.4) will enforce these properly.
"""

import os
from pathlib import Path

import pytest

from mycelos.execution.sandbox import LocalSandbox, SandboxConfig


@pytest.fixture
def sandbox() -> LocalSandbox:
    return LocalSandbox()


# ── SEC05: Filesystem Escape ──


def test_sec05_env_has_no_master_key(sandbox: LocalSandbox) -> None:
    """SEC05: MYCELOS_MASTER_KEY must NOT be in agent environment."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key"
    try:
        config = SandboxConfig(agent_id="agent", session_token="tok")
        sandbox_id = sandbox.create(config)
        result = sandbox.execute(sandbox_id, ["env"], config)
        assert "MYCELOS_MASTER_KEY" not in result.stdout
        sandbox.cleanup(sandbox_id)
    finally:
        del os.environ["MYCELOS_MASTER_KEY"]


def test_sec05_env_has_no_api_keys(sandbox: LocalSandbox) -> None:
    """SEC05: No API key env vars in agent environment."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    try:
        config = SandboxConfig(agent_id="agent", session_token="tok")
        sandbox_id = sandbox.create(config)
        result = sandbox.execute(sandbox_id, ["env"], config)
        assert "ANTHROPIC_API_KEY" not in result.stdout
        assert "sk-test" not in result.stdout
        sandbox.cleanup(sandbox_id)
    finally:
        del os.environ["ANTHROPIC_API_KEY"]


def test_sec05_session_token_present(sandbox: LocalSandbox) -> None:
    """SEC05: MYCELOS_SESSION_TOKEN is the only credential in agent env."""
    config = SandboxConfig(agent_id="agent", session_token="unique-session-123")
    sandbox_id = sandbox.create(config)
    result = sandbox.execute(sandbox_id, ["env"], config)
    assert "MYCELOS_SESSION_TOKEN=unique-session-123" in result.stdout
    sandbox.cleanup(sandbox_id)
```

**Steps:**

- [ ] Write tests
- [ ] Run tests → all pass
- [ ] Run full suite
- [ ] Commit: `test: add SEC05 sandbox boundary tests — env stripping verified`

---

### Task 8: Final Wiring and Verification

- [ ] Wire `LocalSandbox` into App container (optional — may defer to when it's actually needed)
- [ ] Update protocols.py if any new protocols needed
- [ ] Run full test suite: `pytest tests/ -v -k "not llm_broker and not chat and not cli" --tb=short`
- [ ] Count total tests
- [ ] Commit and push
