"""Tests for truncate_json_response."""

from __future__ import annotations

import json

from trimble_agentic_docs_mcp.json_output import truncate_json_response


def test_no_truncation_when_under_limit() -> None:
    obj = {"a": 1, "b": [2, 3]}
    out = truncate_json_response(obj, 10_000)
    assert json.loads(out) == obj
    assert "truncated" not in out


def test_truncation_wrapper_when_over_limit() -> None:
    obj = {"big": "x" * 5000}
    out = truncate_json_response(obj, 800)
    data = json.loads(out)
    assert data.get("truncated") is True
    assert data.get("max_chars") == 800
    assert "hint" in data
    assert isinstance(data.get("preview"), str)
    assert len(data["preview"]) < 5000
