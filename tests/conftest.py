from pathlib import Path

import pytest

from hoyo.config import Settings
from hoyo.manager import Manager
from hoyo.sources import StubSource


@pytest.fixture(autouse=True)
def _isolate_hoyo_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("hoyo-env")
    monkeypatch.setenv("HOYO_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("HOYO_CONFIG", str(root / "config.json"))
    # Language tests must not rewrite a real 原神 GENERAL_DATA value.
    monkeypatch.setattr("hoyo.manager._apply_registry_keys", lambda *_a, **_k: [])


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def manager(data_dir: Path) -> Manager:
    return Manager(Settings(data_dir=data_dir), source=StubSource())


@pytest.fixture
def fake_game(tmp_path: Path) -> Path:
    root = tmp_path / "game"
    (root / "GenshinImpact_Data").mkdir(parents=True)
    (root / "YuanShen.exe").write_bytes(b"exe-v1")
    (root / "GenshinImpact_Data" / "shared.dat").write_bytes(b"shared-bytes")
    (root / "GenshinImpact_Data" / "unique_a.dat").write_bytes(b"only-in-a")
    return root
