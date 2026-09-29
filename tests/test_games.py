from pathlib import Path

from typer.testing import CliRunner

from hoyo.cli import app
from hoyo.config import Settings
from hoyo.games import require_game, sort_versions
from hoyo.manager import Manager
from hoyo.sources import StubSource

runner = CliRunner()


def test_aliases_and_version_sort() -> None:
    assert require_game("Genshin").id == "hk4e"
    assert require_game("hk4e_cn").id == "hk4e"
    assert require_game("star rail").id == "hkrpg"
    assert require_game("ZZZ").id == "nap"
    assert require_game("honkai3").id == "bh3"
    assert sort_versions(["1.10.0", "1.2.0", "1.9.0"]) == ["1.2.0", "1.9.0", "1.10.0"]


def test_games_do_not_share_a_version_tree(tmp_path: Path) -> None:
    data = tmp_path / "data"
    gi = tmp_path / "gi"
    gi.mkdir()
    (gi / "YuanShen.exe").write_bytes(b"gi")
    zzz = tmp_path / "zzz"
    zzz.mkdir()
    (zzz / "ZenlessZoneZero.exe").write_bytes(b"zzz")
    star = tmp_path / "star"
    star.mkdir()
    (star / "StarRail.exe").write_bytes(b"sr")

    hk = Manager(Settings(data_dir=data), game="hk4e", source=StubSource())
    nap = Manager(Settings(data_dir=data), game="zzz", source=StubSource())
    hsr = Manager(Settings(data_dir=data), game="hkrpg", source=StubSource())
    hk.install("1.0.0", from_dir=gi)
    nap.install("1.0.0", from_dir=zzz, channel="os")
    hsr.install("1.0.0", from_dir=star)

    assert hk.paths.home != nap.paths.home
    assert (hk.paths.worktree / "YuanShen.exe").is_file()
    assert (nap.paths.worktree / "ZenlessZoneZero.exe").is_file()
    assert (hsr.paths.worktree / "StarRail.exe").is_file()
    assert hk.paths.worktree.name == "current"
    nap_ini = (nap.paths.worktree / "config.ini").read_text(encoding="utf-8")
    assert "game_biz=nap_global" in nap_ini
    assert "cps=hyp_hoyoverse" in nap_ini
    hsr_ini = (hsr.paths.worktree / "config.ini").read_text(encoding="utf-8")
    assert "game_biz=hkrpg_cn" in hsr_ini
    assert hsr.load_state().voice_language is None
    assert not list(nap.paths.worktree.rglob("audio_lang*"))
    assert hk.installed_versions() == ["1.0.0"]
    assert nap.installed_versions() == ["1.0.0"]


def test_cli_alias_list_and_unknown_game(tmp_path: Path) -> None:
    data = tmp_path / "data"
    gi = tmp_path / "gi"
    gi.mkdir()
    (gi / "YuanShen.exe").write_bytes(b"gi")
    empty = tmp_path / "empty"
    empty.mkdir()

    r = runner.invoke(app, ["games"])
    assert r.exit_code == 0, r.output
    assert "hk4e" in r.output
    assert "绝区零" in r.output

    r = runner.invoke(
        app,
        ["--data-dir", str(data), "install", "genshin", "5.0.0", "--from-dir", str(gi)],
    )
    assert r.exit_code == 0, r.output
    assert (data / "hk4e" / "versions" / "current" / "YuanShen.exe").is_file()

    r = runner.invoke(app, ["--data-dir", str(data), "list"])
    assert r.exit_code == 0, r.output
    assert "hk4e" in r.output
    assert "5.0.0" in r.output

    r = runner.invoke(app, ["--data-dir", str(data), "install", "nope", "1.0.0"])
    assert r.exit_code == 1
    assert "未知游戏" in r.output

    r = runner.invoke(app, ["--data-dir", str(data), "set", "language", "ja"])
    assert r.exit_code == 1
    assert "未知游戏" in r.output

    r = runner.invoke(
        app,
        [
            "--data-dir",
            str(data),
            "install",
            "bh3",
            "1.0.0",
            "--from-dir",
            str(empty),
            "--channel",
            "jp",
        ],
    )
    assert r.exit_code == 1
    assert "cn" in r.output
