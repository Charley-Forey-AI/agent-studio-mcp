"""Load optional repository `.env` files into the process environment (sync CLIs only).

Does not override variables already set in the environment. No external dependency.
"""

from __future__ import annotations

import os
from pathlib import Path

from trimble_agentic_docs_mcp.store import get_repository_root


def load_optional_repo_env() -> None:
    """Load `<repo>/.env` then `<repo>/trimble-agentic-docs-mcp/.env` if they exist."""
    root = get_repository_root()
    for path in (
        root / ".env",
        root / "trimble-agentic-docs-mcp" / ".env",
    ):
        if path.is_file():
            _parse_dotenv_file(path)


def _parse_dotenv_file(path: Path) -> None:
    raw = path.read_text(encoding="utf-8-sig")
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if key in os.environ:
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        os.environ[key] = val
