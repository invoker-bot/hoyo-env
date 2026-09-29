from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import zstandard

from hoyo.catalog import HoyoSource
from hoyo.language import parse_voice_lang
from hoyo.sophon import parse_manifest_binary
from hoyo.store import BlobStore, md5_file


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _key(field: int, wtype: int) -> bytes:
    return _varint((field << 3) | wtype)


def _bytes(field: int, data: bytes) -> bytes:
    return _key(field, 2) + _varint(len(data)) + data


def _str(field: int, value: str) -> bytes:
    return _bytes(field, value.encode())


def _var(field: int, n: int) -> bytes:
    return _key(field, 0) + _varint(n)


def encode_sophon_manifest(path: str, chunk_id: str, payload: bytes) -> tuple[bytes, int]:
    compressed = zstandard.ZstdCompressor().compress(payload)
    chunk = (
        _str(1, chunk_id)
        + _str(2, "ck")
        + _var(3, 0)
        + _var(4, len(compressed))
        + _var(5, len(payload))
    )
    file_msg = _str(1, path) + _bytes(2, chunk) + _var(3, 0) + _var(4, len(payload)) + _str(5, "md")
    inner = _bytes(1, file_msg)
    blob = zstandard.ZstdCompressor().compress(inner)
    return blob, len(inner)


GAME_LINE = json.dumps(
    {
        "remoteName": "YuanShen.exe",
        "md5": "d41d8cd98f00b204e9800998ecf8427e",
        "fileSize": 0,
    }
)
VOICE_LINE = json.dumps(
    {
        "remoteName": "YuanShen_Data/StreamingAssets/AudioAssets/Japanese/1001.pck",
        "md5": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "fileSize": 4,
    }
)


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("hk4e_versions.json"):
        return httpx.Response(
            200,
            json={
                "7.0.0": {
                    "decompressed_path": None,
                    "chunk": {"tag": "7.0.0"},
                    "game": {},
                    "voice": {},
                    "update": {},
                }
            },
        )
    if path.endswith("/7.0.0/pkg_version"):
        return httpx.Response(200, text=GAME_LINE + "\n")
    if path.endswith("/7.0.0/Audio_Japanese_pkg_version"):
        return httpx.Response(200, text=VOICE_LINE + "\n")
    if path.endswith("/7.0.0/Audio_Chinese_pkg_version"):
        return httpx.Response(404, text="missing")
    return httpx.Response(404, text="nope")


def test_resolve_game_and_voice() -> None:
    client = httpx.Client(transport=httpx.MockTransport(_handler))
    src = HoyoSource(client=client)
    game = src.resolve("7.0.0")
    assert game.files[0].path == "YuanShen.exe"
    assert game.executable == "YuanShen.exe"
    assert game.game == "hk4e"
    voice = src.resolve_voice("7.0.0", parse_voice_lang("ja"))
    assert voice.files[0].path.endswith("Japanese/1001.pck")
    empty = src.resolve_voice("7.0.0", parse_voice_lang("zh"))
    assert empty.files == []
    with pytest.raises(Exception, match="没有版本"):
        src.resolve("0.0.0")


def test_parse_manifest_and_md5_index(tmp_path: Path) -> None:
    payload = b"hello-sophon"
    blob, uncompressed = encode_sophon_manifest("YuanShen.exe", "cid", payload)
    parsed = parse_manifest_binary(blob, uncompressed)
    assert parsed.by_path["YuanShen.exe"].chunks[0].id == "cid"
    assert parsed.by_path["YuanShen.exe"].chunks[0].uncompressed_size == len(payload)

    src = tmp_path / "a.bin"
    src.write_bytes(payload)
    store = BlobStore(tmp_path / "blobs", md5_index=tmp_path / "md5")
    digest = store.put_file(src, expected_md5=md5_file(src))
    from hoyo.models import FileEntry

    entry = FileEntry(path="YuanShen.exe", md5=md5_file(src), size=len(payload))
    assert store.has_entry(entry)
    assert store.resolve_sha256(entry) == digest


def test_other_games_share_the_index() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path.endswith("nap_versions.json"):
            return httpx.Response(200, json={"1.10.0": {}, "1.2.0": {}, "1.0.0": {}})
        if path.endswith("hkrpg_versions.json"):
            return httpx.Response(200, json={"4.6.0": {}})
        if path.endswith("bh3_versions.json"):
            return httpx.Response(200, json={"9.1.0": {}})
        if path.endswith("/hkrpg/4.6.0/pkg_version"):
            line = json.dumps(
                {"remoteName": "StarRail.exe", "md5": "ab" * 16, "fileSize": 3}
            )
            return httpx.Response(200, text=line + "\n")
        if path.endswith("/bh3/9.1.0/pkg_version"):
            line = json.dumps(
                {
                    "remoteName": "BH3.exe",
                    "md5": "AABBCCDDEEFF00112233445566778899",
                    "fileSize": 4,
                }
            )
            return httpx.Response(200, text=line + "\n")
        if "Audio_" in path:
            return httpx.Response(200, text=VOICE_LINE + "\n")
        return httpx.Response(404, text="nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    nap = HoyoSource(game="zzz", client=client)
    assert nap.game_id == "nap"
    assert nap.list_available() == ["1.0.0", "1.2.0", "1.10.0"]

    hsr = HoyoSource(game="hsr", client=client)
    manifest = hsr.resolve("4.6.0")
    assert manifest.game == "hkrpg"
    assert manifest.executable == "StarRail.exe"
    voice = hsr.resolve_voice("4.6.0", parse_voice_lang("ja"))
    assert voice.files == []

    bh3 = HoyoSource(game="hi3", client=client)
    bh3_manifest = bh3.resolve("9.1.0")
    assert bh3_manifest.executable == "BH3.exe"
    assert bh3_manifest.files[0].md5 == "aabbccddeeff00112233445566778899"
    assert not any("Audio_" in path for path in seen)
