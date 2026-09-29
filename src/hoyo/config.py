from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from hoyo.errors import HoyoError
from hoyo.games import sort_versions
from hoyo.language import is_voice_manifest


def builtin_data_dir() -> Path:
    """Built-in fallback, ignoring the saved user config."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "hoyo"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "hoyo"
    return Path.home() / ".local" / "share" / "hoyo"


def config_path() -> Path:
    """Persistent CLI config (not inside data-dir, so it can point at another disk)."""
    override = os.environ.get("HOYO_CONFIG")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "hoyo" / "config.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "hoyo" / "config.json"
    return Path.home() / ".config" / "hoyo" / "config.json"


def _read_user_config() -> dict:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def saved_data_dir() -> Path | None:
    raw = _read_user_config().get("data_dir")
    if not raw:
        return None
    return Path(str(raw)).expanduser()


def default_data_dir() -> Path:
    saved = saved_data_dir()
    if saved is not None:
        return saved
    return builtin_data_dir()


def save_data_dir(path: Path) -> Path:
    """Remember data-dir in the user config file. Does not move existing files."""
    resolved = path.expanduser().resolve()
    if resolved.exists() and not resolved.is_dir():
        raise HoyoError(f"不能作为数据目录（已存在且不是文件夹）: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    cfg_path = config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_user_config()
    data["data_dir"] = str(resolved)
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOYO_",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Field(default_factory=default_data_dir)


class Paths:
    """On-disk layout. Each game lives in its own subdirectory of the shared root.

    The runnable install is always ``versions/current``. The selected version is
    recorded in ``state.json``, not in the directory name.
    """

    CURRENT_DIR = "current"

    def __init__(self, root: Path, game: str) -> None:
        self.root = root.expanduser().resolve()
        self.game = game
        self.home = self.root / game

    @property
    def blobs(self) -> Path:
        return self.home / "blobs"

    @property
    def manifests(self) -> Path:
        return self.home / "manifests"

    @property
    def versions(self) -> Path:
        return self.home / "versions"

    @property
    def tmp(self) -> Path:
        return self.home / "tmp"

    @property
    def state(self) -> Path:
        return self.home / "state.json"

    @property
    def md5_index(self) -> Path:
        return self.home / "md5"

    @property
    def cache(self) -> Path:
        return self.home / "cache"

    @property
    def worktree(self) -> Path:
        return self.versions / self.CURRENT_DIR

    def manifest_file(self, version: str) -> Path:
        return self.manifests / f"{version}.json"

    def voice_manifest_file(self, version: str, lang: str) -> Path:
        return self.manifests / f"{version}.{lang}.json"

    def installed_version_ids(self) -> list[str]:
        if not self.manifests.is_dir():
            return []
        versions = [
            path.stem
            for path in self.manifests.glob("*.json")
            if not is_voice_manifest(path.name)
        ]
        return sort_versions(versions)

    def ensure(self) -> None:
        for path in (
            self.blobs,
            self.manifests,
            self.versions,
            self.tmp,
            self.md5_index,
            self.cache,
        ):
            path.mkdir(parents=True, exist_ok=True)
