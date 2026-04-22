"""Short-lived file materialization for MCP servers that hardcode
file paths.

Many upstream MCP packages read their OAuth credentials from a fixed
path under `$HOME` (e.g. `~/.gmail-mcp/gcp-oauth.keys.json`) with no
env var override. To stay within the 'DB is the only persistent copy
of the cleartext' rule, we materialize the credentials into a
per-session tmp HOME directly before spawning the subprocess and
purge on exit.

This module is pure — it knows nothing about subprocesses. The caller
is responsible for:
  - using the returned `MaterializedSession.home_dir` as HOME in the
    spawned subprocess
  - calling `persist_token(...)` after a successful run so any tokens
    the package wrote get saved back to the DB
"""
from __future__ import annotations

import contextlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


@dataclass
class MaterializedSession:
    """A tmp-dir scope that owns a HOME directory for one subprocess."""
    home_dir: Path


@contextlib.contextmanager
def materialize_credentials(
    recipe,
    credential_proxy,
    user_id: str,
    tmp_root: Path,
    session_id: str,
) -> Iterator[MaterializedSession]:
    """Materialize a recipe's OAuth keys (and token, if present) into a
    session-scoped tmp HOME directory.

    Guarantees purge on exit via try/finally. Layout:
        <tmp_root>/mycelos-oauth-<session_id>/          <- HOME
            <oauth_keys_home_dir>/<oauth_keys_filename>
            <oauth_keys_home_dir>/<oauth_token_filename> (if token exists)

    Recipes without oauth_keys_credential_service still get an empty
    HOME — some subprocesses set up files on their own under HOME and
    would write to the user's real ~/ otherwise.
    """
    session_root = tmp_root / f"mycelos-oauth-{session_id}"
    session_root.mkdir(parents=True, exist_ok=False)
    try:
        keys_service = getattr(recipe, "oauth_keys_credential_service", "") or ""
        home_dir_name = getattr(recipe, "oauth_keys_home_dir", "") or ""
        if keys_service and home_dir_name:
            target_dir = session_root / home_dir_name
            target_dir.mkdir(parents=True, exist_ok=True)

            keys_cred = credential_proxy.get_credential(keys_service, user_id=user_id)
            if keys_cred and keys_cred.get("api_key"):
                keys_path = target_dir / recipe.oauth_keys_filename
                keys_path.write_text(keys_cred["api_key"])
                # Best-effort mode tightening — the package only needs
                # to read, the proxy process is the only writer.
                try:
                    keys_path.chmod(0o600)
                except OSError:
                    pass

            token_service = getattr(recipe, "oauth_token_credential_service", "") or ""
            token_name = getattr(recipe, "oauth_token_filename", "") or ""
            if token_service and token_name:
                token_cred = credential_proxy.get_credential(token_service, user_id=user_id)
                if token_cred and token_cred.get("api_key"):
                    token_path = target_dir / token_name
                    token_path.write_text(token_cred["api_key"])
                    try:
                        token_path.chmod(0o600)
                    except OSError:
                        pass

        yield MaterializedSession(home_dir=session_root)
    finally:
        # Best-effort purge. If this raises we still want the exception
        # from the `with` body to propagate, so swallow cleanup errors.
        try:
            shutil.rmtree(session_root, ignore_errors=True)
        except Exception:
            logger.warning("credential_materializer cleanup failed for %s", session_root)


def persist_token(
    recipe,
    credential_proxy,
    user_id: str,
    home_dir: Path,
) -> None:
    """Read the token file the subprocess wrote (if any) and store it.

    Called by the proxy's /oauth/stream handler after the auth subprocess
    exits with code 0. If the subprocess didn't produce a token (auth
    failed, wrong scopes, upstream bug), this is a silent no-op — the
    caller should already be looking at exit_code to decide success.
    """
    token_service = getattr(recipe, "oauth_token_credential_service", "") or ""
    token_name = getattr(recipe, "oauth_token_filename", "") or ""
    home_dir_name = getattr(recipe, "oauth_keys_home_dir", "") or ""
    if not token_service or not token_name or not home_dir_name:
        return

    token_path = home_dir / home_dir_name / token_name
    if not token_path.exists():
        return

    content = token_path.read_text()
    if not content.strip():
        return

    credential_proxy.store_credential(
        token_service,
        {"api_key": content},
        user_id=user_id,
        label="default",
        description=f"OAuth token materialized from {home_dir_name}/{token_name}",
    )
