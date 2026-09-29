from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from hoyo import __version__
from hoyo.catalog import HoyoSource
from hoyo.config import Settings, config_path, save_data_dir
from hoyo.errors import HoyoError, LaunchError
from hoyo.games import Game, all_games, require_game
from hoyo.language import VOICE_LANGS
from hoyo.launcher import find_executable, open_folder
from hoyo.launcher import launch as launch_game
from hoyo.manager import Manager, format_bytes, summarize_installed
from hoyo.progress import RichReporter

app = typer.Typer(
    name="hoyo",
    help=(
        "米哈游游戏版本管理：安装、切换、启动 hk4e / hkrpg / nap / bh3。"
        "未改动文件共享存储，不重复复制。"
    ),
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console(stderr=True)

GameArg = Annotated[
    str,
    typer.Argument(
        metavar="GAME",
        help="游戏：hk4e、hkrpg、nap、bh3（别名 genshin / hsr / zzz / hi3）",
    ),
]
VersionArg = Annotated[str, typer.Argument(help="版本号，例如 7.1.0")]
OptionalGameArg = Annotated[
    str | None,
    typer.Argument(metavar="[GAME]", help="游戏；省略则显示全部"),
]


def _settings(data_dir: Path | None) -> Settings:
    if data_dir is not None:
        return Settings(data_dir=data_dir)
    return Settings()


def _data_dir(ctx: typer.Context) -> Path | None:
    root = ctx.find_root()
    return (root.obj or {}).get("data_dir")


def _manager(data_dir: Path | None, game: Game, downloads: RichReporter | None = None) -> Manager:
    if downloads is not None:
        return Manager(_settings(data_dir), game=game, downloads=downloads)
    return Manager(
        _settings(data_dir),
        game=game,
        progress=lambda msg: console.print(f"[dim]{msg}[/dim]"),
    )


def _handle(exc: HoyoError) -> None:
    console.print(f"[red]{exc}[/red]")
    raise typer.Exit(code=1)


def _language_note(game: Game) -> str:
    if game.audio_lang:
        return "下次启动生效。若语音包缺失会尝试下载。"
    if game.voice_packs:
        return "已记录语言并尝试下载语音文件；客户端里仍需选中该语言。"
    return "该游戏没有独立语音包，语音在游戏本体里。"


DataDirOption = Annotated[
    Path | None,
    typer.Option(
        "--data-dir",
        envvar="HOYO_DATA_DIR",
        help="共享数据目录。各游戏放在其子目录（hk4e/、hkrpg/、nap/、bh3/）",
    ),
]


@app.callback()
def main(
    ctx: typer.Context,
    data_dir: DataDirOption = None,
) -> None:
    ctx.obj = {"data_dir": data_dir}


@app.command("games")
def games_cmd() -> None:
    """列出支持的游戏。"""
    table = Table("ID", "名称", "别名", "程序", "语音")
    for game in all_games():
        table.add_row(
            game.id,
            game.name,
            ", ".join(game.aliases),
            ", ".join(game.executables),
            "独立语音包" if game.voice_packs else "含在本体",
        )
    console.print(table)


@app.command("versions")
def versions_cmd(game_id: GameArg) -> None:
    """列出清单里可以安装的版本。"""
    try:
        game = require_game(game_id)
        versions = list(HoyoSource(game=game).list_available())
    except HoyoError as exc:
        _handle(exc)
        return
    if not versions:
        console.print(f"{game.name} 没有可安装版本。")
        return
    console.print(f"{game.name} ({game.id}) 共 {len(versions)} 个版本，最新 {versions[-1]}")
    line: list[str] = []
    for index, version in enumerate(versions, start=1):
        line.append(version)
        if index % 8 == 0:
            console.print("  ".join(line))
            line = []
    if line:
        console.print("  ".join(line))


@app.command()
def install(
    ctx: typer.Context,
    game_id: GameArg,
    version: VersionArg,
    from_dir: Annotated[
        Path | None,
        typer.Option("--from-dir", exists=True, file_okay=False, help="从本地游戏目录导入"),
    ] = None,
    channel: Annotated[
        str,
        typer.Option(help="渠道：cn 或 os。os 只改 config.ini，清单仍是国服"),
    ] = "cn",
    language: Annotated[
        str | None,
        typer.Option("--language", "-l", help="同时安装的语音：zh / en / ja / ko"),
    ] = None,
) -> None:
    """安装指定版本到共享仓库，并生成可启动的游戏目录。

    例: hoyo install hk4e 7.1.0
    """
    try:
        game = require_game(game_id)
        with RichReporter(console) as reporter:
            mgr = _manager(ctx.obj["data_dir"], game, downloads=reporter)
            manifest = mgr.install(version, from_dir=from_dir, channel=channel, language=language)
    except HoyoError as exc:
        _handle(exc)
        return
    worktree = mgr.paths.worktree
    console.print(
        f"[green]已安装[/green] {game.name} ({game.id}) {manifest.version}  "
        f"({len(manifest.files)} 个文件, {format_bytes(manifest.total_size)})"
    )
    console.print(f"数据目录: {mgr.paths.root}")
    console.print(f"游戏目录: {worktree}")
    try:
        exe = find_executable(worktree, manifest, game.executables)
    except LaunchError:
        console.print("可执行文件: (未找到)")
    else:
        console.print(f"可执行文件: {exe}")


@app.command("switch")
def switch_cmd(
    ctx: typer.Context,
    game_id: GameArg,
    version: VersionArg,
) -> None:
    """切换当前版本。未改动文件保留硬链接，不重复复制。"""
    try:
        game = require_game(game_id)
        mgr = _manager(ctx.obj["data_dir"], game)
        stats = mgr.switch(version)
    except HoyoError as exc:
        _handle(exc)
        return
    console.print(
        f"[green]已切换到[/green] {game.name} {version}  "
        f"硬链接 {stats.linked}，复制 {stats.copied}，"
        f"跳过 {stats.skipped}，移除 {stats.removed}"
    )


@app.command()
def launch(
    ctx: typer.Context,
    game_id: GameArg,
    extra: Annotated[
        list[str] | None,
        typer.Argument(help="传给游戏进程的额外参数"),
    ] = None,
) -> None:
    """启动该游戏当前切换的版本。"""
    try:
        game = require_game(game_id)
        mgr = _manager(ctx.obj["data_dir"], game)
        version, worktree, manifest = mgr.current_worktree()
        proc = launch_game(
            worktree,
            manifest,
            extra_args=extra,
            executables=game.executables,
        )
    except HoyoError as exc:
        _handle(exc)
        return
    console.print(f"[green]已启动[/green] {game.name} {version}  (pid {proc.pid})")


@app.command("open")
def open_cmd(ctx: typer.Context, game_id: GameArg) -> None:
    """在文件管理器中打开当前游戏目录。"""
    try:
        game = require_game(game_id)
        mgr = _manager(ctx.obj["data_dir"], game)
        version, worktree, _manifest = mgr.current_worktree()
        open_folder(worktree)
    except HoyoError as exc:
        _handle(exc)
        return
    console.print(f"[green]已打开[/green] {game.name} {version}  游戏目录: {worktree}")


@app.command("list")
def list_cmd(ctx: typer.Context, game_id: OptionalGameArg = None) -> None:
    """列出已安装版本。省略游戏时列出全部。"""
    settings = _settings(_data_dir(ctx))
    if game_id is None:
        rows = summarize_installed(settings.data_dir)
        if not rows:
            console.print("还没有安装任何游戏。")
            console.print("可用: " + "、".join(f"{game.id}（{game.name}）" for game in all_games()))
            return
        table = Table("游戏", "ID", "版本", "当前")
        for game, versions, current in rows:
            if not versions:
                table.add_row(game.name, game.id, current or "", "*")
                continue
            for version in versions:
                table.add_row(game.name, game.id, version, "*" if version == current else "")
        console.print(table)
        return

    try:
        game = require_game(game_id)
        mgr = _manager(settings.data_dir, game)
    except HoyoError as exc:
        _handle(exc)
        return
    current = mgr.load_state().current_version
    versions = mgr.installed_versions()
    if not versions:
        console.print(f"还没有安装 {game.name} 的任何版本。")
        return
    table = Table("游戏", "版本", "当前", "文件数", "清单体积")
    for version in versions:
        manifest = mgr.load_manifest(version)
        table.add_row(
            game.id,
            version,
            "*" if version == current else "",
            str(len(manifest.files)),
            str(manifest.total_size),
        )
    console.print(table)


@app.command()
def status(ctx: typer.Context, game_id: OptionalGameArg = None) -> None:
    """显示数据目录、当前版本和仓库概况。"""
    settings = _settings(_data_dir(ctx))
    root = settings.data_dir.expanduser().resolve()
    console.print(f"数据目录: {root}")
    console.print(f"配置文件: {config_path()}")
    if game_id is None:
        rows = summarize_installed(root)
        if not rows:
            console.print("已安装: (无)")
            console.print("可用: " + "、".join(game.id for game in all_games()))
            return
        for game, versions, current in rows:
            mark = current or "(无)"
            installed = ", ".join(versions) or "(无)"
            console.print(f"{game.name} ({game.id})  当前 {mark}  已安装 {installed}")
        return

    try:
        game = require_game(game_id)
        mgr = _manager(root, game)
    except HoyoError as exc:
        _handle(exc)
        return
    state = mgr.load_state()
    voice = mgr.current_voice()
    if voice is None:
        voice_text = "(未设置)"
    else:
        voice_text = f"{voice.code} ({voice.label} / {voice.folder})"
    console.print(f"游戏: {game.name} ({game.id})")
    console.print(f"游戏数据: {mgr.paths.home}")
    current = state.current_version or "(无)"
    console.print(f"当前版本: {current}")
    if state.current_version:
        worktree = mgr.paths.worktree
        console.print(f"游戏目录: {worktree}")
        try:
            exe = find_executable(
                worktree,
                mgr.load_manifest(state.current_version),
                game.executables,
            )
        except (HoyoError, LaunchError):
            pass
        else:
            console.print(f"可执行文件: {exe}")
    console.print(f"语音: {voice_text}")
    console.print(f"已安装: {', '.join(mgr.installed_versions()) or '(无)'}")
    console.print(f"仓库对象: {mgr.blob_count()}")


@app.command("version")
def version_cmd() -> None:
    """显示 CLI 版本。"""
    console.print(__version__)


set_app = typer.Typer(
    name="set",
    help="修改本地设置（数据目录、语音等）。",
    no_args_is_help=True,
)
app.add_typer(set_app, name="set")


@set_app.command("language")
def set_language(
    ctx: typer.Context,
    game_id: GameArg,
    code: Annotated[
        str | None,
        typer.Argument(help="语音代码：zh / en / ja / ko（可省略以查看当前值）"),
    ] = None,
) -> None:
    """设置游戏语音。原神会同步文本语言。未传代码时显示当前设置。"""
    try:
        game = require_game(game_id)
    except HoyoError as exc:
        _handle(exc)
        return
    mgr = _manager(_data_dir(ctx), game)
    if code is None:
        voice = mgr.current_voice()
        if voice is None:
            console.print(f"{game.name} 尚未设置语音。可用: " + ", ".join(VOICE_LANGS))
        else:
            console.print(f"当前语音: {voice.code} ({voice.label} / {voice.folder})")
        return
    try:
        with RichReporter(console) as reporter:
            mgr = _manager(_data_dir(ctx), game, downloads=reporter)
            lang = mgr.set_language(code)
    except HoyoError as exc:
        _handle(exc)
        return
    console.print(
        f"[green]语音已设为[/green] {game.name} {lang.code} ({lang.label} / {lang.folder})"
    )
    console.print(_language_note(game))


@set_app.command("data-dir")
def set_data_dir_cmd(
    path: Annotated[
        Path | None,
        typer.Argument(help="数据目录路径；省略则显示当前值"),
    ] = None,
) -> None:
    """设置共享数据目录。只改配置，不搬迁已有文件。"""
    if path is None:
        settings = Settings()
        console.print(f"数据目录: {settings.data_dir.expanduser().resolve()}")
        console.print(f"配置文件: {config_path()}")
        console.print("优先级: --data-dir > HOYO_DATA_DIR > 本配置 > 系统默认")
        console.print("各游戏在其子目录中：hk4e/、hkrpg/、nap/、bh3/")
        return
    try:
        resolved = save_data_dir(path)
    except HoyoError as exc:
        _handle(exc)
        return
    console.print(f"[green]数据目录已设为[/green] {resolved}")
    console.print(f"已写入 {config_path()}")
    console.print("不会自动搬迁旧目录里的文件；若已下载过，请自行复制或剪切到新位置。")
