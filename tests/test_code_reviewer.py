"""Tests for the code_reviewer workflow and read_file tool."""
from __future__ import annotations

from pathlib import Path

import pytest

from waypoint.builtin_tools import make_read_file_tool
from waypoint.workflows import BUILTIN_WORKFLOWS


def test_read_file_allowed(tmp_path):
    """Tool returns file contents when path is inside an allowed directory."""
    sample = tmp_path / "sample.py"
    sample.write_text("print('hello')", encoding="utf-8")

    tool = make_read_file_tool(allowed_dirs=[tmp_path])
    result = tool.fn(file_path=str(sample))

    assert "print('hello')" in result


def test_read_file_blocked(tmp_path):
    """Tool returns an error string (not raises) for paths outside allowed dirs."""
    tool = make_read_file_tool(allowed_dirs=[tmp_path])
    result = tool.fn(file_path="/etc/passwd")

    assert "not allowed" in result.lower()


def test_code_reviewer_in_registry():
    """code_reviewer is registered with the correct structure."""
    assert "code_reviewer" in BUILTIN_WORKFLOWS

    workflow = BUILTIN_WORKFLOWS["code_reviewer"]
    assert workflow.entry_point == "reader"
    assert set(workflow.agents.keys()) == {"reader", "critic", "summarizer"}
