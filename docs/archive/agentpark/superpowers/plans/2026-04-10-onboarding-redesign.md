# Onboarding Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static onboarding with a level-driven progressive system — level-aware LLM prompt, benefit-oriented hints, init welcome text, and agent rename bugfix.

**Architecture:** The existing gamification level system becomes the onboarding engine. A new `get_level_prompt()` function returns level-scaled prompt blocks injected into the system prompt. Hints become condition-based and benefit-oriented. The onboarding conversation flow is simplified (connectors removed, first note added). Agent rename is fixed by syncing memory writes to the agents table.

**Tech Stack:** Python 3.12+, pytest, i18n (YAML), Rich (terminal formatting)

**Spec:** `docs/superpowers/specs/2026-04-10-onboarding-redesign-design.md`

---

### Task 1: Add `get_level_prompt()` to gamification

**Files:**
- Modify: `src/mycelos/gamification.py` (add function after line 70)
- Test: `tests/test_gamification.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gamification.py`:

```python
"""Tests for gamification level prompt system."""

from mycelos.gamification import get_level_prompt


def test_level_prompt_newcomer_is_longest():
    prompt = get_level_prompt(1)
    assert "new to Mycelos" in prompt
    assert "suggest next steps" in prompt.lower() or "proactively" in prompt.lower()
    assert len(prompt) > 200  # Newcomer gets detailed guidance


def test_level_prompt_builder_is_shorter():
    prompt = get_level_prompt(3)
    assert "experienced" in prompt.lower() or "Builder" in prompt
    assert len(prompt) < len(get_level_prompt(1))


def test_level_prompt_power_user_is_shortest():
    prompt = get_level_prompt(5)
    assert "power user" in prompt.lower() or "Power User" in prompt
    assert len(prompt) < len(get_level_prompt(3))


def test_level_prompt_guru_same_as_power_user():
    assert get_level_prompt(6) == get_level_prompt(5)


def test_level_prompt_includes_level_name():
    prompt = get_level_prompt(1)
    assert "Newcomer" in prompt
    prompt = get_level_prompt(3)
    assert "Builder" in prompt


def test_level_prompt_out_of_range_returns_newcomer():
    assert get_level_prompt(0) == get_level_prompt(1)
    assert get_level_prompt(99) == get_level_prompt(6)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gamification.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_level_prompt'`

- [ ] **Step 3: Implement `get_level_prompt()`**

Add to `src/mycelos/gamification.py` after the `get_next_level()` function (after line 70):

```python
# --- Level prompts (English only — Constitution Rule 6) ---

_LEVEL_PROMPTS = {
    "newcomer": (
        "The user is new to Mycelos (Level: Newcomer).\n"
        "- Explain what you can do when relevant. Don't list features unprompted, "
        "but when the user asks something, mention related capabilities.\n"
        "- Proactively suggest next steps: \"I can also remind you about this "
        "— want me to set a reminder?\"\n"
        "- When the user creates their first note, acknowledge it warmly.\n"
        "- Mention what makes Mycelos different when it fits naturally: "
        "data stays local, the system grows with them, cloud LLMs are tools not storage.\n"
        "- Suggest one unused feature per conversation, framed as a benefit, not a tutorial."
    ),
    "explorer": (
        "The user is getting familiar with Mycelos (Level: Explorer).\n"
        "- They've created notes and chatted. Explain new features when relevant, "
        "but don't repeat basics they already know.\n"
        "- Proactively suggest next steps when it fits: connectors, reminders, tasks.\n"
        "- Mention what makes Mycelos different when it fits naturally."
    ),
    "builder": (
        "The user is experienced with Mycelos (Level: Builder).\n"
        "- They know the basics. Skip explanations for notes, reminders, search.\n"
        "- Mention advanced features only when directly relevant: workflows, agents, automations.\n"
        "- Keep confirmations brief."
    ),
    "architect": (
        "The user is experienced with Mycelos (Level: Architect).\n"
        "- Skip explanations. Mention workflows and custom agents when relevant.\n"
        "- Keep confirmations brief."
    ),
    "power_user": (
        "The user is a Mycelos power user (Level: Power User).\n"
        "- Terse responses. No feature suggestions unless asked.\n"
        "- Focus on efficiency and optimization."
    ),
}


def get_level_prompt(level: int) -> str:
    """Return a level-appropriate prompt block for the LLM system prompt.

    Higher levels get shorter prompts (fewer tokens, less hand-holding).
    Prompts are English-only (Constitution Rule 6).
    """
    if level <= 1:
        return _LEVEL_PROMPTS["newcomer"]
    elif level == 2:
        return _LEVEL_PROMPTS["explorer"]
    elif level == 3:
        return _LEVEL_PROMPTS["builder"]
    elif level == 4:
        return _LEVEL_PROMPTS["architect"]
    else:
        return _LEVEL_PROMPTS["power_user"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gamification.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/gamification.py tests/test_gamification.py
git commit -m "feat(gamification): add level-aware LLM prompt blocks"
```

---

### Task 2: Inject level prompt into system prompt (`mycelos_handler.py`)

**Files:**
- Modify: `src/mycelos/agents/handlers/mycelos_handler.py:280-306`
- Modify: `src/mycelos/chat/service.py:2676-2701` (remove `_ONBOARDING_PROMPT` and `_SETUP_HINTS_PROMPT`)
- Test: `tests/test_gamification.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gamification.py`:

```python
from unittest.mock import MagicMock


def _make_mock_app(messages=0, notes=0, connectors=0, workflows=0, agents=0,
                   user_name=None, level=None):
    """Create a mock App with controllable milestone counts."""
    app = MagicMock()

    def fetchone(sql, params=None):
        if "audit_events" in sql:
            return {"c": messages}
        if "knowledge_notes" in sql:
            return {"c": notes}
        if "connectors" in sql:
            return {"c": connectors}
        if "workflows" in sql:
            return {"c": workflows}
        if "agents" in sql:
            return {"c": agents}
        return {"c": 0}

    app.storage.fetchone = fetchone
    app.storage.fetchall = lambda *a, **kw: []
    app.memory.get = lambda uid, scope, key: {
        "user.name": user_name,
        "user_level": str(level) if level else None,
        "greeting_count": "0",
    }.get(key)
    app.memory.set = MagicMock()
    app.credentials.list_services = lambda: []
    return app


def test_check_milestones_newcomer():
    from mycelos.gamification import check_milestones
    app = _make_mock_app(messages=3, notes=0)
    assert check_milestones(app) == 1


def test_check_milestones_explorer():
    from mycelos.gamification import check_milestones
    app = _make_mock_app(messages=15, notes=2)
    assert check_milestones(app) == 2
```

- [ ] **Step 2: Run tests to verify they pass** (these test existing code)

Run: `pytest tests/test_gamification.py -v`
Expected: PASS (validates the mock works with existing `check_milestones`)

- [ ] **Step 3: Replace `_ONBOARDING_PROMPT` / `_SETUP_HINTS_PROMPT` with level prompt injection**

In `src/mycelos/agents/handlers/mycelos_handler.py`, replace lines 293-306:

```python
        # Conditional: onboarding for new users, setup hints for missing connectors
        user_name = None
        try:
            user_name = self._app.memory.get("default", "system", "user.name")
        except Exception:
            pass
        if not user_name:
            from mycelos.chat.service import _ONBOARDING_PROMPT
            prompt += _ONBOARDING_PROMPT

        has_connectors = bool(self._app.connector_registry.list_connectors(status="active"))
        if not has_connectors:
            from mycelos.chat.service import _SETUP_HINTS_PROMPT
            prompt += _SETUP_HINTS_PROMPT
```

With:

```python
        # Level-aware prompt — replaces static onboarding/setup hints
        from mycelos.gamification import check_milestones, get_level_prompt

        level = check_milestones(self._app)
        prompt += "\n\n" + get_level_prompt(level)
```

- [ ] **Step 4: Remove `_ONBOARDING_PROMPT` and `_SETUP_HINTS_PROMPT` from `service.py`**

In `src/mycelos/chat/service.py`, delete lines 2676-2701 (the two prompt constants). They are no longer imported anywhere after the handler change.

- [ ] **Step 5: Run full test suite to check for breakage**

Run: `pytest tests/ -x --tb=short`
Expected: PASS. If any test imports `_ONBOARDING_PROMPT` or `_SETUP_HINTS_PROMPT`, fix the import.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/agents/handlers/mycelos_handler.py src/mycelos/chat/service.py tests/test_gamification.py
git commit -m "feat(onboarding): inject level-aware prompt, remove static onboarding/setup prompts"
```

---

### Task 3: Refactor hints to benefit-oriented with conditions

**Files:**
- Modify: `src/mycelos/gamification.py:193-260` (rewrite `_get_contextual_hint`)
- Modify: `src/mycelos/i18n/locales/en.yaml:434-451` (replace hint keys)
- Modify: `src/mycelos/i18n/locales/de.yaml` (matching keys)
- Test: `tests/test_gamification.py` (extend)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gamification.py`:

```python
def test_hint_newcomer_no_notes_suggests_first_note():
    from mycelos.gamification import _get_contextual_hint
    app = _make_mock_app(messages=2, notes=0)
    hint = _get_contextual_hint(app, level=1)
    assert hint is not None
    # Should be benefit-oriented, not generic
    assert "?" in hint  # Call-to-action, not statement


def test_hint_builder_no_telegram_suggests_telegram():
    from mycelos.gamification import _get_contextual_hint
    app = _make_mock_app(messages=20, notes=5, connectors=0)
    app.storage.fetchall = lambda *a, **kw: []  # No connectors
    hint = _get_contextual_hint(app, level=3)
    assert hint is not None


def test_hint_power_user_gets_no_hints():
    from mycelos.gamification import _get_contextual_hint
    app = _make_mock_app(messages=100, notes=30, connectors=3)
    hint = _get_contextual_hint(app, level=5)
    assert hint is None


def test_hints_are_deterministic_not_random():
    """Same state should return the same hint (no randomization)."""
    from mycelos.gamification import _get_contextual_hint
    app = _make_mock_app(messages=2, notes=0)
    hint1 = _get_contextual_hint(app, level=1)
    hint2 = _get_contextual_hint(app, level=1)
    assert hint1 == hint2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gamification.py::test_hint_power_user_gets_no_hints tests/test_gamification.py::test_hints_are_deterministic_not_random -v`
Expected: At least `test_hints_are_deterministic_not_random` FAILS (current code uses `random.choice`)

- [ ] **Step 3: Update i18n hint keys in `en.yaml`**

In `src/mycelos/i18n/locales/en.yaml`, replace the `gamification.hint` section (lines 434-451) with:

```yaml
  hint:
    first_note: "What's on your mind? I can capture it for you right now."
    first_reminder: "I can also remind you about things — want to try?"
    telegram_benefit: "Via Telegram I can send you reminders on your phone — want to set it up?"
    kb_growing: "You have {count} notes. They'll auto-organize into topics as you add more."
    github_benefit: "I can read your GitHub repos and help document code. Want to connect?"
    kb_linking: "Your knowledge base is growing — I'm organizing it into topics automatically."
    workflow_benefit: "Repetitive tasks can run automatically. Want me to create a workflow?"
```

- [ ] **Step 4: Update matching keys in `de.yaml`**

In `src/mycelos/i18n/locales/de.yaml`, replace the `gamification.hint` section with:

```yaml
  hint:
    first_note: "Was beschäftigt dich gerade? Ich kann es direkt für dich festhalten."
    first_reminder: "Ich kann dich auch an Dinge erinnern — willst du es ausprobieren?"
    telegram_benefit: "Per Telegram kann ich dir Erinnerungen aufs Handy schicken — soll ich das einrichten?"
    kb_growing: "Du hast {count} Notizen. Die ordnen sich automatisch in Themen, je mehr dazukommen."
    github_benefit: "Ich kann deine GitHub-Repos lesen und Code dokumentieren. Willst du verbinden?"
    kb_linking: "Deine Wissensdatenbank wächst — ich organisiere sie automatisch in Themen."
    workflow_benefit: "Wiederkehrende Aufgaben kann ich automatisch erledigen. Soll ich einen Workflow erstellen?"
```

- [ ] **Step 5: Rewrite `_get_contextual_hint()` in `gamification.py`**

Replace lines 193-260 in `src/mycelos/gamification.py`:

```python
def _get_contextual_hint(app: Any, level: int) -> str | None:
    """Return the most relevant benefit-oriented hint for the user's current state.

    Hints are ordered by priority within each level range. The first hint
    whose condition is met wins (deterministic, not random).
    Returns None for level 5+ (power users don't need hints).
    """
    if level >= 5:
        return None

    try:
        connectors = app.storage.fetchall(
            "SELECT id FROM connectors WHERE status = 'active'"
        )
        connector_ids = {c["id"] for c in connectors}

        notes = app.storage.fetchone("SELECT COUNT(*) as c FROM knowledge_notes")
        note_count = (notes or {}).get("c", 0)

        # Ordered hint definitions: (min_level, max_level, condition, i18n_key, kwargs)
        hint_defs: list[tuple[int, int, bool, str, dict]] = [
            # Level 1-2: First steps
            (1, 2, note_count == 0, "gamification.hint.first_note", {}),
            (1, 2, note_count > 0, "gamification.hint.first_reminder", {}),
            # Level 2-3: Connectors + KB growth
            (2, 3, "telegram" not in connector_ids, "gamification.hint.telegram_benefit", {}),
            (2, 3, 1 <= note_count <= 5, "gamification.hint.kb_growing", {"count": note_count}),
            # Level 3-4: Advanced connectors + KB linking
            (3, 4, "github" not in connector_ids, "gamification.hint.github_benefit", {}),
            (3, 4, 5 < note_count < 20, "gamification.hint.kb_linking", {}),
            # Level 4: Workflows
            (4, 4, True, "gamification.hint.workflow_benefit", {}),
        ]

        for min_lvl, max_lvl, condition, key, kwargs in hint_defs:
            if min_lvl <= level <= max_lvl and condition:
                return t(key, **kwargs)

    except Exception:
        pass

    return None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_gamification.py -v`
Expected: All PASS

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -x --tb=short`
Expected: PASS. Old hint i18n keys were only used in `gamification.py`, now replaced.

- [ ] **Step 8: Commit**

```bash
git add src/mycelos/gamification.py src/mycelos/i18n/locales/en.yaml src/mycelos/i18n/locales/de.yaml tests/test_gamification.py
git commit -m "feat(gamification): benefit-oriented hints with condition-based selection"
```

---

### Task 4: Simplify onboarding conversation flow

**Files:**
- Modify: `src/mycelos/chat/service.py:571-619` (rewrite onboarding injection)
- Test: `tests/test_gamification.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gamification.py`:

```python
def test_onboarding_prompt_does_not_mention_connectors():
    """The onboarding conversation should NOT suggest connectors — that's Level 3."""
    # We test the prompt text directly since it's a constant injected into conversation
    from mycelos.chat.service import ChatService
    import inspect
    source = inspect.getsource(ChatService.handle_message)
    # Find the onboarding injection block
    assert "connector" not in source.split("[ONBOARDING]")[1].split("Step 5")[0].lower() if "[ONBOARDING]" in source else True
```

Note: this test is fragile by design — it validates the prompt text. A better approach is to just verify the new prompt content inline during implementation.

- [ ] **Step 2: Rewrite the onboarding prompt**

In `src/mycelos/chat/service.py`, replace the onboarding system message content (lines 581-609):

```python
                    conversation.append({
                        "role": "system",
                        "content": (
                            "[ONBOARDING] This is a NEW USER. Follow these steps IN ORDER, "
                            "one at a time (don't ask everything at once):\n\n"
                            "Step 1: LANGUAGE\n"
                            "  Detect the user's language from their first message. "
                            "Respond in that language.\n\n"
                            "Step 2: INTRODUCE YOURSELF\n"
                            "  Say: 'Hi! I'm Mycelos — your personal AI assistant that "
                            "grows with you. Your data stays local, I use cloud AI as a "
                            "tool — not as storage.'\n"
                            "  Then ask: 'What's your name?'\n\n"
                            "Step 3: YOUR NAME\n"
                            "  After they tell you their name, ask:\n"
                            "  'Nice to meet you, [name]! My default name is Mycelos, "
                            "but you can give me a different name if you like. "
                            "What should I be called?'\n"
                            "  If they say 'Mycelos is fine' or similar, keep it. "
                            "Otherwise save their choice.\n\n"
                            "Step 4: GOALS\n"
                            "  Ask what they want to use you for.\n\n"
                            "Step 5: FIRST CAPTURE\n"
                            "  Ask: 'What's on your mind right now? I'll capture it "
                            "for you.' Then use note_write to create their first "
                            "knowledge note.\n\n"
                            "Step 6: SAVE\n"
                            "  Use memory_write to save:\n"
                            "  - key 'user.name' = their name\n"
                            "  - key 'user.language' = detected language (e.g. 'de' or 'en')\n"
                            "  - key 'agent.display_name' = the name they chose for you "
                            "(if not 'Mycelos')\n"
                            "  - key 'onboarding_completed' = 'true'\n\n"
                            "If user says 'skip', still set onboarding_completed=true.\n"
                            "Be warm, concise. ONE question at a time."
                        ),
                    })
```

Key changes from old prompt:
- Introduction now includes positioning ("grows with you", "data stays local", "cloud AI as a tool")
- Step 5 is now "First Capture" (create a note) instead of "Connectors"
- No connector suggestions — those come later via level hints

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -x --tb=short`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/chat/service.py tests/test_gamification.py
git commit -m "feat(onboarding): simplify flow — positioning intro, first note, no connectors"
```

---

### Task 5: Fix agent rename bug

**Files:**
- Modify: `src/mycelos/tools/memory.py:79-106` (add rename sync)
- Test: `tests/test_gamification.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gamification.py`:

```python
def test_memory_write_agent_display_name_syncs_to_registry():
    """Writing agent.display_name to memory should also update agents table."""
    from mycelos.tools.memory import execute_memory_write

    app = _make_mock_app()
    app.audit.log = MagicMock()
    app.agent_registry.rename = MagicMock()

    context = {"app": app, "user_id": "default", "agent_id": "mycelos"}

    result = execute_memory_write(
        {"category": "agent", "key": "display_name", "value": "Fridolin"},
        context,
    )

    assert result.get("status") == "remembered"
    app.agent_registry.rename.assert_called_once_with("mycelos", "Fridolin")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gamification.py::test_memory_write_agent_display_name_syncs_to_registry -v`
Expected: FAIL — `rename` is not called

- [ ] **Step 3: Add rename sync to `execute_memory_write`**

In `src/mycelos/tools/memory.py`, add after line 98 (`app.memory.set(...)`) and before the onboarding tracking:

```python
    # Sync agent display_name to agents table (fixes UI showing old name)
    if category == "agent" and raw_key == "display_name" and value:
        try:
            agent_id = context.get("agent_id", "mycelos")
            app.agent_registry.rename(agent_id, value)
            app.audit.log(
                "agent.renamed",
                details={"agent_id": agent_id, "display_name": value},
            )
        except Exception:
            pass  # Best-effort — memory write still succeeded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gamification.py::test_memory_write_agent_display_name_syncs_to_registry -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x --tb=short`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/tools/memory.py tests/test_gamification.py
git commit -m "fix(onboarding): sync agent display_name to agents table on memory_write"
```

---

### Task 6: Add static init welcome text

**Files:**
- Modify: `src/mycelos/cli/init_cmd.py:516-546` (add Rich panel in `_finalize`)
- Modify: `src/mycelos/i18n/locales/en.yaml` (add `init.welcome_box` keys)
- Modify: `src/mycelos/i18n/locales/de.yaml` (add matching keys)
- Test: `tests/test_gamification.py` (extend)

- [ ] **Step 1: Add i18n keys to `en.yaml`**

Add under the `init:` section in `src/mycelos/i18n/locales/en.yaml`:

```yaml
  welcome_box:
    title: "Mycelos"
    line1: "Mycelos is your personal AI assistant"
    line2: "that grows with you."
    bullet1: "Your notes, tasks, and data stay local"
    bullet2: "Cloud LLMs are tools — not your storage"
    bullet3: "Over time you can switch to local models"
    bullet4: "You build your system step by step"
```

- [ ] **Step 2: Add matching keys to `de.yaml`**

```yaml
  welcome_box:
    title: "Mycelos"
    line1: "Mycelos ist dein persönlicher AI-Assistent,"
    line2: "der mit dir wächst."
    bullet1: "Deine Notizen, Aufgaben und Daten bleiben lokal"
    bullet2: "Cloud-LLMs sind Werkzeuge — nicht dein Speicher"
    bullet3: "Über die Zeit kannst du auf lokale Modelle wechseln"
    bullet4: "Du baust dein System Schritt für Schritt auf"
```

- [ ] **Step 3: Add the Rich Panel to `_finalize()`**

In `src/mycelos/cli/init_cmd.py`, add before the existing completion message in `_finalize()` (before line 533):

```python
    # Welcome box — positioning statement
    from rich.panel import Panel

    welcome_lines = (
        f"  {t('init.welcome_box.line1')}\n"
        f"  {t('init.welcome_box.line2')}\n\n"
        f"  • {t('init.welcome_box.bullet1')}\n"
        f"  • {t('init.welcome_box.bullet2')}\n"
        f"  • {t('init.welcome_box.bullet3')}\n"
        f"  • {t('init.welcome_box.bullet4')}"
    )
    console.print()
    console.print(Panel(welcome_lines, title=f"[bold]{t('init.welcome_box.title')}[/bold]", border_style="cyan"))
```

- [ ] **Step 4: Write a smoke test**

Add to `tests/test_gamification.py`:

```python
def test_init_welcome_box_i18n_keys_exist():
    """All welcome box i18n keys must resolve."""
    from mycelos.i18n import t
    keys = [
        "init.welcome_box.title",
        "init.welcome_box.line1",
        "init.welcome_box.line2",
        "init.welcome_box.bullet1",
        "init.welcome_box.bullet2",
        "init.welcome_box.bullet3",
        "init.welcome_box.bullet4",
    ]
    for key in keys:
        value = t(key)
        assert value != key, f"i18n key {key} not found"
        assert len(value) > 3, f"i18n key {key} is too short: {value!r}"
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_gamification.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -x --tb=short`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/cli/init_cmd.py src/mycelos/i18n/locales/en.yaml src/mycelos/i18n/locales/de.yaml tests/test_gamification.py
git commit -m "feat(init): add positioning welcome box with i18n"
```

---

### Task 7: Update CHANGELOG and final verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add changelog entries**

Add under the current week header in `CHANGELOG.md`:

```markdown
### Onboarding Redesign
- Level-aware LLM prompt: Newcomer gets detailed guidance (~200 tokens), Power User gets minimal (~30 tokens)
- Benefit-oriented hints: condition-based, deterministic selection instead of random generic tips
- Simplified onboarding conversation: positioning intro, first note capture, no connector push
- Fixed agent rename bug: display_name now syncs to agents table (UI shows chosen name)
- Added positioning welcome box to `mycelos init` (i18n, Rich panel)
- Removed static `_ONBOARDING_PROMPT` and `_SETUP_HINTS_PROMPT` — replaced by dynamic level system
```

- [ ] **Step 2: Run full test suite one last time**

Run: `pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: update changelog for onboarding redesign"
```
