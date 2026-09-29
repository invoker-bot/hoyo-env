from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["cn", "os"]


class FileEntry(BaseModel):
    """A single file in a version snapshot."""

    path: str = Field(description="Relative POSIX path inside the version worktree")
    sha256: str = ""
    md5: str | None = None
    size: int = Field(ge=0)
    writable: bool = Field(
        default=False,
        description="Copy instead of hardlink so in-place writes do not mutate the blob store",
    )


class Manifest(BaseModel):
    """Complete file list for one game version."""

    version: str
    game: str = "hk4e"
    channel: Channel = "cn"
    executable: str | None = Field(
        default=None,
        description="Relative POSIX path to the game executable, if known",
    )
    files: list[FileEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    def by_path(self) -> dict[str, FileEntry]:
        return {f.path: f for f in self.files}


class State(BaseModel):
    current_version: str | None = None
    voice_language: str | None = Field(
        default=None,
        description="Active voice pack: zh / en / ja / ko",
    )


class MaterializeStats(BaseModel):
    linked: int = 0
    copied: int = 0
    skipped: int = 0
    removed: int = 0
