"""Security tests for dotfile/sensitive file protection.

Verifies that filesystem tools block access to sensitive files:
.env, .ssh/*, .master_key, credentials.json, *.pem, *.key
"""

import pytest

from mycelos.tools.filesystem import _is_sensitive_path


class TestSensitivePathDetection:
    """_is_sensitive_path blocks known sensitive patterns."""

    def test_env_file_blocked(self):
        assert _is_sensitive_path("/home/user/.env") is not None

    def test_env_local_blocked(self):
        assert _is_sensitive_path("/app/.env.local") is not None

    def test_env_production_blocked(self):
        assert _is_sensitive_path("/app/.env.production") is not None

    def test_master_key_blocked(self):
        assert _is_sensitive_path("/home/user/.mycelos/.master_key") is not None

    def test_ssh_private_key_blocked(self):
        assert _is_sensitive_path("/home/user/.ssh/id_rsa") is not None

    def test_ssh_dir_blocked(self):
        assert _is_sensitive_path("/home/user/.ssh/known_hosts") is not None

    def test_gnupg_blocked(self):
        assert _is_sensitive_path("/home/user/.gnupg/secring.gpg") is not None

    def test_aws_credentials_blocked(self):
        assert _is_sensitive_path("/home/user/.aws/credentials") is not None

    def test_kube_config_blocked(self):
        assert _is_sensitive_path("/home/user/.kube/config") is not None

    def test_pem_file_blocked(self):
        assert _is_sensitive_path("/certs/server.pem") is not None

    def test_key_file_blocked(self):
        assert _is_sensitive_path("/certs/private.key") is not None

    def test_credentials_json_blocked(self):
        assert _is_sensitive_path("/app/credentials.json") is not None

    def test_service_account_blocked(self):
        assert _is_sensitive_path("/app/service-account.json") is not None

    def test_netrc_blocked(self):
        assert _is_sensitive_path("/home/user/.netrc") is not None

    def test_npmrc_blocked(self):
        assert _is_sensitive_path("/home/user/.npmrc") is not None

    # --- Safe files ---

    def test_normal_txt_allowed(self):
        assert _is_sensitive_path("/home/user/notes.txt") is None

    def test_normal_json_allowed(self):
        assert _is_sensitive_path("/home/user/config.json") is None

    def test_normal_md_allowed(self):
        assert _is_sensitive_path("/home/user/README.md") is None

    def test_normal_py_allowed(self):
        assert _is_sensitive_path("/home/user/app.py") is None

    def test_dotgitignore_allowed(self):
        """Regular dotfiles like .gitignore are NOT blocked."""
        assert _is_sensitive_path("/project/.gitignore") is None

    def test_normal_yaml_allowed(self):
        assert _is_sensitive_path("/app/config.yaml") is None


class TestFileToolIntegration:
    """Filesystem tools reject sensitive file access."""

    def test_read_env_blocked(self, tmp_path):
        import os
        from mycelos.app import App
        from mycelos.tools.filesystem import execute_filesystem_read

        os.environ["MYCELOS_MASTER_KEY"] = "test-dotfile"
        app = App(tmp_path / "data")
        app.initialize()

        # Create a .env file in a mounted directory
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET_KEY=super-secret")

        result = execute_filesystem_read(
            {"path": str(env_file)},
            {"app": app},
        )
        assert "error" in result
        assert "blocked" in result["error"].lower()

    def test_read_normal_file_allowed(self, tmp_path):
        import os
        from mycelos.app import App
        from mycelos.tools.filesystem import execute_filesystem_read

        os.environ["MYCELOS_MASTER_KEY"] = "test-dotfile2"
        app = App(tmp_path / "data")
        app.initialize()

        # Mount the directory
        from mycelos.security.mounts import MountRegistry
        mounts = MountRegistry(app.storage)
        mounts.add(str(tmp_path), "read")

        # Create a normal file
        normal = tmp_path / "readme.txt"
        normal.write_text("Hello world")

        result = execute_filesystem_read(
            {"path": str(normal)},
            {"app": app},
        )
        assert "error" not in result
        assert result.get("content") == "Hello world"

    def test_write_env_blocked(self, tmp_path):
        import os
        from mycelos.app import App
        from mycelos.tools.filesystem import execute_filesystem_write

        os.environ["MYCELOS_MASTER_KEY"] = "test-dotfile3"
        app = App(tmp_path / "data")
        app.initialize()

        result = execute_filesystem_write(
            {"path": str(tmp_path / ".env"), "content": "LEAK=yes"},
            {"app": app},
        )
        assert "error" in result
        assert "blocked" in result["error"].lower()
