"""Validate the two-container shape of docker-compose.yml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def compose():
    path = Path(__file__).parent.parent / "docker-compose.yml"
    return yaml.safe_load(path.read_text())


def test_two_services(compose):
    assert set(compose["services"]) == {"gateway", "proxy"}


def test_gateway_does_not_mount_master_key(compose):
    gw = compose["services"]["gateway"]
    for v in gw.get("volumes", []):
        assert ".master_key" not in str(v), \
            "gateway must not mount the master key; proxy owns it"


def test_proxy_mounts_data_dir(compose):
    """Proxy needs the full /data directory — not just the .db file —
    so SQLite WAL siblings stay visible and .master_key is reachable.
    File-level bind mounts were dropped in 65ed1ff because the proxy
    could not see the gateway's fresh writes (they live in the WAL
    file, which a single-file bind does not expose).
    """
    px = compose["services"]["proxy"]
    found_data_dir = False
    for v in px.get("volumes", []):
        if isinstance(v, dict):
            if str(v.get("target", "")) == "/data":
                found_data_dir = True
        else:
            s = str(v)
            # short-form 'source:/data' or 'source:/data:...'
            if ":/data" in s and ":/data/" not in s:
                found_data_dir = True
    assert found_data_dir, "proxy must mount the full /data directory"


def test_gateway_has_proxy_url_and_token(compose):
    env = compose["services"]["gateway"].get("environment", [])
    env_str = " ".join(str(e) for e in env)
    assert "MYCELOS_PROXY_URL" in env_str
    assert "MYCELOS_PROXY_TOKEN" in env_str


def test_gateway_depends_on_proxy(compose):
    depends = compose["services"]["gateway"].get("depends_on", {})
    if isinstance(depends, dict):
        assert "proxy" in depends
    else:
        assert "proxy" in depends


def test_proxy_port_not_published(compose):
    """Proxy TCP stays on the internal network — must not map to a host port."""
    px = compose["services"]["proxy"]
    assert px.get("ports", []) == [], \
        f"proxy must not publish ports (got {px.get('ports')})"


def test_same_image_for_both(compose):
    """One image, two commands — avoids image drift between gateway and proxy."""
    gw_image = compose["services"]["gateway"].get("image")
    px_image = compose["services"]["proxy"].get("image")
    assert gw_image and gw_image == px_image


def test_gateway_not_on_default_network(compose):
    """Phase 1b: gateway reaches only the proxy; no direct internet route."""
    gw = compose["services"]["gateway"]
    networks = gw.get("networks", [])
    if isinstance(networks, dict):
        networks = list(networks.keys())
    assert "default" not in networks, \
        f"gateway must not be on default network (got {networks})"
    assert "mycelos-internal" in networks


def test_proxy_db_mount_is_writable(compose):
    """Phase 1b: proxy writes credentials — mycelos.db mount must not be read_only."""
    px = compose["services"]["proxy"]
    for v in px.get("volumes", []):
        if isinstance(v, dict) and "mycelos.db" in str(v.get("target", "")):
            assert v.get("read_only") is not True, \
                "proxy must be able to write credentials — mycelos.db must not be read_only"
