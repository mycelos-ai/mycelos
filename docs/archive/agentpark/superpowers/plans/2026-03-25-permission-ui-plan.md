# Permission UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interactive, agent-scoped permission prompts with 5 options across Terminal (Rich panel), Telegram (inline buttons), and Web (radio widget).

**Architecture:** Extend existing `PermissionRequired` → `_handle_permission_response()` flow with 5 granular scope options. Each channel renders the prompt natively. Permanent grants trigger config generation. Telegram callbacks are user-verified.

**Tech Stack:** Rich (Terminal), aiogram InlineKeyboardMarkup (Telegram), inline JS (Web), existing i18n system

**Spec:** `docs/superpowers/specs/2026-03-25-permission-ui-design.md`

---

## File Structure

### New Files
- `tests/test_permission_ui.py` — Tests for all permission UI changes

### Modified Files
- `src/mycelos/security/permissions.py` — Add `allow_all_always` + config generation
- `src/mycelos/chat/service.py` — 5-option prompt format + response handling
- `src/mycelos/channels/telegram.py` — Inline keyboard + callback handler
- `src/mycelos/frontend/out/index.html` — Permission widget script
- `src/mycelos/i18n/locales/en.yaml` — Permission i18n keys
- `src/mycelos/i18n/locales/de.yaml` — Permission i18n keys (German)

---

## Task 1: Backend — 5-Option Permission + Config Generation

**Files:**
- Modify: `src/mycelos/security/permissions.py`
- Modify: `src/mycelos/chat/service.py`
- Modify: `src/mycelos/i18n/locales/en.yaml`
- Modify: `src/mycelos/i18n/locales/de.yaml`
- Create: `tests/test_permission_ui.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_permission_ui.py
"""Tests for Permission UI — 5-option prompt, agent-scoped grants."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.security.permissions import PermissionRequired, grant_permission


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-perm"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def permission():
    return PermissionRequired(
        tool="filesystem_read",
        action="mount add /tmp/test --rw",
        reason="Need read access to check files",
        target="/tmp/test",
        action_type="mount",
    )


class TestGrantPermission:
    def test_allow_session(self, app, permission):
        grants = set()
        result = grant_permission(app, permission, "allow_session",
                                   agent_id="creator", session_grants=grants)
        assert "creator:filesystem_read:/tmp/test" in grants

    def test_always_allow_creates_policy(self, app, permission):
        grant_permission(app, permission, "always_allow", agent_id="creator")
        decision = app.policy_engine.evaluate("default", "creator", "filesystem_read")
        assert decision == "always"

    def test_allow_all_always_creates_global_policy(self, app, permission):
        grant_permission(app, permission, "allow_all_always", agent_id="creator")
        # agent_id=None → applies to any agent
        decision = app.policy_engine.evaluate("default", "other-agent", "filesystem_read")
        assert decision == "always"

    def test_always_allow_triggers_config_generation(self, app, permission):
        gen_before = app.config.get_active_generation_id()
        grant_permission(app, permission, "always_allow", agent_id="creator")
        gen_after = app.config.get_active_generation_id()
        assert gen_after > gen_before

    def test_allow_all_triggers_config_generation(self, app, permission):
        gen_before = app.config.get_active_generation_id()
        grant_permission(app, permission, "allow_all_always", agent_id="creator")
        gen_after = app.config.get_active_generation_id()
        assert gen_after > gen_before

    def test_never_allow_triggers_config_generation(self, app, permission):
        gen_before = app.config.get_active_generation_id()
        grant_permission(app, permission, "never_allow", agent_id="creator")
        gen_after = app.config.get_active_generation_id()
        assert gen_after > gen_before

    def test_deny_does_not_create_policy(self, app, permission):
        gen_before = app.config.get_active_generation_id()
        grant_permission(app, permission, "deny", agent_id="creator")
        gen_after = app.config.get_active_generation_id()
        assert gen_after == gen_before


class TestHandlePermissionResponse:
    """Test _handle_permission_response accepts 1-5 and legacy shortcuts."""

    def _make_service(self, app):
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        service._pending_permission = {
            "tool_name": "filesystem_read",
            "tool_call_id": "tc-1",
            "args": {"path": "/tmp/test"},
            "permission": PermissionRequired(
                tool="filesystem_read",
                action="mount add /tmp/test --rw",
                reason="Need read access",
                target="/tmp/test",
            ),
            "session_id": "test-sess",
            "user_id": "default",
            "agent_id": "creator",
            "conversation": [],
        }
        return service

    def test_input_1_maps_to_allow_session(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("1", service._pending_permission)
        # Should not raise, should process

    def test_input_2_maps_to_always_allow(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("2", service._pending_permission)
        decision = app.policy_engine.evaluate("default", "creator", "filesystem_read")
        assert decision == "always"

    def test_input_3_maps_to_allow_all(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("3", service._pending_permission)
        decision = app.policy_engine.evaluate("default", "any-agent", "filesystem_read")
        assert decision == "always"

    def test_input_4_maps_to_deny(self, app):
        service = self._make_service(app)
        events = service._handle_permission_response("4", service._pending_permission)
        assert any("denied" in str(e.data).lower() for e in events)

    def test_input_5_maps_to_never(self, app):
        service = self._make_service(app)
        events = service._handle_permission_response("5", service._pending_permission)
        decision = app.policy_engine.evaluate("default", "creator", "filesystem_read")
        assert decision == "never"

    def test_legacy_y_still_works(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("y", service._pending_permission)
        # Should not raise

    def test_legacy_a_still_works(self, app):
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response("a", service._pending_permission)

    def test_perm_prefix_from_web(self, app):
        """Web sends PERM:{id}:{value} format."""
        service = self._make_service(app)
        with patch.object(service, '_execute_tool', return_value={"ok": True}):
            events = service._handle_permission_response(
                "PERM:abc123:1", service._pending_permission)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_permission_ui.py -v`
Expected: Failures (allow_all_always not implemented yet)

- [ ] **Step 3: Add i18n keys**

Add to `src/mycelos/i18n/locales/en.yaml`:
```yaml
# --- Permissions ---
permission:
  title: "Permission Required"
  agent_needs: "{agent} needs:"
  choose: "Choose [1-5, default=1]"
  option_allow_session: "Allow for {agent} (this session)"
  option_allow_always: "Allow for {agent} (always)"
  option_allow_all: "Allow for all agents (always)"
  option_deny: "Deny"
  option_never: "Never allow for {agent}"
  granted: "Permission granted."
  denied: "Permission denied."
  expired: "Permission request expired."
```

Add to `src/mycelos/i18n/locales/de.yaml`:
```yaml
permission:
  title: "Berechtigung erforderlich"
  agent_needs: "{agent} braucht:"
  choose: "Auswahl [1-5, Standard=1]"
  option_allow_session: "Erlauben für {agent} (diese Sitzung)"
  option_allow_always: "Erlauben für {agent} (immer)"
  option_allow_all: "Erlauben für alle Agents (immer)"
  option_deny: "Ablehnen"
  option_never: "Nie erlauben für {agent}"
  granted: "Berechtigung erteilt."
  denied: "Berechtigung abgelehnt."
  expired: "Berechtigungsanfrage abgelaufen."
```

- [ ] **Step 4: Extend grant_permission() in permissions.py**

Add `allow_all_always` handling and config generation:

```python
def grant_permission(app, permission, decision, agent_id=None, session_grants=None):
    from mycelos.chat.slash_commands import handle_slash_command

    if decision in ("deny", "never_allow"):
        if decision == "never_allow":
            app.policy_engine.set_policy("default", agent_id, permission.tool, "never")
            app.config.apply_from_state()
            app.audit.log("permission.never", details={
                "tool": permission.tool, "action": permission.action,
                "agent_id": agent_id,
            })
        return "Permission denied."

    # Execute the action (e.g., create mount)
    result = handle_slash_command(app, f"/{permission.action}")

    if decision == "allow_all_always":
        # Global — agent_id=None means all agents
        app.policy_engine.set_policy("default", None, permission.tool, "always")
        app.config.apply_from_state()
        app.audit.log("permission.allow_all", details={
            "tool": permission.tool, "action": permission.action,
            "agent_id": None,
        })
    elif decision == "always_allow":
        # Agent-scoped permanent
        app.policy_engine.set_policy("default", agent_id, permission.tool, "always")
        app.config.apply_from_state()
        app.audit.log("permission.always_allow", details={
            "tool": permission.tool, "action": permission.action,
            "agent_id": agent_id,
        })
    else:
        # Session-scoped
        if session_grants is not None:
            grant_key = f"{agent_id or '*'}:{permission.tool}:{permission.target}"
            session_grants.add(grant_key)
        app.audit.log("permission.allow_session", details={
            "tool": permission.tool, "action": permission.action,
            "agent_id": agent_id,
        })

    return result
```

- [ ] **Step 5: Update _handle_permission_response in service.py**

Update the decision mapping to accept `1-5`, `PERM:` prefix, and legacy shortcuts:

```python
def _handle_permission_response(self, message, pending):
    from mycelos.security.permissions import grant_permission

    events = []
    self._pending_permission = None

    cleaned = message.strip()
    perm = pending["permission"]
    agent_id = pending.get("agent_id", "mycelos")

    # Parse PERM:{id}:{value} from web
    if cleaned.startswith("PERM:"):
        parts = cleaned.split(":")
        if len(parts) >= 3:
            cleaned = parts[2]

    cleaned_lower = cleaned.lower()

    # Map input to decision
    if cleaned in ("1",) or cleaned_lower in ("", "y", "yes", "ja", "ok"):
        decision = "allow_session"
    elif cleaned in ("2",) or cleaned_lower in ("a", "always", "immer"):
        decision = "always_allow"
    elif cleaned == "3":
        decision = "allow_all_always"
    elif cleaned in ("4",) or cleaned_lower in ("n", "no", "nein"):
        decision = "deny"
    elif cleaned in ("5",) or cleaned_lower in ("!", "never", "nie"):
        decision = "never_allow"
    else:
        decision = "allow_session"

    # ... rest of existing logic (deny path, grant path, re-execute tool)
```

- [ ] **Step 6: Update permission prompt format in service.py**

Where PermissionRequired is caught (~line 629), update the prompt text to use i18n and show 5 numbered options with agent name:

```python
from mycelos.i18n import t

agent_name = current_agent.replace("-", " ").title()
prompt_text = (
    f"**{t('permission.title')}**\n\n"
    f"[{agent_name}] `{perm.tool}`\n"
    f"  `{perm.target}`\n"
    f"  _{perm.reason}_\n\n"
    f"  1. {t('permission.option_allow_session', agent=agent_name)}\n"
    f"  2. {t('permission.option_allow_always', agent=agent_name)}\n"
    f"  3. {t('permission.option_allow_all')}\n"
    f"  4. {t('permission.option_deny')}\n"
    f"  5. {t('permission.option_never', agent=agent_name)}\n\n"
    f"{t('permission.choose')}"
)
events.append(system_response_event(prompt_text))
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_permission_ui.py -v`
Expected: All pass

- [ ] **Step 8: Run full suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add src/mycelos/security/permissions.py src/mycelos/chat/service.py \
       src/mycelos/i18n/locales/en.yaml src/mycelos/i18n/locales/de.yaml \
       tests/test_permission_ui.py
git commit -m "feat: Permission UI — 5-option agent-scoped prompts + config generation"
```

---

## Task 2: Telegram Inline Buttons

**Files:**
- Modify: `src/mycelos/channels/telegram.py`
- Modify: `tests/test_permission_ui.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_permission_ui.py`:

```python
class TestTelegramPermissionButtons:
    def test_build_permission_keyboard(self):
        from mycelos.channels.telegram import _build_permission_keyboard
        keyboard = _build_permission_keyboard("perm123", "Creator")
        # Should have 3 rows of buttons
        assert len(keyboard.inline_keyboard) == 3
        # First row: Allow session + Allow always
        assert len(keyboard.inline_keyboard[0]) == 2
        # Check callback data format
        first_btn = keyboard.inline_keyboard[0][0]
        assert first_btn.callback_data == "perm:allow_session:perm123"

    def test_parse_permission_callback(self):
        from mycelos.channels.telegram import _parse_permission_callback
        decision, perm_id = _parse_permission_callback("perm:allow_always:abc123")
        assert decision == "allow_always"
        assert perm_id == "abc123"

    def test_invalid_callback_returns_none(self):
        from mycelos.channels.telegram import _parse_permission_callback
        result = _parse_permission_callback("invalid:data")
        assert result is None
```

- [ ] **Step 2: Implement Telegram inline keyboard**

In `src/mycelos/channels/telegram.py`:

1. Add imports:
```python
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
```

2. Add module-level pending permissions dict:
```python
_pending_permissions: dict[str, dict] = {}  # permission_id → {permission, user_id, ...}
```

3. Add helper functions:
```python
def _build_permission_keyboard(permission_id: str, agent_name: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for permission prompt."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Allow {agent_name} (session)",
                callback_data=f"perm:allow_session:{permission_id}"),
            InlineKeyboardButton(
                text=f"Allow {agent_name} (always)",
                callback_data=f"perm:allow_always:{permission_id}"),
        ],
        [
            InlineKeyboardButton(
                text="Allow all agents",
                callback_data=f"perm:allow_all:{permission_id}"),
            InlineKeyboardButton(
                text="Deny",
                callback_data=f"perm:deny:{permission_id}"),
        ],
        [
            InlineKeyboardButton(
                text=f"Never for {agent_name}",
                callback_data=f"perm:never:{permission_id}"),
        ],
    ])


def _parse_permission_callback(data: str) -> tuple[str, str] | None:
    """Parse 'perm:{decision}:{id}' callback data."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "perm":
        return None
    return parts[1], parts[2]
```

4. Add callback handler (BEFORE existing `@dp.message` handlers):
```python
@dp.callback_query(F.data.startswith("perm:"))
async def handle_permission_callback(callback: types.CallbackQuery) -> None:
    """Handle permission button clicks."""
    result = _parse_permission_callback(callback.data)
    if not result:
        await callback.answer("Invalid callback")
        return

    decision_key, perm_id = result
    pending = _pending_permissions.get(perm_id)

    if not pending:
        await callback.answer("Permission request expired")
        return

    # SECURITY: verify the clicking user matches the requesting user
    if callback.from_user.id != pending["telegram_user_id"]:
        await callback.answer("Not your permission prompt")
        return

    # Map callback to grant_permission decision string
    DECISION_MAP = {
        "allow_session": "allow_session",
        "allow_always": "always_allow",
        "allow_all": "allow_all_always",
        "deny": "deny",
        "never": "never_allow",
    }
    decision = DECISION_MAP.get(decision_key, "deny")

    # Process via ChatService
    del _pending_permissions[perm_id]

    # Send decision as message to ChatService
    user_id = pending["telegram_user_id"]
    session_id = _get_session(user_id)
    # Use the numeric mapping so _handle_permission_response can parse it
    DECISION_TO_NUM = {
        "allow_session": "1", "always_allow": "2",
        "allow_all_always": "3", "deny": "4", "never_allow": "5",
    }
    events = _chat_service.handle_message(
        DECISION_TO_NUM[decision], session_id, f"telegram:{user_id}"
    )

    # Edit original message to show result
    result_text = "Granted" if decision not in ("deny", "never_allow") else "Denied"
    await callback.message.edit_text(
        f"{callback.message.text}\n\n✅ {result_text}",
        reply_markup=None,
    )
    await callback.answer(result_text)

    # Send response if any
    response_text = _render_events(events)
    if response_text:
        await _safe_answer(callback.message, response_text)
```

5. In the existing `handle_message` function, detect permission widget events and send inline keyboard. After calling `_chat_service.handle_message()`, check if events contain a permission prompt:

```python
# After getting events from chat_service, check for permission prompt
for event in events:
    if event.type == "widget" and event.data.get("widget", {}).get("type") == "permission_prompt":
        widget = event.data["widget"]
        import uuid
        perm_id = uuid.uuid4().hex[:12]
        agent_name = widget.get("agent", "Agent")

        _pending_permissions[perm_id] = {
            "telegram_user_id": user_id,
            "permission": widget,
        }

        keyboard = _build_permission_keyboard(perm_id, agent_name)
        text = (
            f"⚠️ *Permission Required*\n\n"
            f"*{agent_name}* → `{widget.get('tool', '')}`\n"
            f"`{widget.get('target', '')}`\n"
            f"_{widget.get('reason', '')}_"
        )
        await _safe_answer(message, text, reply_markup=keyboard)
        return  # Don't send normal response — wait for button click
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_permission_ui.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/channels/telegram.py tests/test_permission_ui.py
git commit -m "feat: Telegram permission inline buttons + callback handler"
```

---

## Task 3: Web Frontend Permission Widget

**Files:**
- Modify: `src/mycelos/frontend/out/index.html`

- [ ] **Step 1: Add permission widget script**

Add to the existing inline script in `index.html`, inside the IIFE:

```javascript
// Permission Widget — intercepts permission_prompt SSE events
function handlePermissionEvent(evt) {
  var chatArea = document.querySelector('.space-y-4');
  if (!chatArea) return;

  var card = document.createElement('div');
  card.style.cssText = 'border:1px solid var(--border);border-radius:0.5rem;padding:1rem;margin:0.5rem 0;background:var(--bg-secondary,#1a1a2e);';
  card.innerHTML = '<div style="color:#f59e0b;font-weight:bold;margin-bottom:0.5rem">⚠️ Permission Required</div>'
    + '<div><strong>' + (evt.agent || 'Agent') + '</strong> → <code>' + (evt.tool || '') + '</code></div>'
    + '<div><code>' + (evt.target || '') + '</code></div>'
    + '<div style="font-style:italic;margin:0.5rem 0;opacity:0.8">"' + (evt.reason || '') + '"</div>'
    + '<div style="margin-top:0.75rem">'
    + '<label style="display:block;margin:0.25rem 0"><input type="radio" name="perm_' + (evt.permission_id||'') + '" value="1" checked> Allow for ' + (evt.agent||'Agent') + ' (this session)</label>'
    + '<label style="display:block;margin:0.25rem 0"><input type="radio" name="perm_' + (evt.permission_id||'') + '" value="2"> Allow for ' + (evt.agent||'Agent') + ' (always)</label>'
    + '<label style="display:block;margin:0.25rem 0"><input type="radio" name="perm_' + (evt.permission_id||'') + '" value="3"> Allow for all agents (always)</label>'
    + '<label style="display:block;margin:0.25rem 0"><input type="radio" name="perm_' + (evt.permission_id||'') + '" value="4"> Deny</label>'
    + '<label style="display:block;margin:0.25rem 0"><input type="radio" name="perm_' + (evt.permission_id||'') + '" value="5"> Never allow for ' + (evt.agent||'Agent') + '</label>'
    + '</div>'
    + '<button style="margin-top:0.5rem;padding:0.5rem 1rem;border-radius:0.25rem;background:var(--accent,#3b82f6);color:white;border:none;cursor:pointer" onclick="submitPerm(this,\'' + (evt.permission_id||'') + '\')">Confirm</button>';
  chatArea.appendChild(card);
  chatArea.scrollTop = chatArea.scrollHeight;
}

window.submitPerm = function(btn, permId) {
  var card = btn.parentNode;
  var selected = card.querySelector('input[name=perm_' + permId + ']:checked');
  var value = selected ? selected.value : '1';
  var sessionId = window.__mycelosSessionId || '';

  // Send as PERM:{id}:{value}
  fetch('/api/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      message: 'PERM:' + permId + ':' + value,
      session_id: sessionId,
    })
  });

  // Disable card
  card.style.opacity = '0.5';
  btn.disabled = true;
  btn.textContent = 'Submitted';
};
```

Also update the SSE reader to detect permission events:
```javascript
// In the existing SSE reader (both chat and audio), add:
if (evt.type === 'widget' && evt.widget && evt.widget.type === 'permission_prompt') {
  handlePermissionEvent(evt.widget);
}
```

- [ ] **Step 2: Test manually**

Start gateway, trigger a filesystem read on an unmounted path, verify widget appears.

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/frontend/out/index.html
git commit -m "feat: Web Frontend permission widget with radio buttons"
```

---

## Task 4: Final Tests + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 2: Update CHANGELOG**

```markdown
### Permission UI
- 5 agent-scoped permission options (session/always/all-agents/deny/never)
- Terminal: Rich panel with numbered selection + i18n (de/en)
- Telegram: Inline keyboard buttons with user-verified callbacks
- Web: Radio button permission widget
- Permanent grants trigger config generation (rollbackable)
- Legacy Y/A/N/! shortcuts still work
- PERM:{id}:{value} protocol for web responses
```

- [ ] **Step 3: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "feat: Permission UI — interactive, agent-scoped, multi-channel"
git push origin main
```
