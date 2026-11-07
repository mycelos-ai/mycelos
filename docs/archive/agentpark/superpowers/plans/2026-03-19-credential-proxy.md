# Credential Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plaintext `.env` credential storage with an encrypted Credential Proxy that satisfies the CredentialProxy protocol and SEC04/SEC10 security scenarios.

**Architecture:** Credentials are encrypted with AES-256-GCM using a key derived from MYCELOS_MASTER_KEY via HKDF. The derived key never leaves the CredentialProxy class. Credentials are stored as encrypted BLOBs in SQLite alongside a 12-byte nonce. The existing `cryptography` library handles all crypto — no new dependencies needed.

**Tech Stack:** Python 3.12+, `cryptography` (already installed, add to pyproject.toml), SQLite, pytest

**Scope Decisions:**
- **Symmetric vs. Asymmetric:** The spec (Section 7.2) describes X25519 asymmetric encryption so agents can *write* encrypted credentials without the master key. For Milestone 1, no agent writes credentials — only the user via `init_cmd`. Symmetric AES-256-GCM is simpler and correct for this phase. TODO: Upgrade to age-compatible asymmetric model when agents need credential-write capability.
- **Master Key Rotation:** SEC10 requires `rotate_master_key` (re-encrypt all credentials with new key). This is a separate task (Phase 1.1b) — the flag infrastructure (`security_rotated`) is built here, the rotation procedure comes next.
- **Rollback Guard:** The `security_rotated` flag prevents rollback of rotated credentials. The flag is stored and testable now. Integration with `ConfigGenerationManager.rollback()` comes in Phase 4.3 (Blueprint Lifecycle). TODO: Add rollback guard + `credential.rollback_skipped` audit event in Phase 4.3.
- **SEC04 Proxy Abuse / Header Injection:** The spec describes capability-token-gated header injection. This requires the Capability Token Manager (Phase 1.2) — out of scope here. `get_credential` returns raw dicts for now; header injection comes when the Security Layer is complete.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/mycelos/security/__init__.py` | Package init |
| `src/mycelos/security/credentials.py` | `EncryptedCredentialProxy` — encrypt/decrypt/store/retrieve credentials |
| `src/mycelos/storage/schema.sql` | Add `credentials` table |
| `src/mycelos/protocols.py` | Add `rotate_credential` and `delete_credential` to CredentialProxy protocol |
| `src/mycelos/app.py` | Add `credentials` property, remove `_load_env()` |
| `src/mycelos/cli/init_cmd.py` | Store API key via CredentialProxy instead of `.env` |
| `src/mycelos/cli/chat_cmd.py` | Load API key from CredentialProxy instead of `.env` |
| `pyproject.toml` | Add `cryptography>=43.0` dependency |
| `tests/security/__init__.py` | Package init |
| `tests/security/test_credential_isolation.py` | Security invariant tests (SEC04 + SEC10) |

---

### Task 1: Add `cryptography` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add cryptography to dependencies**

In `pyproject.toml`, add `cryptography` to the dependencies list:

```toml
dependencies = [
    "click>=8.1",
    "cryptography>=43.0",
    "litellm>=1.40",
    "rich>=13.0",
]
```

- [ ] **Step 2: Install the updated package**

Run: `pip install -e ".[dev]"`

- [ ] **Step 3: Verify import works**

Run: `python3 -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add cryptography for credential encryption"
```

---

### Task 2: Add credentials table to schema

**Files:**
- Modify: `src/mycelos/storage/schema.sql`

- [ ] **Step 1: Write failing test — credentials table exists after init**

Create `tests/security/__init__.py` (empty) and `tests/security/test_credential_isolation.py`:

```python
"""Security invariant tests for credential isolation (SEC04 + SEC10)."""

from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage


def make_storage(db_path: Path) -> SQLiteStorage:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return storage


def test_credentials_table_exists(db_path: Path) -> None:
    """The credentials table must exist after schema initialization."""
    storage = make_storage(db_path)
    result = storage.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='credentials'"
    )
    assert result is not None
    assert result["name"] == "credentials"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/security/test_credential_isolation.py::test_credentials_table_exists -v`
Expected: FAIL — `credentials` table doesn't exist yet

- [ ] **Step 3: Add credentials table to schema.sql**

Append to `src/mycelos/storage/schema.sql` after the workflow_events section:

```sql
-- Credentials (encrypted, Credential Proxy)
CREATE TABLE IF NOT EXISTS credentials (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    service           TEXT NOT NULL UNIQUE,
    encrypted         BLOB NOT NULL,
    nonce             BLOB NOT NULL,
    security_rotated  INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/security/test_credential_isolation.py::test_credentials_table_exists -v`
Expected: PASS

- [ ] **Step 5: Run all existing tests to verify no regressions**

Run: `pytest tests/test_storage.py tests/test_config.py tests/test_memory.py tests/test_audit.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/storage/schema.sql tests/security/__init__.py tests/security/test_credential_isolation.py
git commit -m "schema: add credentials table for encrypted credential storage"
```

---

### Task 3: Implement EncryptedCredentialProxy

**Files:**
- Create: `src/mycelos/security/__init__.py`
- Create: `src/mycelos/security/credentials.py`
- Modify: `src/mycelos/protocols.py`
- Test: `tests/security/test_credential_isolation.py`

- [ ] **Step 1: Write failing tests for core credential operations**

Append to `tests/security/test_credential_isolation.py`:

```python
from mycelos.security.credentials import EncryptedCredentialProxy


@pytest.fixture
def proxy(db_path: Path) -> EncryptedCredentialProxy:
    storage = make_storage(db_path)
    return EncryptedCredentialProxy(storage, master_key="test-master-key-32chars!!")


def test_store_and_retrieve_credential(proxy: EncryptedCredentialProxy) -> None:
    """Storing a credential and retrieving it returns the original data."""
    proxy.store_credential("anthropic", {"api_key": "sk-ant-test123", "provider": "anthropic"})
    result = proxy.get_credential("anthropic")
    assert result is not None
    assert result["api_key"] == "sk-ant-test123"
    assert result["provider"] == "anthropic"


def test_credential_stored_encrypted(proxy: EncryptedCredentialProxy) -> None:
    """The raw database row must NOT contain the plaintext API key."""
    proxy.store_credential("openai", {"api_key": "sk-openai-secret"})
    row = proxy._storage.fetchone("SELECT encrypted FROM credentials WHERE service = 'openai'")
    assert row is not None
    # The encrypted blob must not contain the plaintext key
    assert b"sk-openai-secret" not in row["encrypted"]


def test_get_nonexistent_credential(proxy: EncryptedCredentialProxy) -> None:
    """Getting a credential that doesn't exist returns None."""
    result = proxy.get_credential("nonexistent")
    assert result is None


def test_list_services(proxy: EncryptedCredentialProxy) -> None:
    """list_services returns all stored service names."""
    proxy.store_credential("anthropic", {"key": "a"})
    proxy.store_credential("openai", {"key": "b"})
    services = proxy.list_services()
    assert set(services) == {"anthropic", "openai"}


def test_overwrite_credential(proxy: EncryptedCredentialProxy) -> None:
    """Storing a credential for an existing service overwrites it."""
    proxy.store_credential("anthropic", {"key": "old"})
    proxy.store_credential("anthropic", {"key": "new"})
    result = proxy.get_credential("anthropic")
    assert result["key"] == "new"
    # Only one row for this service
    rows = proxy._storage.fetchall("SELECT * FROM credentials WHERE service = 'anthropic'")
    assert len(rows) == 1


def test_wrong_master_key_cannot_decrypt(db_path: Path) -> None:
    """A different master key cannot decrypt stored credentials."""
    storage = make_storage(db_path)
    proxy1 = EncryptedCredentialProxy(storage, master_key="correct-key-12345678")
    proxy1.store_credential("test", {"secret": "value"})

    proxy2 = EncryptedCredentialProxy(storage, master_key="wrong-key-987654321")
    with pytest.raises(Exception):  # InvalidTag from AES-GCM
        proxy2.get_credential("test")


def test_delete_credential(proxy: EncryptedCredentialProxy) -> None:
    """Deleting a credential removes it from storage."""
    proxy.store_credential("temp", {"key": "val"})
    assert proxy.get_credential("temp") is not None
    proxy.delete_credential("temp")
    assert proxy.get_credential("temp") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/security/test_credential_isolation.py -v`
Expected: FAIL — `EncryptedCredentialProxy` doesn't exist

- [ ] **Step 3: Create security package and implement EncryptedCredentialProxy**

Create `src/mycelos/security/__init__.py`:

```python
"""Mycelos Security Layer — credential management, policies, capabilities."""
```

Create `src/mycelos/security/credentials.py`:

```python
"""Encrypted Credential Proxy — stores credentials encrypted in SQLite.

Uses AES-256-GCM with a key derived from MYCELOS_MASTER_KEY via HKDF.
The master key never leaves this module. Credentials are stored as
encrypted BLOBs with a 12-byte random nonce per entry.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from mycelos.protocols import StorageBackend


class EncryptedCredentialProxy:
    """Credential Proxy that encrypts all credentials at rest.

    Satisfies the CredentialProxy protocol from protocols.py.
    """

    def __init__(self, storage: StorageBackend, master_key: str) -> None:
        self._storage = storage
        self._aesgcm = AESGCM(self._derive_key(master_key))

    @staticmethod
    def _derive_key(master_key: str) -> bytes:
        """Derive a 256-bit AES key from the master key string via HKDF."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"mycelos-credentials",
        )
        return hkdf.derive(master_key.encode("utf-8"))

    def _encrypt(self, data: dict) -> tuple[bytes, bytes]:
        """Encrypt a dict to (ciphertext, nonce)."""
        plaintext = json.dumps(data, sort_keys=True).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, None)
        return ciphertext, nonce

    def _decrypt(self, ciphertext: bytes, nonce: bytes) -> dict:
        """Decrypt ciphertext back to a dict. Raises on wrong key."""
        plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode("utf-8"))

    def store_credential(self, service: str, credential: dict) -> None:
        """Encrypt and store a credential. Overwrites if service exists."""
        ciphertext, nonce = self._encrypt(credential)
        existing = self._storage.fetchone(
            "SELECT id FROM credentials WHERE service = ?", (service,)
        )
        if existing:
            self._storage.execute(
                "UPDATE credentials SET encrypted = ?, nonce = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE service = ?",
                (ciphertext, nonce, service),
            )
        else:
            self._storage.execute(
                "INSERT INTO credentials (service, encrypted, nonce) VALUES (?, ?, ?)",
                (service, ciphertext, nonce),
            )

    def get_credential(self, service: str) -> dict | None:
        """Retrieve and decrypt a credential. Returns None if not found."""
        row = self._storage.fetchone(
            "SELECT encrypted, nonce FROM credentials WHERE service = ?", (service,)
        )
        if row is None:
            return None
        return self._decrypt(row["encrypted"], row["nonce"])

    def delete_credential(self, service: str) -> None:
        """Remove a credential from storage."""
        self._storage.execute(
            "DELETE FROM credentials WHERE service = ?", (service,)
        )

    def list_services(self) -> list[str]:
        """List all services that have stored credentials."""
        rows = self._storage.fetchall("SELECT service FROM credentials ORDER BY service")
        return [row["service"] for row in rows]

    def is_security_rotated(self, service: str) -> bool:
        """Check if a credential was rotated for security reasons."""
        row = self._storage.fetchone(
            "SELECT security_rotated FROM credentials WHERE service = ?", (service,)
        )
        return bool(row and row["security_rotated"])

    def mark_security_rotated(self, service: str) -> None:
        """Mark a credential as security-rotated (prevents rollback)."""
        self._storage.execute(
            "UPDATE credentials SET security_rotated = 1 WHERE service = ?", (service,)
        )
```

- [ ] **Step 4: Update protocol to include delete_credential**

In `src/mycelos/protocols.py`, update the CredentialProxy protocol:

```python
@runtime_checkable
class CredentialProxy(Protocol):
    """Interface for credential management."""

    def get_credential(self, service: str) -> dict | None: ...
    def store_credential(self, service: str, credential: dict) -> None: ...
    def delete_credential(self, service: str) -> None: ...
    def list_services(self) -> list[str]: ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/security/test_credential_isolation.py -v`
Expected: All 8 tests PASS

- [ ] **Step 6: Verify protocol compliance**

Run: `python3 -c "from mycelos.protocols import CredentialProxy; from mycelos.security.credentials import EncryptedCredentialProxy; print(issubclass(EncryptedCredentialProxy, CredentialProxy))"`
Expected: `True`

- [ ] **Step 7: Run all tests for regressions**

Run: `pytest tests/test_storage.py tests/test_config.py tests/test_memory.py tests/test_audit.py tests/security/ -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add src/mycelos/security/ src/mycelos/protocols.py tests/security/
git commit -m "feat: implement EncryptedCredentialProxy with AES-256-GCM"
```

---

### Task 4: Wire CredentialProxy into App container

**Files:**
- Modify: `src/mycelos/app.py`

- [ ] **Step 1: Write failing test — App exposes credentials property**

Create `tests/test_app_credentials.py`:

```python
"""Tests for App credential proxy integration."""

from pathlib import Path
import os

import pytest

from mycelos.app import App


def test_app_has_credentials_property(tmp_data_dir: Path) -> None:
    """App should expose a credentials property."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-app"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        assert hasattr(app, "credentials")
        cred = app.credentials
        assert cred is not None
        # Should satisfy the protocol
        assert hasattr(cred, "store_credential")
        assert hasattr(cred, "get_credential")
    finally:
        del os.environ["MYCELOS_MASTER_KEY"]


def test_app_credentials_roundtrip(tmp_data_dir: Path) -> None:
    """Credentials stored via App are retrievable."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-app"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        app.credentials.store_credential("test_svc", {"token": "abc123"})
        result = app.credentials.get_credential("test_svc")
        assert result is not None
        assert result["token"] == "abc123"
    finally:
        del os.environ["MYCELOS_MASTER_KEY"]


def test_app_no_master_key_raises(tmp_data_dir: Path) -> None:
    """Accessing credentials without MYCELOS_MASTER_KEY raises a clear error."""
    # Ensure key is not set
    os.environ.pop("MYCELOS_MASTER_KEY", None)
    app = App(tmp_data_dir)
    app.initialize()
    with pytest.raises(RuntimeError, match="MYCELOS_MASTER_KEY"):
        _ = app.credentials


def test_app_load_credential_to_env(tmp_data_dir: Path) -> None:
    """load_credential_to_env sets the correct environment variable."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-app"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        app.credentials.store_credential("anthropic", {
            "api_key": "sk-test-key",
            "env_var": "ANTHROPIC_API_KEY",
            "provider": "anthropic",
        })
        # Remove any existing env var
        os.environ.pop("ANTHROPIC_API_KEY", None)
        app.load_credential_to_env("anthropic")
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-test-key"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_app_load_credential_missing_api_key(tmp_data_dir: Path) -> None:
    """load_credential_to_env does nothing if credential has no api_key field."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-app"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        app.credentials.store_credential("misc", {"token": "abc"})  # no "api_key"
        os.environ.pop("MISC_API_KEY", None)
        app.load_credential_to_env("misc")
        assert "MISC_API_KEY" not in os.environ
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app_credentials.py -v`
Expected: FAIL — App has no `credentials` property

- [ ] **Step 3: Add credentials property to App and remove _load_env**

Update `src/mycelos/app.py`:

```python
"""Application container — wires all services together."""

from __future__ import annotations

import os
from pathlib import Path

from mycelos.audit import SQLiteAuditLogger
from mycelos.config.generations import ConfigGenerationManager
from mycelos.llm.broker import LiteLLMBroker
from mycelos.memory.service import SQLiteMemoryService
from mycelos.security.credentials import EncryptedCredentialProxy
from mycelos.storage.database import SQLiteStorage


class App:
    """Central application container.

    Creates and holds all service instances.
    Services are lazily initialized.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._storage: SQLiteStorage | None = None
        self._config_mgr: ConfigGenerationManager | None = None
        self._audit: SQLiteAuditLogger | None = None
        self._llm: LiteLLMBroker | None = None
        self._memory: SQLiteMemoryService | None = None
        self._credentials: EncryptedCredentialProxy | None = None

    @property
    def storage(self) -> SQLiteStorage:
        if self._storage is None:
            self._storage = SQLiteStorage(self.data_dir / "mycelos.db")
        return self._storage

    @property
    def config(self) -> ConfigGenerationManager:
        if self._config_mgr is None:
            self._config_mgr = ConfigGenerationManager(self.storage)
        return self._config_mgr

    @property
    def audit(self) -> SQLiteAuditLogger:
        if self._audit is None:
            self._audit = SQLiteAuditLogger(self.storage)
        return self._audit

    @property
    def llm(self) -> LiteLLMBroker:
        if self._llm is None:
            config = self.config.get_active_config() or {}
            default_model = config.get("default_model", "claude-haiku-4-5-20251001")
            self._llm = LiteLLMBroker(default_model=default_model)
        return self._llm

    @property
    def memory(self) -> SQLiteMemoryService:
        if self._memory is None:
            self._memory = SQLiteMemoryService(self.storage)
        return self._memory

    @property
    def credentials(self) -> EncryptedCredentialProxy:
        if self._credentials is None:
            master_key = os.environ.get("MYCELOS_MASTER_KEY")
            if not master_key:
                raise RuntimeError(
                    "MYCELOS_MASTER_KEY environment variable is not set. "
                    "This is required for credential encryption. "
                    "Set it before running Mycelos."
                )
            self._credentials = EncryptedCredentialProxy(self.storage, master_key)
        return self._credentials

    def load_credential_to_env(self, service: str) -> None:
        """Load a credential from the proxy and set it as an environment variable.

        This is the ONLY way credentials reach the LLM broker — via env vars
        set by the App container, never by reading .env files.
        """
        cred = self.credentials.get_credential(service)
        if cred and "api_key" in cred:
            env_var = cred.get("env_var", f"{service.upper()}_API_KEY")
            os.environ[env_var] = cred["api_key"]

    def initialize(self) -> None:
        """Initialize database and apply first config generation."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage.initialize()

        if self.config.get_active_generation_id() is None:
            self.config.apply(
                {"version": "0.1.0", "default_model": "claude-haiku-4-5-20251001"},
                description="initial config",
                trigger="init",
            )
            self.audit.log("system.initialized")

    def initialize_with_config(
        self,
        default_model: str,
        provider: str,
    ) -> None:
        """Initialize with specific model and provider configuration."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage.initialize()

        config = {
            "version": "0.1.0",
            "default_model": default_model,
            "provider": provider,
        }
        self.config.apply(config, description="initial config", trigger="init")
        self.audit.log("system.initialized", details=config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app_credentials.py -v`
Expected: All 5 PASS

- [ ] **Step 5: Run all tests for regressions**

Run: `pytest tests/test_storage.py tests/test_config.py tests/test_memory.py tests/test_audit.py tests/security/ tests/test_app_credentials.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/app.py tests/test_app_credentials.py
git commit -m "feat: wire EncryptedCredentialProxy into App container, remove _load_env"
```

---

### Task 5: Update init_cmd to use Credential Proxy

**Files:**
- Modify: `src/mycelos/cli/init_cmd.py`

- [ ] **Step 1: Rewrite init_cmd.py to store credentials encrypted**

Replace `src/mycelos/cli/init_cmd.py` with:

```python
"""mycelos init command."""

import os
from pathlib import Path

import click
from rich.console import Console

from mycelos.app import App

console = Console()

PROVIDERS = {
    "anthropic": {
        "env_var": "ANTHROPIC_API_KEY",
        "default_model": "claude-haiku-4-5-20251001",
        "models": ["claude-haiku-4-5-20251001", "claude-sonnet-4-20250514", "claude-opus-4-20250514"],
    },
    "openai": {
        "env_var": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "o3-mini"],
    },
    "gemini": {
        "env_var": "GEMINI_API_KEY",
        "default_model": "gemini/gemini-2.0-flash",
        "models": ["gemini/gemini-2.0-flash", "gemini/gemini-2.5-pro"],
    },
}


@click.command()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=Path.home() / ".mycelos",
    help="Data directory for Mycelos",
)
def init_cmd(data_dir: Path):
    """Initialize Mycelos."""
    console.print("\n[bold]Welcome to Mycelos![/bold]\n")

    # Check if already initialized
    db_path = data_dir / "mycelos.db"
    if db_path.exists():
        console.print(f"[yellow]Mycelos already initialized at {data_dir}[/yellow]")
        if not click.confirm("Reinitialize?", default=False):
            return

    # Check for MYCELOS_MASTER_KEY
    master_key = os.environ.get("MYCELOS_MASTER_KEY")
    if not master_key:
        console.print("[yellow]MYCELOS_MASTER_KEY not set.[/yellow]")
        console.print(
            "This key encrypts all your credentials. Choose a strong passphrase.\n"
            "You will need it every time you start Mycelos.\n"
        )
        master_key = click.prompt("Enter a master key", hide_input=True)
        confirm_key = click.prompt("Confirm master key", hide_input=True)
        if master_key != confirm_key:
            console.print("[red]Keys do not match. Aborting.[/red]")
            raise SystemExit(1)
        os.environ["MYCELOS_MASTER_KEY"] = master_key
        console.print(
            "\n[dim]Tip: set MYCELOS_MASTER_KEY in your shell profile so you "
            "don't have to enter it every time.[/dim]\n"
        )

    # Ask for LLM provider
    console.print("Choose your LLM provider:\n")
    provider_names = list(PROVIDERS.keys())
    for i, name in enumerate(provider_names, 1):
        console.print(f"  ({i}) {name}")

    console.print()
    choice = click.prompt(
        "Provider",
        type=click.IntRange(1, len(provider_names)),
        default=1,
    )
    provider_name = provider_names[choice - 1]
    provider = PROVIDERS[provider_name]

    # Check for API key
    env_var = provider["env_var"]
    existing_key = os.environ.get(env_var)

    if existing_key:
        console.print(f"\n[green]Found {env_var} in environment.[/green]")
        api_key = existing_key
    else:
        console.print(f"\n[yellow]{env_var} not found in environment.[/yellow]")
        api_key = click.prompt(
            f"Enter your {provider_name} API key",
            hide_input=True,
        )
        # Set for this session so the LLM broker works immediately
        os.environ[env_var] = api_key

    # Choose model
    console.print(f"\nAvailable models for {provider_name}:")
    for i, model in enumerate(provider["models"], 1):
        default_marker = " (default)" if model == provider["default_model"] else ""
        console.print(f"  ({i}) {model}{default_marker}")

    console.print()
    model_choice = click.prompt(
        "Model",
        type=click.IntRange(1, len(provider["models"])),
        default=1,
    )
    chosen_model = provider["models"][model_choice - 1]

    # Initialize
    app = App(data_dir)
    app.initialize_with_config(
        default_model=chosen_model,
        provider=provider_name,
    )

    # Store credential encrypted via Credential Proxy
    app.credentials.store_credential(provider_name, {
        "api_key": api_key,
        "env_var": env_var,
        "provider": provider_name,
    })
    app.audit.log("credential.stored", details={"service": provider_name})

    # Remove any leftover .env file from previous installations
    env_file = data_dir / ".env"
    if env_file.exists():
        env_file.unlink()
        console.print("[dim]Removed old .env file (credentials are now encrypted).[/dim]")

    console.print(f"\n[green]Mycelos initialized at {data_dir}[/green]")
    console.print(f"Provider: {provider_name}")
    console.print(f"Model: {chosen_model}")
    console.print()
    console.print("[bold]Your API key is encrypted in the Credential Proxy.[/bold]")
    console.print("[dim]No agent — not even the Creator-Agent — can access your credentials directly.[/dim]")
    console.print(f"\nNext: run [bold]mycelos chat[/bold] to start.")
```

- [ ] **Step 2: Run existing CLI tests to check for breakage**

Run: `pytest tests/ -v -k "not llm_broker and not chat and not cli" --tb=short`
Expected: All non-LLM tests pass

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/cli/init_cmd.py
git commit -m "feat: init_cmd stores API key encrypted via Credential Proxy"
```

---

### Task 6: Update chat_cmd to load credentials from proxy

**Files:**
- Modify: `src/mycelos/cli/chat_cmd.py`

- [ ] **Step 1: Update chat_cmd to use App.load_credential_to_env**

Replace the `app._load_env()` call in `src/mycelos/cli/chat_cmd.py` (line 43) with credential loading:

```python
"""Mycelos interactive chat command — REPL backed by the LLM broker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.markdown import Markdown

from mycelos.app import App

console = Console()

_SYSTEM_PROMPT = """\
You are the God-Agent — the central orchestrator of the Mycelos security-first \
agent operating system. You help users configure, operate, and reason about \
their AI-agent environment. You are precise, helpful, and security-conscious. \
Never suggest actions that would compromise system integrity or expose secrets.\
"""


@click.command()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=Path.home() / ".mycelos",
    show_default=True,
    help="Data directory for Mycelos (must already be initialized).",
)
def chat_cmd(data_dir: Path) -> None:
    """Start an interactive chat session with the God-Agent."""
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print(
            f"[red]Error:[/red] Mycelos is not initialized at [bold]{data_dir}[/bold]. "
            "Run [bold]mycelos init[/bold] first."
        )
        raise SystemExit(1)

    app = App(data_dir)

    # Load LLM credentials from encrypted storage into environment
    config = app.config.get_active_config() or {}
    provider = config.get("provider")
    if provider:
        try:
            app.load_credential_to_env(provider)
        except RuntimeError:
            console.print(
                "[red]Error:[/red] MYCELOS_MASTER_KEY is not set. "
                "Set it in your environment to decrypt credentials."
            )
            raise SystemExit(1)

    app.audit.log("session.started")

    # Load persisted user name from memory (best-effort).
    user_name: str | None = app.memory.get("default", "system", "user.name")

    if user_name:
        console.print(f"\n[bold green]Welcome back, {user_name}![/bold green]")
    else:
        console.print("\n[bold green]Welcome to Mycelos![/bold green]")

    console.print(
        "Type your message and press [bold]Enter[/bold]. "
        "Type [bold]exit[/bold], [bold]quit[/bold], or [bold]q[/bold] to end the session.\n"
    )

    conversation: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
    ]

    try:
        while True:
            try:
                user_input = console.input("[bold cyan]You>[/bold cyan] ").strip()
            except EOFError:
                # Non-interactive / piped input exhausted.
                console.print("\n[dim]End of input.[/dim]")
                break

            if not user_input:
                continue

            if user_input.lower() in {"exit", "quit", "q"}:
                console.print("[dim]Ending session…[/dim]")
                break

            conversation.append({"role": "user", "content": user_input})

            try:
                response = app.llm.complete(conversation)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]LLM error:[/red] {exc}")
                # Remove the failed user turn so the loop stays consistent.
                conversation.pop()
                continue

            assistant_content = response.content
            conversation.append({"role": "assistant", "content": assistant_content})

            console.print()
            console.print(Markdown(assistant_content))
            console.print(
                f"[dim]({response.total_tokens} tokens | {response.model})[/dim]\n"
            )

    except KeyboardInterrupt:
        console.print("\n[dim]Session interrupted.[/dim]")

    app.audit.log("session.ended")
    console.print("[green]Session ended. Goodbye![/green]")
```

- [ ] **Step 2: Verify no `.env` references remain in src/**

Run: `grep -r "\.env" src/mycelos/ --include="*.py" | grep -v __pycache__`
Expected: No matches referencing `.env` file loading

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/cli/chat_cmd.py
git commit -m "feat: chat_cmd loads credentials from Credential Proxy instead of .env"
```

---

### Task 7: Security invariant tests (SEC04 + SEC10)

**Files:**
- Modify: `tests/security/test_credential_isolation.py`

- [ ] **Step 1: Add SEC04 scenario tests**

Append to `tests/security/test_credential_isolation.py`:

```python
# ── SEC04: Agent Cannot Access Credentials Directly ──────────────


def test_sec04_credentials_are_encrypted_at_rest(proxy: EncryptedCredentialProxy) -> None:
    """SEC04: Even with database access, credentials are not readable."""
    proxy.store_credential("github", {"token": "ghp_supersecret123"})

    # Simulate an agent that somehow got database access
    row = proxy._storage.fetchone(
        "SELECT encrypted, nonce FROM credentials WHERE service = 'github'"
    )
    raw_bytes = row["encrypted"]

    # The plaintext token must not appear anywhere in the encrypted blob
    assert b"ghp_supersecret123" not in raw_bytes
    assert b"supersecret" not in raw_bytes


def test_sec04_no_env_files_exist(tmp_data_dir: Path) -> None:
    """SEC04: No .env files should exist in the data directory."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        app.credentials.store_credential("test", {"key": "val"})

        # No .env file should be created
        env_file = tmp_data_dir / ".env"
        assert not env_file.exists(), ".env file must not exist — credentials belong in Credential Proxy"
    finally:
        del os.environ["MYCELOS_MASTER_KEY"]


# ── SEC10: Master Key Compromise Scenarios ────────────────────────


def test_sec10_different_master_keys_are_isolated(db_path: Path) -> None:
    """SEC10: Credentials encrypted with one key cannot be read with another."""
    storage = make_storage(db_path)

    proxy_a = EncryptedCredentialProxy(storage, master_key="key-alpha-secure")
    proxy_a.store_credential("service_a", {"secret": "alpha-secret"})

    proxy_b = EncryptedCredentialProxy(storage, master_key="key-beta-different")
    # proxy_b should NOT be able to decrypt proxy_a's credentials
    with pytest.raises(Exception):
        proxy_b.get_credential("service_a")


def test_sec10_security_rotated_flag(proxy: EncryptedCredentialProxy) -> None:
    """SEC10: Credentials can be marked as security-rotated to prevent rollback."""
    proxy.store_credential("compromised", {"key": "old-key"})
    assert not proxy.is_security_rotated("compromised")

    proxy.mark_security_rotated("compromised")
    assert proxy.is_security_rotated("compromised")

    # Update the credential (rotation)
    proxy.store_credential("compromised", {"key": "new-rotated-key"})
    # Flag persists after update
    assert proxy.is_security_rotated("compromised")
```

The `test_sec10_security_rotated_flag` test needs a fix to `store_credential` — the `mark_security_rotated` flag should survive overwrites. Check: the current `UPDATE` in `store_credential` doesn't touch `security_rotated`, so it should persist. Good.

- [ ] **Step 2: Add imports at top of test file**

Ensure these imports are at the top of `tests/security/test_credential_isolation.py`:

```python
import os
import pytest
from pathlib import Path
from mycelos.app import App
from mycelos.storage.database import SQLiteStorage
from mycelos.security.credentials import EncryptedCredentialProxy
```

- [ ] **Step 3: Run all security tests**

Run: `pytest tests/security/ -v`
Expected: All tests PASS (should be ~12 tests total)

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v -k "not llm_broker and not chat and not cli" --tb=short`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add tests/security/test_credential_isolation.py
git commit -m "test: add SEC04 + SEC10 security invariant tests for credential isolation"
```

---

### Task 8: Final cleanup and verification

**Files:**
- Review all modified files

- [ ] **Step 1: Verify no .env references remain in production code**

Run: `grep -rn "_load_env\|\.env\|dotenv" src/mycelos/ --include="*.py" | grep -v __pycache__`
Expected: No matches (all `.env` loading removed)

- [ ] **Step 2: Verify MYCELOS_MASTER_KEY is the only credential-related env var**

Run: `grep -rn "os.environ" src/mycelos/ --include="*.py" | grep -v __pycache__`
Expected: Only references to `MYCELOS_MASTER_KEY` and the dynamic `env_var` loading in `load_credential_to_env`

- [ ] **Step 3: Run full test suite one final time**

Run: `pytest tests/ -v -k "not llm_broker and not chat and not cli" --tb=short`
Expected: All tests pass, including all security tests

- [ ] **Step 4: Final commit with all changes**

```bash
git add -A
git status  # Review — should only be expected changes
git commit -m "cleanup: verify credential proxy integration, no .env remnants"
```
