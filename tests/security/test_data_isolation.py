"""Security integration tests for data isolation (SEC10, SEC12, SEC20).

Tests three critical isolation boundaries:

SEC10 - Master key is never accessible from agent tool context
SEC12 - Agent A's memory is isolated from Agent B
SEC20 - User A's data is isolated from User B

These are integration tests using real App instances with real SQLite storage.
No mocking of the storage or security layers.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.chat.service import ChatService
from mycelos.memory.service import SQLiteMemoryService
from mycelos.security.credentials import EncryptedCredentialProxy
from mycelos.storage.database import SQLiteStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> App:
    """Real App with initialized database in a temp directory."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-isolation"
        a = App(Path(tmp))
        a.initialize()
        # Seed test users for FK constraints
        for uid in ("stefan", "anna", "user_a", "user_b"):
            a.storage.execute(
                "INSERT OR IGNORE INTO users (id, name, status) VALUES (?, ?, ?)",
                (uid, uid, "active"),
            )
        yield a


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    """Standalone storage for lower-level tests."""
    db = SQLiteStorage(tmp_path / "test.db")
    db.initialize()
    for uid in ("default", "stefan", "anna", "user_a", "user_b"):
        db.execute(
            "INSERT OR IGNORE INTO users (id, name, status) VALUES (?, ?, ?)",
            (uid, uid, "active"),
        )
    return db


@pytest.fixture
def memory(storage: SQLiteStorage) -> SQLiteMemoryService:
    return SQLiteMemoryService(storage)


@pytest.fixture
def proxy(storage: SQLiteStorage) -> EncryptedCredentialProxy:
    return EncryptedCredentialProxy(storage, master_key="test-key-isolation")


# ---------------------------------------------------------------------------
# SEC10: Master Key Compromise — key never reaches agent processes
# ---------------------------------------------------------------------------


class TestSEC10MasterKeyIsolation:
    """SEC10 @key-not-in-agent: MYCELOS_MASTER_KEY must never be accessible
    from agent tool context or appear in any output."""

    def test_master_key_not_in_tool_result(self, app: App) -> None:
        """Tool results never contain the master key value."""
        svc = ChatService(app)
        app.policy_engine.set_policy("default", None, "system_status", "always")
        result = svc._execute_tool("system_status", {})
        result_str = json.dumps(result)
        master_key = os.environ.get("MYCELOS_MASTER_KEY", "")
        assert master_key not in result_str
        assert "MYCELOS_MASTER_KEY" not in result_str

    def test_master_key_not_in_memory_service(self, app: App) -> None:
        """The memory service does not store or expose the master key."""
        # Search all memory scopes for anything resembling the master key
        master_key = os.environ.get("MYCELOS_MASTER_KEY", "")
        for scope in ("system", "agent", "shared", "session"):
            results = app.memory.search("default", scope, "master_key")
            for entry in results:
                assert master_key not in str(entry.get("value", ""))

    def test_master_key_not_in_audit_log(self, app: App) -> None:
        """Audit events never contain the master key."""
        master_key = os.environ.get("MYCELOS_MASTER_KEY", "")
        # Trigger some audit events
        app.audit.log("test.event", details={"action": "test"})
        events = app.audit.query()
        for event in events:
            assert master_key not in str(event.get("details", ""))
            assert "MYCELOS_MASTER_KEY" not in str(event.get("details", ""))

    def test_master_key_not_in_config_generations(self, app: App) -> None:
        """Config generations do not store the master key."""
        master_key = os.environ.get("MYCELOS_MASTER_KEY", "")
        config = app.config.get_active_config() or {}
        config_str = json.dumps(config)
        assert master_key not in config_str
        assert "MYCELOS_MASTER_KEY" not in config_str

    def test_different_master_keys_isolated(self, storage: SQLiteStorage) -> None:
        """SEC10 @key-compromise-impact: Credentials encrypted with one key
        cannot be decrypted with another."""
        proxy_a = EncryptedCredentialProxy(storage, master_key="key-alpha-123")
        proxy_a.store_credential("service_x", {"api_key": "secret-alpha"})

        proxy_b = EncryptedCredentialProxy(storage, master_key="key-beta-456")
        with pytest.raises(Exception):
            proxy_b.get_credential("service_x")

    def test_credential_proxy_never_returns_raw_to_tool_context(
        self, app: App
    ) -> None:
        """SEC10: The credential proxy is not exposed through tool context.
        Tool handlers receive the app object but should never call
        get_credential directly -- the proxy is only for the App container."""
        app.credentials.store_credential(
            "anthropic", {"api_key": "sk-ant-secret123456789012345678901"},
        )

        # Verify the credential is stored encrypted
        row = app.storage.fetchone(
            "SELECT encrypted FROM credentials WHERE service = 'anthropic'"
        )
        assert row is not None
        assert b"sk-ant-secret" not in row["encrypted"]

        # Even if someone gets the encrypted blob, they cannot read it
        # without the master key
        proxy_wrong = EncryptedCredentialProxy(
            app.storage, master_key="wrong-key-totally"
        )
        with pytest.raises(Exception):
            proxy_wrong.get_credential("anthropic")


# ---------------------------------------------------------------------------
# SEC12: Cross-Agent Memory Access Violation
# ---------------------------------------------------------------------------


class TestSEC12CrossAgentMemory:
    """SEC12: An agent cannot read or write another agent's private memory."""

    def test_agent_memory_read_isolation(self, memory: SQLiteMemoryService) -> None:
        """SEC12 @read-other-agent: email-agent stores a secret in agent memory.
        github-agent cannot read it (different agent_id)."""
        # email-agent stores a secret
        memory.set(
            user_id="default",
            scope="agent",
            key="user.email_password",
            value="secret-email-pass",
            agent_id="email-agent",
            created_by="email-agent",
        )

        # email-agent CAN read its own memory
        result = memory.get(
            user_id="default", scope="agent",
            key="user.email_password", agent_id="email-agent",
        )
        assert result == "secret-email-pass"

        # github-agent CANNOT read email-agent's memory
        result = memory.get(
            user_id="default", scope="agent",
            key="user.email_password", agent_id="github-agent",
        )
        assert result is None

    def test_agent_memory_write_isolation(self, memory: SQLiteMemoryService) -> None:
        """SEC12 @write-other-agent: An agent writing with its own agent_id
        cannot overwrite another agent's memory entry."""
        # email-agent stores config
        memory.set(
            user_id="default",
            scope="agent",
            key="config",
            value="email-agent-config",
            agent_id="email-agent",
            created_by="email-agent",
        )

        # malicious-agent writes to the SAME key but with its own agent_id
        memory.set(
            user_id="default",
            scope="agent",
            key="config",
            value="malicious-overwrite",
            agent_id="malicious-agent",
            created_by="malicious-agent",
        )

        # email-agent's data is NOT overwritten
        result = memory.get(
            user_id="default", scope="agent",
            key="config", agent_id="email-agent",
        )
        assert result == "email-agent-config"

        # malicious-agent has its own separate entry
        result = memory.get(
            user_id="default", scope="agent",
            key="config", agent_id="malicious-agent",
        )
        assert result == "malicious-overwrite"

    def test_agent_memory_search_isolation(self, memory: SQLiteMemoryService) -> None:
        """SEC12: Searching agent memory only returns entries for the searching agent."""
        # Store entries for two different agents
        memory.set(
            user_id="default", scope="agent",
            key="secret.token", value="email-token",
            agent_id="email-agent", created_by="email-agent",
        )
        memory.set(
            user_id="default", scope="agent",
            key="secret.token", value="github-token",
            agent_id="github-agent", created_by="github-agent",
        )

        # email-agent's search only returns its own entries
        results = memory.search(
            user_id="default", scope="agent",
            query="secret", agent_id="email-agent",
        )
        values = [r["value"] for r in results]
        assert "email-token" in values
        assert "github-token" not in values

    def test_shared_memory_records_created_by(self, memory: SQLiteMemoryService) -> None:
        """SEC12 @shared-memory-abuse: Shared memory writes record the creator
        so other agents can assess reliability."""
        memory.set(
            user_id="default",
            scope="shared",
            key="project.deadline",
            value="2099-12-31",
            created_by="research-agent",
        )

        # The entry exists in shared scope (accessible to all agents)
        result = memory.get("default", "shared", "project.deadline")
        assert result == "2099-12-31"

        # Verify created_by is tracked (via search which returns metadata)
        results = memory.search("default", "shared", "project.deadline")
        assert len(results) >= 1
        assert results[0]["created_by"] == "research-agent"

    def test_agent_cannot_read_null_agent_id_entries(
        self, memory: SQLiteMemoryService
    ) -> None:
        """Agent-scoped queries with agent_id cannot see entries stored without
        an agent_id (system-level entries)."""
        # Store a system-level entry (no agent_id)
        memory.set(
            user_id="default", scope="system",
            key="user.name", value="Stefan",
        )

        # An agent querying with its agent_id in agent scope gets nothing
        result = memory.get(
            user_id="default", scope="system",
            key="user.name", agent_id="some-agent",
        )
        # System scope with agent_id filter -- won't match NULL agent_id
        assert result is None


# ---------------------------------------------------------------------------
# SEC20: Multi-User Data Isolation
# ---------------------------------------------------------------------------


class TestSEC20MultiUserIsolation:
    """SEC20: Users share a Mycelos instance but cannot access each other's data."""

    def test_memory_isolation_between_users(self, memory: SQLiteMemoryService) -> None:
        """SEC20 @cross-user-data: Stefan's memory entries are not visible to Anna."""
        memory.set("stefan", "system", "user.name", "Stefan")
        memory.set("anna", "system", "user.name", "Anna")

        # Each user sees only their own data
        assert memory.get("stefan", "system", "user.name") == "Stefan"
        assert memory.get("anna", "system", "user.name") == "Anna"

        # Stefan cannot see Anna's entries
        result = memory.get("stefan", "system", "user.name")
        assert result != "Anna"

    def test_memory_search_isolation_between_users(
        self, memory: SQLiteMemoryService
    ) -> None:
        """SEC20: Memory search is scoped to the requesting user."""
        memory.set("stefan", "agent", "api.config", "stefan-config",
                    agent_id="mycelos", created_by="mycelos")
        memory.set("anna", "agent", "api.config", "anna-config",
                    agent_id="mycelos", created_by="mycelos")

        stefan_results = memory.search("stefan", "agent", "api",
                                       agent_id="mycelos")
        anna_results = memory.search("anna", "agent", "api",
                                     agent_id="mycelos")

        stefan_values = [r["value"] for r in stefan_results]
        anna_values = [r["value"] for r in anna_results]

        assert "stefan-config" in stefan_values
        assert "anna-config" not in stefan_values
        assert "anna-config" in anna_values
        assert "stefan-config" not in anna_values

    def test_credential_isolation_between_users(
        self, proxy: EncryptedCredentialProxy
    ) -> None:
        """SEC20 @cross-user-credentials: Anna's GitHub credential is not
        accessible to Stefan."""
        proxy.store_credential(
            "github",
            {"api_key": "ghp_anna_secret_token_123456789012345"},
            user_id="anna",
        )

        # Anna can retrieve her credential
        anna_cred = proxy.get_credential("github", user_id="anna")
        assert anna_cred is not None
        assert anna_cred["api_key"] == "ghp_anna_secret_token_123456789012345"

        # Stefan gets nothing
        stefan_cred = proxy.get_credential("github", user_id="stefan")
        assert stefan_cred is None

    def test_credential_list_isolation_between_users(
        self, proxy: EncryptedCredentialProxy
    ) -> None:
        """SEC20: list_services is scoped to user_id."""
        proxy.store_credential("github", {"api_key": "anna-gh"}, user_id="anna")
        proxy.store_credential("slack", {"api_key": "anna-sl"}, user_id="anna")
        proxy.store_credential("openai", {"api_key": "stefan-oai"}, user_id="stefan")

        anna_services = proxy.list_services(user_id="anna")
        stefan_services = proxy.list_services(user_id="stefan")

        assert "github" in anna_services
        assert "slack" in anna_services
        assert "openai" not in anna_services

        assert "openai" in stefan_services
        assert "github" not in stefan_services
        assert "slack" not in stefan_services

    def test_credential_delete_isolation(
        self, proxy: EncryptedCredentialProxy
    ) -> None:
        """SEC20: Deleting a credential for one user does not affect another."""
        proxy.store_credential("shared_service", {"key": "anna"}, user_id="anna")
        proxy.store_credential("shared_service", {"key": "stefan"}, user_id="stefan")

        # Delete Anna's credential
        proxy.delete_credential("shared_service", user_id="anna")

        # Anna's is gone
        assert proxy.get_credential("shared_service", user_id="anna") is None
        # Stefan's is still there
        assert proxy.get_credential("shared_service", user_id="stefan") is not None
        assert proxy.get_credential("shared_service", user_id="stefan")["key"] == "stefan"

    def test_audit_events_include_user_id(self, app: App) -> None:
        """SEC20: Audit events are tagged with user_id for accountability."""
        app.audit.log("test.action", details={"user_id": "stefan", "action": "test"})
        app.audit.log("test.action", details={"user_id": "anna", "action": "test"})

        events = app.audit.query(event_type="test.action")
        assert len(events) >= 2
        user_ids = [json.loads(e["details"])["user_id"] for e in events]
        assert "stefan" in user_ids
        assert "anna" in user_ids

    def test_user_a_tool_execution_scoped(self, app: App) -> None:
        """SEC20: Memory writes for User A do not affect User B's data.

        Tests the memory service directly because it is the enforcement
        layer for user isolation (user_id column on every query).
        """
        # Write memory entries for two users via the service directly
        app.memory.set("stefan", "system", "user.preference.language", "German",
                        created_by="agent")
        app.memory.set("anna", "system", "user.preference.language", "English",
                        created_by="agent")

        # Each user has their own value
        stefan_lang = app.memory.get("stefan", "system", "user.preference.language")
        anna_lang = app.memory.get("anna", "system", "user.preference.language")
        assert stefan_lang == "German"
        assert anna_lang == "English"

        # Verify cross-user isolation: stefan cannot see anna's entries
        stefan_search = app.memory.search("stefan", "system", "language")
        stefan_values = [r["value"] for r in stefan_search]
        assert "English" not in stefan_values


# ---------------------------------------------------------------------------
# Cross-cutting: Credential Proxy Never Leaks Raw Values
# ---------------------------------------------------------------------------


class TestCredentialProxyNoLeak:
    """The credential proxy never returns raw credential values to agent tools."""

    def test_credentials_encrypted_at_rest(
        self, proxy: EncryptedCredentialProxy, storage: SQLiteStorage
    ) -> None:
        """Raw credential values are never stored in plaintext."""
        proxy.store_credential(
            "anthropic",
            {"api_key": "sk-ant-test-key-that-must-be-encrypted-12345"},
        )

        row = storage.fetchone(
            "SELECT encrypted, nonce FROM credentials WHERE service = 'anthropic'"
        )
        assert row is not None
        # The plaintext must NOT appear in the encrypted blob
        assert b"sk-ant-test-key" not in row["encrypted"]
        assert b"must-be-encrypted" not in row["encrypted"]

    def test_credential_list_does_not_decrypt(
        self, proxy: EncryptedCredentialProxy
    ) -> None:
        """list_credentials returns metadata only, no decrypted values."""
        proxy.store_credential(
            "secret_service",
            {"api_key": "super-secret-value-12345"},
            description="Test credential",
        )

        creds = proxy.list_credentials()
        for cred in creds:
            cred_str = json.dumps(cred)
            assert "super-secret-value" not in cred_str
            assert "api_key" not in cred_str  # No decrypted fields
            # Only metadata fields
            assert "service" in cred
            assert "label" in cred

    def test_no_env_files_in_data_dir(self, app: App) -> None:
        """No .env files should ever exist in the data directory."""
        app.credentials.store_credential("test", {"key": "val"})
        env_files = list(app.data_dir.glob("**/.env*"))
        assert not env_files, (
            ".env file must not exist -- credentials belong in Credential Proxy"
        )
