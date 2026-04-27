"""Tests for _transport_security_for_bind (public Host behind nginx)."""

from __future__ import annotations

import pytest

from trimble_agentic_docs_mcp.transport_security_bind import transport_security_for_bind


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRIMBLE_AGENTIC_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("TRIMBLE_AGENTIC_MCP_DISABLE_DNS_REBINDING", raising=False)


def test_no_extra_returns_none_for_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    assert transport_security_for_bind("127.0.0.1") is None


def test_disable_rebinding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIMBLE_AGENTIC_MCP_DISABLE_DNS_REBINDING", "1")
    ts = transport_security_for_bind("127.0.0.1")
    assert ts is not None
    assert ts.enable_dns_rebinding_protection is False


def test_allowed_hosts_merges_public_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIMBLE_AGENTIC_MCP_ALLOWED_HOSTS", "52.13.6.105")
    ts = transport_security_for_bind("127.0.0.1")
    assert ts is not None
    assert ts.enable_dns_rebinding_protection is True
    assert "52.13.6.105" in ts.allowed_hosts
    assert "52.13.6.105:*" in ts.allowed_hosts
    assert "http://52.13.6.105" in ts.allowed_origins


def test_non_loopback_bind_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIMBLE_AGENTIC_MCP_ALLOWED_HOSTS", "52.13.6.105")
    assert transport_security_for_bind("0.0.0.0") is None
