"""Voice language codes used by the CN/OS clients.

audio_lang files and GENERAL_DATA ids below match 原神. Other games download
voice files (when the index has them) but do not reuse these paths.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hoyo.errors import HoyoError
from hoyo.games import GAMES


@dataclass(frozen=True)
class VoiceLang:
    code: str
    folder: str
    """Directory name under AudioAssets / value written to audio_lang_* files."""
    voice_id: int
    """GENERAL_DATA.deviceVoiceLanguageType (0–3)."""
    text_id: int
    """GENERAL_DATA.deviceLanguageType (matching text for zh/en/ja/ko)."""
    label: str


VOICE_LANGS: dict[str, VoiceLang] = {
    "zh": VoiceLang("zh", "Chinese", 0, 2, "汉语"),
    "en": VoiceLang("en", "English(US)", 1, 1, "英语"),
    "ja": VoiceLang("ja", "Japanese", 2, 9, "日语"),
    "ko": VoiceLang("ko", "Korean", 3, 10, "韩语"),
}

_ALIASES: dict[str, str] = {
    "zh": "zh",
    "cn": "zh",
    "chs": "zh",
    "sc": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "chinese": "zh",
    "en": "en",
    "us": "en",
    "en-us": "en",
    "en_us": "en",
    "english": "en",
    "ja": "ja",
    "jp": "ja",
    "ja-jp": "ja",
    "ja_jp": "ja",
    "japanese": "ja",
    "ko": "ko",
    "kr": "ko",
    "ko-kr": "ko",
    "ko_kr": "ko",
    "korean": "ko",
}


def parse_voice_lang(value: str) -> VoiceLang:
    key = value.strip().lower().replace(" ", "")
    code = _ALIASES.get(key)
    if code is None:
        allowed = ", ".join(VOICE_LANGS)
        raise HoyoError(f"不支持的语言 {value!r}。可用: {allowed}（或 cn/jp/kr 等别名）")
    return VOICE_LANGS[code]


def is_voice_manifest(name: str) -> bool:
    """True for ``<version>.<lang>.json`` voice manifests, not the game manifest."""
    return any(name.endswith(f".{code}.json") for code in VOICE_LANGS)


def apply_to_worktree(
    worktree: Path,
    lang: VoiceLang,
    data_dirs: Sequence[str] | None = None,
) -> list[Path]:
    """Write Persistent/audio_lang_* so the 原神 client loads this voice pack."""
    names = data_dirs if data_dirs is not None else GAMES["hk4e"].data_dirs
    written: list[Path] = []
    for data_name in names:
        data_dir = worktree / data_name
        if not data_dir.is_dir():
            continue
        persistent = data_dir / "Persistent"
        persistent.mkdir(parents=True, exist_ok=True)
        existing = sorted(persistent.glob("audio_lang*"))
        targets = existing or [persistent / "audio_lang_14"]
        for path in targets:
            path.write_text(lang.folder + "\n", encoding="utf-8")
            written.append(path)
    return written


def apply_to_registry(lang: VoiceLang, keys: Sequence[str] | None = None) -> list[str]:
    """Best-effort: patch GENERAL_DATA in HKCU if the game has already run."""
    if os.name != "nt":
        return []
    import winreg

    subkeys = keys if keys is not None else GAMES["hk4e"].registry_keys
    updated: list[str] = []
    for subkey in subkeys:
        try:
            access = winreg.KEY_READ | winreg.KEY_SET_VALUE
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, access)
        except OSError:
            continue
        try:
            i = 0
            names: list[str] = []
            while True:
                try:
                    names.append(winreg.EnumValue(key, i)[0])
                    i += 1
                except OSError:
                    break
            for name in names:
                if not name.startswith("GENERAL_DATA"):
                    continue
                if _patch_general_data(key, name, lang):
                    updated.append(f"{subkey}\\{name}")
        finally:
            winreg.CloseKey(key)
    return updated


def _patch_general_data(key: object, name: str, lang: VoiceLang) -> bool:
    import winreg

    try:
        raw, typ = winreg.QueryValueEx(key, name)  # type: ignore[attr-defined]
    except OSError:
        return False
    if typ != winreg.REG_BINARY:  # type: ignore[attr-defined]
        return False
    data = bytes(raw)
    text, encoding = _decode_prefs(data)
    if text is None:
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    payload["deviceVoiceLanguageType"] = lang.voice_id
    payload["deviceLanguageType"] = lang.text_id
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(encoding)
    winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, encoded + b"\x00")  # type: ignore[attr-defined]
    return True


def _decode_prefs(data: bytes) -> tuple[str | None, str]:
    blob = data[:-1] if data.endswith(b"\x00") else data
    if b"\x00" in blob[:8]:
        encodings = ("utf-16-le", "utf-8")
    else:
        encodings = ("utf-8", "utf-16-le")
    for encoding in encodings:
        try:
            text = blob.decode(encoding)
            json.loads(text)
            return text, encoding
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None, "utf-8"
