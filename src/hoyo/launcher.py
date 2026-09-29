from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from hoyo.channel import channel_missing_message, find_channel_dll
from hoyo.errors import HoyoError, LaunchError
from hoyo.games import GAMES
from hoyo.models import Manifest


def find_executable(
    worktree: Path,
    manifest: Manifest | None = None,
    executables: Sequence[str] | None = None,
) -> Path:
    if manifest and manifest.executable:
        candidate = worktree / manifest.executable
        if candidate.is_file():
            return candidate
    names = tuple(executables) if executables is not None else GAMES["hk4e"].executables
    for name in names:
        matches = list(worktree.rglob(name))
        files = [path for path in matches if path.is_file()]
        if files:
            return files[0]
    raise LaunchError(f"在 {worktree} 中未找到游戏可执行文件（{' / '.join(names)}）")


def open_folder(path: Path) -> None:
    """Open a directory in the system file manager (Explorer / Finder / xdg-open)."""
    folder = path.expanduser().resolve()
    if not folder.is_dir():
        raise HoyoError(f"目录不存在: {folder}")
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
            return
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, str(folder)], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HoyoError(f"无法在文件管理器中打开 {folder}: {exc}") from exc


def launch(
    worktree: Path,
    manifest: Manifest | None = None,
    extra_args: list[str] | None = None,
    *,
    executables: Sequence[str] | None = None,
) -> subprocess.Popen[bytes]:
    exe = find_executable(worktree, manifest, executables)
    if find_channel_dll(exe.parent) is None:
        raise LaunchError(channel_missing_message(exe.parent))
    args = [str(exe), *(extra_args or [])]
    return subprocess.Popen(args, cwd=str(exe.parent))
