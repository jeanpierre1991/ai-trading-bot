"""M12.1 KillSwitch unit tests (no network)."""

from __future__ import annotations

from pathlib import Path

from runtime.kill_switch import FileEnvKillSwitch


def test_missing_file_and_unset_env_not_engaged(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    switch = FileEnvKillSwitch(tmp_path / "KILL")
    assert switch.is_engaged() is False


def test_existing_file_engages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    kill_path = tmp_path / "KILL"
    kill_path.write_text("", encoding="utf-8")
    switch = FileEnvKillSwitch(kill_path)
    assert switch.is_engaged() is True


def test_truthy_env_engages_without_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PAPER_OPERATOR_KILL", "true")
    switch = FileEnvKillSwitch(tmp_path / "missing-kill-file")
    assert switch.is_engaged() is True


def test_falsy_env_does_not_engage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PAPER_OPERATOR_KILL", "0")
    switch = FileEnvKillSwitch(tmp_path / "missing")
    assert switch.is_engaged() is False


def test_custom_env_var_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CUSTOM_KILL", "1")
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    switch = FileEnvKillSwitch(tmp_path / "missing", env_var="CUSTOM_KILL")
    assert switch.is_engaged() is True


def test_none_path_env_only(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_OPERATOR_KILL", "yes")
    switch = FileEnvKillSwitch(None)
    assert switch.is_engaged() is True
    monkeypatch.delenv("PAPER_OPERATOR_KILL", raising=False)
    assert switch.is_engaged() is False
