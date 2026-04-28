"""HTTP helpers: optional anonymous retry when Bearer gets 401 (portal/CDN quirks)."""

from __future__ import annotations

import os
from typing import Any

import httpx


def no_auth_retry_disabled() -> bool:
    return os.environ.get("TRIMBLE_AGENTIC_SYNC_NO_AUTH_RETRY", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def request_with_optional_anonymous_retry(
    client: httpx.Client,
    method: str,
    url: str,
    headers: dict[str, str],
) -> tuple[httpx.Response, dict[str, Any]]:
    """
    On 401 with Authorization set, retry once without Bearer unless TRIMBLE_AGENTIC_SYNC_NO_AUTH_RETRY.

    Some hosts serve public assets anonymously but reject unrelated OAuth Bearer tokens.
    """
    meta: dict[str, Any] = {}
    first = client.request(method, url, headers=headers)
    if first.status_code == 401 and headers.get("Authorization") and not no_auth_retry_disabled():
        pub = {k: v for k, v in headers.items() if k.lower() != "authorization"}
        second = client.request(method, url, headers=pub)
        meta["auth_retry_without_bearer"] = True
        return second, meta
    return first, meta
