from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
import zstandard

from hoyo.catalog import HoyoSource
from hoyo.errors import HoyoError
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


def _manifest_md5(blob: bytes) -> str:
    raw = zstandard.ZstdDecompressor().decompress(blob)
    return hashlib.md5(raw, usedforsecurity=False).hexdigest()


def test_parse_manifest_rejects_nested_chunk_as_text() -> None:
    """A file record decoded as a chunk fails UTF-8 at the size varint.

    hkrpg chunk ids are 49 bytes and the decompressed MD5 is 32 bytes, so the
    next field starts at offset 85. Compressed size 239683 is encoded as
    ``c3 d0 0e``; treating that chunk message as UTF-8 dies at byte 86.
    """
    nested = _str(1, "i" * 49) + _str(2, "m" * 32) + _var(4, 239683)
    try:
        nested.decode("utf-8")
    except UnicodeDecodeError as exc:
        assert exc.start == 86
        assert "invalid continuation" in exc.reason
    else:
        raise AssertionError("expected the chunk message itself to be invalid UTF-8")
    chunk = _str(1, "cid") + _bytes(2, nested) + _var(4, 1) + _var(5, 1)
    file_msg = _str(1, "GameAssembly.dll") + _bytes(2, chunk) + _var(4, 1) + _str(5, "md")
    inner = _bytes(1, file_msg)
    blob = zstandard.ZstdCompressor().compress(inner)
    with pytest.raises(HoyoError, match="无法解析"):
        parse_manifest_binary(blob, len(inner))


def test_truncated_manifest_raises_hoyo_error() -> None:
    blob, _uncompressed = encode_sophon_manifest("a.bin", "cid", b"xyz")
    raw = zstandard.ZstdDecompressor().decompress(blob)
    cut = raw[:20]
    broken = zstandard.ZstdCompressor().compress(cut)
    with pytest.raises(HoyoError, match="无法解析"):
        parse_manifest_binary(broken, len(cut))


def test_corrupt_sophon_cache_is_replaced(tmp_path: Path) -> None:
    blob, uncompressed = encode_sophon_manifest("StarRail.exe", "cid", b"xyz")
    url = "https://example.test/manifest"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=blob)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HoyoSource(game="hkrpg", cache_dir=tmp_path, client=client)
    cache = tmp_path / "sophon" / "mid"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"not-a-manifest")
    parsed = src._load_sophon("mid", url, uncompressed, len(blob), _manifest_md5(blob))
    assert parsed.by_path["StarRail.exe"].chunks[0].id == "cid"
    assert cache.read_bytes() == blob
    assert seen == [url]
    client.close()


def test_valid_sophon_cache_skips_download(tmp_path: Path) -> None:
    blob, uncompressed = encode_sophon_manifest("StarRail.exe", "cid", b"xyz")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("valid cache should not be downloaded again")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HoyoSource(game="hkrpg", cache_dir=tmp_path, client=client)
    cache = tmp_path / "sophon" / "mid"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(blob)
    parsed = src._load_sophon("mid", "https://example.test/manifest", uncompressed, len(blob))
    assert parsed.by_path["StarRail.exe"].chunks[0].id == "cid"
    client.close()


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
