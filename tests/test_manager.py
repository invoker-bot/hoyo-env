from pathlib import Path
from types import SimpleNamespace

import pytest

from hoyo.errors import InsufficientSpaceError
from hoyo.manager import Manager, format_bytes
from hoyo.store import same_inode


def _variant(src: Path, dest: Path, unique: bytes) -> Path:
    dest.mkdir(parents=True)
    (dest / "YuanShen.exe").write_bytes((src / "YuanShen.exe").read_bytes())
    data = dest / "GenshinImpact_Data"
    data.mkdir()
    (data / "shared.dat").write_bytes((src / "GenshinImpact_Data" / "shared.dat").read_bytes())
    (data / "unique.dat").write_bytes(unique)
    return dest


def test_format_bytes() -> None:
    assert format_bytes(100) == "100 B"
    assert format_bytes(512 * 1024 * 1024) == "512 MB"


def test_install_rejects_insufficient_space(
    manager: Manager, fake_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hoyo.manager.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=1024, used=1023, free=100),
    )
    with pytest.raises(InsufficientSpaceError, match="空间不足"):
        manager.install("5.0", from_dir=fake_game)


def test_install_from_dir_shares_blobs(manager: Manager, fake_game: Path, tmp_path: Path) -> None:
    v2 = _variant(fake_game, tmp_path / "game-v2", b"only-in-b")
    manager.install("5.0", from_dir=fake_game)
    manager.install("5.1", from_dir=v2)

    assert manager.installed_versions() == ["5.0", "5.1"]
    # shared.dat + YuanShen.exe + two uniques = 3 unique blobs? 
    # v1: exe, shared, unique_a  (fake_game has unique_a.dat)
    # v2: exe, shared, unique.dat
    # unique files: exe, shared, unique_a, unique = 4 blobs
    assert manager.blob_count() == 4


def test_switch_skips_unchanged_hardlinks(
    manager: Manager, fake_game: Path, tmp_path: Path
) -> None:
    v2 = _variant(fake_game, tmp_path / "game-v2", b"only-in-b")
    manager.install("5.0", from_dir=fake_game)
    manager.install("5.1", from_dir=v2)

    work = manager.paths.worktree
    shared = work / "GenshinImpact_Data" / "shared.dat"
    m0 = manager.load_manifest("5.0")
    shared_hash = m0.by_path()["GenshinImpact_Data/shared.dat"].sha256
    assert same_inode(shared, manager.store.blob_path(shared_hash))
    assert (work / "GenshinImpact_Data" / "unique.dat").is_file()

    again = manager.switch("5.0")
    # exe + shared stay linked; unique_a comes back, unique.dat is removed.
    assert again.skipped == 2
    assert again.linked == 1
    assert (work / "GenshinImpact_Data" / "unique_a.dat").is_file()
    assert not (work / "GenshinImpact_Data" / "unique.dat").exists()
    assert manager.load_state().current_version == "5.0"
    assert work == manager.paths.versions / "current"


def test_voice_manifest_merged_on_switch(manager: Manager, fake_game: Path) -> None:
    from hoyo.models import FileEntry, Manifest
    from hoyo.store import hash_file

    manager.install("5.0", from_dir=fake_game)
    manager.switch("5.0")
    extra = fake_game / "YuanShen_Data" / "StreamingAssets" / "AudioAssets" / "Japanese"
    extra.mkdir(parents=True)
    pack = extra / "1001.pck"
    pack.write_bytes(b"jp-voice")
    md5, sha = hash_file(pack)
    manager.store.put_file(pack, expected_md5=md5)
    voice = Manifest(
        version="5.0",
        files=[
            FileEntry(
                path="YuanShen_Data/StreamingAssets/AudioAssets/Japanese/1001.pck",
                sha256=sha,
                md5=md5,
                size=8,
            )
        ],
    )
    manager.paths.voice_manifest_file("5.0", "ja").write_text(
        voice.model_dump_json(indent=2), encoding="utf-8"
    )
    assert manager.installed_versions() == ["5.0"]
    manager.set_language("ja")
    work = manager.paths.worktree
    packed = work / "YuanShen_Data/StreamingAssets/AudioAssets/Japanese/1001.pck"
    assert packed.read_bytes() == b"jp-voice"
    marker = work / "YuanShen_Data" / "Persistent" / "audio_lang_14"
    assert marker.read_text(encoding="utf-8").strip() == "Japanese"


def test_switch_sets_current(manager: Manager, fake_game: Path) -> None:
    manager.install("5.0", from_dir=fake_game)
    manager.switch("5.0")
    version, worktree, manifest = manager.current_worktree()
    assert version == "5.0"
    assert (worktree / "YuanShen.exe").is_file()
    assert manifest.executable == "YuanShen.exe"
