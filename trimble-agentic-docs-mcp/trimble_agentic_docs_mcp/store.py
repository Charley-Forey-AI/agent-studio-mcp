"""Load and index local OpenAPI 3.x JSON specs."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_HTTP_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
)


def _repo_root() -> Path:
    """Package at <root>/trimble-agentic-docs-mcp/trimble_agentic_docs_mcp/."""
    return Path(__file__).resolve().parent.parent.parent


def get_repository_root() -> Path:
    """Repository root (directory that contains `docs/` and `trimble-agentic-docs-mcp/`)."""
    return _repo_root()


def _default_api_dir() -> Path:
    env = os.environ.get("TRIMBLE_AGENTIC_API_DOCS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    root = _repo_root()
    for rel in ("docs/api", "api"):
        candidate = (root / rel).resolve()
        if candidate.is_dir():
            return candidate
    return (Path.cwd() / "api").resolve()


def _default_urls_file() -> Path:
    env = os.environ.get("TRIMBLE_AGENTIC_URLS_FILE")
    if env:
        return Path(env).expanduser().resolve()
    root = _repo_root()
    for rel in ("docs/urls.txt", "urls.txt"):
        candidate = (root / rel).resolve()
        if candidate.is_file():
            return candidate
    return (Path.cwd() / "urls.txt").resolve()


def _safe_spec_id(name: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name))


class OpenAPIDocStore:
    """In-memory OpenAPI documents keyed by stem filename (e.g. agents, tools)."""

    def __init__(self, api_dir: Path | None = None, urls_file: Path | None = None) -> None:
        self.api_dir = api_dir or _default_api_dir()
        self.urls_file = urls_file or _default_urls_file()
        self._docs: dict[str, dict[str, Any]] = {}
        self._indexes: dict[str, list[dict[str, Any]]] = {}

    def clear_cache(self) -> None:
        self._docs.clear()
        self._indexes.clear()

    def list_spec_ids(self) -> list[str]:
        if not self.api_dir.is_dir():
            return []
        ids: list[str] = []
        for p in sorted(self.api_dir.glob("*.json")):
            sid = p.stem
            if _safe_spec_id(sid):
                ids.append(sid)
        return ids

    def _load(self, spec_id: str) -> dict[str, Any]:
        if spec_id in self._docs:
            return self._docs[spec_id]
        if not _safe_spec_id(spec_id):
            raise ValueError(f"Invalid spec_id: {spec_id!r}")
        path = (self.api_dir / f"{spec_id}.json").resolve()
        if not path.is_file():
            raise FileNotFoundError(f"No OpenAPI file for spec_id={spec_id!r} at {path}")
        try:
            path.relative_to(self.api_dir.resolve())
        except ValueError as e:
            raise FileNotFoundError("Path escapes api directory") from e
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        self._docs[spec_id] = data
        self._indexes[spec_id] = self._build_index(data)
        return data

    def _build_index(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        paths = doc.get("paths") or {}
        rows: list[dict[str, Any]] = []
        for path_key, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, op in path_item.items():
                m = method.lower()
                if m not in _HTTP_METHODS or not isinstance(op, dict):
                    continue
                rows.append(
                    {
                        "path": path_key,
                        "method": m.upper(),
                        "operationId": op.get("operationId"),
                        "summary": op.get("summary"),
                        "tags": op.get("tags") or [],
                    }
                )
        return rows

    def get_paths_index(self, spec_id: str) -> list[dict[str, Any]]:
        self._load(spec_id)
        return list(self._indexes.get(spec_id, []))

    def get_doc(self, spec_id: str) -> dict[str, Any]:
        return self._load(spec_id)

    def spec_summary(self, spec_id: str) -> dict[str, Any]:
        doc = self._load(spec_id)
        info = doc.get("info") or {}
        servers = doc.get("servers") or []
        return {
            "spec_id": spec_id,
            "openapi": doc.get("openapi"),
            "title": info.get("title"),
            "version": info.get("version"),
            "servers": servers,
            "path_count": len(doc.get("paths") or {}),
            "source": str((self.api_dir / f"{spec_id}.json").resolve()),
        }

    def list_all_summaries(self) -> list[dict[str, Any]]:
        return [self.spec_summary(sid) for sid in self.list_spec_ids()]

    def search_operations(
        self,
        query: str,
        *,
        spec_id: str | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        out: list[dict[str, Any]] = []
        ids = [spec_id] if spec_id else self.list_spec_ids()
        for sid in ids:
            if sid is None:
                continue
            self._load(sid)
            for row in self._indexes.get(sid, []):
                blob = " ".join(
                    str(x)
                    for x in (
                        row.get("path"),
                        row.get("method"),
                        row.get("operationId"),
                        row.get("summary"),
                        " ".join(row.get("tags") or []),
                    )
                    if x
                ).lower()
                if q in blob:
                    hit = {**row, "spec_id": sid}
                    out.append(hit)
                    if len(out) >= limit:
                        return out
        return out

    def get_operation(self, spec_id: str, path: str, method: str) -> dict[str, Any] | None:
        doc = self._load(spec_id)
        paths = doc.get("paths") or {}
        path_key = path if path in paths else None
        if path_key is None:
            # normalize: ensure leading slash
            alt = path if path.startswith("/") else f"/{path}"
            path_key = alt if alt in paths else None
        if path_key is None:
            return None
        path_item = paths.get(path_key)
        if not isinstance(path_item, dict):
            return None
        m = method.strip().lower()
        op = path_item.get(m)
        if not isinstance(op, dict):
            return None
        return {
            "spec_id": spec_id,
            "path": path_key,
            "method": m.upper(),
            "operation": op,
            "path_level_parameters": path_item.get("parameters"),
        }

    def resolve_internal_ref(self, spec_id: str, ref: str, depth: int = 0, max_depth: int = 12) -> Any:
        if depth > max_depth:
            return {"$ref": ref, "_error": "max_depth exceeded"}
        doc = self._load(spec_id)
        if not ref.startswith("#/"):
            return {"$ref": ref, "_error": "only internal #/ refs are resolved"}
        parts = ref[2:].split("/")
        node: Any = doc
        for p in parts:
            if not isinstance(node, dict) or p not in node:
                return {"$ref": ref, "_error": f"missing segment {p!r}"}
            node = node[p]
        if isinstance(node, dict) and "$ref" in node and isinstance(node["$ref"], str):
            return self.resolve_internal_ref(spec_id, node["$ref"], depth + 1, max_depth)
        return node

    def list_schema_names(self, spec_id: str, limit: int = 500) -> list[str]:
        doc = self._load(spec_id)
        schemas = (doc.get("components") or {}).get("schemas") or {}
        if not isinstance(schemas, dict):
            return []
        names = sorted(schemas.keys())
        return names[:limit]

    def read_urls_file(self) -> str:
        p = self.urls_file
        if not p.is_file():
            return f"(urls file not found: {p})"
        return p.read_text(encoding="utf-8")
