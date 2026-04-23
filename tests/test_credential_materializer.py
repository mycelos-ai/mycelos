"""The credential_materializer creates a per-session HOME tmpdir,
writes credential blobs to disk for subprocess consumption, and
cleans up on exit. All cleartext lives only while the context is
open; `finally` guarantees purge even on exceptions."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.security.credential_materializer import (
    MaterializedSession,
    materialize_credentials,
    persist_token,
)


def test_materialize_creates_expected_file_layout(tmp_path):
    """Given a recipe with oauth_keys_home_dir and oauth_keys_filename,
    the context manager creates <root>/<home_dir>/<filename> containing
    the credential api_key."""
    credential_proxy = MagicMock()
    credential_proxy.get_credential.return_value = {
        "api_key": '{"installed": {"client_id": "x"}}',
    }

    recipe = _recipe_with(
        oauth_keys_credential_service="gmail-oauth-keys",
        oauth_keys_home_dir=".gmail-mcp",
        oauth_keys_filename="gcp-oauth.keys.json",
    )

    with materialize_credentials(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        tmp_root=tmp_path,
        session_id="sid-1",
    ) as session:
        home = session.home_dir
        assert home.exists()
        keys_path = home / ".gmail-mcp" / "gcp-oauth.keys.json"
        assert keys_path.exists()
        assert json.loads(keys_path.read_text()) == {"installed": {"client_id": "x"}}
        assert home.name == "mycelos-oauth-sid-1"

    # After __exit__, everything under the session root is gone.
    assert not home.exists()


def test_materialize_is_a_no_op_for_recipes_without_keys(tmp_path):
    """Recipes that use env-var injection (no oauth_keys_credential_service)
    still get a HOME tmpdir but no files. Subprocess can still be spawned
    with HOME= pointing there."""
    credential_proxy = MagicMock()
    recipe = _recipe_with(
        oauth_keys_credential_service="",  # Empty
    )

    with materialize_credentials(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        tmp_root=tmp_path,
        session_id="sid-2",
    ) as session:
        assert session.home_dir.exists()
        # No file was written.
        credential_proxy.get_credential.assert_not_called()


def test_materialize_includes_token_when_already_stored(tmp_path):
    """If the recipe's oauth_token_credential_service has a value in the
    store, the token file is also materialized — this is what /mcp/start
    uses for the real server run after auth has happened."""
    credential_proxy = MagicMock()
    credential_proxy.get_credential.side_effect = lambda service, user_id="default": {
        "gmail-oauth-keys": {"api_key": '{"installed": {"client_id": "x"}}'},
        "gmail-oauth-token": {"api_key": '{"access_token": "ya29.test"}'},
    }.get(service)

    recipe = _recipe_with(
        oauth_keys_credential_service="gmail-oauth-keys",
        oauth_keys_home_dir=".gmail-mcp",
        oauth_keys_filename="gcp-oauth.keys.json",
        oauth_token_credential_service="gmail-oauth-token",
        oauth_token_filename="credentials.json",
    )

    with materialize_credentials(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        tmp_root=tmp_path,
        session_id="sid-3",
    ) as session:
        token_path = session.home_dir / ".gmail-mcp" / "credentials.json"
        assert token_path.exists()
        assert json.loads(token_path.read_text()) == {"access_token": "ya29.test"}


def test_materialize_skips_token_when_not_yet_stored(tmp_path):
    """During the initial `npx ... auth` run the token doesn't exist yet.
    The materializer must not crash; it just writes the keys file."""
    credential_proxy = MagicMock()

    def fake_get(service, user_id="default"):
        if service == "gmail-oauth-keys":
            return {"api_key": '{"installed": {}}'}
        return None  # Token not in store yet.

    credential_proxy.get_credential.side_effect = fake_get

    recipe = _recipe_with(
        oauth_keys_credential_service="gmail-oauth-keys",
        oauth_keys_home_dir=".gmail-mcp",
        oauth_keys_filename="gcp-oauth.keys.json",
        oauth_token_credential_service="gmail-oauth-token",
        oauth_token_filename="credentials.json",
    )

    with materialize_credentials(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        tmp_root=tmp_path,
        session_id="sid-4",
    ) as session:
        keys_path = session.home_dir / ".gmail-mcp" / "gcp-oauth.keys.json"
        token_path = session.home_dir / ".gmail-mcp" / "credentials.json"
        assert keys_path.exists()
        assert not token_path.exists()


def test_materialize_purges_on_exception(tmp_path):
    """`finally` must delete the tmpdir even if the `with` block raises."""
    credential_proxy = MagicMock()
    credential_proxy.get_credential.return_value = {"api_key": "{}"}
    recipe = _recipe_with(
        oauth_keys_credential_service="x",
        oauth_keys_home_dir=".x",
        oauth_keys_filename="k.json",
    )

    home_dir = None
    with pytest.raises(RuntimeError):
        with materialize_credentials(
            recipe=recipe,
            credential_proxy=credential_proxy,
            user_id="default",
            tmp_root=tmp_path,
            session_id="sid-5",
        ) as session:
            home_dir = session.home_dir
            raise RuntimeError("boom")
    assert home_dir is not None
    assert not home_dir.exists()


def test_persist_token_reads_file_and_stores(tmp_path):
    """After a successful subprocess run, persist_token reads the written
    file and calls credential_proxy.store_credential with it as api_key."""
    credential_proxy = MagicMock()
    recipe = _recipe_with(
        oauth_keys_home_dir=".gmail-mcp",
        oauth_token_filename="credentials.json",
        oauth_token_credential_service="gmail-oauth-token",
    )

    # Simulate the subprocess having written a token file during its run.
    home = tmp_path / "mycelos-oauth-sid-7"
    (home / ".gmail-mcp").mkdir(parents=True)
    (home / ".gmail-mcp" / "credentials.json").write_text('{"access_token": "new"}')

    persist_token(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        home_dir=home,
    )

    credential_proxy.store_credential.assert_called_once()
    call = credential_proxy.store_credential.call_args
    service = call.args[0] if call.args else call.kwargs.get("service")
    payload = call.args[1] if len(call.args) > 1 else call.kwargs.get("credential")
    assert service == "gmail-oauth-token"
    assert payload["api_key"] == '{"access_token": "new"}'


def test_persist_token_is_noop_when_file_missing(tmp_path):
    """If the subprocess didn't write a token (e.g. auth failed), don't
    call store_credential — persist_token just quietly exits."""
    credential_proxy = MagicMock()
    recipe = _recipe_with(
        oauth_keys_home_dir=".gmail-mcp",
        oauth_token_filename="credentials.json",
        oauth_token_credential_service="gmail-oauth-token",
    )
    home = tmp_path / "mycelos-oauth-sid-8"
    (home / ".gmail-mcp").mkdir(parents=True)
    # No file written.

    persist_token(
        recipe=recipe,
        credential_proxy=credential_proxy,
        user_id="default",
        home_dir=home,
    )

    credential_proxy.store_credential.assert_not_called()


def _recipe_with(**overrides):
    """Build a throwaway MCPRecipe for these tests — populate only the
    fields the materializer reads."""
    from mycelos.connectors.mcp_recipes import MCPRecipe
    base = dict(id="test", name="test", description="", command="npx -y x")
    base.update(overrides)
    return MCPRecipe(**base)
