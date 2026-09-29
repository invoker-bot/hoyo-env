"""Sophon chunk manifests: protobuf + zstd."""

from __future__ import annotations

from dataclasses import dataclass, field

import zstandard


@dataclass
class ParsedChunk:
    id: str
    checksum: str
    offset: int
    compressed_size: int
    uncompressed_size: int


@dataclass
class ParsedFile:
    path: str
    chunks: list[ParsedChunk]
    size: int
    checksum: str


@dataclass
class ParsedManifest:
    files: list[ParsedFile]
    by_path: dict[str, ParsedFile] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_path = {f.path: f for f in self.files}


def read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def decode_fields(buf: bytes) -> dict[int, list]:
    i = 0
    fields: dict[int, list] = {}
    n = len(buf)
    while i < n:
        key, i = read_varint(buf, i)
        field_id, wtype = key >> 3, key & 7
        if wtype == 0:
            val, i = read_varint(buf, i)
        elif wtype == 2:
            length, i = read_varint(buf, i)
            val = buf[i : i + length]
            i += length
        elif wtype == 5:
            val = int.from_bytes(buf[i : i + 4], "little")
            i += 4
        elif wtype == 1:
            val = int.from_bytes(buf[i : i + 8], "little")
            i += 8
        else:
            raise ValueError(f"unsupported wire type {wtype}")
        fields.setdefault(field_id, []).append(val)
    return fields


def _str_field(fields: dict[int, list], n: int) -> str:
    raw = fields.get(n, [b""])[0]
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)


def parse_chunk(raw: bytes) -> ParsedChunk:
    f = decode_fields(raw)
    return ParsedChunk(
        id=_str_field(f, 1),
        checksum=_str_field(f, 2),
        offset=int(f.get(3, [0])[0]),
        compressed_size=int(f.get(4, [0])[0]),
        uncompressed_size=int(f.get(5, [0])[0]),
    )


def parse_file(raw: bytes) -> ParsedFile:
    f = decode_fields(raw)
    chunks = [parse_chunk(c) for c in f.get(2, [])]
    return ParsedFile(
        path=_str_field(f, 1),
        chunks=chunks,
        size=int(f.get(4, [0])[0]),
        checksum=_str_field(f, 5),
    )


def parse_manifest_binary(data: bytes, uncompressed_size: int) -> ParsedManifest:
    raw = zstandard.ZstdDecompressor().decompress(data, max_output_size=uncompressed_size)
    files = [parse_file(item) for item in decode_fields(raw).get(1, [])]
    return ParsedManifest(files=files)
