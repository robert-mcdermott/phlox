"""Tests for the web_fetch SSRF guard (blocks private/loopback/link-local targets)."""
from app.agent.tools.web import _host_allowlisted, _is_blocked_ip, _ssrf_guard


def test_is_blocked_ip_flags_private_and_loopback_and_metadata():
    assert _is_blocked_ip("10.0.0.5")
    assert _is_blocked_ip("172.16.0.1")
    assert _is_blocked_ip("192.168.1.1")
    assert _is_blocked_ip("127.0.0.1")
    assert _is_blocked_ip("169.254.169.254")  # cloud metadata endpoint
    assert _is_blocked_ip("::1")
    assert _is_blocked_ip("fc00::1")  # IPv6 unique local


def test_is_blocked_ip_allows_public_ip():
    assert not _is_blocked_ip("8.8.8.8")


def test_is_blocked_ip_fails_closed_on_garbage():
    assert _is_blocked_ip("not-an-ip")


def test_host_allowlisted_matches_hostname_and_cidr():
    assert _host_allowlisted("internal.example.com", ["internal.example.com"])
    assert _host_allowlisted("10.0.0.5", ["10.0.0.0/8"])
    assert not _host_allowlisted("10.0.0.5", ["192.168.0.0/16"])
    assert not _host_allowlisted("evil.example.com", ["internal.example.com"])


def test_ssrf_guard_blocks_loopback_url():
    assert _ssrf_guard("http://127.0.0.1/admin") is not None


def test_ssrf_guard_blocks_metadata_ip():
    assert _ssrf_guard("http://169.254.169.254/latest/meta-data/") is not None


def test_ssrf_guard_allows_url_with_no_private_resolution(monkeypatch):
    # Avoid a real DNS lookup in CI: stub getaddrinfo to resolve to a public IP.
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )
    assert _ssrf_guard("http://example.com/") is None


def test_ssrf_guard_respects_allow_private_networks_config(monkeypatch):
    from app.agent.tools import web as web_module

    monkeypatch.setattr(
        web_module, "get_web_fetch_config", lambda: {"allow_private_networks": True, "allowlist_hosts": []}
    )
    assert _ssrf_guard("http://127.0.0.1/admin") is None
