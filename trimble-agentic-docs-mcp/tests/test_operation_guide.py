"""Tests for build_operation_guide."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from trimble_agentic_docs_mcp.operation_guide import build_operation_guide
from trimble_agentic_docs_mcp.store import OpenAPIDocStore


@pytest.fixture
def minimal_store(tmp_path: Path) -> OpenAPIDocStore:
    root = Path(__file__).resolve().parent
    fixture = root / "fixtures" / "minimal.json"
    api = tmp_path / "api"
    api.mkdir()
    shutil.copy(fixture, api / "minimal.json")
    return OpenAPIDocStore(api_dir=api, urls_file=api / "missing.txt")


def test_build_operation_guide_get(minimal_store: OpenAPIDocStore) -> None:
    g = build_operation_guide(
        minimal_store,
        "minimal",
        "/widgets",
        "get",
        include_request_schema=True,
        include_response_codes=True,
        max_schema_depth=2,
    )
    assert g is not None
    assert g["operationId"] == "listWidgets"
    assert g["method"] == "GET"
    assert g["path"] == "/widgets"
    assert "parameters" in g
    assert "responses" in g and "200" in g["responses"]


def test_build_operation_guide_post_request_body(minimal_store: OpenAPIDocStore) -> None:
    g = build_operation_guide(
        minimal_store,
        "minimal",
        "/widgets",
        "post",
        include_request_schema=True,
        include_response_codes=True,
        max_schema_depth=2,
    )
    assert g is not None
    assert g["operationId"] == "createWidget"
    rb = g.get("requestBody")
    assert isinstance(rb, dict)
    assert "content" in rb


def test_build_operation_guide_not_found(minimal_store: OpenAPIDocStore) -> None:
    assert (
        build_operation_guide(
            minimal_store,
            "minimal",
            "/missing",
            "get",
            include_request_schema=True,
            include_response_codes=True,
            max_schema_depth=1,
        )
        is None
    )
