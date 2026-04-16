"""LocalSandbox -- subprocess-based agent execution.

Creates an isolated subprocess with:
- Stripped environment (only MYCELOS_SESSION_TOKEN + paths)
- Temp directories: input (read), workspace (work), output (write)
- Timeout enforcement

WARNING: LocalSandbox is NOT a security boundary.
Use DockerSandbox for production (Phase 2.4).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SandboxConfig:
    """Configuration for creating and executing within a sandbox."""

    agent_id: str
    session_token: str
    timeout_seconds: int = 30
    input_files: dict[str, str] = field(default_factory=dict)


@dataclass
class SandboxResult:
    """Result of a sandbox command execution."""

    exit_code: int
    stdout: str
    stderr: str
    output_dir: Path
    timed_out: bool = False


class LocalSandbox:
    """Development sandbox using subprocess + temp dirs.

    NOT a security boundary. For development and testing only.
    """

    def __init__(self) -> None:
        self._sandboxes: dict[str, Path] = {}

    def create(self, config: SandboxConfig) -> str:
        """Create a new sandbox with input/workspace/output directories.

        Args:
            config: Sandbox configuration including agent_id and input files.

        Returns:
            A unique sandbox identifier.
        """
        sandbox_id = str(uuid.uuid4())
        base_dir = Path(tempfile.mkdtemp(prefix=f"mycelos-sandbox-{config.agent_id}-"))

        input_dir = base_dir / "input"
        workspace_dir = base_dir / "workspace"
        output_dir = base_dir / "output"

        input_dir.mkdir()
        workspace_dir.mkdir()
        output_dir.mkdir()

        for name, source_path in config.input_files.items():
            shutil.copy2(source_path, input_dir / name)

        self._sandboxes[sandbox_id] = base_dir
        return sandbox_id

    def execute(
        self, sandbox_id: str, command: list[str], config: SandboxConfig
    ) -> SandboxResult:
        """Execute a command inside the sandbox with a stripped environment.

        Args:
            sandbox_id: Identifier returned by create().
            command: Command and arguments to execute.
            config: Configuration with session token and timeout.

        Returns:
            SandboxResult with exit code, stdout, stderr, and timeout status.

        Raises:
            ValueError: If sandbox_id is not found.
        """
        base_dir = self._sandboxes.get(sandbox_id)
        if base_dir is None:
            raise ValueError(f"Sandbox {sandbox_id} not found")

        env = {
            "MYCELOS_SESSION_TOKEN": config.session_token,
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(base_dir / "workspace"),
            "MYCELOS_INPUT": str(base_dir / "input"),
            "MYCELOS_WORKSPACE": str(base_dir / "workspace"),
            "MYCELOS_OUTPUT": str(base_dir / "output"),
        }

        try:
            result = subprocess.run(
                command,
                cwd=str(base_dir / "workspace"),
                env=env,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
            )
            return SandboxResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                output_dir=base_dir / "output",
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Timeout after {config.timeout_seconds}s",
                output_dir=base_dir / "output",
                timed_out=True,
            )

    def cleanup(self, sandbox_id: str) -> None:
        """Remove all sandbox directories and deregister.

        Args:
            sandbox_id: Identifier returned by create().
        """
        base_dir = self._sandboxes.pop(sandbox_id, None)
        if base_dir and base_dir.exists():
            shutil.rmtree(base_dir, ignore_errors=True)

    def get_output_dir(self, sandbox_id: str) -> Path | None:
        """Return the output directory path for a sandbox.

        Args:
            sandbox_id: Identifier returned by create().

        Returns:
            Path to output directory, or None if sandbox not found.
        """
        base_dir = self._sandboxes.get(sandbox_id)
        if base_dir:
            return base_dir / "output"
        return None
