"""Remote source: autopatch.amarea.cn lists + official Sophon / ScatteredFiles CDN.

One index covers hk4e, hkrpg, nap, and bh3. Independent Audio_*_pkg_version
files exist for hk4e and nap; hkrpg and bh3 ship audio inside the main list.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from hoyo.download import (
    adownload_chunks,
    adownload_url,
    http_get,
    partial_chunk_bytes,
    partial_direct_bytes,
)
from hoyo.errors import HoyoError, VersionNotFoundError
from hoyo.games import Game, pick_executable, require_game, sort_versions
from hoyo.language import VoiceLang
from hoyo.models import FileEntry, Manifest
from hoyo.sophon import ParsedFile, parse_manifest_binary
from hoyo.sources import WRITABLE_GLOBS, _match

API_BASE = "https://autopatch.amarea.cn/pkg_version"

AUDIO_PKG_FILES = {
    "zh": "Audio_Chinese_pkg_version",
    "en": "Audio_English(US)_pkg_version",
    "ja": "Audio_Japanese_pkg_version",
    "ko": "Audio_Korean_pkg_version",
}

CHUNK_MATCHING = {
    "game": "game",
    "zh": "zh-cn",
    "en": "en-us",
    "ja": "ja-jp",
    "ko": "ko-kr",
}


def _pkg_entries(text: str) -> list[FileEntry]:
    files: list[FileEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        path = str(rec["remoteName"]).replace("\\", "/")
        md5 = str(rec["md5"]).lower() if rec.get("md5") else None
        files.append(
            FileEntry(
                path=path,
                md5=md5,
                size=int(rec["fileSize"]),
                writable=_match(path, WRITABLE_GLOBS),
            )
        )
    return files


def _executable(files: list[FileEntry], names: Sequence[str]) -> str | None:
    found: dict[str, str] = {}
    wanted = set(names)
    for entry in files:
        name = Path(entry.path).name
        if name in wanted:
            found.setdefault(name, entry.path)
    return pick_executable(found, names)


class HoyoSource:
    name = "hoyo"

    def __init__(
        self,
        *,
        api_base: str = API_BASE,
        game: Game | str = "hk4e",
        cache_dir: Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.game = require_game(game)
        self.api_base = api_base.rstrip("/")
        self.game_id = self.game.id
        self.cache_dir = cache_dir
        self._client = client
        self._versions: dict[str, dict[str, Any]] | None = None
        self._active_version: str | None = None
        self._chunk_files: dict[tuple[str, str], tuple[ParsedFile, str]] = {}
        self._chunk_loaded: set[tuple[str, str]] = set()

    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=120.0, follow_redirects=True)

    def _get_text(self, url: str) -> str:
        own = self._client is None
        client = self._http()
        try:
            return http_get(client, url).decode("utf-8")
        finally:
            if own:
                client.close()

    def _get_bytes(self, url: str) -> bytes:
        own = self._client is None
        client = self._http()
        try:
            return http_get(client, url)
        finally:
            if own:
                client.close()

    def _load_versions(self) -> dict[str, dict[str, Any]]:
        if self._versions is None:
            url = f"{self.api_base}/{self.game_id}_versions.json"
            self._versions = json.loads(self._get_text(url))
        return self._versions

    def list_available(self) -> Sequence[str]:
        return sort_versions(self._load_versions())

    def resolve(self, version: str) -> Manifest:
        return self._resolve(version, pkg_name="pkg_version")

    def resolve_voice(self, version: str, lang: VoiceLang) -> Manifest:
        if not self.game.voice_packs:
            return Manifest(version=version, game=self.game.id, files=[])
        pkg = AUDIO_PKG_FILES[lang.code]
        try:
            return self._resolve(version, pkg_name=pkg)
        except HoyoError:
            return Manifest(version=version, game=self.game.id, files=[])

    def _resolve(self, version: str, pkg_name: str) -> Manifest:
        versions = self._load_versions()
        if version not in versions:
            raise VersionNotFoundError(f"清单中没有版本 {version}")
        self._active_version = version
        url = f"{self.api_base}/{self.game_id}/{version}/{pkg_name}"
        try:
            text = self._get_text(url)
        except HoyoError as exc:
            raise HoyoError(f"无法读取 {pkg_name}（{version}）: {exc}") from exc
        files = _pkg_entries(text)
        return Manifest(
            version=version,
            game=self.game.id,
            channel="cn",
            executable=_executable(files, self.game.executables),
            files=files,
        )

    def prefetch(self) -> None:
        if not self._active_version:
            return
        for matching in CHUNK_MATCHING.values():
            self._ensure_chunks(self._active_version, matching)

    def partial_bytes(self, entry: FileEntry, dest: Path) -> int:
        if dest.is_file():
            return dest.stat().st_size
        direct = partial_direct_bytes(dest, entry.size or None)
        if direct:
            return direct
        if not self._active_version or self.cache_dir is None:
            return 0
        found = self._find_chunk(self._active_version, entry.path)
        if found is None:
            return 0
        parsed, _prefix = found
        return partial_chunk_bytes(parsed, self.cache_dir)

    def fetch(self, entry: FileEntry, dest: Path) -> None:
        async def _run() -> None:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                await self.afetch(entry, dest, client=client)

        asyncio.run(_run())

    async def afetch(
        self,
        entry: FileEntry,
        dest: Path,
        *,
        client: httpx.AsyncClient,
        on_bytes: Callable[[int], None] | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        version = self._active_version
        if not version:
            raise HoyoError("内部错误：尚未 resolve 版本")
        vd = self._load_versions()[version]
        prefix = vd.get("decompressed_path")
        if prefix:
            url = f"{str(prefix).rstrip('/')}/{entry.path}"
            try:
                await adownload_url(
                    client,
                    url,
                    dest,
                    expected_size=entry.size or None,
                    on_bytes=on_bytes,
                    semaphore=semaphore,
                )
                return
            except HoyoError:
                pass
        found = self._find_chunk(version, entry.path)
        if found is None:
            raise HoyoError(
                f"无法获取 {entry.path}。该版本可能只提供整包 zip，当前尚未支持从 zip 抽取。"
            )
        parsed, chunk_prefix = found
        cache = self.cache_dir or dest.parent
        await adownload_chunks(
            parsed,
            chunk_prefix,
            dest,
            client=client,
            cache_dir=cache,
            on_bytes=on_bytes,
            semaphore=semaphore,
        )

    def _find_chunk(self, version: str, path: str) -> tuple[ParsedFile, str] | None:
        for matching in CHUNK_MATCHING.values():
            self._ensure_chunks(version, matching)
            hit = self._chunk_files.get((version + ":" + matching, path))
            if hit:
                return hit
        return None

    def _ensure_chunks(self, version: str, matching: str) -> None:
        key = (version, matching)
        if key in self._chunk_loaded:
            return
        self._chunk_loaded.add(key)
        url = f"{self.api_base}/chunk/{self.game_id}_{version}.json"
        try:
            payload = json.loads(self._get_text(url))
        except HoyoError:
            return
        manifests = (payload.get("data") or {}).get("manifests") or []
        for item in manifests:
            if item.get("matching_field") != matching:
                continue
            prefix = item["chunk_download"]["url_prefix"]
            man = item["manifest"]
            man_url = f"{item['manifest_download']['url_prefix'].rstrip('/')}/{man['id']}"
            uncompressed = int(man["uncompressed_size"])
            parsed = self._load_sophon(man["id"], man_url, uncompressed)
            for file in parsed.files:
                self._chunk_files[(version + ":" + matching, file.path)] = (file, prefix)

    def _load_sophon(self, manifest_id: str, url: str, uncompressed: int) -> Any:
        cache: Path | None = None
        if self.cache_dir is not None:
            cache = self.cache_dir / "sophon" / manifest_id
            if cache.is_file():
                return parse_manifest_binary(cache.read_bytes(), uncompressed)
        data = self._get_bytes(url)
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
        return parse_manifest_binary(data, uncompressed)
