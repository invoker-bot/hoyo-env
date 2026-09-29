from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from hoyo.errors import SourceNotConfiguredError
from hoyo.games import Game, pick_executable, require_game
from hoyo.models import Channel, FileEntry, Manifest
from hoyo.store import sha256_file

# Game-written paths: copy on materialize, skip on import when noisy.
WRITABLE_GLOBS = (
    "*.log",
    "*.log.*",
    "config.ini",
    "**/Persistent/audio_lang*",
    "**/ScreenShot/**",
    "**/Screenshot/**",
    "**/dumps/**",
)
IMPORT_IGNORE_GLOBS = (
    "*.log",
    "*.log.*",
    "**/*.tmp",
    "**/ScreenShot/**",
    "**/Screenshot/**",
    "**/dumps/**",
)


def _match(rel: str, globs: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in globs)


@runtime_checkable
class VersionSource(Protocol):
    """Remote or local provider of version manifests and file bytes."""

    name: str

    def list_available(self) -> Sequence[str]:
        """Version ids this source can install."""

    def resolve(self, version: str) -> Manifest:
        """Return the file list for ``version`` (may hit the network)."""

    def fetch(self, entry: FileEntry, dest: Path) -> None:
        """Write ``entry`` bytes to ``dest`` (not yet hashed into the store)."""


class StubSource:
    """Placeholder until a real download source is configured."""

    name = "stub"

    def __init__(self, game: str = "hk4e") -> None:
        self.game_id = game

    def list_available(self) -> Sequence[str]:
        return []

    def resolve(self, version: str) -> Manifest:
        raise SourceNotConfiguredError(
            f"尚未配置 {self.game_id} 版本 {version} 的下载源。\n"
            f"可先用 `hoyo install {self.game_id} {version} --from-dir <游戏目录>` 从本机导入，"
            "或等待接入远程地址后再安装。"
        )

    def fetch(self, entry: FileEntry, dest: Path) -> None:
        raise SourceNotConfiguredError("尚未配置远程下载源，无法拉取文件。")


class LocalDirectorySource:
    """Build a manifest by hashing an existing game install."""

    name = "local"

    def __init__(
        self,
        root: Path,
        version: str,
        channel: Channel = "cn",
        game: Game | str = "hk4e",
    ) -> None:
        self.root = root.expanduser().resolve()
        self.version = version
        self.channel = channel
        self.game = require_game(game)

    def list_available(self) -> Sequence[str]:
        return [self.version]

    def resolve(self, version: str) -> Manifest:
        if version != self.version:
            raise SourceNotConfiguredError(f"本地源仅提供版本 {self.version}")
        if not self.root.is_dir():
            raise SourceNotConfiguredError(f"目录不存在: {self.root}")

        files: list[FileEntry] = []
        found: dict[str, str] = {}
        names = set(self.game.executables)
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if _match(rel, IMPORT_IGNORE_GLOBS):
                continue
            files.append(
                FileEntry(
                    path=rel,
                    sha256=sha256_file(path),
                    size=path.stat().st_size,
                    writable=_match(rel, WRITABLE_GLOBS),
                )
            )
            if path.name in names:
                found.setdefault(path.name, rel)
        return Manifest(
            version=self.version,
            game=self.game.id,
            channel=self.channel,
            executable=pick_executable(found, self.game.executables),
            files=files,
        )

    def fetch(self, entry: FileEntry, dest: Path) -> None:
        src = self.root / entry.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        from hoyo.store import hardlink_or_copy

        hardlink_or_copy(src, dest)


def default_source(game: Game | str | None = None) -> VersionSource:
    from hoyo.catalog import HoyoSource

    return HoyoSource(game=game or "hk4e")
