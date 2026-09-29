from pathlib import Path

import pytest
from typer.testing import CliRunner

from hoyo.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "install" in result.stdout
    assert "switch" in result.stdout
    assert "launch" in result.stdout
    assert "open" in result.stdout
    assert "set" in result.stdout
    assert "games" in result.stdout


def test_install_switch_status_from_dir(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir()
    (game / "YuanShen.exe").write_bytes(b"exe")
    data_dir = tmp_path / "data"

    r = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "install", "hk4e", "5.3.0", "--from-dir", str(game)],
    )
    assert r.exit_code == 0, r.output
    assert "已安装" in r.output
    assert "游戏目录" in r.output
    assert "YuanShen.exe" in r.output
    assert (data_dir / "hk4e" / "versions" / "current" / "YuanShen.exe").is_file()
    assert not (data_dir / "hk4e" / "versions" / "5.3.0").exists()

    r = runner.invoke(app, ["--data-dir", str(data_dir), "switch", "hk4e", "5.3.0"])
    assert r.exit_code == 0, r.output
    assert "已切换到" in r.output

    r = runner.invoke(app, ["--data-dir", str(data_dir), "status", "hk4e"])
    assert r.exit_code == 0, r.output
    assert "5.3.0" in r.output


def test_open_without_current_version(tmp_path: Path) -> None:
    r = runner.invoke(app, ["--data-dir", str(tmp_path / "data"), "open", "hk4e"])
    assert r.exit_code == 1
    assert "尚未切换" in r.output


def test_open_current_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    game = tmp_path / "game"
    game.mkdir()
    (game / "YuanShen.exe").write_bytes(b"exe")
    data_dir = tmp_path / "data"
    r = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "install", "hk4e", "5.3.0", "--from-dir", str(game)],
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["--data-dir", str(data_dir), "switch", "hk4e", "5.3.0"])
    assert r.exit_code == 0, r.output

    opened: list[Path] = []
    monkeypatch.setattr("hoyo.cli.open_folder", lambda path: opened.append(path))
    r = runner.invoke(app, ["--data-dir", str(data_dir), "open", "hk4e"])
    assert r.exit_code == 0, r.output
    assert "已打开" in r.output
    assert "5.3.0" in r.output
    assert opened == [data_dir.resolve() / "hk4e" / "versions" / "current"]


def test_install_unknown_version_fails(tmp_path: Path) -> None:
    r = runner.invoke(
        app,
        ["--data-dir", str(tmp_path / "data"), "install", "hk4e", "9.9.9"],
    )
    assert r.exit_code == 1
    assert "没有版本" in r.output or "尚未配置" in r.output or "下载失败" in r.output
