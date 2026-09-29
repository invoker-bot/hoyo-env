from pathlib import Path

from hoyo.models import FileEntry, Manifest
from hoyo.store import BlobStore, same_inode, sha256_file


def test_put_file_deduplicates(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    ha = store.put_file(a)
    hb = store.put_file(b)
    assert ha == hb
    files = [p for p in (tmp_path / "blobs").rglob("*") if p.is_file()]
    assert len(files) == 1


def test_materialize_hardlinks_and_skips_unchanged(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    src = tmp_path / "payload.bin"
    src.write_bytes(b"payload")
    digest = store.put_file(src)
    entry = FileEntry(path="game/payload.bin", sha256=digest, size=7)
    manifest = Manifest(version="1.0", files=[entry])
    dest = tmp_path / "work"

    stats = store.materialize(manifest, dest)
    assert stats.linked == 1
    linked = dest / "game" / "payload.bin"
    assert linked.read_bytes() == b"payload"
    assert same_inode(linked, store.blob_path(digest))

    stats2 = store.materialize(manifest, dest)
    assert stats2.skipped == 1
    assert stats2.linked == 0
    assert stats2.removed == 0


def test_writable_files_are_copied(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    src = tmp_path / "game.log"
    src.write_bytes(b"log")
    digest = store.put_file(src)
    manifest = Manifest(
        version="1.0",
        files=[FileEntry(path="game.log", sha256=digest, size=3, writable=True)],
    )
    dest = tmp_path / "work"
    store.materialize(manifest, dest)
    work = dest / "game.log"
    assert work.read_bytes() == b"log"
    assert not same_inode(work, store.blob_path(digest))
    work.write_bytes(b"mutated")
    assert sha256_file(store.blob_path(digest)) == digest
