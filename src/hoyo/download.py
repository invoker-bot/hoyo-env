"""HTTP downloads with Range resume, Sophon chunk cache, and asyncio."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import zstandard

from hoyo.errors import HoyoError
from hoyo.sophon import ParsedChunk, ParsedFile

USER_AGENT = "hoyo/0.1"
TIMEOUT = httpx.Timeout(120.0, connect=30.0)
OnBytes = Callable[[int], None]


def part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def chunk_cache_path(cache_dir: Path, chunk_id: str) -> Path:
    return cache_dir / "chunks" / chunk_id


async def ahttp_get(client: httpx.AsyncClient, url: str, retries: int = 5) -> bytes:
    last: Exception | None = None
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(1, retries + 1):
        try:
            response = await client.get(url, headers=headers, timeout=TIMEOUT)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as exc:
            last = exc
            if attempt == retries:
                break
            await asyncio.sleep(0.4 * attempt)
    raise HoyoError(f"下载失败 {url}: {last}") from last


def http_get(client: httpx.Client, url: str, retries: int = 5) -> bytes:
    last: Exception | None = None
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(1, retries + 1):
        try:
            response = client.get(url, headers=headers, timeout=TIMEOUT)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as exc:
            last = exc
            if attempt == retries:
                break
            time.sleep(0.4 * attempt)
    raise HoyoError(f"下载失败 {url}: {last}") from last


async def adownload_url(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    expected_size: int | None = None,
    on_bytes: OnBytes | None = None,
    retries: int = 5,
    semaphore: asyncio.Semaphore | None = None,
) -> None:
    """GET ``url`` into ``dest``, resuming via HTTP Range on ``dest.part``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(dest)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            await _download_url_once(
                client,
                url,
                dest,
                part,
                expected_size=expected_size,
                on_bytes=on_bytes,
                semaphore=semaphore,
            )
            return
        except (httpx.HTTPError, OSError, HoyoError) as exc:
            last = exc
            if attempt == retries:
                break
            await asyncio.sleep(0.4 * attempt)
    raise HoyoError(f"下载失败 {url}: {last}") from last


async def _download_url_once(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    part: Path,
    *,
    expected_size: int | None,
    on_bytes: OnBytes | None,
    semaphore: asyncio.Semaphore | None,
) -> None:
    existing = part.stat().st_size if part.is_file() else 0
    if expected_size is not None and expected_size > 0:
        if existing > expected_size:
            part.unlink()
            existing = 0
        elif existing == expected_size:
            part.replace(dest)
            return

    headers = {"User-Agent": USER_AGENT}
    if existing:
        headers["Range"] = f"bytes={existing}-"

    async def _stream() -> None:
        nonlocal existing
        async with client.stream("GET", url, headers=headers, timeout=TIMEOUT) as resp:
            if existing and resp.status_code == 206:
                mode = "ab"
            elif existing and resp.status_code == 200:
                part.unlink(missing_ok=True)
                existing = 0
                mode = "wb"
            else:
                resp.raise_for_status()
                mode = "wb"
                existing = 0
            with part.open(mode) as fh:
                async for chunk in resp.aiter_bytes(1024 * 1024):
                    fh.write(chunk)
                    if on_bytes:
                        on_bytes(len(chunk))

    if semaphore is None:
        await _stream()
    else:
        async with semaphore:
            await _stream()

    got = part.stat().st_size if part.is_file() else 0
    if expected_size is not None and expected_size > 0 and got != expected_size:
        raise HoyoError(f"大小不符 {url}: 得到 {got}，期望 {expected_size}")
    part.replace(dest)


async def adownload_chunks(
    parsed: ParsedFile,
    chunk_prefix: str,
    dest: Path,
    *,
    client: httpx.AsyncClient,
    cache_dir: Path,
    on_bytes: OnBytes | None = None,
    semaphore: asyncio.Semaphore | None = None,
    workers: int = 4,
) -> None:
    """Download zstd chunks (cached on disk) and assemble ``dest``."""
    chunks = sorted(parsed.chunks, key=lambda c: c.offset)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not chunks:
        dest.write_bytes(b"")
        return

    cache_root = cache_dir / "chunks"
    cache_root.mkdir(parents=True, exist_ok=True)
    limit = asyncio.Semaphore(workers)

    async def one(chunk: ParsedChunk) -> tuple[int, Path]:
        cached = cache_root / chunk.id
        if cached.is_file() and cached.stat().st_size == chunk.compressed_size:
            if on_bytes:
                on_bytes(chunk.uncompressed_size)
            return chunk.offset, cached
        url = f"{chunk_prefix.rstrip('/')}/{chunk.id}"
        tmp = cached.with_name(cached.name + ".part")

        async def fetch_compressed() -> None:
            await _download_url_once(
                client,
                url,
                cached,
                tmp,
                expected_size=chunk.compressed_size or None,
                on_bytes=None,
                semaphore=semaphore,
            )

        async with limit:
            last: Exception | None = None
            for attempt in range(1, 6):
                try:
                    await fetch_compressed()
                    break
                except (httpx.HTTPError, OSError, HoyoError) as exc:
                    last = exc
                    await asyncio.sleep(0.4 * attempt)
            else:
                raise HoyoError(f"chunk {chunk.id} 失败: {last}") from last
        if on_bytes:
            on_bytes(chunk.uncompressed_size)
        return chunk.offset, cached

    results = await asyncio.gather(*[one(c) for c in chunks])
    by_offset = dict(results)
    assembled = part_path(dest)
    with assembled.open("wb") as fh:
        for chunk in chunks:
            compressed = by_offset[chunk.offset].read_bytes()
            data = await asyncio.to_thread(
                zstandard.ZstdDecompressor().decompress,
                compressed,
                chunk.uncompressed_size,
            )
            if len(data) != chunk.uncompressed_size:
                raise HoyoError(
                    f"chunk {chunk.id} 解压大小 {len(data)} != {chunk.uncompressed_size}"
                )
            fh.write(data)
    assembled.replace(dest)
    for chunk in chunks:
        (cache_root / chunk.id).unlink(missing_ok=True)
        (cache_root / chunk.id).with_name(chunk.id + ".part").unlink(missing_ok=True)


def partial_direct_bytes(dest: Path, expected_size: int | None = None) -> int:
    if dest.is_file():
        return dest.stat().st_size
    part = part_path(dest)
    if not part.is_file():
        return 0
    size = part.stat().st_size
    if expected_size is not None and expected_size > 0:
        return min(size, expected_size)
    return size


def partial_chunk_bytes(parsed: ParsedFile, cache_dir: Path) -> int:
    total = 0
    root = cache_dir / "chunks"
    for chunk in parsed.chunks:
        cached = root / chunk.id
        if cached.is_file() and cached.stat().st_size == chunk.compressed_size:
            total += chunk.uncompressed_size
    return total
