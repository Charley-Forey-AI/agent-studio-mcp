"""Tests for optional repository .env loading."""

from __future__ import annotations

import os

import pytest


def test_load_optional_repo_env_does_not_override_existing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(
        "trimble_agentic_docs_mcp.repo_env.get_repository_root",
        lambda: tmp_path,
    )
    (tmp_path / ".env").write_text(
        'ALREADY_SET=from_file\nONLY_IN_FILE=ok\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ALREADY_SET", "from_os")

    from trimble_agentic_docs_mcp.repo_env import load_optional_repo_env

    load_optional_repo_env()
    assert os.environ["ALREADY_SET"] == "from_os"
    assert os.environ["ONLY_IN_FILE"] == "ok"


def test_load_optional_repo_env_strips_quotes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(
        "trimble_agentic_docs_mcp.repo_env.get_repository_root",
        lambda: tmp_path,
    )
    (tmp_path / ".env").write_text('QUOTED="hello world"\n', encoding="utf-8")

    from trimble_agentic_docs_mcp.repo_env import load_optional_repo_env

    load_optional_repo_env()
    assert os.environ["QUOTED"] == "hello world"
