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
    db_file = tmp_data_dir / "mycelos.db"
    storage = SQLiteStorage(db_file)
    storage.initialize()

    proxy = EncryptedCredentialProxy(storage, master_key="test-key")
    proxy.store_credential("test", {"key": "val"})

    # No .env file should be created anywhere in the data directory
    env_files = list(tmp_data_dir.glob("**/.env*"))
    assert not env_files, ".env file must not exist — credentials belong in Credential Proxy"


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
    # Flag persists after update (UPDATE doesn't touch security_rotated)
    assert proxy.is_security_rotated("compromised")
