"""Security tests for SSRF protection in HTTP tools.

Verifies that http_get and http_post block requests to:
- Private IP ranges (10.x, 172.16-31.x, 192.168.x)
- Localhost (127.0.0.1, ::1)
- Link-local (169.254.x.x — AWS/GCP metadata)
- Cloud metadata endpoints
"""

import pytest

from mycelos.connectors.http_tools import _validate_url, http_get, http_post


class TestValidateUrl:
    """Direct tests for URL validation."""

    def test_blocks_localhost_ip(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://127.0.0.1/admin")

    def test_blocks_localhost_ipv6(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://[::1]/admin")

    def test_blocks_private_10(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://10.0.0.5:5432/")

    def test_blocks_private_172(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://172.16.0.1/")

    def test_blocks_private_192(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://192.168.1.1/")

    def test_blocks_link_local_metadata(self):
        with pytest.raises(ValueError, match="Blocked host|Non-global IP|Private/reserved"):
            _validate_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_gcp_metadata_host(self):
        with pytest.raises(ValueError, match="Blocked host"):
            _validate_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_blocks_non_http_scheme(self):
        with pytest.raises(ValueError, match="Blocked scheme"):
            _validate_url("file:///etc/passwd")

    def test_blocks_ftp_scheme(self):
        with pytest.raises(ValueError, match="Blocked scheme"):
            _validate_url("ftp://internal.server/data")

    def test_blocks_empty_hostname(self):
        with pytest.raises(ValueError, match="Empty hostname"):
            _validate_url("http:///path")

    def test_allows_public_https(self):
        # Should not raise — but may fail if DNS is unavailable (sandbox/CI)
        try:
            _validate_url("https://api.github.com/repos")
        except ValueError as e:
            if "Cannot resolve" in str(e):
                pytest.skip("DNS not available in this environment")
            raise

    def test_allows_public_http(self):
        try:
            _validate_url("http://example.com/page")
        except ValueError as e:
            if "Cannot resolve" in str(e):
                pytest.skip("DNS not available in this environment")
            raise

    def test_blocks_zero_ip(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://0.0.0.0/")

    def test_blocks_cgnat_range(self):
        with pytest.raises(ValueError, match="Non-global IP"):
            _validate_url("http://100.64.0.1/")

    def test_blocks_cgnat_alibaba_adjacent(self):
        with pytest.raises(ValueError, match="Non-global IP"):
            _validate_url("http://100.100.100.201/")

    def test_blocks_cgnat_upper(self):
        with pytest.raises(ValueError, match="Non-global IP"):
            _validate_url("http://100.127.255.254/")


class TestHttpGetSsrf:
    """Integration tests: http_get returns error for blocked URLs."""

    def test_get_blocks_localhost(self):
        result = http_get("http://127.0.0.1:8080/secret")
        assert result["status"] == 0
        assert "blocked" in result["error"].lower()

    def test_get_blocks_metadata(self):
        result = http_get("http://169.254.169.254/latest/meta-data/")
        assert result["status"] == 0
        assert "blocked" in result["error"].lower()


class TestHttpPostSsrf:
    """Integration tests: http_post returns error for blocked URLs."""

    def test_post_blocks_localhost(self):
        result = http_post("http://127.0.0.1:9200/_search", body={"query": "*"})
        assert result["status"] == 0
        assert "blocked" in result["error"].lower()

    def test_post_blocks_private_ip(self):
        result = http_post("http://10.0.0.5:5432/", body="SELECT 1")
        assert result["status"] == 0
        assert "blocked" in result["error"].lower()


class TestMulticastAndUnspecified:
    """SSRF: multicast and unspecified IPs are blocked."""

    def test_blocks_multicast_ipv4(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://224.0.0.1/")

    def test_blocks_multicast_ipv6(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://[ff02::1]/")

    def test_blocks_unspecified_ipv6(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://[::]/")

    def test_blocks_multicast_high(self):
        with pytest.raises(ValueError, match="Non-global IP|Private/reserved"):
            _validate_url("http://239.255.255.250:1900/")
