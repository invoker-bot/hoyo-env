"""Game profiles for the HoYoverse PC clients served by HoyoFiles."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from hoyo.errors import UnknownGameError


@dataclass(frozen=True)
class Game:
    id: str
    name: str
    english: str
    executables: tuple[str, ...]
    data_dirs: tuple[str, ...]
    registry_keys: tuple[str, ...]
    biz_cn: str
    biz_os: str
    aliases: tuple[str, ...] = ()
    #: Separate Audio_*_pkg_version lists exist (hk4e, nap).
    voice_packs: bool = False
    #: Write Persistent/audio_lang_* (原神). Other clients pick language differently.
    audio_lang: bool = False
    #: Patch HKCU GENERAL_DATA language ids. The numeric ids are 原神-specific.
    registry_language: bool = False

    def biz(self, channel: str) -> str:
        if channel == "os":
            return self.biz_os
        return self.biz_cn


GAMES: dict[str, Game] = {
    game.id: game
    for game in (
        Game(
            id="hk4e",
            name="原神",
            english="Genshin Impact",
            executables=("YuanShen.exe", "GenshinImpact.exe"),
            data_dirs=("YuanShen_Data", "GenshinImpact_Data"),
            registry_keys=(
                r"Software\miHoYo\原神",
                r"Software\miHoYo\Genshin Impact",
            ),
            biz_cn="hk4e_cn",
            biz_os="hk4e_global",
            aliases=("genshin", "yuanshen", "gi"),
            voice_packs=True,
            audio_lang=True,
            registry_language=True,
        ),
        Game(
            id="hkrpg",
            name="崩坏：星穹铁道",
            english="Honkai: Star Rail",
            executables=("StarRail.exe",),
            data_dirs=("StarRail_Data",),
            registry_keys=(
                r"Software\miHoYo\崩坏：星穹铁道",
                r"Software\Cognosphere\Star Rail",
            ),
            biz_cn="hkrpg_cn",
            biz_os="hkrpg_global",
            aliases=("hsr", "starrail"),
            voice_packs=False,
        ),
        Game(
            id="nap",
            name="绝区零",
            english="Zenless Zone Zero",
            executables=("ZenlessZoneZero.exe",),
            data_dirs=("ZenlessZoneZero_Data",),
            registry_keys=(
                r"Software\miHoYo\绝区零",
                r"Software\miHoYo\ZenlessZoneZero",
            ),
            biz_cn="nap_cn",
            biz_os="nap_global",
            aliases=("zzz",),
            voice_packs=True,
        ),
        Game(
            id="bh3",
            name="崩坏3",
            english="Honkai Impact 3rd",
            executables=("BH3.exe",),
            data_dirs=("BH3_Data",),
            registry_keys=(
                r"Software\miHoYo\崩坏3",
                r"Software\miHoYo\Honkai Impact 3rd",
            ),
            biz_cn="bh3_cn",
            biz_os="bh3_global",
            aliases=("hi3", "honkai3"),
            voice_packs=False,
        ),
    )
}

_ALIASES: dict[str, str] = {}
for _game_profile in GAMES.values():
    _ALIASES[_game_profile.id] = _game_profile.id
    _ALIASES[f"{_game_profile.id}cn"] = _game_profile.id
    _ALIASES[f"{_game_profile.id}global"] = _game_profile.id
    for _alias in _game_profile.aliases:
        _ALIASES[_alias] = _game_profile.id


def all_games() -> tuple[Game, ...]:
    return tuple(GAMES.values())


def require_game(value: Game | str) -> Game:
    if isinstance(value, Game):
        return value
    key = value.strip().lower().replace(" ", "").replace("_", "")
    game_id = _ALIASES.get(key)
    if game_id is None:
        raise UnknownGameError(
            f"未知游戏 {value!r}。可用: hk4e、hkrpg、nap、bh3"
            "（别名 genshin / hsr / zzz / hi3）。"
        )
    return GAMES[game_id]


def sort_versions(versions: Iterable[str]) -> list[str]:
    """Sort dotted numeric versions (1.9.0 before 1.10.0). Others trail, lexically."""

    def key(version: str) -> tuple[int, tuple[int, ...], str]:
        parts: list[int] = []
        for piece in version.split("."):
            if not piece.isdigit():
                return (1, (), version)
            parts.append(int(piece))
        return (0, tuple(parts), version)

    return sorted(versions, key=key)


def pick_executable(found: Mapping[str, str], names: Sequence[str]) -> str | None:
    """Return the relative path of the first known executable name."""
    for name in names:
        if name in found:
            return found[name]
    return None
