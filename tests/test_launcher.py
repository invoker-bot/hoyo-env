import os
import subprocess
import sys
from pathlib import Path

import pytest

from hoyo.errors import HoyoError
from hoyo.launcher import open_folder


def test_open_folder_missing(tmp_path: Path) -> None:
    with pytest.raises(HoyoError, match="目录不存在"):
        open_folder(tmp_path / "missing")


def test_open_folder_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Path] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", lambda path: called.append(Path(path)), raising=False)
    open_folder(tmp_path)
    assert called == [tmp_path.resolve()]


def test_open_folder_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[list[str]] = []
    monkeypatch.setattr(sys, "platform", "darwin")

    def fake_run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        recorded.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    open_folder(tmp_path)
    assert recorded == [["open", str(tmp_path.resolve())]]


def test_open_folder_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[list[str]] = []
    monkeypatch.setattr(sys, "platform", "linux")

    def fake_run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        recorded.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    open_folder(tmp_path)
    assert recorded == [["xdg-open", str(tmp_path.resolve())]]


def test_open_folder_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def fake_run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(HoyoError, match="无法在文件管理器中打开"):
        open_folder(tmp_path)
