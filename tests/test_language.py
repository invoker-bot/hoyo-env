from pathlib import Path

import pytest
from typer.testing import CliRunner

from hoyo.cli import app
from hoyo.config import Settings
from hoyo.errors import HoyoError
from hoyo.language import apply_to_worktree, parse_voice_lang
from hoyo.manager import Manager
from hoyo.sources import StubSource

runner = CliRunner()


def test_parse_aliases() -> None:
    assert parse_voice_lang("zh").folder == "Chinese"
    assert parse_voice_lang("CN").code == "zh"
    assert parse_voice_lang("en-us").voice_id == 1
    assert parse_voice_lang("jp").code == "ja"
    assert parse_voice_lang("kr").folder == "Korean"


def test_parse_rejects_unknown() -> None:
    with pytest.raises(HoyoError, match="不支持的语言"):
        parse_voice_lang("fr")


def test_apply_writes_audio_lang(tmp_path: Path) -> None:
    data = tmp_path / "YuanShen_Data" / "Persistent"
    data.mkdir(parents=True)
    (data / "audio_lang_14").write_text("English(US)\n", encoding="utf-8")
    written = apply_to_worktree(tmp_path, parse_voice_lang("ja"))
    assert written
    assert (data / "audio_lang_14").read_text(encoding="utf-8").strip() == "Japanese"


def test_set_language_persists_and_applies(tmp_path: Path) -> None:
    game = tmp_path / "game"
    (game / "YuanShen_Data" / "Persistent").mkdir(parents=True)
    (game / "YuanShen.exe").write_bytes(b"exe")
    (game / "YuanShen_Data" / "dummy.bin").write_bytes(b"data")
    mgr = Manager(Settings(data_dir=tmp_path / "data"), source=StubSource())
    mgr.install("7.0.0", from_dir=game)
    mgr.switch("7.0.0")
    lang = mgr.set_language("zh")
    assert lang.code == "zh"
    assert mgr.load_state().voice_language == "zh"
    work = mgr.paths.worktree
    marker = work / "YuanShen_Data" / "Persistent" / "audio_lang_14"
    assert marker.read_text(encoding="utf-8").strip() == "Chinese"

    mgr.set_language("en")
    mgr.switch("7.0.0")
    assert marker.read_text(encoding="utf-8").strip() == "English(US)"


def test_cli_set_language(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    r = runner.invoke(app, ["--data-dir", str(data_dir), "set", "language", "hk4e"])
    assert r.exit_code == 0, r.output
    assert "尚未设置" in r.output

    r = runner.invoke(app, ["--data-dir", str(data_dir), "set", "language", "hk4e", "ja"])
    assert r.exit_code == 0, r.output
    assert "ja" in r.output

    r = runner.invoke(app, ["--data-dir", str(data_dir), "set", "language", "hk4e"])
    assert r.exit_code == 0, r.output
    assert "ja" in r.output

    r = runner.invoke(app, ["--data-dir", str(data_dir), "set", "language", "hk4e", "fr"])
    assert r.exit_code == 1
    assert "不支持的语言" in r.output
