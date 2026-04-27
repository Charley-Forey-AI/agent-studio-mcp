"""Process-wide OpenAPI doc store (shared by server and optional admin tools)."""

from __future__ import annotations

import threading

from trimble_agentic_docs_mcp.store import OpenAPIDocStore, _default_api_dir, _default_urls_file

_store: OpenAPIDocStore | None = None
_lock = threading.Lock()


def get_store() -> OpenAPIDocStore:
    global _store
    with _lock:
        if _store is None:
            _store = OpenAPIDocStore(api_dir=_default_api_dir(), urls_file=_default_urls_file())
        return _store


def clear_openapi_cache() -> None:
    """Clear in-memory parsed OpenAPI JSON (e.g. after disk files change)."""
    global _store
    with _lock:
        if _store is not None:
            _store.clear_cache()


def reset_store() -> None:
    """Drop the process-wide store (tests or reload after changing TRIMBLE_AGENTIC_API_DOCS_DIR)."""
    global _store
    with _lock:
        _store = None
