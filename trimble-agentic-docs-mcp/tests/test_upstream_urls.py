"""Tests for OpenAPI URL parsing from urls.txt."""

from __future__ import annotations

from trimble_agentic_docs_mcp.upstream_sync import load_spec_source_urls


def test_parse_specs_json_urls(tmp_path) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_text(
        """## APIs

https://developer.ai.trimble.com/specs/agents-openapi.json
https://example.com/specs/kb-openapi.json
""",
        encoding="utf-8",
    )
    got = load_spec_source_urls(urls)
    assert got["agents"] == "https://developer.ai.trimble.com/specs/agents-openapi.json"
    assert got["knowledge-base"] == "https://example.com/specs/kb-openapi.json"


def test_fallback_uses_specs_pattern(tmp_path) -> None:
    urls = tmp_path / "missing.txt"
    got = load_spec_source_urls(urls)
    assert got["agents"].endswith("/specs/agents-openapi.json")
    assert "modes-inference" in got or "modes-inference" in str(got)
    assert "/specs/models-inference-openapi.json" in got["modes-inference"]
