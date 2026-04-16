"""Tests for Filesystem Mounts — scoped directory access."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App
from mycelos.chat.service import ChatService
from mycelos.chat.slash_commands import handle_slash_command
from mycelos.security.mounts import MountRegistry


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-mounts"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def mounts(app: App) -> MountRegistry:
    return MountRegistry(app.storage)


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    d = tmp_path / "testfiles"
    d.mkdir()
    (d / "invoice.txt").write_text("Invoice #123, Amount: 500 EUR")
    (d / "readme.md").write_text("# Test README")
    (d / "subdir").mkdir()
    (d / "subdir" / "nested.txt").write_text("nested content")
    return d


# --- MountRegistry ---

class TestMountRegistry:

    def test_add_mount(self, mounts):
        mid = mounts.add("/tmp/test", "read", purpose="Test files")
        assert mid is not None
        m = mounts.get(mid)
        assert m is not None
        assert m["access"] == "read"

    def test_add_mount_write(self, mounts):
        mid = mounts.add("/tmp/test", "write")
        m = mounts.get(mid)
        assert m["access"] == "write"

    def test_add_mount_invalid_access(self, mounts):
        with pytest.raises(ValueError):
            mounts.add("/tmp/test", "execute")

    def test_list_mounts(self, mounts):
        mounts.add("/tmp/a", "read")
        mounts.add("/tmp/b", "write")
        result = mounts.list_mounts()
        assert len(result) == 2

    def test_list_mounts_by_agent(self, mounts, app):
        app.agent_registry.register("a1", "Test", "deterministic", [], "system")
        mounts.add("/tmp/global", "read")
        mounts.add("/tmp/agent", "read", agent_id="a1")
        result = mounts.list_mounts(agent_id="a1")
        paths = [m["path"] for m in result]
        # Should see both global and agent-specific
        assert len(result) == 2

    def test_revoke_mount(self, mounts):
        mid = mounts.add("/tmp/test", "read")
        assert mounts.revoke(mid)
        m = mounts.get(mid)
        assert m["status"] == "revoked"

    def test_revoked_not_in_list(self, mounts):
        mid = mounts.add("/tmp/test", "read")
        mounts.revoke(mid)
        result = mounts.list_mounts(status="active")
        assert len(result) == 0

    def test_check_access_allowed(self, mounts, test_dir):
        mounts.add(str(test_dir), "read")
        assert mounts.check_access(str(test_dir / "invoice.txt"), "read")

    def test_check_access_nested(self, mounts, test_dir):
        mounts.add(str(test_dir), "read")
        assert mounts.check_access(str(test_dir / "subdir" / "nested.txt"), "read")

    def test_check_access_denied_not_mounted(self, mounts):
        assert not mounts.check_access("/etc/passwd", "read")

    def test_path_traversal_prefix_attack(self, mounts, test_dir):
        """SECURITY: /data mounted must NOT allow /data_secrets access."""
        mounts.add(str(test_dir), "read")
        # Attacker tries a path that shares the same prefix but is outside
        sibling = str(test_dir) + "_secrets"
        assert not mounts.check_access(sibling + "/passwords.txt", "read")

    def test_path_traversal_dotdot(self, mounts, test_dir):
        """SECURITY: .. traversal must not escape mount."""
        mounts.add(str(test_dir), "read")
        # Path resolves to parent — should be denied
        traversal = str(test_dir / "subdir" / ".." / ".." / "etc" / "passwd")
        assert not mounts.check_access(traversal, "read")

    def test_check_access_denied_wrong_level(self, mounts, test_dir):
        mounts.add(str(test_dir), "read")
        assert not mounts.check_access(str(test_dir / "output.csv"), "write")

    def test_check_access_write_allowed(self, mounts, test_dir):
        mounts.add(str(test_dir), "write")
        assert mounts.check_access(str(test_dir / "output.csv"), "write")

    def test_check_access_read_write(self, mounts, test_dir):
        mounts.add(str(test_dir), "read_write")
        assert mounts.check_access(str(test_dir / "file.txt"), "read")
        assert mounts.check_access(str(test_dir / "file.txt"), "write")

    def test_resolve_mounts_agent_specific(self, mounts, app, test_dir):
        app.agent_registry.register("a1", "Test", "deterministic", [], "system")
        mounts.add(str(test_dir), "read")
        mounts.add(str(test_dir / "special"), "write", agent_id="a1")
        result = mounts.resolve_mounts(agent_id="a1")
        assert len(result) == 2


# --- Filesystem Tools (via ChatService) ---

class TestFilesystemTools:

    def test_read_file(self, app, test_dir):
        mounts = MountRegistry(app.storage)
        mounts.add(str(test_dir), "read")

        svc = ChatService(app)
        result = svc._filesystem_read(str(test_dir / "invoice.txt"))
        assert "Invoice #123" in result["content"]

    def test_read_denied_no_mount(self, app, test_dir):
        from mycelos.security.permissions import PermissionRequired
        svc = ChatService(app)
        with pytest.raises(PermissionRequired) as exc_info:
            svc._filesystem_read(str(test_dir / "invoice.txt"))
        assert "mount" in exc_info.value.action

    def test_write_file(self, app, test_dir):
        mounts = MountRegistry(app.storage)
        mounts.add(str(test_dir), "write")

        svc = ChatService(app)
        out_path = str(test_dir / "output.csv")
        result = svc._filesystem_write(out_path, "col1,col2\n1,2\n")
        assert result["status"] == "written"
        assert Path(out_path).read_text() == "col1,col2\n1,2\n"

    def test_write_denied_read_only(self, app, test_dir):
        from mycelos.security.permissions import PermissionRequired
        mounts = MountRegistry(app.storage)
        mounts.add(str(test_dir), "read")

        svc = ChatService(app)
        with pytest.raises(PermissionRequired):
            svc._filesystem_write(str(test_dir / "hack.txt"), "bad")

    def test_list_directory(self, app, test_dir):
        mounts = MountRegistry(app.storage)
        mounts.add(str(test_dir), "read")

        svc = ChatService(app)
        result = svc._filesystem_list(str(test_dir))
        assert result["count"] >= 3
        names = [f["name"] for f in result["files"]]
        assert "invoice.txt" in names

    def test_list_denied_no_mount(self, app, test_dir):
        from mycelos.security.permissions import PermissionRequired
        svc = ChatService(app)
        with pytest.raises(PermissionRequired):
            svc._filesystem_list(str(test_dir))


# --- /mount Slash Commands ---

class TestMountSlashCommands:

    def test_mount_list_empty(self, app):
        result = handle_slash_command(app, "/mount list")
        assert "No directories" in result

    def test_mount_add(self, app, test_dir):
        result = handle_slash_command(app, f"/mount add {test_dir} --read")
        assert "mounted" in result.lower() or "Mount" in result
        assert str(test_dir) in result

    def test_mount_add_write(self, app, test_dir):
        result = handle_slash_command(app, f"/mount add {test_dir} --write")
        assert "write" in result.lower()

    def test_mount_add_for_agent(self, app, test_dir):
        app.agent_registry.register("a1", "Test", "deterministic", [], "system")
        result = handle_slash_command(app, f"/mount add {test_dir} --read --agent a1")
        assert "a1" in result

    def test_mount_list_after_add(self, app, test_dir):
        handle_slash_command(app, f"/mount add {test_dir} --read")
        result = handle_slash_command(app, "/mount list")
        assert str(test_dir) in result

    def test_mount_revoke(self, app, test_dir):
        handle_slash_command(app, f"/mount add {test_dir} --read")
        mounts = MountRegistry(app.storage)
        all_mounts = mounts.list_mounts()
        mid = all_mounts[0]["id"][:8]
        result = handle_slash_command(app, f"/mount revoke {mid}")
        assert "revoked" in result.lower()

    def test_mount_nonexistent_path(self, app):
        result = handle_slash_command(app, "/mount add /nonexistent/path/xyz --read")
        assert "not exist" in result.lower() or "not found" in result.lower()

    def test_mount_creates_config_generation(self, app, test_dir):
        gen_before = app.config.get_active_generation_id()
        handle_slash_command(app, f"/mount add {test_dir} --read")
        gen_after = app.config.get_active_generation_id()
        assert gen_after != gen_before


# --- NixOS State ---

class TestMountState:

    def test_mount_in_snapshot(self, app, test_dir):
        mounts = MountRegistry(app.storage)
        mounts.add(str(test_dir), "read", purpose="Test")
        snapshot = app.state_manager.snapshot()
        assert "mounts" in snapshot
        assert len(snapshot["mounts"]) == 1

    def test_mount_rollback(self, app, test_dir):
        gen1 = app.config.apply_from_state(app.state_manager, "before", "test")
        mounts = MountRegistry(app.storage)
        mounts.add(str(test_dir), "read")
        app.config.apply_from_state(app.state_manager, "with mount", "test")
        assert len(mounts.list_mounts()) == 1

        app.config.rollback(to_generation=gen1, state_manager=app.state_manager)
        assert len(mounts.list_mounts()) == 0
