"""JSON serialization for MCP tools with explicit truncation metadata."""

from __future__ import annotations

import json
from typing import Any

_DEFAULT_HINT = (
    "Narrow your query, reduce limit, or use get_operation_details / resolve_schema_ref "
    "for a single operation or schema."
)


def truncate_json_response(obj: Any, max_chars: int, *, hint: str | None = None) -> str:
    """
    Serialize obj to indented JSON. If the result exceeds max_chars, return a JSON object
    with truncated=true, a short preview of the original serialization, and a stable hint.
    """
    text = json.dumps(obj, indent=2)
    if len(text) <= max_chars:
        return text

    h = hint or _DEFAULT_HINT
    preview_budget = max_chars - 500
    if preview_budget < 120:
        preview_budget = max(80, max_chars // 3)

    while preview_budget >= 40:
        preview = text[:preview_budget]
        wrapper: dict[str, Any] = {
            "truncated": True,
            "max_chars": max_chars,
            "hint": h,
            "preview": preview,
        }
        out = json.dumps(wrapper, indent=2)
        if len(out) <= max_chars:
            return out
        preview_budget = preview_budget * 2 // 3

    return json.dumps(
        {
            "truncated": True,
            "max_chars": max_chars,
            "hint": h,
            "preview": text[:40],
        },
        indent=2,
    )
