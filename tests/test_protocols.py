from mycelos.protocols import (
    StorageBackend,
    MemoryService,
    LLMBroker,
    SandboxManager,
    AuditLogger,
    CredentialProxy,
)


def test_protocols_are_importable():
    """All core service protocols can be imported."""
    assert StorageBackend is not None
    assert MemoryService is not None
    assert LLMBroker is not None
    assert SandboxManager is not None
    assert AuditLogger is not None
    assert CredentialProxy is not None
