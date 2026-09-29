from __future__ import annotations

import hashlib
import os
import shutil
import threading
from collections.abc import Iterable
from pathlib import Path

from hoyo.errors import HashMismatchError, MissingBlobError
from hoyo.models import FileEntry, Manifest, MaterializeStats

CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    return hash_file(path)[1]


def md5_file(path: Path) -> str:
    return hash_file(path)[0]


def hash_file(path: Path) -> tuple[str, str]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            md5.update(chunk)
            sha.update(chunk)
    return md5.hexdigest(), sha.hexdigest()


def same_inode(a: Path, b: Path) -> bool:
    """True if both paths are the same file (hardlink or identical path)."""
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    if sa.st_ino and sb.st_ino:
        return sa.st_dev == sb.st_dev and sa.st_ino == sb.st_ino
    return os.path.samefile(a, b)


def hardlink_or_copy(src: Path, dest: Path) -> str:
    """Create dest as a hardlink of src; copy if the volumes differ.

    Returns ``"linked"`` or ``"copied"``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        os.link(src, dest)
        return "linked"
    except OSError:
        shutil.copy2(src, dest)
        return "copied"


class BlobStore:
    """Content-addressed store. Each unique SHA-256 is stored once."""

    def __init__(self, root: Path, md5_index: Path | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.md5_index = md5_index if md5_index is not None else root.parent / "md5"
        self.md5_index.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def blob_path(self, sha256: str) -> Path:
        sha256 = sha256.lower()
        return self.root / sha256[:2] / sha256

    def md5_path(self, md5: str) -> Path:
        md5 = md5.lower()
        return self.md5_index / md5[:2] / md5

    def has(self, sha256: str) -> bool:
        return bool(sha256) and self.blob_path(sha256).is_file()

    def sha256_for_md5(self, md5: str) -> str | None:
        path = self.md5_path(md5)
        if not path.is_file():
            return None
        digest = path.read_text(encoding="utf-8").strip()
        if digest and self.has(digest):
            return digest
        return None

    def has_entry(self, entry: FileEntry) -> bool:
        if entry.sha256 and self.has(entry.sha256):
            return True
        if entry.md5 and self.sha256_for_md5(entry.md5):
            return True
        return False

    def resolve_sha256(self, entry: FileEntry) -> str | None:
        if entry.sha256 and self.has(entry.sha256):
            return entry.sha256.lower()
        if entry.md5:
            return self.sha256_for_md5(entry.md5)
        return None

    def _index_md5(self, md5: str, sha256: str) -> None:
        path = self.md5_path(md5)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sha256.lower(), encoding="utf-8")

    def put_file(
        self,
        src: Path,
        *,
        expected_hash: str | None = None,
        expected_md5: str | None = None,
        move: bool = False,
    ) -> str:
        """Admit ``src`` into the store. Skip if the blob already exists.

        Prefers a hardlink so importing an existing game install does not
        duplicate bytes. Falls back to copy/move across volumes.
        """
        with self._lock:
            return self._put_file_locked(
                src, expected_hash=expected_hash, expected_md5=expected_md5, move=move
            )

    def _put_file_locked(
        self,
        src: Path,
        *,
        expected_hash: str | None,
        expected_md5: str | None,
        move: bool,
    ) -> str:
        md5, digest = hash_file(src)
        if expected_hash and digest != expected_hash.lower():
            raise HashMismatchError(
                f"哈希不匹配: {src} 实际 {digest}，期望 {expected_hash}"
            )
        if expected_md5 and md5 != expected_md5.lower():
            raise HashMismatchError(
                f"MD5 不匹配: {src} 实际 {md5}，期望 {expected_md5}"
            )
        dest = self.blob_path(digest)
        if dest.exists():
            self._index_md5(md5, digest)
            if move:
                src.unlink(missing_ok=True)
            return digest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.unlink(missing_ok=True)
        placed = False
        if not move:
            try:
                os.link(src, dest)
                placed = True
            except OSError:
                shutil.copy2(src, tmp)
        else:
            try:
                os.replace(src, dest)
                placed = True
            except OSError:
                shutil.copy2(src, tmp)
                src.unlink(missing_ok=True)
        if not placed:
            os.replace(tmp, dest)
        self._index_md5(md5, digest)
        return digest

    def link_into(self, sha256: str, dest: Path, *, copy: bool = False) -> str:
        blob = self.blob_path(sha256)
        if not blob.is_file():
            raise MissingBlobError(f"仓库中缺少文件 {sha256}")
        if dest.exists() and same_inode(dest, blob) and not copy:
            return "skipped"
        if copy:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            shutil.copy2(blob, dest)
            return "copied"
        if dest.exists() and not same_inode(dest, blob):
            dest.unlink()
        return hardlink_or_copy(blob, dest)

    def unique_size(self, entries: Iterable[FileEntry]) -> int:
        seen: set[str] = set()
        total = 0
        for entry in entries:
            key = entry.sha256 or entry.md5 or entry.path
            if key in seen:
                continue
            seen.add(key)
            total += entry.size
        return total

    def materialize(self, manifest: Manifest, dest: Path) -> MaterializeStats:
        """Make ``dest`` match ``manifest`` using hardlinks; skip unchanged files."""
        dest.mkdir(parents=True, exist_ok=True)
        desired = manifest.by_path()
        stats = MaterializeStats()

        extras: list[Path] = []
        for existing in dest.rglob("*"):
            if not existing.is_file():
                continue
            rel = existing.relative_to(dest).as_posix()
            entry = desired.get(rel)
            if entry is None:
                extras.append(existing)
                continue
            blob = self.blob_path(entry.sha256)
            if entry.writable:
                continue
            if blob.is_file() and same_inode(existing, blob):
                continue
            extras.append(existing)
        for path in extras:
            path.unlink()
            stats.removed += 1

        for rel, entry in desired.items():
            target = dest / rel
            if not entry.sha256:
                raise MissingBlobError(f"版本 {manifest.version} 缺少哈希 ({rel})")
            blob = self.blob_path(entry.sha256)
            if not blob.is_file():
                raise MissingBlobError(
                    f"版本 {manifest.version} 缺少 blob {entry.sha256} ({rel})"
                )
            if not entry.writable and target.exists() and same_inode(target, blob):
                stats.skipped += 1
                continue
            if entry.writable and target.exists() and sha256_file(target) == entry.sha256:
                stats.skipped += 1
                continue
            action = self.link_into(entry.sha256, target, copy=entry.writable)
            if action == "copied":
                stats.copied += 1
            elif action == "skipped":
                stats.skipped += 1
            else:
                stats.linked += 1

        self._prune_empty_dirs(dest)
        return stats

    @staticmethod
    def _prune_empty_dirs(root: Path) -> None:
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            if dirpath == str(root):
                continue
            if not dirnames and not filenames:
                Path(dirpath).rmdir()
