from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.cli.doctor import claude_login_check

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "variable",
    [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    ],
)
def test_a_login_variable_is_a_login(tmp_path: Path, variable: str):
    check = claude_login_check({variable: "1"}, tmp_path, "win32")
    assert check.ok
    assert variable in check.detail


def test_the_credentials_file_is_a_login(tmp_path: Path):
    credentials = tmp_path / ".claude" / ".credentials.json"
    credentials.parent.mkdir()
    credentials.write_text("{}", encoding="utf-8")
    check = claude_login_check({}, tmp_path, "win32")
    assert check.ok
    assert check.detail == str(credentials)


def test_claude_config_dir_moves_the_credentials(tmp_path: Path):
    config = tmp_path / "cfg"
    config.mkdir()
    (config / ".credentials.json").write_text("{}", encoding="utf-8")
    check = claude_login_check({"CLAUDE_CONFIG_DIR": str(config)}, tmp_path, "linux")
    assert check.ok


def test_no_login(tmp_path: Path):
    check = claude_login_check({}, tmp_path, "linux")
    assert not check.ok
    assert "/login" in check.detail


def test_macos_keychain_is_not_inspected(tmp_path: Path):
    assert claude_login_check({}, tmp_path, "darwin").ok
