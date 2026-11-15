"""Security invariant: a password-less gateway answers only for known hosts.

DNS rebinding: a page on evil.example re-points its own name to
127.0.0.1. The browser then treats http://evil.example:9100 as the
page's own origin, so the localhost check (TCP peer is 127.0.0.1) and
the CSRF check (Origin == Host) both pass. Only the Host header still
names the attacker's domain — the gateway must refuse it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mycelos.gateway.host_guard import build_allowed_hosts, normalize_host


def _make_client(tmp_path: Path, monkeypatch, *, allowed: str = "", password: str | None = None,
                 host: str = "127.0.0.1") -> TestClient:
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "host-guard-test-key")
    monkeypatch.setenv("MYCELOS_ALLOWED_HOSTS", allowed)
    monkeypatch.delenv("MYCELOS_ALLOWED_ORIGINS", raising=False)
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    App(tmp_path).initialize()
    api = create_app(tmp_path, no_scheduler=True, host=host, port=9100, password=password,
                     allow_insecure_bind=host not in ("127.0.0.1", "::1", "localhost"))
    # Real browsers talk to http://localhost:9100; TestClient's own peer
    # address is "testclient", which LocalhostMiddleware would refuse.
    return TestClient(api, base_url="http://localhost:9100", client=("127.0.0.1", 50000))


# --- normalize_host -------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("localhost:9100", "localhost"),
    ("LOCALHOST", "localhost"),
    ("[::1]:9100", "::1"),
    ("::1", "::1"),
    ("Pi5.Local:9100", "pi5.local"),
    ("example.com.", "example.com"),
    ("", ""),
    ("[::1", ""),
])
def test_normalize_host(raw: str, expected: str) -> None:
    assert normalize_host(raw) == expected


def test_allowed_hosts_include_loopback_bind_origins_and_env() -> None:
    hosts = build_allowed_hosts(
        "192.168.1.5",
        origins={"http://pi5.local:9100", "https://dash.example.org"},
        extra_hosts="mycelos.lan, Other.Example ",
    )
    assert hosts is not None
    assert {"localhost", "127.0.0.1", "::1", "192.168.1.5", "pi5.local",
            "dash.example.org", "mycelos.lan", "other.example"} <= hosts
    assert "evil.example" not in hosts


def test_wildcard_turns_the_check_off() -> None:
    assert build_allowed_hosts("127.0.0.1", extra_hosts="*") is None


def test_unspecified_bind_address_is_not_a_host_name() -> None:
    hosts = build_allowed_hosts("0.0.0.0", extra_hosts="")
    assert hosts is not None and "0.0.0.0" not in hosts


# --- middleware ------------------------------------------------------------

def test_rebinding_host_is_refused_for_reads_and_writes(tmp_path: Path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    evil = {"Host": "evil.example:9100", "Origin": "http://evil.example:9100"}

    read = client.get("/api/health", headers=evil)
    assert read.status_code == 421
    assert "Host not allowed" in read.text

    write = client.post("/api/credentials", json={"service": "x", "secret": "y"}, headers=evil)
    assert write.status_code == 421


@pytest.mark.parametrize("host", ["localhost:9100", "127.0.0.1:9100", "[::1]:9100"])
def test_loopback_hosts_are_served(tmp_path: Path, monkeypatch, host: str) -> None:
    client = _make_client(tmp_path, monkeypatch)
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_missing_host_header_is_refused(tmp_path: Path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    assert client.get("/api/health", headers={"Host": ""}).status_code == 421


def test_operator_listed_host_is_served(tmp_path: Path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch, allowed="mycelos.lan")
    assert client.get("/api/health", headers={"Host": "mycelos.lan:9100"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "evil.example:9100"}).status_code == 421


def test_password_protected_gateway_skips_the_host_check(tmp_path: Path, monkeypatch) -> None:
    """With a password the attacker's origin never holds credentials, so
    LAN / reverse-proxy host names work without an allowlist."""
    client = _make_client(tmp_path, monkeypatch, password="s3cret-pass", host="0.0.0.0")
    resp = client.get("/api/health", headers={"Host": "mycelos.example.net"})
    assert resp.status_code != 421


def test_telegram_webhook_is_reachable_under_its_public_name(tmp_path: Path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post("/telegram/webhook", json={}, headers={"Host": "bot.example.org"})
    # Not refused by the host guard; the route checks its own secret.
    assert resp.status_code != 421


# --- security headers -------------------------------------------------------

def test_responses_carry_security_headers(tmp_path: Path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    resp = client.get("/api/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "base-uri 'self'" in csp
    # Injected script cannot ship data to arbitrary servers via fetch/XHR.
    connect = next(d for d in csp.split(";") if d.strip().startswith("connect-src"))
    assert "https:" not in connect.split() and "*" not in connect
    # No third-party script host beyond the explicit list.
    script = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "cdn.jsdelivr.net" not in script and "*" not in script.split()
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "SAMEORIGIN"
