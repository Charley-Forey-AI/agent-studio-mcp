"""Optional smoke: real docs/api when present in the repository checkout."""

from __future__ import annotations

from pathlib import Path

import pytest

from trimble_agentic_docs_mcp.store import OpenAPIDocStore, get_repository_root


def test_real_docs_api_loads_if_present() -> None:
    root = get_repository_root()
    api = root / "docs" / "api"
    if not api.is_dir():
        pytest.skip("docs/api not in this checkout")
    json_files = [p for p in api.glob("*.json") if p.stem != "_openapi_manifest"]
    if not json_files:
        pytest.skip("no OpenAPI json specs under docs/api")
    store = OpenAPIDocStore(api_dir=api)
    ids = store.list_spec_ids()
    assert ids
    for sid in ids[:5]:
        summary = store.spec_summary(sid)
        assert summary.get("spec_id") == sid
        assert summary.get("title") is not None or summary.get("openapi")
