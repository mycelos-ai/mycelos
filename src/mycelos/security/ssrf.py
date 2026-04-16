"""SSRF Validation — single source of truth for URL safety checks.

Used by both the SecurityProxy and the direct http_tools path.
Blocks private IPs, loopback, link-local, metadata services, and
dangerous schemes. Resolves hostnames to catch DNS rebinding.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Hosts that must never be reached (cloud metadata services)
BLOCKED_HOSTS: frozenset[str] = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "metadata.azure.internal",      # Azure IMDS
    "169.254.169.254",              # AWS/GCP/Azure metadata (also caught by link-local check)
    "100.100.100.200",              # Alibaba Cloud metadata
    "instance-data",                # EC2 metadata alias
})


def validate_url(url: str) -> None:
    """Validate URL against SSRF attacks. Raises ValueError if blocked.

    Checks:
    1. Scheme must be http or https
    2. Hostname must not be empty or in blocklist
    3. IP addresses must not be private/loopback/link-local/reserved
    4. Hostnames are resolved to check the actual IP (DNS rebinding defense)
    5. Unresolvable hostnames are blocked (fail-closed)
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    scheme = parsed.scheme.lower()

    if scheme not in ("http", "https"):
        raise ValueError(f"Blocked scheme: {scheme}")

    if not hostname:
        raise ValueError("Empty hostname")

    if hostname in BLOCKED_HOSTS:
        raise ValueError(f"Blocked host: {hostname}")

    # Check if hostname is an IP address.
    # Use is_global as an allowlist — only truly routable public IPs pass.
    # This catches CGNAT (100.64.0.0/10), private, loopback, link-local,
    # reserved, and any other non-global range.
    try:
        addr = ipaddress.ip_address(hostname)
        if not addr.is_global or addr.is_multicast:
            raise ValueError(f"Non-global IP not allowed: {hostname}")
    except ValueError as e:
        if "not allowed" in str(e):
            raise
        # Not an IP — resolve hostname to check
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            for _, _, _, _, sockaddr in resolved:
                addr = ipaddress.ip_address(sockaddr[0])
                if not addr.is_global or addr.is_multicast:
                    raise ValueError(
                        f"Hostname {hostname} resolves to non-global IP: {sockaddr[0]}"
                    )
        except socket.gaierror:
            raise ValueError(
                f"Cannot resolve hostname: {hostname} — denied (fail-closed)"
            )
