"""Tests for OpenAPIDocStore."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from trimble_agentic_docs_mcp.store import OpenAPIDocStore


@pytest.fixture
def minimal_api_dir(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parent
    fixture = root / "fixtures" / "minimal.json"
    api = tmp_path / "api"
    api.mkdir()
    shutil.copy(fixture, api / "minimal.json")
    return api


def test_list_spec_ids(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    assert store.list_spec_ids() == ["minimal"]


def test_search_operations(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    hits = store.search_operations("widget", limit=20)
    assert len(hits) >= 2
    methods = {h["method"] for h in hits}
    assert "GET" in methods and "POST" in methods
    assert all(h["spec_id"] == "minimal" for h in hits)


def test_search_operations_spec_filter(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    assert store.search_operations("list", spec_id="minimal", limit=5)
    assert store.search_operations("zzznomatchxyz", spec_id="minimal", limit=5) == []


def test_get_operation_normalizes_path(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    op1 = store.get_operation("minimal", "/widgets", "get")
    op2 = store.get_operation("minimal", "widgets", "get")
    assert op1 is not None and op2 is not None
    assert op1["path"] == "/widgets"
    assert op2["path"] == "/widgets"
    assert op1["operation"]["operationId"] == "listWidgets"


def test_get_operation_missing(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    assert store.get_operation("minimal", "/nope", "get") is None


def test_resolve_internal_ref(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    node = store.resolve_internal_ref("minimal", "#/components/schemas/Widget")
    assert isinstance(node, dict)
    assert node.get("type") == "object"
    assert "properties" in node


def test_resolve_internal_ref_invalid_spec_id(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    with pytest.raises(FileNotFoundError):
        store.resolve_internal_ref("missing", "#/components/schemas/Widget")


def test_invalid_spec_id_load_raises(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    with pytest.raises(ValueError):
        store.get_doc("../evil")


def test_read_urls_file_missing(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    body = store.read_urls_file()
    assert "not found" in body.lower()


def test_list_schema_names(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    names = store.list_schema_names("minimal", limit=50)
    assert "Widget" in names and "WidgetCreate" in names


def test_spec_summary(minimal_api_dir: Path) -> None:
    store = OpenAPIDocStore(api_dir=minimal_api_dir, urls_file=minimal_api_dir / "missing.txt")
    s = store.spec_summary("minimal")
    assert s["spec_id"] == "minimal"
    assert s["title"] == "Minimal Fixture API"
    assert s["path_count"] == 1
