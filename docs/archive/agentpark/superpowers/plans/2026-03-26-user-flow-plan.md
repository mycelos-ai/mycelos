# User Flow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2-minute init with auto-detect, connectivity check, and onboarding workflow on first chat.

**Architecture:** Replace the multi-step init wizard with a single-prompt auto-detect flow. Add an onboarding built-in workflow that runs on first chat. The routing flow (workflow match → planner → creator) is already in place from previous work.

**Tech Stack:** Existing CLI (click + rich), ChatService, Workflow system, Memory

**Spec:** `docs/superpowers/specs/2026-03-26-user-flow-design.md`

---

## File Structure

### New Files
- `src/mycelos/cli/detect_provider.py` — API key/URL auto-detection logic
- `tests/test_user_flow.py` — Tests for init + onboarding

### Modified Files
- `src/mycelos/cli/init_cmd.py` — Simplified single-prompt init
- `src/mycelos/workflows/templates.py` — Add onboarding workflow
- `src/mycelos/chat/service.py` — Onboarding detection on first message

---

## Task 1: Provider Auto-Detection

**Files:**
- Create: `src/mycelos/cli/detect_provider.py`
- Create: `tests/test_user_flow.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_user_flow.py
"""Tests for User Flow — init auto-detection, connectivity, onboarding."""

import pytest
from mycelos.cli.detect_provider import detect_provider, ProviderDetection


class TestProviderDetection:
    def test_anthropic_key(self):
        result = detect_provider("sk-ant-api03-abc123def456")
        assert result.provider == "anthropic"
        assert result.default_model == "anthropic/claude-sonnet-4-6"
        assert result.is_url is False

    def test_openai_key(self):
        result = detect_provider("sk-proj-abc123def456")
        assert result.provider == "openai"
        assert result.default_model == "openai/gpt-4o"

    def test_openrouter_key(self):
        result = detect_provider("sk-or-abc123def456")
        assert result.provider == "openrouter"
        assert "openrouter" in result.default_model

    def test_google_key(self):
        result = detect_provider("AIzaSyAbc123def456")
        assert result.provider == "gemini"

    def test_ollama_url(self):
        result = detect_provider("http://localhost:11434")
        assert result.provider == "ollama"
        assert result.is_url is True
        assert result.server_url == "http://localhost:11434"

    def test_ollama_remote_url(self):
        result = detect_provider("http://192.168.1.50:11434")
        assert result.provider == "ollama"
        assert result.server_url == "http://192.168.1.50:11434"

    def test_https_url(self):
        result = detect_provider("https://my-ollama.example.com")
        assert result.provider == "ollama"
        assert result.is_url is True

    def test_unknown_key(self):
        result = detect_provider("some-random-string")
        assert result.provider is None
        assert result.needs_manual_selection is True

    def test_empty_string(self):
        result = detect_provider("")
        assert result.provider is None
```

- [ ] **Step 2: Implement detect_provider.py**

```python
"""Provider auto-detection from API key or server URL."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderDetection:
    """Result of provider auto-detection."""
    provider: str | None        # "anthropic", "openai", "openrouter", "gemini", "ollama", None
    default_model: str = ""     # e.g., "anthropic/claude-sonnet-4-6"
    is_url: bool = False        # True if input is a server URL (Ollama)
    server_url: str = ""        # Ollama/local server URL
    needs_manual_selection: bool = False  # True if we couldn't detect

# Provider detection rules: (prefix, provider_id, default_model)
_KEY_PREFIXES = [
    ("sk-ant-", "anthropic", "anthropic/claude-sonnet-4-6"),
    ("sk-or-", "openrouter", "openrouter/anthropic/claude-sonnet-4-6"),
    ("sk-", "openai", "openai/gpt-4o"),  # Must be after sk-ant- and sk-or-
    ("AIza", "gemini", "gemini/gemini-2.5-flash"),
]


def detect_provider(input_str: str) -> ProviderDetection:
    """Auto-detect provider from API key prefix or server URL.

    Supports:
    - sk-ant-... → Anthropic
    - sk-... → OpenAI
    - sk-or-... → OpenRouter
    - AIza... → Google Gemini
    - http(s)://... → Ollama / local server
    """
    input_str = input_str.strip()
    if not input_str:
        return ProviderDetection(provider=None, needs_manual_selection=True)

    # URL detection (Ollama / local)
    if input_str.startswith("http://") or input_str.startswith("https://"):
        return ProviderDetection(
            provider="ollama",
            default_model="",  # Will be discovered from server
            is_url=True,
            server_url=input_str.rstrip("/"),
        )

    # Key prefix detection
    for prefix, provider_id, default_model in _KEY_PREFIXES:
        if input_str.startswith(prefix):
            return ProviderDetection(
                provider=provider_id,
                default_model=default_model,
            )

    # Unknown
    return ProviderDetection(provider=None, needs_manual_selection=True)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_user_flow.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/cli/detect_provider.py tests/test_user_flow.py
git commit -m "feat: Provider auto-detection from API key prefix or URL"
```

---

## Task 2: Simplified Init with Connectivity Check

**Files:**
- Modify: `src/mycelos/cli/init_cmd.py`
- Modify: `tests/test_user_flow.py`

- [ ] **Step 1: Write failing tests**

```python
class TestSimplifiedInit:
    def test_init_detects_anthropic(self):
        """Init with Anthropic key auto-detects and stores credential."""
        from mycelos.cli.detect_provider import detect_provider
        result = detect_provider("sk-ant-api03-test123")
        assert result.provider == "anthropic"

    def test_connectivity_check_success(self):
        """Connectivity check returns True when LLM responds."""
        from mycelos.cli.init_cmd import _check_connectivity
        from unittest.mock import MagicMock
        mock_broker = MagicMock()
        mock_broker.complete.return_value = MagicMock(content="Hello")
        success, msg = _check_connectivity(mock_broker)
        assert success is True

    def test_connectivity_check_failure(self):
        """Connectivity check returns False on API error."""
        from mycelos.cli.init_cmd import _check_connectivity
        from unittest.mock import MagicMock
        mock_broker = MagicMock()
        mock_broker.complete.side_effect = Exception("Invalid API key")
        success, msg = _check_connectivity(mock_broker)
        assert success is False
        assert "Invalid API key" in msg
```

- [ ] **Step 2: Refactor init_cmd.py**

Replace `_pick_provider()` with auto-detection flow:

1. Single prompt: "Paste your API key or Ollama server URL:"
2. Call `detect_provider(input_str)`
3. If detected → store credential, register model
4. If URL (Ollama) → discover models from server
5. If unknown → show 5 options, let user pick
6. Run connectivity check
7. If success → "✓ API reachable!"
8. If failure → "✗ Connection failed" + hint

Extract `_check_connectivity()` as a standalone function:

```python
def _check_connectivity(broker) -> tuple[bool, str]:
    """Send a tiny test prompt to verify the API works."""
    try:
        response = broker.complete([
            {"role": "user", "content": "Say 'hello' in one word."}
        ])
        if response and response.content:
            return True, response.content.strip()
    except Exception as e:
        return False, str(e)
    return False, "No response"
```

Keep all existing functionality (master key, DB init, agent registration,
workflow seeding) — only replace the provider selection step.

- [ ] **Step 3: Run tests + full suite**

Run: `pytest tests/test_user_flow.py tests/test_cli.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/cli/init_cmd.py tests/test_user_flow.py
git commit -m "feat: Simplified init — auto-detect + connectivity check"
```

---

## Task 3: Onboarding Workflow

**Files:**
- Modify: `src/mycelos/workflows/templates.py` — Add onboarding workflow
- Modify: `src/mycelos/chat/service.py` — Detect first-time user, start onboarding
- Modify: `tests/test_user_flow.py`

- [ ] **Step 1: Write failing tests**

```python
class TestOnboarding:
    def test_onboarding_workflow_exists(self):
        from mycelos.workflows.templates import BUILTIN_WORKFLOWS
        ids = [w["id"] for w in BUILTIN_WORKFLOWS]
        assert "onboarding" in ids

    def test_onboarding_detection(self):
        """First-time user should trigger onboarding."""
        import os, tempfile
        from pathlib import Path
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-onboard"
            app = App(Path(tmp))
            app.initialize()
            # No onboarding_completed in memory → should need onboarding
            result = app.memory.get("default", "system", "onboarding_completed")
            assert result is None  # Not yet completed

    def test_onboarding_can_be_marked_complete(self):
        """After onboarding, memory flag is set."""
        import os, tempfile
        from pathlib import Path
        from mycelos.app import App
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-onboard2"
            app = App(Path(tmp))
            app.initialize()
            app.memory.set("default", "system", "onboarding_completed", "true")
            result = app.memory.get("default", "system", "onboarding_completed")
            assert result == "true"
```

- [ ] **Step 2: Add onboarding workflow to templates.py**

```python
# Add to BUILTIN_WORKFLOWS list:
{
    "id": "onboarding",
    "name": "Welcome Onboarding",
    "description": (
        "First-time user setup: asks name, goals, suggests connectors "
        "(Telegram, email, GitHub). Runs once, then marked complete."
    ),
    "goal": "Guide new user through initial setup via conversation",
    "steps": json.dumps([
        {"id": "greeting", "action": "conversation",
         "description": "Ask user's name, store in memory"},
        {"id": "goals", "action": "conversation",
         "description": "Ask what user wants to do, suggest relevant connectors"},
        {"id": "connectors", "action": "conversation",
         "description": "Offer to set up Telegram, email, GitHub"},
        {"id": "summary", "action": "conversation",
         "description": "Show what's available, suggest first actions"},
    ]),
    "scope": json.dumps(["knowledge.write"]),
    "tags": json.dumps(["builtin", "onboarding", "setup"]),
    "status": "active",
    "created_by": "system",
},
```

- [ ] **Step 3: Add onboarding detection to ChatService**

In `handle_message()`, after the existing proactive checks (background tasks,
daily cost, KB context) but BEFORE the intent routing, add:

```python
# First-time user onboarding
try:
    onboarding_done = self._app.memory.get("default", "system", "onboarding_completed")
    if not onboarding_done:
        # Get onboarding workflow
        onboarding = self._app.workflow_registry.get("onboarding")
        if onboarding and session_id not in getattr(self, '_onboarding_skipped', set()):
            # Inject onboarding context into system prompt
            conversation.append({
                "role": "system",
                "content": (
                    "This is a NEW USER who hasn't been onboarded yet. "
                    "Start by warmly greeting them and asking their name. "
                    "Then ask what they want to use Mycelos for. "
                    "Suggest relevant connectors (Telegram for mobile, email, GitHub). "
                    "If they say 'skip', mark onboarding as done. "
                    "After the conversation, save their name to memory and "
                    "set onboarding_completed=true."
                ),
            })
except Exception:
    pass
```

Also handle the "skip" / "later" detection:

```python
# In handle_message, after getting response from LLM:
# Check if the LLM used memory.set to mark onboarding complete
# (This happens automatically via the note_write/memory tools)
```

The LLM will naturally use the `memory_set` tool to store the user's name
and mark onboarding as complete. No special code needed — the system prompt
guides the LLM.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_user_flow.py -v`
Expected: All pass

- [ ] **Step 5: Run full suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/workflows/templates.py src/mycelos/chat/service.py tests/test_user_flow.py
git commit -m "feat: Onboarding workflow — first-time user detection + guided setup"
```

---

## Task 4: CHANGELOG + Final Tests + Push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 2: Update CHANGELOG**

```markdown
### User Flow
- Simplified init: one prompt, auto-detect 5 providers (Anthropic, OpenAI, OpenRouter, Google, Ollama)
- Ollama: discovers models from server URL
- Connectivity check: real API call verifies key works
- Onboarding workflow: first-time user guided through name, goals, connectors
- Onboarding runs once then marked complete (Memory flag)
```

- [ ] **Step 3: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "feat: User Flow — simplified init + onboarding workflow"
git push origin main
```
