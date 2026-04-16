"""Integration test: Filesystem mount lifecycle.

Tests mount + read + unmount flow, verifying that access is denied after revocation.

Cost estimate: ~$0.00 (no LLM calls needed for mount tests)
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_mount_read_unmount(integration_app, tmp_path):
    """Mount a directory, read a file via _filesystem_read, unmount, verify access denied."""
    from mycelos.chat.service import ChatService
    from mycelos.security.mounts import MountRegistry
    from mycelos.security.permissions import PermissionRequired

    app = integration_app

    # Create a test file
    test_dir = tmp_path / "test_files"
    test_dir.mkdir()
    (test_dir / "readme.txt").write_text("Hello from test file!")

    # Mount it using the app's mount registry (with notifier)
    mounts = MountRegistry(app.storage)
    mount_id = mounts.add(str(test_dir), "read")

    # Read via ChatService tool
    service = ChatService(app)
    result = service._filesystem_read(str(test_dir / "readme.txt"))
    assert "Hello from test file!" in result.get("content", ""), \
        f"Should read file content: {result}"

    # Unmount
    revoked = mounts.revoke(mount_id)
    assert revoked, "Mount should be successfully revoked"

    # Try to read again — should raise PermissionRequired (mount is revoked)
    try:
        result2 = service._filesystem_read(str(test_dir / "readme.txt"))
        # Some implementations may return an error dict instead of raising
        assert "error" in result2 or result2.get("content") != "Hello from test file!", \
            f"After revoke, access should be denied or return error: {result2}"
    except PermissionRequired:
        pass  # Expected — access denied


@pytest.mark.integration
def test_mount_access_check(integration_app, tmp_path):
    """Verify MountRegistry.check_access returns correct values."""
    from mycelos.security.mounts import MountRegistry

    app = integration_app
    mounts = MountRegistry(app.storage)

    test_dir = tmp_path / "allowed"
    test_dir.mkdir()
    other_dir = tmp_path / "forbidden"
    other_dir.mkdir()

    # Mount only the allowed dir
    mounts.add(str(test_dir), "read")

    # Access to mounted path should be allowed
    assert mounts.check_access(str(test_dir), "read"), \
        "Read access to mounted directory should be allowed"

    # Access to unmounted path should be denied
    assert not mounts.check_access(str(other_dir), "read"), \
        "Read access to unmounted directory should be denied"


@pytest.mark.integration
def test_mount_write_access(integration_app, tmp_path):
    """Read-only mount should deny write access."""
    from mycelos.security.mounts import MountRegistry

    app = integration_app
    mounts = MountRegistry(app.storage)

    test_dir = tmp_path / "readonly"
    test_dir.mkdir()

    # Mount as read-only
    mounts.add(str(test_dir), "read")

    # Read access should be allowed
    assert mounts.check_access(str(test_dir), "read"), \
        "Read access to read-only mount should be allowed"

    # Write access should be denied
    assert not mounts.check_access(str(test_dir), "write"), \
        "Write access to read-only mount should be denied"


@pytest.mark.integration
def test_filesystem_read_without_mount(integration_app, tmp_path):
    """Reading a file without any mount should raise PermissionRequired."""
    from mycelos.chat.service import ChatService
    from mycelos.security.permissions import PermissionRequired

    app = integration_app

    # Create a file but do NOT mount its directory
    test_file = tmp_path / "secret.txt"
    test_file.write_text("Secret content")

    service = ChatService(app)

    with pytest.raises(PermissionRequired):
        service._filesystem_read(str(test_file))
