"""Official-channel config.ini and HoYoChannel SDK probe."""

from __future__ import annotations

import os
from pathlib import Path

from hoyo.games import Game, require_game

CHANNEL_DLLS = (
    "play_pc_sdk.dll",
    "steam_api64.dll",
    "Microsoft.Xbox.Services.GDK.C.Thunks.dll",
    "EOSSDK-Win64-Shipping.dll",
)

HOYOPLAY_HINTS = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "HoYoPlay",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "miHoYo" / "HoYoPlay",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "HoYoPlay",
    (
        Path(os.environ["LOCALAPPDATA"]) / "HoYoPlay"
        if os.environ.get("LOCALAPPDATA")
        else None
    ),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "miHoYo Launcher",
)


def write_config_ini(
    worktree: Path,
    *,
    version: str,
    channel: str = "cn",
    game: Game | str | None = None,
) -> Path:
    """Write the official PC channel config next to the game executable.

    Sophon file lists do not include this; the official launcher generates it.
    ``channel`` selects CN vs global biz/cps. The file bytes themselves still
    come from the CN HoyoFiles index unless imported with ``--from-dir``.
    """
    profile = require_game(game or "hk4e")
    if channel == "os":
        sub_channel = "0"
        cps = "hyp_hoyoverse"
    else:
        sub_channel = "1"
        cps = "hyp_mihoyo"
    body = (
        "[General]\n"
        "channel=1\n"
        f"sub_channel={sub_channel}\n"
        f"cps={cps}\n"
        f"game_version={version}\n"
        f"game_biz={profile.biz(channel)}\n"
    )
    path = worktree / "config.ini"
    path.write_text(body, encoding="utf-8")
    return path


def find_channel_dll(worktree: Path) -> Path | None:
    for name in CHANNEL_DLLS:
        candidate = worktree / name
        if candidate.is_file():
            return candidate
    return None


def find_play_pc_sdk() -> Path | None:
    """Look for play_pc_sdk.dll beside a local HoYoPlay / miHoYo launcher install."""
    for root in HOYOPLAY_HINTS:
        if root is None or not root.is_dir():
            continue
        hit = root / "play_pc_sdk.dll"
        if hit.is_file():
            return hit
        for child in root.glob("*/play_pc_sdk.dll"):
            if child.is_file():
                return child
    return None


def ensure_channel_sdk(worktree: Path) -> Path | None:
    existing = worktree / "play_pc_sdk.dll"
    if existing.is_file():
        return existing
    src = find_play_pc_sdk()
    if src is None:
        return None
    dest = worktree / src.name
    dest.write_bytes(src.read_bytes())
    return dest


def channel_missing_message(worktree: Path) -> str:
    return (
        f"游戏目录缺少渠道 SDK（play_pc_sdk.dll）。\n"
        f"Sophon 清单只有游戏本体，HoYoChannel 还需要官方启动器提供的 DLL。\n"
        f"请安装「米哈游启动器 / HoYoPlay」，用「查找游戏」指向：\n  {worktree}\n"
        f"或把已有官服安装里的 play_pc_sdk.dll 复制到该目录后再 launch。"
    )
