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


def test_proxy_mounts_master_key_readonly(compose):
    px = compose["services"]["proxy"]
    found = False
    for v in px.get("volumes", []):
        if isinstance(v, dict):
            if ".master_key" in str(v.get("target", "")) or ".master_key" in str(v.get("source", "")):
                assert v.get("read_only") is True, \
                    f"proxy must mount .master_key read-only (got {v!r})"
                found = True
        else:
            s = str(v)
            if ".master_key" in s:
                assert ":ro" in s, f"proxy must mount .master_key read-only (got {s!r})"
                found = True
    assert found, "proxy must mount .master_key"


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
