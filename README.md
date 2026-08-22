# Bilibili Subtitles

把 B 站已经提供的字幕保存为本地 Markdown，再按目录、关键词或时间段小范围读取。提取与本地检索都不调用大模型；真正节省 token 的方式，是只把筛选后的字幕片段交给大模型，而不是把整份字幕放进对话。

工具只读取字幕和视频元数据，不下载视频或音频，也不对无字幕视频做语音转写。

## 安全边界

- 只接受完整的 `https://www.bilibili.com/video/BV...` 链接（也接受裸域名 `bilibili.com`）。
- 默认匿名访问，不读取浏览器登录信息。
- 只有显式传入 `-UseBrowserCookies` 时，才临时读取 Chrome 登录状态；不会保存或输出 Cookie。
- 不支持短链接、番剧链接、交互视频，也不绕过会员、付费、登录或地区限制。
- 链接中的 `?p=N` 会只提取指定分 P。
- 未指定分 P 时，默认最多探测 21 P；超过 20 P 会停止并要求选择 `?p=N` / `-Page N`，或显式确认 `-AllParts`。阈值可以配置，但默认保持保守。
- B 站公开接口响应最多读取 8 MiB，单字幕轨最多接受 100,000 条字幕，避免异常响应无限占用本机资源。

## 安装

要求 Windows PowerShell 5.1+ 和 Python 3.11–3.13。在本目录执行：

```powershell
.\setup.ps1
.\install.ps1 -OutputRoot "D:\subtitles" -MaxParts 20
```

`setup.ps1` 创建项目自己的 `.venv`、安装固定版本依赖并运行全部测试。已有环境损坏时使用：

```powershell
.\setup.ps1 -Repair -Python "C:\path\to\python.exe"
```

修复会先保留带时间戳的旧环境，失败时自动还原。`install.ps1` 会先在安装目录暂存并校验启动脚本与配置，最后才切换 CMD 入口；任一步失败都会恢复原安装并清理事务文件。它把轻量启动器安装到用户目录、完整保留用户 PATH，并让全局命令始终指向本项目这份源码。安装目录同时生成 `bili-subtitles.config.json`，所以安装完成后无需重启终端即可使用。

需要做便携安装或隔离验证、且不希望修改用户 PATH 和环境变量时使用：

```powershell
.\install.ps1 -InstallRoot "D:\tools\bili-subtitles" -OutputRoot "D:\subtitles" -NoUserEnvironment
```

此模式仍会生成并校验完整安装文件，但只通过安装目录中的 `bili-subtitles.cmd` 直接运行。

`requirements.txt` 记录直接依赖，`requirements-lock.txt` 固定实际安装的完整依赖集合。更新依赖必须显式更新锁文件并重新执行完整验证，不会在安装时自动追随最新版。

配置优先级为“本次命令 > 用户环境变量 > 安装目录配置 > 安全默认值”。输出目录使用 `BILIBILI_SUBTITLE_OUTPUT_ROOT`，大型合集阈值使用 `BILIBILI_SUBTITLE_MAX_PARTS`；通常只需通过 `install.ps1` 设置一次，也可以在单次命令中用 `-OutputRoot`、`-MaxParts` 覆盖。安装目录配置只保存工具路径、输出路径和分 P 阈值，不保存 Cookies 或其他登录信息。

## 提取字幕

```powershell
# 单视频或不超过 20 P 的合集
bili-subtitles "https://www.bilibili.com/video/BVxxxxxxxxxx"

# 只提取第 3 P（两种写法等价）
bili-subtitles "https://www.bilibili.com/video/BVxxxxxxxxxx?p=3"
bili-subtitles "https://www.bilibili.com/video/BVxxxxxxxxxx" -Page 3

# 明确允许处理大型合集的全部分 P
bili-subtitles "https://www.bilibili.com/video/BVxxxxxxxxxx" -AllParts

# 临时把大型合集阈值改为 30 P
bili-subtitles "https://www.bilibili.com/video/BVxxxxxxxxxx" -MaxParts 30

# 仅在确实需要登录态字幕时启用
bili-subtitles "https://www.bilibili.com/video/BVxxxxxxxxxx" -UseBrowserCookies

# 自动化场景可关闭 stderr 上的实时进度；最终结果仍写入 stdout
bili-subtitles "https://www.bilibili.com/video/BVxxxxxxxxxx?p=3" -NoProgress
```

输出目录中每个 BV 号对应一个文件夹：`index.md` 是给人看的分 P 索引，`manifest.json` 是供后续工具读取的稳定结构化清单，严格命名为 `part-001.md` 等文件保存带 `[HH:MM:SS]` 时间戳的字幕。工具只拥有这三类路径；`part-001-learning-note-draft.md` 等其他文件属于用户，不会被当作字幕。

提取时会在错误输出中实时显示元数据、字幕轨、分 P 准备和原子发布阶段，以及从命令开始计算的耗时；进度不包含字幕正文、Cookie 或授权信息。最终计数与输出目录仍写入标准输出。按 `Ctrl+C` 取消会清理临时目录；如果中断发生在发布期间，会恢复上一次结果并返回取消状态。

完整提取成功后会替换 manifest、索引和严格命名的旧字幕，同时保留同目录里的学习笔记及其他非受管文件；单 P 提取则原子更新该 P，并保留其他分 P 和用户文件。旧版目录第一次执行单 P 更新时，会自动从已有 Markdown 迁移出清单。任何中途失败都会保留上一次成功结果，同一 BV 的并发提取会被拒绝。

## 少量读取，减少 token

```powershell
# 先确认清单有效、覆盖是否完整以及哪些分 P 没有字幕
bili-subtitles -Action Status -Target "D:\subtitles\BVxxxxxxxxxx"

# 先看有多少文件、字幕范围与字符量
bili-subtitles -Action Inventory -Target "D:\subtitles\BVxxxxxxxxxx"

# 只找关键词及少量上下文
bili-subtitles -Action Search -Target "D:\subtitles\BVxxxxxxxxxx" -Query "关键词"

# 只读取 10:00 到 15:00
bili-subtitles -Action Slice -Target "D:\subtitles\BVxxxxxxxxxx\part-001.md" -Start 00:10:00 -End 00:15:00

# 给长字幕生成按字符数划分的范围地图
bili-subtitles -Action Map -Target "D:\subtitles\BVxxxxxxxxxx" -ChunkChars 5000

# 给后续脚本或工具读取稳定的结构化结果
bili-subtitles -Action Search -Target "D:\subtitles\BVxxxxxxxxxx" -Query "关键词" -Format Json
```

这些读取动作默认限制终端输出长度，也可以用 `-MaxChars` 调整。目录读取以 `manifest.json` 引用为准；没有 manifest 的兼容目录只接受严格的 `part-NNN.md`。`Status` 核对 manifest 与受管字幕文件，报告覆盖范围、上次请求以及 `captioned` / `no_subtitles` 数量；清单损坏、版本不支持、缺失正文或孤立的规范字幕文件会失败，而学习笔记等非规范文件会被忽略。`-Format Json` 输出紧凑的 UTF-8 JSON；达到长度限制时只移除完整条目并标记 `truncated`，不会截断成无效 JSON。协议见 [READER_JSON_V1.md](READER_JSON_V1.md)。这些动作不会初始化 yt-dlp、访问 B 站或调用大模型。运行 `bili-subtitles --help` 查看简明帮助。

## 项目结构

- `bilibili_subtitles/`：URL 校验、字幕获取、分 P 选择、Markdown 写入与回滚。
- `bilibili_subtitles/output_manifest.py`：结构化结果协议、旧输出迁移与单 P 合并。
- `bilibili_subtitles/progress.py`：无敏感正文的结构化进度事件与终端格式。
- `bilibili_subtitles/reader.py`：Status、Inventory、Map、Search、Slice、文本/JSON 渲染和输出限长；可以独立单测和被后续工具复用。
- `bilibili-subtitles.ps1`：统一入口，只负责运行环境定位、兼容参数和结果转发。
- `bili-subtitles.cmd`：全局命令启动器。
- `extract.ps1`：兼容旧调用的薄代理，不再包含第二套运行逻辑。
- `setup.ps1` / `install.ps1`：可重建环境和可重复安装。
- `verify.ps1`：统一执行依赖、测试、编译、脚本语法和 Git 跟踪边界检查。
- `GOVERNANCE.md`：稳定底座、私有数据、分支、依赖和提炼功能解冻规则。
- `tests/`：离线单元测试与入口脚本测试。

## 验证与排错

```powershell
.\verify.ps1

# 提交后形成干净治理快照
.\verify.ps1 -RequireClean
```

- 环境缺失或损坏：运行 `.\setup.ps1 -Repair`。
- 匿名访问找不到字幕：确认网页本身确实提供字幕；必要时再显式使用 `-UseBrowserCookies`。
- 匿名提取时，工具会先检查 yt-dlp 可见字幕，再检查公开字幕接口；公开回退已启用时，不会重复显示“字幕需登录”的误导性提示，其他警告仍会正常显示。
- Chrome Cookie 无法读取：关闭占用 Cookie 数据库的 Chrome 后重试。Windows/Chromium 的 DPAPI 解密问题可能仍使登录字幕不可用；不要把完整 Cookie 提交到仓库或第三方服务。
- 提取长时间停留在某一阶段：根据进度中的分 P 和阶段定位；公开接口请求单次最多等待 30 秒。当前不自动重试未知失败，避免把一次等待静默放大为多次等待。

字幕文件仅用于你有权访问和使用的本地学习材料。引用内容前应回看原视频，并遵守平台条款与相关权利限制。
