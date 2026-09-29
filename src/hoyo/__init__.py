"""米哈游游戏版本管理器。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("hoyo-env")
except PackageNotFoundError:  # pragma: no cover - editable/uninstalled fallback
    __version__ = "0.1.1"

__all__ = ["__version__"]
