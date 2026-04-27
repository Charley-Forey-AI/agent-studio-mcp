"""DNS rebinding / Host header allowlist for MCP behind reverse proxies."""

from __future__ import annotations

import os

from mcp.server.transport_security import TransportSecuritySettings


def transport_security_for_bind(bind_host: str) -> TransportSecuritySettings | None:
    """
    When the MCP binds to loopback, the MCP library rejects unknown Host headers (421).
    Nginx typically forwards Host: <public_ip_or_dns>. Allow those via
    TRIMBLE_AGENTIC_MCP_ALLOWED_HOSTS (comma-separated hostnames or IPs).

    Returns None to use FastMCP defaults (localhost-only allowlist).
    """
    flag = os.environ.get("TRIMBLE_AGENTIC_MCP_DISABLE_DNS_REBINDING", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    if bind_host not in ("127.0.0.1", "localhost", "::1"):
        return None

    raw = os.environ.get("TRIMBLE_AGENTIC_MCP_ALLOWED_HOSTS", "").strip()
    if not raw:
        return None

    extra_hosts: list[str] = []
    extra_origins: list[str] = []
    for part in raw.split(","):
        h = part.strip()
        if not h:
            continue
        if h.endswith(":*"):
            extra_hosts.append(h)
        else:
            extra_hosts.append(h)
            if ":" not in h:
                extra_hosts.append(f"{h}:*")
        if not h.startswith("http"):
            extra_origins.extend(
                (
                    f"http://{h}",
                    f"http://{h}:*",
                    f"https://{h}",
                    f"https://{h}:*",
                )
            )

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"] + extra_hosts,
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ]
        + extra_origins,
    )
