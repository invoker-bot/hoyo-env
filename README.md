# hoyo

米哈游 PC 游戏版本管理 CLI。支持：

| ID | 游戏 | 别名 | 语音 |
|----|------|------|------|
| `hk4e` | 原神 | `genshin`、`yuanshen`、`gi` | 独立语音包 |
| `hkrpg` | 崩坏：星穹铁道 | `hsr`、`starrail` | 含在本体 |
| `nap` | 绝区零 | `zzz` | 独立语音包 |
| `bh3` | 崩坏3 | `hi3`、`honkai3` | 含在本体 |

各版本文件进**该游戏自己的内容寻址仓库**。下载和切换时：

- 已经有的文件（按 SHA-256）**不再下载**
- 工作副本用 **硬链接** 指向仓库，未改动文件**不复制**

```text
hoyo games
hoyo versions hk4e
hoyo install hk4e 7.1.0
hoyo install hkrpg 4.6.0
hoyo install nap 3.2.0 -l ja
hoyo install bh3 9.1.0
hoyo switch hk4e 7.1.0
hoyo set language hk4e ja
hoyo launch hk4e
hoyo open hk4e
```

`genshin install 5.3.0` 对应 `hoyo install hk4e 5.3.0`。`genshin`、`hsr`、`zzz`、`hi3` 只是游戏 ID 的别名。

远程清单来自 [HoyoFiles](https://hoyo-files.amarea.cn/)（`autopatch.amarea.cn`），文件块来自官方 CDN。有直链时优先直链，否则走 Sophon Chunk。清单是国服；`--channel os` 只改写 `config.ini` 里的 `game_biz` / `cps`，不会换成国际服客户端。

下载：

- **断点续传**：直链用 HTTP `Range` 接着 `.part` 写；Sophon chunk 缓存在 `cache/chunks/`
- **进度条**：Rich 显示总体/单文件进度、速度、剩余时间
- **并发**：默认同时下 4 个文件（asyncio + httpx），HTTP 连接上限 8。可用 `HOYO_FILE_CONCURRENCY` / `HOYO_HTTP_CONCURRENCY` 调整

原神会写入 `audio_lang_*`，并在 Windows 上更新注册表里的 `deviceVoiceLanguageType` / `deviceLanguageType`。绝区零会下载对应语音文件，但不会改注册表。星穹铁道和崩坏3的语音在本体清单里，没有单独的 `Audio_*_pkg_version`。

## 安装

需要 Python 3.11+。

```powershell
pip install -e ".[dev]"
hoyo --help
```

## 命令

| 命令 | 作用 |
|------|------|
| `hoyo games` | 列出支持的游戏 |
| `hoyo versions <game>` | 列出清单中的版本 |
| `hoyo install <game> <version>` | 拉取缺失文件（含当前语言的语音包，若该游戏有独立语音包）；开始前检查磁盘空间 |
| `hoyo install <game> <version> -l ja` | 安装时指定语音 |
| `hoyo install <game> <version> --from-dir <path>` | 从本地游戏目录导入 |
| `hoyo switch <game> <version>` | 把该游戏的当前目录切到该版本 |
| `hoyo launch <game>` | 启动该游戏当前版本 |
| `hoyo open <game>` | 在文件管理器中打开当前游戏目录 |
| `hoyo set language <game> <zh\|en\|ja\|ko>` | 记录语音。原神会同时切文本语言 |
| `hoyo set data-dir <path>` | 切换共享数据目录（省略参数则显示当前值） |
| `hoyo list [game]` | 列出已安装版本 |
| `hoyo status [game]` | 数据目录、当前版本、语音、仓库占用 |

数据目录优先级：`--data-dir` > 环境变量 `HOYO_DATA_DIR` > `hoyo set data-dir` 写入的配置 > 系统默认。

默认数据目录（未 `set` 过时）：

- Windows：`%LOCALAPPDATA%\hoyo`
- Linux/macOS：`$XDG_DATA_HOME/hoyo` 或 `~/.local/share/hoyo`

持久配置（只存数据目录在哪）：

- Windows：`%APPDATA%\hoyo\config.json`
- Linux/macOS：`$XDG_CONFIG_HOME/hoyo/config.json` 或 `~/.config/hoyo/config.json`

```powershell
hoyo set data-dir D:\Games\hoyo
hoyo set data-dir
hoyo status
```

`set data-dir` **不会搬迁**已下载文件。换盘后请自行把旧目录拷到新位置，或重新 `install`。

以前用 `genshin` 时，把旧数据目录里的内容移到新数据目录的 `hk4e` 子目录即可。旧清单没有 `game` 字段也能读。不要把旧的 `config.json` 直接拿来用：那里的 `data_dir` 指的是扁平目录，不是现在的共享根目录。

## 磁盘布局

```text
<data-dir>/
  hk4e/
    blobs/ab/abcd…          # 内容寻址：每个唯一文件只存一份
    manifests/<version>.json
    versions/current/       # 唯一的游戏目录（硬链接工作副本）
    state.json              # 该游戏的当前版本号
    tmp/
  hkrpg/
  nap/
  bh3/
```

同一个版本号可以同时存在于不同游戏里，仓库互不混用。每个游戏只有一个可运行目录 `versions/current`，路径里不带版本号；`hoyo switch` 在这个目录里原地换成目标版本。

切换时会对比目标清单与现有目录：哈希相同且已是指向仓库的硬链接则跳过；只增删、替换有变化的路径。

日志、截图一类可变文件会**复制**而不是硬链接，避免游戏写入污染共享仓库。

极早的版本若只剩整包 zip、没有直链/Chunk，目前还不能按文件抽取。
