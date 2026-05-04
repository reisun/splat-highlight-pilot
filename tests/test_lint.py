"""lint / format チェックテスト."""

from __future__ import annotations

import subprocess


def test_ruff_check():
    """ruff check がパスすること."""
    result = subprocess.run(
        ["ruff", "check", "."],  # noqa: S603, S607
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ruff check failed:\n{result.stdout}\n{result.stderr}"
    )


def test_ruff_format():
    """ruff format --check がパスすること."""
    result = subprocess.run(
        ["ruff", "format", "--check", "."],  # noqa: S603, S607
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ruff format check failed:\n{result.stdout}\n{result.stderr}"
    )
