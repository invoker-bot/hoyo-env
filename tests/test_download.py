from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import zstandard

from hoyo.download import adownload_chunks, adownload_url, partial_direct_bytes
from hoyo.sophon import ParsedChunk, ParsedFile


def test_range_resume(tmp_path: Path) -> None:
    body = b"abcdefghij"

    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range")
        if rng == "bytes=4-":
            return httpx.Response(
                206,
                content=body[4:],
                headers={"Content-Range": "bytes 4-9/10"},
            )
        if rng:
            return httpx.Response(416, text="bad range")
        return httpx.Response(200, content=body, headers={"Content-Length": "10"})

    dest = tmp_path / "out.bin"
    part = dest.with_name("out.bin.part")
    part.write_bytes(body[:4])

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await adownload_url(
                client, "https://cdn.example/file", dest, expected_size=10
            )

    asyncio.run(_run())
    assert dest.read_bytes() == body
    assert not part.exists()


def test_range_ignored_restarts(tmp_path: Path) -> None:
    body = b"0123456789"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Length": "10"})

    dest = tmp_path / "out.bin"
    dest.with_name("out.bin.part").write_bytes(b"xxxx")

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await adownload_url(
                client, "https://cdn.example/file", dest, expected_size=10
            )

    asyncio.run(_run())
    assert dest.read_bytes() == body


def test_chunk_cache_skips_http(tmp_path: Path) -> None:
    payload = b"hello-sophon"
    compressed = zstandard.ZstdCompressor().compress(payload)
    parsed = ParsedFile(
        path="YuanShen.exe",
        chunks=[
            ParsedChunk(
                id="cid",
                checksum="ck",
                offset=0,
                compressed_size=len(compressed),
                uncompressed_size=len(payload),
            )
        ],
        size=len(payload),
        checksum="md",
    )
    cache = tmp_path / "cache"
    (cache / "chunks").mkdir(parents=True)
    (cache / "chunks" / "cid").write_bytes(compressed)
    dest = tmp_path / "out.bin"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="should not hit network")

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await adownload_chunks(
                parsed,
                "https://cdn.example/chunks",
                dest,
                client=client,
                cache_dir=cache,
            )

    asyncio.run(_run())
    assert dest.read_bytes() == payload
    assert not (cache / "chunks" / "cid").exists()


def test_partial_direct_bytes(tmp_path: Path) -> None:
    dest = tmp_path / "file"
    assert partial_direct_bytes(dest, 100) == 0
    dest.with_name("file.part").write_bytes(b"abc")
    assert partial_direct_bytes(dest, 100) == 3
