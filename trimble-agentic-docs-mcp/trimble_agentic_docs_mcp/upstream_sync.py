"""
Download OpenAPI JSON from the developer portal (official spec URLs), not HTML scraping.

The human docs under /docs/... are not fetched here; only machine-readable OpenAPI
documents from the ## APIs section of urls.txt (or a built-in fallback map).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

# Path segment in /api/{segment} -> local filename stem (without .json)
_API_PATH_TO_SPEC_ID: dict[str, str] = {
    "agents": "agents",
    "evals": "evals",
    "ingest": "ingest",
    "kb": "knowledge-base",
    "models-control-plane": "model-control-plane",
    "models-inference": "modes-inference",
    "tools": "tools",
}

EXPECTED_OPENAPI_SPECS: tuple[str, ...] = tuple(sorted(set(_API_PATH_TO_SPEC_ID.values())))

_MANIFEST_NAME = "_openapi_manifest.json"

_DEFAULT_UA = "trimble-agentic-docs-mcp/0.1 (+https://github.com/trimble; enterprise sync)"


def _parse_api_urls_from_urls_txt(text: str) -> dict[str, str]:
    """Return spec_id -> absolute URL for lines under '## APIs'."""
    lines = text.splitlines()
    in_apis = False
    out: dict[str, str] = {}
    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            in_apis = s.lower().startswith("## apis")
            continue
        if not in_apis or not s.startswith("http"):
            continue
        m = re.search(r"/api/([^/?#]+)/?\s*$", s.rstrip("/"))
        if not m:
            continue
        segment = m.group(1)
        spec_id = _API_PATH_TO_SPEC_ID.get(segment)
        if spec_id:
            out[spec_id] = s.split()[0]
    return out


def load_spec_source_urls(urls_file: Path | None) -> dict[str, str]:
    """Resolve OpenAPI download URLs: defaults for every spec, overridden by urls.txt ## APIs."""
    base = (
        os.environ.get("TRIMBLE_AGENTIC_OPENAPI_FALLBACK_BASE") or "https://developer.ai.trimble.com/api"
    ).rstrip("/")
    fallback = {sid: f"{base}/{path}" for path, sid in _API_PATH_TO_SPEC_ID.items()}
    if not urls_file or not urls_file.is_file():
        return fallback
    parsed = _parse_api_urls_from_urls_txt(urls_file.read_text(encoding="utf-8"))
    return {**fallback, **parsed}


def manifest_path(api_dir: Path) -> Path:
    return api_dir / _MANIFEST_NAME


def load_manifest(api_dir: Path) -> dict[str, Any]:
    p = manifest_path(api_dir)
    if not p.is_file():
        return {"entries": {}, "updated_at": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"entries": {}, "updated_at": None, "error": "invalid_manifest_json"}


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(suffix=".json.tmp", dir=str(path.parent))
    try:
        os.write(fd, raw)
        os.close(fd)
        Path(tmp).replace(path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _normalize_etag(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    e = value.strip()
    if e.startswith("W/") and len(e) >= 4 and e[2] == '"':
        e = e[2:]
    if e.startswith('"') and e.endswith('"') and len(e) >= 2:
        return e[1:-1]
    return e or None


def _validate_openapi(doc: Any) -> tuple[bool, str]:
    if not isinstance(doc, dict):
        return False, "root_not_object"
    ver = doc.get("openapi")
    if not isinstance(ver, str) or not ver.startswith("3."):
        return False, f"missing_or_bad_openapi_field:{ver!r}"
    if "paths" not in doc:
        return False, "missing_paths"
    return True, "ok"


def run_openapi_sync(
    *,
    api_dir: Path,
    urls_file: Path | None,
    dry_run: bool,
    timeout_s: float = 120.0,
    user_agent: str | None = None,
    if_changed: bool = False,
) -> dict[str, Any]:
    """
    GET each OpenAPI URL, validate, optionally write docs/api/{spec}.json and manifest.

    Uses httpx with redirects; no browser / HTML parsing.
    When if_changed is True (and not dry_run), sends HEAD first and skips GET + disk write
    if the ETag matches the last successful entry in _openapi_manifest.json.
    """
    sources = load_spec_source_urls(urls_file)
    ua = user_agent or os.environ.get("TRIMBLE_AGENTIC_SYNC_USER_AGENT") or _DEFAULT_UA
    headers = {"Accept": "application/json,*/*;q=0.8", "User-Agent": ua}
    token = (os.environ.get("TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    results: dict[str, Any] = {}
    manifest_entries: dict[str, Any] = {}
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    prev_manifest = load_manifest(api_dir) if if_changed and not dry_run else {"entries": {}}
    prev_entries: dict[str, Any] = dict(prev_manifest.get("entries") or {})

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    all_spec_ids = sorted(set(_API_PATH_TO_SPEC_ID.values()))
    with httpx.Client(timeout=timeout_s, follow_redirects=True, limits=limits) as client:
        for spec_id in all_spec_ids:
            url = sources.get(spec_id)
            if not url:
                results[spec_id] = {"spec_id": spec_id, "error": "no_source_url"}
                continue
            one: dict[str, Any] = {"spec_id": spec_id, "source_url": url}
            try:
                if if_changed and not dry_run:
                    prev_etag = _normalize_etag(
                        (prev_entries.get(spec_id) or {}).get("etag") if isinstance(prev_entries.get(spec_id), dict) else None
                    )
                    try:
                        hr = client.head(url, headers=headers)
                        one["head_status"] = hr.status_code
                        if hr.status_code == 200:
                            remote_etag = _normalize_etag(hr.headers.get("etag"))
                            one["etag_head"] = hr.headers.get("etag")
                            if prev_etag and remote_etag and prev_etag == remote_etag:
                                one["status"] = "skipped_unchanged"
                                one["etag"] = hr.headers.get("etag")
                                results[spec_id] = one
                                continue
                    except httpx.HTTPError:
                        pass

                r = client.get(url, headers=headers)
                one["http_status"] = r.status_code
                one["etag"] = r.headers.get("etag")
                one["last_modified"] = r.headers.get("last-modified")
                body = r.content
                one["bytes"] = len(body)
                one["sha256"] = hashlib.sha256(body).hexdigest()
                if r.status_code != 200:
                    one["error"] = f"http_{r.status_code}"
                    results[spec_id] = one
                    continue
                doc = json.loads(body.decode("utf-8"))
                ok, why = _validate_openapi(doc)
                if not ok:
                    one["error"] = why
                    results[spec_id] = one
                    continue
                info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
                one["openapi_version"] = doc.get("openapi")
                one["info_version"] = info.get("version")
                one["info_title"] = info.get("title")
                if not dry_run:
                    out_file = (api_dir / f"{spec_id}.json").resolve()
                    try:
                        out_file.relative_to(api_dir.resolve())
                    except ValueError:
                        one["error"] = "path_traversal"
                        results[spec_id] = one
                        continue
                    _atomic_write_json(out_file, doc)
                    one["written"] = str(out_file)
                else:
                    one["written"] = None
                    one["dry_run"] = True
                manifest_entries[spec_id] = {
                    "source_url": url,
                    "fetched_at": now,
                    "http_status": one["http_status"],
                    "etag": one.get("etag"),
                    "last_modified": one.get("last_modified"),
                    "sha256": one["sha256"],
                    "bytes": one["bytes"],
                    "openapi_version": one.get("openapi_version"),
                    "info_version": one.get("info_version"),
                }
                results[spec_id] = one
            except httpx.HTTPError as e:
                one["error"] = f"http_error:{e!s}"
                results[spec_id] = one
            except json.JSONDecodeError as e:
                one["error"] = f"json_decode:{e!s}"
                results[spec_id] = one

    skipped = sum(1 for v in results.values() if v.get("status") == "skipped_unchanged")
    summary = {
        "dry_run": dry_run,
        "if_changed": if_changed,
        "fetched_at": now,
        "api_dir": str(api_dir.resolve()),
        "results": results,
        "ok_count": sum(1 for v in results.values() if "error" not in v and v.get("status") != "skipped_unchanged"),
        "error_count": sum(1 for v in results.values() if "error" in v),
        "skipped_unchanged_count": skipped,
    }

    if not dry_run and manifest_entries:
        full = load_manifest(api_dir)
        full["updated_at"] = now
        ent = dict(full.get("entries") or {})
        ent.update(manifest_entries)
        full["entries"] = ent
        _atomic_write_json(manifest_path(api_dir), full)

    return summary
