"""Download progress reporting (Rich bars in the CLI, no-op in tests)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)


class DownloadReporter(Protocol):
    def log(self, message: str) -> None: ...
    def start_batch(self, files: int, total_bytes: int, resumed_bytes: int = 0) -> None: ...
    def start_file(self, path: str, size: int, resumed: int = 0) -> None: ...
    def advance(self, path: str, nbytes: int) -> None: ...
    def finish_file(self, path: str) -> None: ...


class NullReporter:
    def log(self, message: str) -> None:
        return

    def start_batch(self, files: int, total_bytes: int, resumed_bytes: int = 0) -> None:
        return

    def start_file(self, path: str, size: int, resumed: int = 0) -> None:
        return

    def advance(self, path: str, nbytes: int) -> None:
        return

    def finish_file(self, path: str) -> None:
        return


class RichReporter:
    def __init__(self, console: Console) -> None:
        self.console = console
        self._progress = Progress(
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            expand=True,
        )
        self._overall: TaskID | None = None
        self._files: dict[str, TaskID] = {}
        self._started = False
        self._done_files = 0
        self._total_files = 0

    def __enter__(self) -> RichReporter:
        self._progress.start()
        self._started = True
        return self

    def __exit__(self, *exc: object) -> None:
        self._progress.stop()
        self._started = False

    def log(self, message: str) -> None:
        if self._started:
            self._progress.log(message)
        else:
            self.console.print(f"[dim]{message}[/dim]")

    def start_batch(self, files: int, total_bytes: int, resumed_bytes: int = 0) -> None:
        self._total_files = files
        self._done_files = 0
        self._overall = self._progress.add_task(
            f"下载 0/{files}",
            total=max(total_bytes, 1),
            completed=min(resumed_bytes, total_bytes),
        )

    def start_file(self, path: str, size: int, resumed: int = 0) -> None:
        name = Path(path).name
        task = self._progress.add_task(name, total=max(size, 1), completed=min(resumed, size))
        self._files[path] = task

    def advance(self, path: str, nbytes: int) -> None:
        if nbytes <= 0:
            return
        task = self._files.get(path)
        if task is not None:
            self._progress.advance(task, nbytes)
        if self._overall is not None:
            self._progress.advance(self._overall, nbytes)

    def finish_file(self, path: str) -> None:
        task = self._files.pop(path, None)
        if task is not None:
            self._progress.remove_task(task)
        self._done_files += 1
        if self._overall is not None:
            self._progress.update(
                self._overall, description=f"下载 {self._done_files}/{self._total_files}"
            )
