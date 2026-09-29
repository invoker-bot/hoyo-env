from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import httpx
from pydantic import ValidationError

from hoyo.catalog import HoyoSource
from hoyo.channel import ensure_channel_sdk, write_config_ini
from hoyo.config import Paths, Settings
from hoyo.download import TIMEOUT, partial_direct_bytes
from hoyo.errors import HoyoError, InsufficientSpaceError, VersionNotInstalledError
from hoyo.games import Game, all_games, require_game
from hoyo.language import VoiceLang, parse_voice_lang
from hoyo.language import apply_to_registry as _apply_registry_keys
from hoyo.language import apply_to_worktree as _apply_worktree_lang
from hoyo.models import Channel, FileEntry, Manifest, MaterializeStats, State
from hoyo.progress import DownloadReporter, NullReporter
from hoyo.sources import LocalDirectorySource, VersionSource
from hoyo.store import BlobStore

DISK_MARGIN = 512 * 1024 * 1024
FILE_CONCURRENCY = int(os.environ.get("HOYO_FILE_CONCURRENCY", "4"))
HTTP_CONCURRENCY = int(os.environ.get("HOYO_HTTP_CONCURRENCY", "8"))


def format_bytes(n: int) -> str:
    n = max(0, int(n))
    for unit, step in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= step:
            value = n / step
            text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
            return f"{text} {unit}"
    return f"{n} B"


def summarize_installed(root: Path) -> list[tuple[Game, list[str], str | None]]:
    """Installed versions per game. Does not create directories."""
    root = root.expanduser().resolve()
    rows: list[tuple[Game, list[str], str | None]] = []
    if not root.exists():
        return rows
    for game in all_games():
        paths = Paths(root, game.id)
        versions = paths.installed_version_ids()
        current: str | None = None
        if paths.state.is_file():
            try:
                current = State.model_validate_json(
                    paths.state.read_text(encoding="utf-8")
                ).current_version
            except (OSError, ValidationError, UnicodeError):
                current = None
        if not versions and not current:
            continue
        rows.append((game, versions, current))
    return rows


class Manager:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        game: Game | str = "hk4e",
        source: VersionSource | None = None,
        progress: Callable[[str], None] | None = None,
        downloads: DownloadReporter | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.game = require_game(game)
        self.paths = Paths(self.settings.data_dir, self.game.id)
        self.paths.ensure()
        self.store = BlobStore(self.paths.blobs, md5_index=self.paths.md5_index)
        self.source = source or HoyoSource(game=self.game, cache_dir=self.paths.cache)
        self._downloads: DownloadReporter = downloads or NullReporter()
        if progress is not None:
            self._progress = progress
        else:
            self._progress = self._downloads.log

    def load_state(self) -> State:
        if not self.paths.state.is_file():
            return State()
        return State.model_validate_json(self.paths.state.read_text(encoding="utf-8"))

    def save_state(self, state: State) -> None:
        self.paths.state.write_text(state.model_dump_json(indent=2), encoding="utf-8")

    def load_manifest(self, version: str) -> Manifest:
        path = self.paths.manifest_file(version)
        if not path.is_file():
            raise VersionNotInstalledError(f"版本 {version} 尚未安装")
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))

    def save_manifest(self, manifest: Manifest) -> None:
        path = self.paths.manifest_file(manifest.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    def installed_versions(self) -> list[str]:
        return self.paths.installed_version_ids()

    def is_installed(self, version: str) -> bool:
        return self.paths.manifest_file(version).is_file()

    def install(
        self,
        version: str,
        *,
        from_dir: Path | None = None,
        channel: str = "cn",
        language: str | None = None,
    ) -> Manifest:
        source: VersionSource
        if channel not in ("cn", "os"):
            raise HoyoError("channel 只能是 cn 或 os")
        if from_dir is not None:
            source = LocalDirectorySource(
                from_dir,
                version,
                channel=cast(Channel, channel),
                game=self.game,
            )
        else:
            source = self.source

        lang = self._language_for_install(language, channel)
        self._progress(f"解析 {self.game.name} {version} 的文件清单")
        manifest = source.resolve(version).model_copy(
            update={"game": self.game.id, "channel": channel}
        )
        voice = Manifest(version=version, game=self.game.id, files=[])
        if from_dir is None and self.game.voice_packs and lang is not None:
            voice = self._resolve_voice(source, version, lang)
        self._ensure_disk_space(
            self._bytes_to_admit([*manifest.files, *voice.files], from_dir),
            from_dir,
        )
        self._admit_all(manifest, source, from_dir)
        self.save_manifest(manifest)
        if voice.files and lang is not None:
            self._admit_all(voice, source, None)
            self.paths.voice_manifest_file(version, lang.code).write_text(
                voice.model_dump_json(indent=2), encoding="utf-8"
            )
        self._progress(f"{self.game.name} {version} 已写入仓库，正在物化游戏目录")
        self.switch(version)
        return manifest

    def install_voice(
        self,
        version: str,
        lang: VoiceLang,
        *,
        source: VersionSource | None = None,
    ) -> Manifest:
        if not self.game.voice_packs:
            return Manifest(version=version, game=self.game.id, files=[])
        source = source or self.source
        manifest = self._resolve_voice(source, version, lang)
        if not manifest.files:
            return manifest
        self._ensure_disk_space(self._bytes_to_admit(manifest.files, None), None)
        self._admit_all(manifest, source, None)
        path = self.paths.voice_manifest_file(version, lang.code)
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return manifest

    def _language_for_install(self, language: str | None, channel: str) -> VoiceLang | None:
        tracks = self.game.voice_packs or self.game.audio_lang or self.game.registry_language
        if language is None and not tracks:
            return None
        return self._ensure_language(language, channel)

    def _resolve_voice(self, source: VersionSource, version: str, lang: VoiceLang) -> Manifest:
        resolve_voice = getattr(source, "resolve_voice", None)
        if resolve_voice is None:
            return Manifest(version=version, game=self.game.id, files=[])
        self._progress(f"解析 {lang.label} 语音包")
        manifest = resolve_voice(version, lang)
        if not manifest.files:
            self._progress(f"版本 {version} 没有独立的 {lang.code} 语音清单")
        return manifest

    def _bytes_to_admit(self, files: Sequence[FileEntry], from_dir: Path | None) -> int:
        if from_dir is not None and self._same_volume(from_dir, self.paths.root):
            return 0
        missing = [entry for entry in files if not self.store.has_entry(entry)]
        return self.store.unique_size(missing)

    def _same_volume(self, left: Path, right: Path) -> bool:
        try:
            left_stat = left if left.exists() else left.parent
            right_stat = right if right.exists() else right.parent
            return left_stat.stat().st_dev == right_stat.stat().st_dev
        except OSError:
            return False

    def _ensure_disk_space(self, needed: int, from_dir: Path | None) -> None:
        usage = shutil.disk_usage(self.paths.root)
        required = needed + DISK_MARGIN
        note = "硬链接导入，几乎不占新空间" if needed == 0 and from_dir is not None else ""
        self._progress(
            f"预计写入 {format_bytes(needed)}，预留 {format_bytes(DISK_MARGIN)}，"
            f"可用 {format_bytes(usage.free)}"
            + (f"（{note}）" if note else "")
        )
        if usage.free < required:
            raise InsufficientSpaceError(
                f"磁盘空间不足：{self.paths.root} 所在盘还需要约 {format_bytes(required)}，"
                f"仅剩 {format_bytes(usage.free)}。"
                f"可用 `hoyo set data-dir` 换到空间更大的盘。"
            )

    def _ensure_language(self, language: str | None, channel: str) -> VoiceLang:
        if language:
            lang = parse_voice_lang(language)
        else:
            current = self.current_voice()
            lang = current or parse_voice_lang("zh" if channel == "cn" else "en")
        state = self.load_state()
        if state.voice_language != lang.code:
            state.voice_language = lang.code
            self.save_state(state)
        return lang

    def _tmp_for(self, entry: FileEntry) -> Path:
        name = entry.md5 or entry.sha256 or entry.path.replace("/", "_")
        return self.paths.tmp / name

    def _partial_bytes(self, source: VersionSource, entry: FileEntry) -> int:
        hook = getattr(source, "partial_bytes", None)
        if hook is not None:
            return int(hook(entry, self._tmp_for(entry)))
        return partial_direct_bytes(self._tmp_for(entry), entry.size or None)

    def _admit_all(
        self,
        manifest: Manifest,
        source: VersionSource,
        from_dir: Path | None,
    ) -> None:
        pending: list[FileEntry] = []
        for entry in manifest.files:
            known = self.store.resolve_sha256(entry)
            if known:
                entry.sha256 = known
            else:
                pending.append(entry)
        self._progress(
            f"清单 {len(manifest.files)} 个文件，"
            f"仓库已有 {len(manifest.files) - len(pending)}，"
            f"需获取 {len(pending)}"
        )
        if not pending:
            return
        prefetch = getattr(source, "prefetch", None)
        if prefetch is not None:
            prefetch()
        asyncio.run(self._admit_pending(pending, source, from_dir))

    async def _admit_pending(
        self,
        pending: list[FileEntry],
        source: VersionSource,
        from_dir: Path | None,
    ) -> None:
        total = sum(max(entry.size, 0) for entry in pending)
        resumed = sum(self._partial_bytes(source, entry) for entry in pending)
        self._downloads.start_batch(len(pending), total, resumed)
        file_sem = asyncio.Semaphore(max(1, FILE_CONCURRENCY))
        http_sem = asyncio.Semaphore(max(1, HTTP_CONCURRENCY))
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            tasks = [
                asyncio.create_task(
                    self._admit_one(entry, source, from_dir, client, file_sem, http_sem)
                )
                for entry in pending
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [item for item in results if isinstance(item, BaseException)]
        if errors:
            raise errors[0]

    async def _admit_one(
        self,
        entry: FileEntry,
        source: VersionSource,
        from_dir: Path | None,
        client: httpx.AsyncClient,
        file_sem: asyncio.Semaphore,
        http_sem: asyncio.Semaphore,
    ) -> None:
        async with file_sem:
            resumed = self._partial_bytes(source, entry)
            self._downloads.start_file(entry.path, max(entry.size, 1), resumed)
            try:
                if from_dir is not None:
                    src = from_dir / entry.path
                    entry.sha256 = await asyncio.to_thread(
                        self.store.put_file,
                        src,
                        expected_hash=entry.sha256 or None,
                        expected_md5=entry.md5,
                        move=False,
                    )
                    leftover = max(entry.size - resumed, 0)
                    if leftover:
                        self._downloads.advance(entry.path, leftover)
                    return
                tmp = self._tmp_for(entry)
                tmp.parent.mkdir(parents=True, exist_ok=True)
                complete = tmp.is_file() and (entry.size <= 0 or tmp.stat().st_size == entry.size)
                if not complete:
                    afetch = getattr(source, "afetch", None)
                    if afetch is not None:

                        def on_bytes(n: int, path: str = entry.path) -> None:
                            self._downloads.advance(path, n)

                        await afetch(
                            entry,
                            tmp,
                            client=client,
                            on_bytes=on_bytes,
                            semaphore=http_sem,
                        )
                    else:
                        await asyncio.to_thread(source.fetch, entry, tmp)
                        leftover = max(entry.size - resumed, 0)
                        if leftover:
                            self._downloads.advance(entry.path, leftover)
                entry.sha256 = await asyncio.to_thread(
                    self.store.put_file,
                    tmp,
                    expected_hash=entry.sha256 or None,
                    expected_md5=entry.md5,
                    move=True,
                )
            finally:
                self._downloads.finish_file(entry.path)

    def switch(self, version: str) -> MaterializeStats:
        manifest = self._combined_manifest(version)
        dest = self.paths.worktree
        self._progress(f"物化工作副本 {dest}")
        stats = self.store.materialize(manifest, dest)
        state = self.load_state()
        state.current_version = version
        self.save_state(state)
        self._apply_voice(dest, state)
        if state.voice_language:
            self._apply_registry(parse_voice_lang(state.voice_language))
        write_config_ini(dest, version=version, channel=manifest.channel, game=self.game)
        sdk = ensure_channel_sdk(dest)
        if sdk:
            self._progress(f"已放入渠道 SDK: {sdk.name}")
        else:
            self._progress(
                "未找到 play_pc_sdk.dll。请用米哈游启动器「查找游戏」指向该目录，"
                "或从官服安装复制该 DLL。"
            )
        return stats

    def _combined_manifest(self, version: str) -> Manifest:
        game = self.load_manifest(version)
        state = self.load_state()
        if not state.voice_language:
            return game
        voice_path = self.paths.voice_manifest_file(version, state.voice_language)
        if not voice_path.is_file():
            return game
        voice = Manifest.model_validate_json(voice_path.read_text(encoding="utf-8"))
        by_path = game.by_path()
        files = list(game.files)
        for entry in voice.files:
            if entry.path not in by_path:
                files.append(entry)
        return game.model_copy(update={"files": files})

    def current_worktree(self) -> tuple[str, Path, Manifest]:
        state = self.load_state()
        if not state.current_version:
            raise VersionNotInstalledError(
                f"尚未切换任何版本，请先 `hoyo switch {self.game.id} <version>`"
            )
        version = state.current_version
        manifest = self.load_manifest(version)
        worktree = self.paths.worktree
        if not worktree.is_dir():
            raise VersionNotInstalledError(
                f"当前版本 {version} 的工作副本不存在，"
                f"请重新 `hoyo switch {self.game.id} {version}`"
            )
        return version, worktree, manifest

    def blob_count(self) -> int:
        if not self.paths.blobs.is_dir():
            return 0
        return sum(1 for path in self.paths.blobs.rglob("*") if path.is_file())

    def current_voice(self) -> VoiceLang | None:
        code = self.load_state().voice_language
        if not code:
            return None
        return parse_voice_lang(code)

    def set_language(self, code: str) -> VoiceLang:
        lang = parse_voice_lang(code)
        state = self.load_state()
        state.voice_language = lang.code
        self.save_state(state)
        if state.current_version and self.is_installed(state.current_version):
            try:
                self.install_voice(state.current_version, lang)
            except HoyoError as exc:
                self._progress(f"语音包未能下载（{exc}），仍会写入语言设置")
            if self.paths.worktree.is_dir():
                self.switch(state.current_version)
                return lang
        worktree: Path | None = None
        if state.current_version:
            candidate = self.paths.worktree
            if candidate.is_dir():
                worktree = candidate
        written = self._apply_voice(worktree, state) if worktree else []
        registry = self._apply_registry(lang)
        self._progress(
            f"语音已设为 {lang.code} ({lang.label} / {lang.folder})"
            + (f"，写入 {len(written)} 个 audio_lang 文件" if written else "")
            + (f"，更新 {len(registry)} 项注册表" if registry else "")
        )
        return lang

    def _apply_voice(self, worktree: Path, state: State) -> list[Path]:
        if not state.voice_language or not self.game.audio_lang:
            return []
        lang = parse_voice_lang(state.voice_language)
        return _apply_worktree_lang(worktree, lang, self.game.data_dirs)

    def _apply_registry(self, lang: VoiceLang) -> list[str]:
        if not self.game.registry_language:
            return []
        return _apply_registry_keys(lang, self.game.registry_keys)
