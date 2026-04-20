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

    def __init__(self, storage: StorageBackend, master_key: str, notifier=None) -> None:
        self._storage = storage
        self._aesgcm = AESGCM(self._derive_key(master_key))
        self._notifier = notifier

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

    def store_credential(
        self, service: str, credential: dict,
        user_id: str = "default", label: str = "default",
        description: str | None = None,
    ) -> None:
        """Encrypt and store a credential. Overwrites if (user_id, service, label) exists."""
        ciphertext, nonce = self._encrypt(credential)
        existing = self._storage.fetchone(
            "SELECT id FROM credentials WHERE user_id = ? AND service = ? AND label = ?",
            (user_id, service, label),
        )
        if existing:
            self._storage.execute(
                "UPDATE credentials SET encrypted = ?, nonce = ?, description = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE user_id = ? AND service = ? AND label = ?",
                (ciphertext, nonce, description, user_id, service, label),
            )
        else:
            self._storage.execute(
                "INSERT INTO credentials (user_id, service, label, description, encrypted, nonce) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, service, label, description, ciphertext, nonce),
            )
        if self._notifier:
            self._notifier.notify_change(f"Credential stored: {service}:{label}", "credential_store")

    def get_credential(
        self, service: str, user_id: str = "default", label: str = "default",
    ) -> dict | None:
        """Retrieve and decrypt a credential. Returns None if not found."""
        row = self._storage.fetchone(
            "SELECT encrypted, nonce FROM credentials WHERE user_id = ? AND service = ? AND label = ?",
            (user_id, service, label),
        )
        if row is None:
            return None
        return self._decrypt(row["encrypted"], row["nonce"])

    def delete_credential(
        self, service: str, user_id: str = "default", label: str = "default",
    ) -> None:
        """Remove a credential from storage."""
        self._storage.execute(
            "DELETE FROM credentials WHERE user_id = ? AND service = ? AND label = ?",
            (user_id, service, label),
        )
        if self._notifier:
            self._notifier.notify_change(f"Credential deleted: {service}:{label}", "credential_delete")

    def list_services(self, user_id: str = "default") -> list[str]:
        """List all services that have stored credentials for a user."""
        rows = self._storage.fetchall(
            "SELECT DISTINCT service FROM credentials WHERE user_id = ? ORDER BY service",
            (user_id,),
        )
        return [row["service"] for row in rows]

    def list_credentials(self, user_id: str = "default") -> list[dict]:
        """List all credentials for a user (service + label, no decryption)."""
        rows = self._storage.fetchall(
            "SELECT service, label, description, created_at FROM credentials "
            "WHERE user_id = ? ORDER BY service, label",
            (user_id,),
        )
        return [dict(r) for r in rows]

    def is_security_rotated(self, service: str, user_id: str = "default", label: str = "default") -> bool:
        """Check if a credential was rotated for security reasons."""
        row = self._storage.fetchone(
            "SELECT security_rotated FROM credentials WHERE user_id = ? AND service = ? AND label = ?",
            (user_id, service, label),
        )
        return bool(row and row["security_rotated"])

    def mark_security_rotated(self, service: str) -> None:
        """Mark a credential as security-rotated (prevents rollback)."""
        self._storage.execute(
            "UPDATE credentials SET security_rotated = 1 WHERE service = ?",
            (service,),
        )
        if self._notifier:
            self._notifier.notify_change(f"Credential rotated: {service}", "credential_rotate")


class DelegatingCredentialProxy:
    """Gateway-side credential proxy for two-container mode.

    Forwards every WRITE operation (store / delete / rotate) to the
    SecurityProxy over the shared bearer-token TCP channel. The master
    key does not exist in this process.

    READ operations:
      - ``list_credentials`` returns METADATA ONLY (service, label,
        description, created_at) from the proxy. Never plaintext.
      - ``list_services`` returns the deduped list of service names.
      - ``get_credential`` raises NotImplementedError. The only callers
        that need plaintext today are the LLM broker and MCP manager,
        and both already route through SecurityProxyClient.llm_complete
        / mcp_start / http_get. New callers needing plaintext should
        route through the proxy too.
    """

    def __init__(self, storage: Any, proxy_client: Any) -> None:
        self._storage = storage
        self._proxy_client = proxy_client

    def store_credential(
        self,
        service: str,
        credential: dict,
        user_id: str = "default",
        label: str = "default",
        description: str | None = None,
    ) -> None:
        # user_id is accepted for interface compatibility with
        # EncryptedCredentialProxy but is not forwarded — the proxy
        # handles user_id from its own bearer-authenticated request.
        self._proxy_client.credential_store(
            service, credential, label=label, description=description,
        )

    def delete_credential(
        self,
        service: str,
        user_id: str = "default",
        label: str = "default",
    ) -> None:
        self._proxy_client.credential_delete(service, label)

    def list_credentials(self, user_id: str = "default") -> list[dict]:
        return self._proxy_client.credential_list()

    def list_services(self, user_id: str = "default") -> list[str]:
        items = self._proxy_client.credential_list()
        return sorted({i["service"] for i in items if i.get("service")})

    def mark_security_rotated(self, service: str) -> None:
        self._proxy_client.credential_rotate(service, "default")

    def get_credential(
        self,
        service: str,
        user_id: str = "default",
        label: str = "default",
    ) -> dict | None:
        raise NotImplementedError(
            "Gateway cannot read credential plaintext in two-container mode. "
            "Route this call through SecurityProxyClient.llm_complete / "
            "mcp_start / http_get instead."
        )
