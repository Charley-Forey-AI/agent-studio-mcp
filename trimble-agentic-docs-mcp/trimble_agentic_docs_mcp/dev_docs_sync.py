"""
Fetch developer-portal /docs/* pages, extract main text (not headless browsing).

Uses HTTPS GET + trafilatura for main-body extraction. JS-rendered sites may return
shell HTML; in that case prefer an official static export or authenticated HTML snapshot pipeline.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura

from trimble_agentic_docs_mcp.http_auth_retry import request_with_optional_anonymous_retry
from trimble_agentic_docs_mcp.upstream_sync import _normalize_etag

_DEV_MANIFEST = "manifest.json"
_PAGES_DIR = "pages"


def _default_dev_docs_cache_dir() -> Path:
    env = os.environ.get("TRIMBLE_AGENTIC_DEV_DOCS_CACHE_DIR")
    if env:
        return Path(env).expanduser().resolve()
    from trimble_agentic_docs_mcp.store import get_repository_root

    return (get_repository_root() / "docs" / "cached" / "dev-portal").resolve()


def parse_docs_section_urls(text: str) -> list[str]:
    """Return HTTP(S) URLs listed under the first '## Docs' block in urls.txt."""
    lines = text.splitlines()
    in_docs = False
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            in_docs = s.lower().startswith("## docs")
            continue
        if in_docs and s.startswith("http"):
            out.append(s.split()[0])
    return out


def page_id_from_docs_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if path.lower().startswith("docs/"):
        path = path[5:]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-").lower()
    return slug or "index"


def manifest_path(cache_dir: Path) -> Path:
    return cache_dir / _DEV_MANIFEST


def pages_dir(cache_dir: Path) -> Path:
    return cache_dir / _PAGES_DIR


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


def load_dev_docs_manifest(cache_dir: Path) -> dict[str, Any]:
    p = manifest_path(cache_dir)
    if not p.is_file():
        return {"pages": {}, "updated_at": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"pages": {}, "updated_at": None, "error": "invalid_manifest_json"}


def run_dev_docs_sync(
    *,
    urls_file: Path | None,
    cache_dir: Path | None = None,
    dry_run: bool = False,
    timeout_s: float = 90.0,
    if_changed: bool = False,
) -> dict[str, Any]:
    """
    GET each /docs URL, extract readable text with trafilatura, write pages/*.json + manifest.

    Does not execute JavaScript (no Playwright). SPAs may yield thin extracts until
    an official export or authenticated snapshot pipeline is used.
    When if_changed is True (and not dry_run), uses HEAD + ETag to skip GET when unchanged.
    """
    base = cache_dir or _default_dev_docs_cache_dir()
    base.mkdir(parents=True, exist_ok=True)
    pdir = pages_dir(base)
    pdir.mkdir(parents=True, exist_ok=True)

    if not urls_file or not urls_file.is_file():
        return {"error": "urls_file_missing", "path": str(urls_file) if urls_file else None}

    urls = parse_docs_section_urls(urls_file.read_text(encoding="utf-8"))
    if not urls:
        return {"error": "no_docs_urls", "hint": "Add URLs under ## Docs in urls.txt"}

    ua = os.environ.get("TRIMBLE_AGENTIC_SYNC_USER_AGENT", "trimble-agentic-docs-mcp/0.1 (dev-docs sync)")
    headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "User-Agent": ua}
    token = (os.environ.get("TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    results: dict[str, Any] = {}
    manifest_pages: dict[str, Any] = {}
    prev_pages: dict[str, Any] = {}
    if if_changed and not dry_run:
        prev_pages = dict(load_dev_docs_manifest(base).get("pages") or {})

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    with httpx.Client(timeout=timeout_s, follow_redirects=True, limits=limits) as client:
        for url in urls:
            pid = page_id_from_docs_url(url)
            one: dict[str, Any] = {"page_id": pid, "url": url}
            try:
                if if_changed and not dry_run:
                    prev_row = prev_pages.get(pid) if isinstance(prev_pages.get(pid), dict) else {}
                    prev_etag = _normalize_etag(prev_row.get("etag"))
                    try:
                        hr, head_meta = request_with_optional_anonymous_retry(client, "HEAD", url, headers)
                        one.update(head_meta)
                        one["head_status"] = hr.status_code
                        if hr.status_code == 200:
                            remote_etag = _normalize_etag(hr.headers.get("etag"))
                            one["etag_head"] = hr.headers.get("etag")
                            if prev_etag and remote_etag and prev_etag == remote_etag:
                                one["status"] = "skipped_unchanged"
                                one["etag"] = hr.headers.get("etag")
                                results[pid] = one
                                continue
                    except httpx.HTTPError:
                        pass

                r, get_meta = request_with_optional_anonymous_retry(client, "GET", url, headers)
                one.update(get_meta)
                one["http_status"] = r.status_code
                one["etag"] = r.headers.get("etag")
                body = r.text or ""
                one["source_chars"] = len(body)
                if r.status_code != 200:
                    one["error"] = f"http_{r.status_code}"
                    results[pid] = one
                    continue
                extracted = trafilatura.extract(
                    body,
                    url=url,
                    output_format="markdown",
                    include_comments=False,
                    include_tables=True,
                )
                if not extracted or not str(extracted).strip():
                    extracted = trafilatura.extract(
                        body,
                        url=url,
                        output_format="txt",
                        include_comments=False,
                        include_tables=True,
                    )
                text = (extracted or "").strip()
                one["extract_chars"] = len(text)
                one["extract_empty"] = len(text) == 0
                meta = trafilatura.extract_metadata(body, default_url=url)
                one["title"] = meta.title if meta and meta.title else None

                page_payload = {
                    "page_id": pid,
                    "url": url,
                    "fetched_at": now,
                    "http_status": r.status_code,
                    "title": one.get("title"),
                    "text_markdown": text,
                }
                if not dry_run:
                    out = (pdir / f"{pid}.json").resolve()
                    try:
                        out.relative_to(pdir.resolve())
                    except ValueError:
                        one["error"] = "bad_page_path"
                        results[pid] = one
                        continue
                    _atomic_write_json(out, page_payload)
                    one["written"] = str(out)
                else:
                    one["written"] = None
                    one["dry_run"] = True

                manifest_pages[pid] = {
                    "url": url,
                    "title": one.get("title"),
                    "fetched_at": now,
                    "http_status": one["http_status"],
                    "etag": one.get("etag"),
                    "file": f"{_PAGES_DIR}/{pid}.json",
                    "extract_chars": one.get("extract_chars"),
                    "extract_empty": one.get("extract_empty"),
                }
                results[pid] = one
            except httpx.HTTPError as e:
                one["error"] = f"http_error:{e!s}"
                results[pid] = one
            except Exception as e:  # noqa: BLE001
                one["error"] = f"unexpected:{e!s}"
                results[pid] = one

    ok = sum(
        1
        for v in results.values()
        if "error" not in v and not v.get("extract_empty") and v.get("status") != "skipped_unchanged"
    )
    empty_extract = sum(1 for v in results.values() if "error" not in v and v.get("extract_empty"))
    skipped = sum(1 for v in results.values() if v.get("status") == "skipped_unchanged")
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "if_changed": if_changed,
        "cache_dir": str(base),
        "fetched_at": now,
        "results": results,
        "ok_count": sum(
            1 for v in results.values() if "error" not in v and v.get("status") != "skipped_unchanged"
        ),
        "error_count": sum(1 for v in results.values() if "error" in v),
        "nonempty_extract_count": ok,
        "empty_extract_count": empty_extract,
        "skipped_unchanged_count": skipped,
    }

    if not dry_run and manifest_pages:
        merged = dict(prev_pages)
        merged.update(manifest_pages)
        full = {"updated_at": now, "pages": merged}
        _atomic_write_json(manifest_path(base), full)

    return summary


def read_dev_docs_page(cache_dir: Path, page_id: str) -> dict[str, Any] | None:
    p = (pages_dir(cache_dir) / f"{page_id}.json").resolve()
    if not p.is_file():
        return None
    try:
        p.relative_to(pages_dir(cache_dir).resolve())
    except ValueError:
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def search_dev_docs(cache_dir: Path, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    man = load_dev_docs_manifest(cache_dir)
    pids = list((man.get("pages") or {}).keys())
    if not pids and pages_dir(cache_dir).is_dir():
        pids = sorted(p.stem for p in pages_dir(cache_dir).glob("*.json") if p.is_file())
    hits: list[dict[str, Any]] = []
    for pid in pids:
        doc = read_dev_docs_page(cache_dir, pid)
        if not doc:
            continue
        blob = f"{pid} {doc.get('url','')} {doc.get('title','')} {doc.get('text_markdown','')}".lower()
        if q in blob:
            text = doc.get("text_markdown") or ""
            idx = text.lower().find(q)
            snippet = text[max(0, idx - 80) : idx + 200] if idx >= 0 else (text[:280] + ("…" if len(text) > 280 else ""))
            hits.append(
                {
                    "page_id": pid,
                    "url": doc.get("url"),
                    "title": doc.get("title"),
                    "snippet": snippet,
                }
            )
    return hits[:limit]


def list_dev_docs_pages(cache_dir: Path) -> list[dict[str, Any]]:
    """Inventory from manifest, or from pages/*.json if manifest is missing."""
    man = load_dev_docs_manifest(cache_dir)
    rows: list[dict[str, Any]] = []
    for pid, meta in sorted((man.get("pages") or {}).items(), key=lambda x: x[0]):
        rows.append({"page_id": pid, **meta})
    if rows:
        return rows
    pdir = pages_dir(cache_dir)
    if not pdir.is_dir():
        return []
    for p in sorted(pdir.glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rows.append(
            {
                "page_id": p.stem,
                "url": doc.get("url"),
                "title": doc.get("title"),
                "fetched_at": doc.get("fetched_at"),
                "file": f"{_PAGES_DIR}/{p.name}",
            }
        )
    return rows
