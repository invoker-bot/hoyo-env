from pathlib import Path

from typer.testing import CliRunner

from hoyo.cli import app
from hoyo.config import Settings, save_data_dir

runner = CliRunner()


def test_save_and_load_data_dir(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("HOYO_CONFIG", str(cfg))
    monkeypatch.delenv("HOYO_DATA_DIR", raising=False)
    target = tmp_path / "games" / "hoyo-data"
    saved = save_data_dir(target)
    assert saved == target.resolve()
    assert cfg.is_file()
    settings = Settings()
    assert settings.data_dir == target.resolve()


def test_env_overrides_saved_data_dir(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("HOYO_CONFIG", str(cfg))
    save_data_dir(tmp_path / "from-config")
    override = tmp_path / "from-env"
    monkeypatch.setenv("HOYO_DATA_DIR", str(override))
    settings = Settings()
    assert settings.data_dir == override


def test_cli_set_data_dir(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("HOYO_CONFIG", str(cfg))
    monkeypatch.delenv("HOYO_DATA_DIR", raising=False)
    target = tmp_path / "disk" / "hoyo-data"

    r = runner.invoke(app, ["set", "data-dir", str(target)])
    assert r.exit_code == 0, r.output
    assert "不会自动搬迁" in r.output

    from hoyo.config import saved_data_dir

    assert saved_data_dir() == target.resolve()

    r = runner.invoke(app, ["set", "data-dir"])
    assert r.exit_code == 0, r.output
    assert "disk" in r.output and "hoyo-data" in r.output

    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0, r.output
    assert "disk" in r.output
