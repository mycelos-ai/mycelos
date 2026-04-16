# Contributing to Mycelos

Thank you for your interest in contributing to Mycelos! This document explains how to set up your development environment, run tests, and submit changes.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/mycelos-ai/mycelos.git
cd mycelos

# Install Python dependencies (Python 3.12+ required)
pip install -e ".[dev]"

# Initialize a test instance
export MYCELOS_MASTER_KEY=$(openssl rand -hex 32)
mycelos init

# Run the test suite
pytest tests/ --ignore=tests/integration -v
```

## Test Strategy

We have three levels of tests:

### Unit Tests (no API keys needed)
```bash
pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v
```
These run in CI on every push. They use mocked LLM responses and don't require any API keys. **All PRs must pass these.**

### E2E Tests (mocked LLM, real DB)
```bash
pytest tests/e2e/ -v
```
End-to-end scenarios with mocked LLM but real database operations. Run in CI.

### Live Integration Tests (real API keys)
```bash
pytest -m integration tests/integration/ -v -s
```
These make **real LLM API calls** and cost real money. They verify that:
- The Creator Pipeline actually builds agents (Gherkin → Tests → Code → Audit)
- Workflows execute with real tool calls (search_web, http_get)
- Scheduling triggers correctly
- The Builder finds existing workflows

**These require API keys** stored in `~/.mycelos/.master_key` and configured via `mycelos credential store`.

**These run ONLY locally, never in CI.** API keys do not belong in GitHub Secrets or any CI system. Contributors run these on their own machine before submitting PRs that affect LLM behavior.

## Writing Tests

### For LLM-dependent code
Always mock the LLM using the shared helper:

```python
from tests.helpers.mock_llm import mock_llm, approve_audit

app._llm = mock_llm(GHERKIN_RESPONSE, TESTS_RESPONSE, CODE_RESPONSE)
app._auditor = approve_audit()
```

The `mock_llm()` automatically prepends a "trivial" response for `classify_effort()`. If your test needs a specific effort level, pass `prepend_trivial=False` and handle it manually.

### For new tools
Every new tool needs:
1. Schema definition in `src/mycelos/tools/<module>.py`
2. Execute function with `(args: dict, context: dict) -> Any` signature
3. Registration in the module's `register()` function
4. Permission level (OPEN, STANDARD, PRIVILEGED, BUILDER, SYSTEM)
5. Unit test verifying the tool works with mocked dependencies
6. (Recommended) Live integration test if the tool calls external services

### For new features
If your feature involves LLM behavior (agent responses, tool selection, etc.), please include a **live integration test** that demonstrates the feature works with a real LLM. This is important because:
- Mocked tests can't verify LLM prompt effectiveness
- Prompt changes can break behavior in subtle ways
- Live tests serve as documentation of expected behavior

## Pull Request Checklist

Before submitting a PR:

- [ ] All unit tests pass: `pytest tests/ --ignore=tests/integration -v`
- [ ] No linting errors
- [ ] New features include unit tests
- [ ] LLM-dependent features include live integration tests
- [ ] Security-sensitive changes add tests in `tests/security/`
- [ ] No credentials, API keys, or secrets in the code
- [ ] Constitution rules respected (audit logging, fail-closed, credential isolation)

## Code Style

- Python 3.12+ with type hints
- No LangChain, LangGraph, or CrewAI — own interfaces only
- All user-facing CLI strings use `t()` from `mycelos.i18n`
- English everywhere in code (comments, variables, prompts)
- Tools go in `src/mycelos/tools/` with proper permission levels

## Architecture Overview

```
Mycelos (Chat Agent) → Builder (Specialist)
         ↓                    ↓
   Tool Registry        Tool Registry
         ↓                    ↓
   Security Layer      Security Layer
         ↓                    ↓
   Storage (SQLite)    Storage (SQLite)
```

- **Deny by default** — all tools require explicit permission
- **Security Proxy** — separate process for credential isolation
- **Immutable config** — NixOS-style generations with rollback

## License

MIT — contributions are welcome under the same license.
