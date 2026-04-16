"""Agent Runner — executes custom agent code in a subprocess.

Runs agent code with an AgentInput, returns AgentOutput.
Uses subprocess isolation like the TestRunner.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mycelos.agents.models import AgentInput, AgentOutput


_RUNNER_SCRIPT = '''\
import json
import sys
sys.path.insert(0, ".")

# Auto-mock mycelos.sdk for subprocess execution
from unittest.mock import MagicMock
import mycelos.sdk
mycelos.sdk.run = MagicMock(side_effect=lambda tool, args=None: {
    "status": "ok", "content": "mocked",
})
mycelos.sdk.progress = lambda text: None

from agent_code import *
from mycelos.agents.models import AgentInput, AgentOutput

input_data = json.loads(sys.argv[1])
agent_input = AgentInput(**input_data)

# Find the agent class (first class with an execute method)
import inspect
agent_class = None
for name, obj in list(globals().items()):
    if inspect.isclass(obj) and hasattr(obj, "execute") and name != "AgentInput" and name != "AgentOutput":
        agent_class = obj
        break

if agent_class is None:
    print(json.dumps({"success": False, "error": "No agent class found"}))
    sys.exit(0)

try:
    agent = agent_class()
    result = agent.execute(agent_input)
    if isinstance(result, AgentOutput):
        print(json.dumps({
            "success": result.success,
            "result": result.result,
            "artifacts": result.artifacts,
            "metadata": result.metadata,
            "error": result.error,
        }, default=str))
    else:
        print(json.dumps({"success": True, "result": str(result)}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
'''


def run_agent_code(
    code: str,
    agent_input: AgentInput,
    timeout: int = 30,
) -> AgentOutput:
    """Execute agent code in a subprocess and return the result.

    Args:
        code: Python agent code (must define a class with execute(AgentInput) -> AgentOutput).
        agent_input: The input to pass to the agent.
        timeout: Maximum execution time in seconds.

    Returns:
        AgentOutput with the agent's result.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Write agent code
        (tmp_path / "agent_code.py").write_text(code)

        # Write runner script
        (tmp_path / "run_agent.py").write_text(_RUNNER_SCRIPT)

        # Serialize input
        input_json = json.dumps({
            "task_goal": agent_input.task_goal,
            "task_inputs": agent_input.task_inputs,
            "artifacts": agent_input.artifacts,
            "context": agent_input.context,
            "config": agent_input.config,
        })

        try:
            result = subprocess.run(
                [sys.executable, "run_agent.py", input_json],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp,
                env=_safe_env(),
            )

            if result.returncode != 0:
                error = result.stderr.strip()[:500] if result.stderr else "Unknown error"
                return AgentOutput(success=False, error=f"Agent crashed: {error}")

            # Parse output
            stdout = result.stdout.strip()
            if not stdout:
                return AgentOutput(success=False, error="Agent produced no output")

            data = json.loads(stdout)
            return AgentOutput(
                success=data.get("success", False),
                result=data.get("result"),
                artifacts=data.get("artifacts", []),
                metadata=data.get("metadata", {}),
                error=data.get("error"),
            )

        except subprocess.TimeoutExpired:
            return AgentOutput(success=False, error=f"Agent timed out after {timeout}s")
        except json.JSONDecodeError:
            return AgentOutput(success=False, error="Agent output is not valid JSON")
        except Exception as e:
            return AgentOutput(success=False, error=str(e))


def _safe_env() -> dict[str, str]:
    """Build a safe environment for agent subprocess."""
    import os
    env = dict(os.environ)
    # Remove sensitive variables
    for key in list(env.keys()):
        if any(s in key.upper() for s in ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "MASTER_KEY")):
            del env[key]
    return env
