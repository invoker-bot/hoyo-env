from pathlib import Path

import pytest

from hoyo.channel import write_config_ini
from hoyo.config import Settings
from hoyo.errors import LaunchError
from hoyo.launcher import launch
from hoyo.manager import Manager
from hoyo.sources import StubSource


def test_write_config_ini(tmp_path: Path) -> None:
    path = write_config_ini(tmp_path, version="7.0.0", channel="cn")
    text = path.read_text(encoding="utf-8")
    assert "channel=1" in text
    assert "game_version=7.0.0" in text
    assert "hk4e_cn" in text


def test_install_writes_config_ini(tmp_path: Path, fake_game: Path) -> None:
    mgr = Manager(Settings(data_dir=tmp_path / "data"), source=StubSource())
    mgr.install("7.0.0", from_dir=fake_game)
    ini = mgr.paths.worktree / "config.ini"
    assert ini.is_file()
    assert "game_biz=hk4e_cn" in ini.read_text(encoding="utf-8")


def test_launch_without_channel_sdk(tmp_path: Path) -> None:
    exe = tmp_path / "YuanShen.exe"
    exe.write_bytes(b"exe")
    with pytest.raises(LaunchError, match="play_pc_sdk.dll"):
        launch(tmp_path)
